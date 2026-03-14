from __future__ import annotations

import pytest


pytest.importorskip("nautilus_trader")

from services.nautilus_bridge.strategy import NautilusBridgeStrategy
from services.nautilus_bridge.strategy import NautilusBridgeStrategyConfig


def test_strategy_class_is_available_in_312_runtime() -> None:
    config = NautilusBridgeStrategyConfig(
        bridge_id="bridge",
        portfolio_id="portfolio",
        environment="paper",
        redis_url="redis://localhost:6379/0",
        managed_universe_path="services/nautilus_bridge/instrument_map.json",
    )
    assert NautilusBridgeStrategy is not None
    assert config.portfolio_id == "portfolio"
