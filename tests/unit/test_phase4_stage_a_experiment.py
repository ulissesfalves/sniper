from __future__ import annotations

import pandas as pd

from services.ml_engine.phase4_stage_a_experiment import (
    _apply_cross_sectional_ranking_proxy,
    _apply_two_stage_activation_utility_proxy,
    _build_stage_a_target,
    _build_stage_a_snapshot_proxy,
    _default_stage2_payload,
    _build_stage2_training_payload,
    _build_cross_sectional_relative_target,
    _build_cross_sectional_ranking_frame,
    _compute_two_stage_activation_thresholds,
    _compute_cluster_local_target_thresholds,
    _evaluate_stage_a_gate,
)


def test_build_stage_a_target_uses_cost_adjusted_edge_rule():
    df = pd.DataFrame(
        {
            "pnl_real": [0.03, 0.01, -0.02],
            "avg_sl_train": [0.02, 0.02, 0.01],
        }
    )
    target = _build_stage_a_target(df)
    assert target.tolist() == [1, 0, 0]


def test_default_stage2_payload_keeps_ranking_path_non_blocking():
    payload = _default_stage2_payload(17)
    assert payload["stage2_training_policy"] == "not_applicable"
    assert payload["train_rows_total"] == 17
    assert payload["train_rows_stage2"] == 17
    assert payload["is_valid"] is True
    assert payload["reason"] == "not_applicable"


def test_build_stage_a_snapshot_proxy_falls_back_to_calibrated_score_when_missing():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "symbol": ["A", "A"],
            "p_stage_a_calibrated": [0.0, 1.0],
            "decision_selected": [False, True],
            "kelly_frac_stage_a": [0.0, 0.1],
            "position_usdt_stage_a": [0.0, 1000.0],
        }
    )
    snapshot = _build_stage_a_snapshot_proxy(frame)
    assert snapshot["decision_score_stage_a"].tolist() == [1.0]
    assert snapshot["is_active"].tolist() == [True]


def test_evaluate_stage_a_gate_requires_latest_activity_or_headroom():
    operational_report = {
        "sharpe": 0.9,
        "dsr_honest": 0.1,
        "n_active": 95,
        "subperiod_summary": {"negative_periods": []},
        "activation_funnel": {
            "latest_snapshot_active_count": 0,
            "latest_snapshot_p_meta_calibrated_gt_050": 1,
            "latest_snapshot_mu_adj_meta_gt_0": 1,
        },
    }
    gate = _evaluate_stage_a_gate(operational_report, ece_calibrated=0.03, positive_rate_oos=0.10)
    assert gate["status"] == "PASS"
    assert gate["headroom_real_documented"] is True


def test_cluster_local_thresholds_use_global_fallback_when_support_is_small():
    train_df = pd.DataFrame(
        {
            "cluster_name": ["cluster_1", "cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "pnl_real": [0.10, 0.12, 0.14, 0.08, -0.02],
        }
    )
    thresholds, summary = _compute_cluster_local_target_thresholds(
        train_df,
        quantile=0.60,
        min_positive_count_per_cluster=2,
    )
    assert thresholds["cluster_1"]["threshold_source"] == "cluster_local_q_train_positive"
    assert thresholds["cluster_2"]["threshold_source"] == "global_positive_q_train_fallback"
    assert summary["train_positive_count_global"] == 4


def test_evaluate_stage_a_gate_fails_when_positive_rate_collapses():
    operational_report = {
        "sharpe": 1.2,
        "dsr_honest": 0.2,
        "n_active": 95,
        "subperiod_summary": {"negative_periods": []},
        "activation_funnel": {
            "latest_snapshot_active_count": 1,
            "latest_snapshot_p_meta_calibrated_gt_050": 1,
            "latest_snapshot_mu_adj_meta_gt_0": 1,
        },
    }
    gate = _evaluate_stage_a_gate(operational_report, ece_calibrated=0.03, positive_rate_oos=0.03)
    assert gate["status"] == "FAIL"
    assert gate["checks"]["positive_rate_min"] is False
    assert gate["abort_early"] is True


def test_cross_sectional_relative_target_uses_local_top1_and_date_fallback():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"] * 4),
            "symbol": ["A", "B", "C", "D"],
            "cluster_name": ["cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "pnl_real": [0.03, 0.04, 0.05, -0.01],
            "avg_sl_train": [0.02, 0.02, 0.02, 0.02],
        }
    )
    out, summary = _build_cross_sectional_relative_target(df, min_eligible_per_date_cluster=2)
    assert out["y_stage_a"].tolist() == [0, 1, 1, 0]
    assert summary["groups_local_target"] == 1
    assert summary["groups_fallback_target"] == 1
    assert summary["groups_without_eligible"] == 0


