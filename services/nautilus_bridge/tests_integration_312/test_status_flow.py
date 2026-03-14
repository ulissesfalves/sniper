from __future__ import annotations

import pytest


nautilus_trader = pytest.importorskip("nautilus_trader")

from services.nautilus_bridge.main import build_node_config
from services.nautilus_bridge.config import BridgeConfig


def test_node_config_builds_with_nautilus_runtime() -> None:
    config = BridgeConfig()
    node_config = build_node_config(config)
    assert node_config is not None
