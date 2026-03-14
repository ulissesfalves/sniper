from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import redis.asyncio as redis_async

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.contract import build_signal_payload
from services.nautilus_bridge.contract import build_stream_envelope
from services.nautilus_bridge.state import BridgeStateStore


def _mock_target_weights(universe_symbols: tuple[str, ...]) -> dict[str, Decimal]:
    weights: dict[str, Decimal] = {symbol: Decimal("0") for symbol in universe_symbols}
    if universe_symbols:
        weights[universe_symbols[0]] = Decimal("0.05")
    if len(universe_symbols) > 1:
        weights[universe_symbols[1]] = Decimal("0.03")
    return weights


async def publish_mock_signal() -> str:
    config = BridgeConfig()
    universe = config.managed_universe()
    redis = redis_async.from_url(config.redis_url)
    state_store = BridgeStateStore(redis=redis, config=config)
    revision = await state_store.claim_next_revision(config.portfolio_id, config.environment)
    now = datetime.now(UTC)
    target_weights = _mock_target_weights(universe.symbols)
    payload = build_signal_payload(
        {
            "portfolio_id": config.portfolio_id,
            "environment": config.environment,
            "portfolio_revision": revision,
            "signal_version": "sniper.portfolio_target.v1",
            "managed_universe_version": universe.version,
            "policy_name": "mock_publisher_v1",
            "as_of": now.isoformat().replace("+00:00", "Z"),
            "published_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
            "replace_semantics": "FULL_SNAPSHOT",
            "capital_reference": {
                "currency": "USD",
                "notional": config.phase4_capital_reference_notional,
            },
            "risk_envelope": {
                "max_gross_weight": config.default_max_gross_weight,
                "rebalance_band_bps": config.rebalance_band_bps,
                "min_order_notional_usd": config.min_order_notional_usd,
                "cash_reserve_weight": round(1.0 - config.default_max_gross_weight, 4),
            },
            "targets": [
                {
                    "symbol": symbol,
                    "instrument_id": universe.instrument_id_for_symbol(symbol),
                    "target_weight": float(target_weights[symbol]),
                    "target_notional_usd": float(
                        target_weights[symbol] * Decimal(str(config.phase4_capital_reference_notional)),
                    ),
                }
                for symbol in universe.symbols
            ],
            "metadata": {"source": "services.nautilus_bridge.mock_publisher"},
        },
    )
    envelope = build_stream_envelope(payload)
    await redis.xadd(config.target_stream_key, envelope.to_stream_fields())
    await redis.aclose()
    return envelope.message_id


if __name__ == "__main__":
    print(asyncio.run(publish_mock_signal()))