def test_cross_sectional_ranking_frame_exposes_truth_top1_and_rank_target():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"] * 4),
            "symbol": ["A", "B", "C", "D"],
            "cluster_name": ["cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "pnl_real": [0.03, 0.04, 0.05, -0.01],
            "avg_sl_train": [0.02, 0.02, 0.02, 0.02],
        }
    )
    out, summary = _build_cross_sectional_ranking_frame(df, min_eligible_per_date_cluster=2)
    assert out["y_stage_a_truth_top1"].tolist() == [0, 1, 1, 0]
    assert out["rank_target_stage_a"].round(4).tolist() == [1.5, 2.0, 2.5, 0.0]
    assert summary["groups_local_target"] == 1
    assert summary["groups_fallback_target"] == 1


def test_cross_sectional_ranking_proxy_reports_hit_rate_and_min_alloc_counts():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"] * 4),
            "symbol": ["A", "B", "C", "D"],
            "cluster_name": ["cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "pnl_real": [0.03, 0.04, 0.05, -0.01],
            "avg_sl_train": [0.02, 0.02, 0.02, 0.02],
            "p_bma_pkf": [0.55, 0.60, 0.65, 0.40],
            "p_stage_a_raw": [0.20, 0.90, 0.80, 0.10],
            "rank_score_stage_a": [0.20, 0.90, 0.80, 0.10],
        }
    )
    out, summary = _apply_cross_sectional_ranking_proxy(df)
    assert int(out["stage_a_selected_proxy"].sum()) == 1
    assert summary["groups_local_selection"] == 1
    assert summary["groups_fallback_selection"] == 1
    assert summary["top1_hit_rate"] == 0.5
    assert summary["naive_top1_hit_rate"] == 1.0


def test_two_stage_thresholds_use_u_real_and_global_fallback():
    train_df = pd.DataFrame(
        {
            "cluster_name": ["cluster_1", "cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "pnl_real": [0.08, 0.10, 0.12, 0.03, 0.01],
            "avg_sl_train": [0.02, 0.02, 0.02, 0.02, 0.02],
        }
    )
    thresholds, summary = _compute_two_stage_activation_thresholds(
        train_df,
        quantile=0.60,
        min_positive_count_per_cluster=2,
    )
    assert thresholds["cluster_1"]["threshold_source"] == "cluster_local_q_train_positive"
    assert thresholds["cluster_2"]["threshold_source"] == "global_positive_q_train_fallback"
    assert summary["train_positive_count_global"] == 4


def test_stage2_training_payload_uses_only_activated_rows():
    train_df = pd.DataFrame(
        {
            "y_stage_a": [0, 1, 0, 1, 1],
            "stage_a_utility_surplus": [0.0, 0.3, 0.0, 0.5, 0.2],
        }
    )
    X_tr = pd.DataFrame({"f1": [1, 2, 3, 4, 5]}).values
    w_tr = pd.Series([1.0, 1.1, 1.2, 1.3, 1.4])
    X2, y2, w2, payload = _build_stage2_training_payload(train_df, X_tr, w_tr, min_rows=2)
    assert payload["stage2_training_policy"] == "activated_train_subset_only"
    assert payload["is_valid"] is True
    assert payload["train_rows_stage2"] == 3
    assert y2.tolist() == [0.3, 0.5, 0.2]
    assert X2.shape[0] == 3
    assert w2.tolist() == [1.1, 1.3, 1.4]


def test_two_stage_proxy_uses_same_geometry_with_predicted_activation():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"] * 4),
            "symbol": ["A", "B", "C", "D"],
            "cluster_name": ["cluster_1", "cluster_1", "cluster_2", "cluster_2"],
            "p_stage_a_calibrated": [0.60, 0.80, 0.70, 0.20],
            "p_activate_calibrated_stage_a": [0.60, 0.80, 0.70, 0.20],
            "utility_surplus_pred_stage_a": [0.10, 0.20, 0.30, 0.00],
        }
    )
    out, summary = _apply_two_stage_activation_utility_proxy(df)
    assert int(out["stage_a_selected_proxy"].sum()) == 2
    assert summary["groups_local_selection"] == 1
    assert summary["groups_fallback_selection"] == 1
    assert bool(out.loc[out["symbol"] == "B", "stage_a_selected_proxy"].iloc[0]) is True
    assert bool(out.loc[out["symbol"] == "C", "stage_a_selected_proxy"].iloc[0]) is True
