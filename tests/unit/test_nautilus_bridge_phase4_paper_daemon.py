from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tests import _path_setup  # noqa: F401
from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.run_phase4_paper_daemon import LOCK_ACTIVE_REASON
from services.nautilus_bridge.run_phase4_paper_daemon import run_phase4_paper_daemon
from services.nautilus_bridge.run_phase4_paper_once import Phase4PaperOnceResult


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.closed = False

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value, *, nx: bool = False, ex=None):  # noqa: ANN001
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeClock:
    current: float = 0.0

    def now(self):
        from datetime import UTC
        from datetime import datetime

        self.current += 1.0
        return datetime(2026, 3, 22, 6, 0, tzinfo=UTC)


def _write_snapshot_file(path: Path, content: str = "snapshot-v1") -> str:
    path.write_text(content, encoding="utf-8")
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _successful_run_once(*, config: BridgeConfig) -> Phase4PaperOnceResult:
    _ = config
    return Phase4PaperOnceResult(
        success=True,
        message_id="019cf25d-c795-7dbe-a2cf-273cafe4e13b",
        statuses=("received", "accepted", "submitted"),
    )


async def _successful_run_once_with_kwargs(
    *,
    config: BridgeConfig,
    resume_message_id: str | None = None,
    after_publish=None,  # noqa: ANN001
) -> Phase4PaperOnceResult:
    _ = config
    if after_publish is not None:
        await after_publish("019cf25d-c795-7dbe-a2cf-273cafe4e13b")
    return Phase4PaperOnceResult(
        success=True,
        message_id=resume_message_id or "019cf25d-c795-7dbe-a2cf-273cafe4e13b",
        statuses=("received", "accepted", "submitted"),
    )


def test_daemon_rejects_double_start_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        _write_snapshot_file(snapshot_path)
        redis = FakeRedis()
        config = BridgeConfig(phase4_snapshot_path=snapshot_path)
        redis.values[config.daemon_lock_key()] = "another-daemon"

        result = asyncio.run(
            run_phase4_paper_daemon(
                cycles=1,
                interval_secs=0.0,
                config=config,
                redis_factory=lambda _url: redis,
                run_once_fn=_successful_run_once_with_kwargs,
            ),
        )

        assert result.success is False
        assert result.reason == LOCK_ACTIVE_REASON


def test_daemon_skips_unchanged_snapshot_hash() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        snapshot_hash = _write_snapshot_file(snapshot_path)
        redis = FakeRedis()
        config = BridgeConfig(phase4_snapshot_path=snapshot_path)
        redis.values[config.run_summary_key()] = json.dumps({"last_snapshot_sha256": snapshot_hash})
        calls = {"count": 0}

        async def fake_run_once(
            *,
            config: BridgeConfig,
            resume_message_id: str | None = None,
            after_publish=None,  # noqa: ANN001
        ) -> Phase4PaperOnceResult:
            _ = config
            _ = resume_message_id
            _ = after_publish
            calls["count"] += 1
            return await _successful_run_once(config=config)

        result = asyncio.run(
            run_phase4_paper_daemon(
                cycles=1,
                interval_secs=0.0,
                config=config,
                redis_factory=lambda _url: redis,
                run_once_fn=fake_run_once,
            ),
        )

        assert result.success is True
        assert result.skipped_unchanged == 1
        assert calls["count"] == 0


def test_daemon_updates_heartbeat_and_summary_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        snapshot_hash = _write_snapshot_file(snapshot_path)
        redis = FakeRedis()
        config = BridgeConfig(phase4_snapshot_path=snapshot_path)

        result = asyncio.run(
            run_phase4_paper_daemon(
                cycles=1,
                interval_secs=0.0,
                config=config,
                redis_factory=lambda _url: redis,
                run_once_fn=_successful_run_once_with_kwargs,
            ),
        )

        heartbeat_payload = json.loads(redis.values[config.heartbeat_key()])
        summary_payload = json.loads(redis.values[config.run_summary_key()])

        assert result.success is True
        assert result.last_snapshot_sha256 == snapshot_hash
        assert heartbeat_payload["cycles_completed"] == 1
        assert summary_payload["succeeded"] == 1
        assert summary_payload["last_message_id"] == "019cf25d-c795-7dbe-a2cf-273cafe4e13b"
        assert summary_payload["result"] == "success"
        assert config.daemon_lock_key() not in redis.values
        assert redis.closed is True


