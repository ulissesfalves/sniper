from __future__ import annotations

import json

import pandas as pd

from services.ml_engine.phase5_cross_sectional_hardening_baseline import (
    _apply_threshold_mask,
    _compute_capacity_proxy,
    _compute_concentration_proxy,
    _compute_turnover_proxy,
    _normalize_payload,
    _validate_research_bundle,
)
from services.ml_engine.phase5_stage_a3_spec_hardening import _compute_decision_space_metrics


def test_threshold_mask_preserves_sovereign_ruler_logic():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"]),
            "symbol": ["A", "B", "A"],
            "decision_selected": [True, True, True],
            "p_stage_a_calibrated": [0.60, 0.54, 0.49],
            "position_usdt_stage_a": [1000.0, 500.0, 250.0],
            "kelly_frac_stage_a": [0.10, 0.05, 0.02],
            "mu_adj_stage_a": [0.10, 0.04, 0.01],
            "pnl_exec_stage_a": [0.02, 0.01, 0.03],
        }
    )
    out = _apply_threshold_mask(frame, 0.55)
    sovereign = _compute_decision_space_metrics(out)
    assert sovereign["latest_active_count_decision_space"] == 0
    assert sovereign["headroom_decision_space"] is False
    assert sovereign["recent_live_dates_decision_space"] == 1
    assert sovereign["historical_active_events_decision_space"] == 1


def test_proxy_helpers_return_non_empty_summaries():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"]),
            "symbol": ["A", "B", "A", "B"],
            "decision_selected": [True, True, True, False],
            "position_usdt_stage_a": [1000.0, 500.0, 700.0, 0.0],
            "reference_order_usdt_exec_stage_a": [1000.0, 1000.0, 1000.0, 1000.0],
            "slippage_exec_stage_a": [0.01, 0.02, 0.015, 0.0],
            "slippage_ref_capped_exec_stage_a": [0, 1, 0, 0],
        }
    )
    turnover = _compute_turnover_proxy(frame)
    concentration = _compute_concentration_proxy(frame)
    capacity = _compute_capacity_proxy(frame)
    assert turnover["turnover_proxy_mean"] >= 0.0
    assert concentration["concentration_proxy_mean"] > 0.0
    assert concentration["max_active_events_per_day"] == 2
    assert capacity["capacity_reference_order_mean"] == 1000.0
    assert capacity["capacity_capped_ref_rate"] >= 0.0


def test_validate_research_bundle_fails_on_missing_snapshot(tmp_path):
    report_path = tmp_path / "stage_a_report.json"
    manifest_path = tmp_path / "stage_a_manifest.json"
    predictions_path = tmp_path / "stage_a_predictions.parquet"
    report_path.write_text(json.dumps({"status": "FAIL"}), encoding="utf-8")
    manifest_path.write_text(json.dumps({"head": "abc"}), encoding="utf-8")
    pd.DataFrame({"x": [1]}).to_parquet(predictions_path, index=False)
    result = _validate_research_bundle(tmp_path)
    assert result["pass"] is False
    assert "missing:stage_a_snapshot_proxy.parquet" in result["issues"]


def test_normalize_payload_ignores_volatile_keys():
    payload = {
        "experiment_name": "phase5_cross_sectional_hardening_replay_run1",
        "generated_at_utc": "2026-04-04T00:00:00+00:00",
        "nested": {"generated_at_utc": "2026-04-04T00:00:01+00:00", "value": 7},
        "value": 3,
    }
    normalized = _normalize_payload(payload, drop_keys={"experiment_name", "generated_at_utc"})
    assert normalized == {"nested": {"value": 7}, "value": 3}
