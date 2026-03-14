from __future__ import annotations

import asyncio
from pathlib import Path

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.config import load_managed_universe
from services.nautilus_bridge.strategy import NAUTILUS_IMPORT_ERROR
from services.nautilus_bridge.strategy import NautilusBridgeStrategy
from services.nautilus_bridge.strategy import NautilusBridgeStrategyConfig

if NAUTILUS_IMPORT_ERROR is None:  # pragma: no branch - runtime gated by import guard
    from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
    from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
    from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory
    from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
    from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
    from nautilus_trader.config import CacheConfig
    from nautilus_trader.config import InstrumentProviderConfig
    from nautilus_trader.config import LiveExecEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.model.identifiers import Venue


def build_node_config(config: BridgeConfig):
    if NAUTILUS_IMPORT_ERROR is not None:  # pragma: no cover - runtime guarded
        raise RuntimeError("nautilus_trader is required for main.py") from NAUTILUS_IMPORT_ERROR
    managed_universe = load_managed_universe(Path(config.managed_universe_path))
    instrument_ids = frozenset(InstrumentId.from_str(item) for item in managed_universe.instrument_ids)
    return TradingNodeConfig(
        trader_id=TraderId("SNIPER-PAPER-001"),
        logging=LoggingConfig(log_level="INFO", log_colors=True, use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=False,
            reconciliation_lookback_mins=1440,
            filter_position_reports=True,
        ),
        cache=CacheConfig(timestamps_as_iso8601=True, flush_on_start=False),
        data_clients={
            managed_universe.venue: BinanceDataClientConfig(
                venue=Venue(managed_universe.venue),
                api_key=None,
                api_secret=None,
                account_type=BinanceAccountType.SPOT,
                instrument_provider=InstrumentProviderConfig(load_ids=instrument_ids),
            ),
        },
        exec_clients={
            managed_universe.venue: SandboxExecutionClientConfig(
                venue=managed_universe.venue,
                account_type="CASH",
                starting_balances=list(config.paper_starting_balances),
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )


async def run_paper_bridge() -> None:
    if NAUTILUS_IMPORT_ERROR is not None:  # pragma: no cover - runtime guarded
        raise RuntimeError("nautilus_trader is required for main.py") from NAUTILUS_IMPORT_ERROR
    config = BridgeConfig()
    managed_universe = config.managed_universe()
    node = TradingNode(config=build_node_config(config))
    strategy = NautilusBridgeStrategy(
        config=NautilusBridgeStrategyConfig(
            bridge_id=config.bridge_id,
            portfolio_id=config.portfolio_id,
            environment=config.environment,
            redis_url=config.redis_url,
            managed_universe_path=str(config.managed_universe_path),
            poll_block_ms=config.poll_block_ms,
            rebalance_band_bps=config.rebalance_band_bps,
            min_order_notional_usd=config.min_order_notional_usd,
        ),
    )
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(managed_universe.venue, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(managed_universe.venue, SandboxLiveExecClientFactory)
    node.build()
    try:
        await node.run_async()
    finally:
        await node.stop_async()
        await asyncio.sleep(1.0)
        node.dispose()


if __name__ == "__main__":
    asyncio.run(run_paper_bridge())
