from __future__ import annotations

import pandas as pd

from services.ml_engine.phase5_stage_a3_activation_calibration_correction import (
    _classify_round,
    _diagnose_dominant_choke,
    _frame_compare,
    _layer_counts,
    _prove_no_leakage_pre_proxy,
)


def test_layer_counts_capture_activation_to_position_funnel():
    frame = pd.DataFrame(
        {
            "p_activate_raw_stage_a": [0.7, 0.4, 0.9],
            "p_activate_calibrated_stage_a": [0.0, 0.0, 0.8],
            "stage_a_predicted_activated": [False, False, True],
            "decision_selected": [False, False, True],
            "position_usdt_stage_a": [0.0, 0.0, 120.0],
        }
    )
    counts = _layer_counts(
        frame,
        variant="challenger_1",
        layer="sovereign_final_aggregated",
        scope_name="decision_space",
        raw_col="p_activate_raw_stage_a",
        cal_col="p_activate_calibrated_stage_a",
        activated_col="stage_a_predicted_activated",
        selected_col="decision_selected",
        position_col="position_usdt_stage_a",
    )
    assert counts["raw_hits_gt_050"] == 2
    assert counts["calibrated_hits_gt_050"] == 1
    assert counts["activated_count"] == 1
    assert counts["decision_selected_count"] == 1
    assert counts["position_gt_0_count"] == 1


def test_frame_compare_recognizes_exact_reproduction():
    left = pd.DataFrame(
        {
            "combo": ["(0, 1)"],
            "date": ["2026-01-01"],
            "symbol": ["BTC"],
            "p_activate_calibrated_stage_a": [0.25],
        }
    )
    right = left.copy()
    result = _frame_compare(
        left,
        right,
        keys=["combo", "date", "symbol"],
        num_cols=["p_activate_calibrated_stage_a"],
    )
    assert result["exact_match"] is True
    assert result["p_activate_calibrated_stage_a_max_abs_diff"] == 0.0


def test_prove_no_leakage_pre_proxy_ignores_realized_columns():
    pre_proxy = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
            "symbol": ["AAA", "BBB"],
            "cluster_name": ["c1", "c1"],
            "p_activate_calibrated_stage_a": [0.8, 0.4],
            "utility_surplus_pred_stage_a": [0.2, 0.1],
            "avg_tp_train": [0.1, 0.1],
            "avg_sl_train": [0.05, 0.05],
            "pnl_real": [10.0, -5.0],
            "y_stage_a": [1, 0],
            "stage_a_utility_real": [1.2, 0.0],
            "stage_a_utility_surplus": [0.7, 0.0],
            "stage_a_score_realized": [0.6, 0.1],
        }
    )
    proof = _prove_no_leakage_pre_proxy(pre_proxy)
    assert proof["pass"] is True
    assert proof["selected_rows_match_when_realized_columns_removed"] is True
    assert proof["decision_scores_match_when_realized_columns_removed"] is True


def test_diagnose_dominant_choke_marks_calibrator_primary_when_aggregation_is_secondary():
    dominant, stage, cause = _diagnose_dominant_choke(
        reconciliation={
            "row_level_reproduction": {"exact_match": True},
            "cpcv_aggregate_reproduction": {"exact_match": True},
            "sovereign_final_reproduction": {"exact_match": True},
            "survivor_loss": {
                "row_level_raw_gt_050": 100,
                "row_level_calibrated_gt_050": 2,
                "cpcv_aggregated_calibrated_gt_050": 0,
            },
        },
        challenger_rows=[
            {
                "calibrated_hits_gt_050_cpcv_aggregated": 5,
                "latest_active_count_decision_space": 0,
                "headroom_decision_space": False,
            }
        ],
    )
    assert dominant is True
    assert stage == "calibrator_fit_mapping_primary__cpcv_mean_aggregation_secondary"
    assert "Primary collapse happens inside the calibrator" in cause


def test_classify_round_requires_live_signal_for_advance():
    integrity = {
        "official_artifacts_unchanged": True,
        "research_only_isolation_pass": True,
    }
    status, decision = _classify_round(
        integrity=integrity,
        no_leakage_proof_pass=True,
        sovereign_metric_definitions_unchanged=True,
        counter_reconciliation_complete=True,
        dominant_choke_confirmed=True,
        bounded_fix_only=True,
        challenger_rows=[
            {
                "calibrated_hits_gt_050_cpcv_aggregated": 16,
                "latest_active_count_decision_space": 0,
                "headroom_decision_space": False,
            }
        ],
    )
    assert (status, decision) == ("PARTIAL", "correct")
