from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.phase4_publisher import _coerce_as_of
from services.nautilus_bridge.phase4_publisher import publish_phase4_snapshot


class FakeRedis:
    def __init__(self) -> None:
        self.revision = 0
        self.stream_entries: list[tuple[str, dict[str, str]]] = []
        self.closed = False

    async def incr(self, _key: str) -> int:
        self.revision += 1
        return self.revision

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> str:
        self.stream_entries.append((stream_key, fields))
        return "100-0"

    async def aclose(self) -> None:
        self.closed = True


def _write_snapshot(path: Path, *, date_value: str) -> None:
    df = pd.DataFrame(
        [
            {"date": date_value, "symbol": "ADA", "position_usdt": 10000.0, "p_calibrated": 0.81},
            {"date": date_value, "symbol": "SOL", "position_usdt": 0.0, "p_calibrated": 0.42},
        ],
    )
    df.to_parquet(path, index=False)


def test_coerce_as_of_accepts_datetime() -> None:
    assert _coerce_as_of(datetime(2024, 12, 16, 15, 30, tzinfo=UTC)) == "2024-12-16T15:30:00Z"


def test_coerce_as_of_accepts_space_separated_datetime() -> None:
    assert _coerce_as_of("2024-12-16 00:00:00") == "2024-12-16T00:00:00Z"


def test_coerce_as_of_accepts_date_only() -> None:
    assert _coerce_as_of("2024-12-16") == "2024-12-16T00:00:00Z"


def test_coerce_as_of_accepts_pandas_timestamp_when_available() -> None:
    assert _coerce_as_of(pd.Timestamp("2024-12-16 00:00:00")) == "2024-12-16T00:00:00Z"


def test_publish_phase4_snapshot_accepts_recent_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        _write_snapshot(snapshot_path, date_value="2026-03-21T00:00:00Z")
        redis = FakeRedis()
        config = BridgeConfig(
            phase4_snapshot_path=snapshot_path,
            phase4_snapshot_max_asof_age_secs=3 * 24 * 3600,
        )

        message_id = asyncio.run(
            publish_phase4_snapshot(
                config=config,
                redis=redis,
                now_fn=lambda: datetime(2026, 3, 22, 0, 0, tzinfo=UTC),
            ),
        )

        assert isinstance(message_id, str)
        assert len(redis.stream_entries) == 1
        stream_key, fields = redis.stream_entries[0]
        payload = json.loads(fields["payload_json"])
        targets_by_symbol = {row["symbol"]: row for row in payload["targets"]}
        assert stream_key == config.target_stream_key
        assert payload["as_of"] == "2026-03-21T00:00:00Z"
        assert targets_by_symbol["ADA"]["instrument_id"] == "ADAUSDT.BINANCE_SPOT"
        assert targets_by_symbol["ADA"]["target_notional_usd"] == 10000.0


def test_publish_phase4_snapshot_rejects_stale_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        _write_snapshot(snapshot_path, date_value="2024-12-16T00:00:00Z")
        redis = FakeRedis()
        config = BridgeConfig(
            phase4_snapshot_path=snapshot_path,
            phase4_snapshot_max_asof_age_secs=24 * 3600,
        )

        try:
            asyncio.run(
                publish_phase4_snapshot(
                    config=config,
                    redis=redis,
                    now_fn=lambda: datetime(2026, 3, 22, 0, 0, tzinfo=UTC),
                ),
            )
        except RuntimeError as exc:
            assert "phase4 snapshot is stale" in str(exc)
        else:
            raise AssertionError("Expected stale phase4 snapshot to be rejected")