def test_daemon_resumes_inflight_cycle_after_restart_without_republish() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        snapshot_hash = _write_snapshot_file(snapshot_path)
        redis = FakeRedis()
        config = BridgeConfig(phase4_snapshot_path=snapshot_path)
        redis.values[config.run_summary_key()] = json.dumps(
            {
                "result": "awaiting_terminal_status",
                "last_snapshot_sha256": snapshot_hash,
                "last_message_id": "resume-msg-1",
            },
        )
        calls: list[dict[str, object]] = []

        async def fake_run_once(
            *,
            config: BridgeConfig,
            resume_message_id: str | None = None,
            after_publish=None,  # noqa: ANN001
        ) -> Phase4PaperOnceResult:
            _ = config
            calls.append(
                {
                    "resume_message_id": resume_message_id,
                    "has_after_publish": after_publish is not None,
                },
            )
            return Phase4PaperOnceResult(
                success=True,
                message_id=resume_message_id or "unexpected-new-message",
                statuses=("received", "accepted", "submitted"),
            )

        result = asyncio.run(
            run_phase4_paper_daemon(
                cycles=1,
                interval_secs=0.0,
                config=config,
                redis_factory=lambda _url: redis,
                run_once_fn=fake_run_once,
            ),
        )

        summary_payload = json.loads(redis.values[config.run_summary_key()])

        assert result.success is True
        assert calls == [{"resume_message_id": "resume-msg-1", "has_after_publish": False}]
        assert result.last_message_id == "resume-msg-1"
        assert summary_payload["last_message_id"] == "resume-msg-1"
        assert summary_payload["result"] == "success"
        assert summary_payload["reason"] == "resumed_inflight_message"


def test_daemon_reconciles_late_confirmation_after_timeout_without_republish() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = Path(tmp_dir) / "phase4_execution_snapshot.parquet"
        snapshot_hash = _write_snapshot_file(snapshot_path)
        redis = FakeRedis()
        config = BridgeConfig(phase4_snapshot_path=snapshot_path)
        redis.values[config.run_summary_key()] = json.dumps(
            {
                "result": "timeout_waiting_terminal_status",
                "last_snapshot_sha256": snapshot_hash,
                "last_message_id": "late-msg-1",
            },
        )
        calls: list[dict[str, object]] = []

        async def fake_run_once(
            *,
            config: BridgeConfig,
            resume_message_id: str | None = None,
            after_publish=None,  # noqa: ANN001
        ) -> Phase4PaperOnceResult:
            _ = config
            calls.append(
                {
                    "resume_message_id": resume_message_id,
                    "has_after_publish": after_publish is not None,
                },
            )
            return Phase4PaperOnceResult(
                success=True,
                message_id=resume_message_id or "unexpected-new-message",
                statuses=("accepted", "submitted"),
            )

        result = asyncio.run(
            run_phase4_paper_daemon(
                cycles=1,
                interval_secs=0.0,
                config=config,
                redis_factory=lambda _url: redis,
                run_once_fn=fake_run_once,
            ),
        )

        summary_payload = json.loads(redis.values[config.run_summary_key()])

        assert result.success is True
        assert calls == [{"resume_message_id": "late-msg-1", "has_after_publish": False}]
        assert result.last_message_id == "late-msg-1"
        assert summary_payload["last_message_id"] == "late-msg-1"
        assert summary_payload["result"] == "success_after_timeout_reconciliation"
        assert summary_payload["reason"] == "reconciled_late_terminal_status"
