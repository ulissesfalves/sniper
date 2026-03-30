from __future__ import annotations

import pandas as pd

from services.ml_engine.phase5_stage_a3_choke_audit import (
    _classify_choke,
    _compare_contest_geometry,
    _funnel_counts,
)


class _DummyRebuilt:
    def __init__(self, *, target_mode: str, experiment_name: str = "dummy"):
        self.target_mode = target_mode
        self.experiment_name = experiment_name
        self.selection_summary = {}


def test_funnel_counts_follow_sovereign_decision_path_for_a3():
    rebuilt = _DummyRebuilt(target_mode="two_stage_activation_utility")
    frame = pd.DataFrame(
        {
            "p_activate_raw_stage_a": [0.6, 0.4, 0.8],
            "p_activate_calibrated_stage_a": [0.0, 0.0, 0.7],
            "stage_a_predicted_activated": [False, False, True],
            "stage_a_selected_proxy": [False, False, True],
            "mu_adj_stage_a": [0.0, 0.0, 0.02],
            "decision_selected": [False, False, True],
            "position_usdt_stage_a": [0.0, 0.0, 100.0],
        }
    )
    counts = _funnel_counts(
        frame,
        rebuilt=rebuilt,
        experiment_label="stage_a3_q60",
        scope_name="oos_aggregated",
    )
    assert counts["n_rows_p_raw_gt_050"] == 2
    assert counts["n_rows_p_cal_gt_050"] == 1
    assert counts["n_rows_activated"] == 1
    assert counts["n_rows_ranked_top"] == 1
    assert counts["n_rows_mu_adj_gt_0"] == 1
    assert counts["n_rows_decision_selected"] == 1
    assert counts["n_rows_position_gt_0"] == 1


def test_funnel_counts_use_eligible_mass_for_baseline():
    rebuilt = _DummyRebuilt(target_mode="cross_sectional_ranking")
    frame = pd.DataFrame(
        {
            "p_stage_a_raw": [0.2, 0.7, 0.1],
            "p_stage_a_calibrated": [0.0, 1.0, 0.0],
            "stage_a_eligible": [True, True, False],
            "stage_a_selected_proxy": [False, True, False],
            "mu_adj_stage_a": [0.0, 0.03, 0.0],
            "decision_selected": [False, True, False],
            "position_usdt_stage_a": [0.0, 120.0, 0.0],
        }
    )
    counts = _funnel_counts(
        frame,
        rebuilt=rebuilt,
        experiment_label="baseline_cross_sectional_current",
        scope_name="oos_aggregated",
    )
    assert counts["n_rows_activated"] == 2
    assert counts["n_rows_ranked_top"] == 1
    assert counts["n_rows_position_gt_0"] == 1


def test_compare_contest_geometry_marks_same_geometry():
    baseline = _DummyRebuilt(target_mode="cross_sectional_ranking", experiment_name="baseline")
    baseline.selection_summary = {
        "groups_local_selection": 10,
        "groups_fallback_selection": 5,
        "groups_without_eligible": 2,
        "groups_total": 17,
    }
    a3 = _DummyRebuilt(target_mode="two_stage_activation_utility", experiment_name="a3")
    a3.selection_summary = {
        "groups_local_selection": 0,
        "groups_fallback_selection": 0,
        "groups_without_eligible": 17,
        "groups_total": 17,
    }
    geometry = _compare_contest_geometry(baseline, a3)
    assert geometry["conclusion"] == "SAME_CONTEST_GEOMETRY"


def test_classify_choke_prefers_stage1_when_stage2_is_positive_but_unused():
    localized, choke_stage, cause = _classify_choke(
        metric_definition_check={"ruler_drift_status": "NO_DRIFT"},
        geometry_check={"conclusion": "SAME_CONTEST_GEOMETRY", "note": "same"},
        stage2_diag={
            "choke_point": "stage1_raw_to_calibrated_activation_gate",
            "cause_root": "upstream calibrated activation collapsed",
        },
    )
    assert localized is True
    assert choke_stage == "stage1_raw_to_calibrated_activation_gate"
    assert "collapsed" in cause
