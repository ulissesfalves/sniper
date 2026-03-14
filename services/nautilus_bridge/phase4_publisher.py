from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.contract import build_signal_payload
from services.nautilus_bridge.contract import build_stream_envelope
from services.nautilus_bridge.state import BridgeStateStore


def _load_snapshot(path: Path):
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on runtime image
        raise RuntimeError("pandas is required to read the phase4 snapshot") from exc
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # pragma: no cover - depends on parquet engine availability
        raise RuntimeError(f"Unable to read parquet snapshot: {path}") from exc


def _coerce_as_of(value: Any) -> str:
    if value is None:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        normalized = text.replace(" ", "T")
        if "T" not in normalized:
            normalized = f"{normalized}T00:00:00"
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat().replace("+00:00", "Z")


async def publish_phase4_snapshot() -> str:
    config = BridgeConfig()
    universe = config.managed_universe()
    snapshot = _load_snapshot(Path(config.phase4_snapshot_path))
    required_columns = {"symbol", "position_usdt"}
    missing_columns = required_columns - set(snapshot.columns)
    if missing_columns:
        raise RuntimeError(f"phase4 snapshot missing required columns: {sorted(missing_columns)}")
    rows_by_symbol = {}
    for record in snapshot.to_dict(orient="records"):
        rows_by_symbol[str(record["symbol"]).strip().upper()] = record
    total_position = Decimal("0")
    capital_notional = Decimal(str(config.phase4_capital_reference_notional))
    targets: list[dict[str, Any]] = []
    as_of = None
    for symbol in universe.symbols:
        row = rows_by_symbol.get(symbol, {})
        position_usdt = Decimal(str(row.get("position_usdt", 0) or 0))
        total_position += position_usdt
        weight = position_usdt / capital_notional if capital_notional > 0 else Decimal("0")
        if as_of is None and row.get("date") is not None:
            as_of = _coerce_as_of(row.get("date"))
        targets.append(
            {
                "symbol": symbol,
                "instrument_id": universe.instrument_id_for_symbol(symbol),
                "target_weight": float(weight),
                "target_notional_usd": float(position_usdt),
                "confidence": float(row.get("p_calibrated", 0) or 0),
                "p_meta": float(row.get("p_calibrated", 0) or 0),
            },
        )
    max_gross = Decimal(str(config.default_max_gross_weight))
    if capital_notional > 0 and (total_position / capital_notional) > max_gross + Decimal("0.000001"):
        raise RuntimeError("Phase4 snapshot exceeds max_gross_weight")
    redis = redis_async.from_url(config.redis_url)
    state_store = BridgeStateStore(redis=redis, config=config)
    revision = await state_store.claim_next_revision(config.portfolio_id, config.environment)
    now = datetime.now(UTC)
    payload = build_signal_payload(
        {
            "portfolio_id": config.portfolio_id,
            "environment": config.environment,
            "portfolio_revision": revision,
            "signal_version": "sniper.portfolio_target.v1",
            "managed_universe_version": universe.version,
            "policy_name": "phase4_snapshot_v1",
            "as_of": as_of or now.isoformat().replace("+00:00", "Z"),
            "published_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "replace_semantics": "FULL_SNAPSHOT",
            "capital_reference": {
                "currency": "USD",
                "notional": float(capital_notional),
            },
            "risk_envelope": {
                "max_gross_weight": config.default_max_gross_weight,
                "rebalance_band_bps": config.rebalance_band_bps,
                "min_order_notional_usd": config.min_order_notional_usd,
                "cash_reserve_weight": round(1.0 - config.default_max_gross_weight, 4),
            },
            "targets": targets,
            "metadata": {
                "source": "data.models.phase4.phase4_execution_snapshot.parquet",
                "artifact_path": str(config.phase4_snapshot_path),
            },
        },
    )
    envelope = build_stream_envelope(payload)
    await redis.xadd(config.target_stream_key, envelope.to_stream_fields())
    await redis.aclose()
    return envelope.message_id


if __name__ == "__main__":
    print(asyncio.run(publish_phase4_snapshot()))
