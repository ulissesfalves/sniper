from __future__ import annotations

from decimal import Decimal

from services.nautilus_bridge.contract import build_signal_payload
from services.nautilus_bridge.reconciler import build_readiness_report
from services.nautilus_bridge.reconciler import reconcile_target_weights


def _payload() -> dict:
    return {
        "portfolio_id": "sniper-paper",
        "environment": "paper",
        "portfolio_revision": 2,
        "signal_version": "sniper.portfolio_target.v1",
        "managed_universe_version": "calibration.v1",
        "as_of": "2026-03-13T00:00:00Z",
        "published_at": "2026-03-13T12:00:00Z",
        "replace_semantics": "FULL_SNAPSHOT",
        "capital_reference": {"currency": "USD", "notional": 200000.0},
        "risk_envelope": {
            "max_gross_weight": 0.98,
            "rebalance_band_bps": 25,
            "min_order_notional_usd": 10.0,
        },
        "targets": [
            {"instrument_id": "ADAUSDT.BINANCE_SPOT", "target_weight": 0.03},
            {"instrument_id": "SOLUSDT.BINANCE_SPOT", "target_weight": 0.00},
        ],
    }


def test_readiness_gate_requires_prices_and_snapshot() -> None:
    payload = build_signal_payload(_payload())
    report = build_readiness_report(
        payload,
        loaded_instruments={"ADAUSDT.BINANCE_SPOT"},
        portfolio_snapshot_loaded=False,
        quantities={"ADAUSDT.BINANCE_SPOT": 0, "SOLUSDT.BINANCE_SPOT": 1},
        prices={"ADAUSDT.BINANCE_SPOT": 1.0, "SOLUSDT.BINANCE_SPOT": None},
        executor_healthy=False,
    )
    assert not report.is_ready
    assert "executor_unhealthy" in report.missing
    assert any(item.startswith("instruments_missing:") for item in report.missing)
    assert any(item.startswith("prices_missing:") for item in report.missing)


def test_readiness_gate_ignores_missing_price_for_flat_zero_target() -> None:
    payload = build_signal_payload(_payload())
    report = build_readiness_report(
        payload,
        loaded_instruments={"ADAUSDT.BINANCE_SPOT"},
        portfolio_snapshot_loaded=True,
        quantities={"ADAUSDT.BINANCE_SPOT": 0, "SOLUSDT.BINANCE_SPOT": 0},
        prices={"ADAUSDT.BINANCE_SPOT": 1.0, "SOLUSDT.BINANCE_SPOT": None},
        executor_healthy=True,
    )
    assert report.is_ready
    assert not any(item.startswith("prices_missing:") for item in report.missing)


def test_reconcile_computes_delta_and_zero_target_close() -> None:
    payload = build_signal_payload(_payload())
    result = reconcile_target_weights(
        payload,
        nav=200000,
        quantities={"ADAUSDT.BINANCE_SPOT": 0, "SOLUSDT.BINANCE_SPOT": 25},
        prices={"ADAUSDT.BINANCE_SPOT": 1, "SOLUSDT.BINANCE_SPOT": 100},
        default_rebalance_band_bps=25,
        default_min_order_notional_usd=10,
    )
    assert result.status == "submitted"
    assert len(result.intents) == 2
    buy_intent = next(item for item in result.intents if item.instrument_id == "ADAUSDT.BINANCE_SPOT")
    close_intent = next(item for item in result.intents if item.instrument_id == "SOLUSDT.BINANCE_SPOT")
    assert buy_intent.order_side == "BUY"
    assert buy_intent.delta_notional == Decimal("6000")
    assert close_intent.close_only
    assert close_intent.order_side == "SELL"


def test_reconcile_skips_inside_band() -> None:
    payload = build_signal_payload(
        {
            **_payload(),
            "targets": [
                {"instrument_id": "ADAUSDT.BINANCE_SPOT", "target_weight": 0.03},
                {"instrument_id": "SOLUSDT.BINANCE_SPOT", "target_weight": 0.01},
            ],
        },
    )
    result = reconcile_target_weights(
        payload,
        nav=100000,
        quantities={"ADAUSDT.BINANCE_SPOT": 3000, "SOLUSDT.BINANCE_SPOT": 10},
        prices={"ADAUSDT.BINANCE_SPOT": 1, "SOLUSDT.BINANCE_SPOT": 100},
        default_rebalance_band_bps=25,
        default_min_order_notional_usd=10,
    )
    assert result.status == "noop_band"
    assert any(skip.reason == "band" for skip in result.skips)


def test_reconcile_skips_dust_close_below_min_notional() -> None:
    payload = build_signal_payload(_payload())
    result = reconcile_target_weights(
        payload,
        nav=200000,
        quantities={"ADAUSDT.BINANCE_SPOT": 0, "SOLUSDT.BINANCE_SPOT": Decimal("0.01")},
        prices={"ADAUSDT.BINANCE_SPOT": 1, "SOLUSDT.BINANCE_SPOT": 100},
        default_rebalance_band_bps=25,
        default_min_order_notional_usd=10,
    )
    assert result.status == "submitted"
    assert all(intent.instrument_id != "SOLUSDT.BINANCE_SPOT" for intent in result.intents)
    assert any(skip.reason == "dust_close" for skip in result.skips)


def test_reconcile_ignores_missing_price_for_zero_target_flat_instrument() -> None:
    payload = build_signal_payload(
        {
            **_payload(),
            "targets": [
                {"instrument_id": "ADAUSDT.BINANCE_SPOT", "target_weight": 0.03},
                {"instrument_id": "SOLUSDT.BINANCE_SPOT", "target_weight": 0.00},
            ],
        },
    )
    result = reconcile_target_weights(
        payload,
        nav=200000,
        quantities={"ADAUSDT.BINANCE_SPOT": 0, "SOLUSDT.BINANCE_SPOT": 0},
        prices={"ADAUSDT.BINANCE_SPOT": 1},
        default_rebalance_band_bps=25,
        default_min_order_notional_usd=10,
    )
    assert result.status == "submitted"
    assert any(intent.instrument_id == "ADAUSDT.BINANCE_SPOT" for intent in result.intents)
    assert any(skip.reason == "already_flat" for skip in result.skips)
