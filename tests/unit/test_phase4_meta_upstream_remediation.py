from __future__ import annotations

import pandas as pd

from services.ml_engine.phase4_meta_upstream_remediation import (
    CLASS_FAMILY_WEAK,
    CLASS_FIX_FOUND,
    build_threshold_survival_rows,
    classify_final_result,
    compute_shrinkage_summary,
)


def test_compute_shrinkage_summary_tracks_raw_to_calibrated_compression():
    df = pd.DataFrame(
        {
            "p_meta_raw": [0.60, 0.52, 0.49, 0.40],
            "p_meta_calibrated": [0.48, 0.50, 0.45, 0.41],
        }
    )

    summary = compute_shrinkage_summary(df, source_name="aggregated", scope_type="overall", scope_value="all")

    assert summary["raw_gt_050"] == 2
    assert summary["calibrated_gt_050"] == 0
    assert summary["shrinkage_mean"] > 0
    assert summary["survival_050"] == 0.0


def test_build_threshold_survival_rows_materializes_threshold_and_stage_counts():
    df = pd.DataFrame(
        {
            "p_meta_raw": [0.60, 0.52, 0.49],
            "p_meta_calibrated": [0.48, 0.50, 0.45],
            "mu_adj_meta": [0.01, 0.0, 0.0],
            "kelly_frac_meta": [0.002, 0.0, 0.0],
            "position_usdt_meta": [400.0, 0.0, 0.0],
        }
    )

    rows = build_threshold_survival_rows(
        df,
        variant_name="official_cluster_calibrated",
        scope_name="historical",
        selected_prob_label="p_meta_calibrated",
    )
    table = pd.DataFrame(rows)

    assert "rows_total" in table["stage_name"].tolist()
    assert "mu_adj_meta_gt_0" in table["stage_name"].tolist()
    assert "selected_prob_gt_threshold" in table["stage_name"].tolist()
    assert int(table.loc[(table["stage_name"] == "p_meta_raw_gt_threshold") & (table["threshold"] == 0.5), "count"].iloc[0]) == 2
    assert int(table.loc[(table["stage_name"] == "selected_prob_gt_threshold") & (table["threshold"] == 0.5), "count"].iloc[0]) == 0


def test_classify_final_result_marks_family_weak_when_bounded_fix_fails():
    baseline = {
        "dsr_honest": 0.0,
        "latest_active_count": 0,
        "headroom_real": False,
    }
    challengers = [
        {
            "variant_name": "diagnostic_raw_passthrough",
            "candidate_fix": False,
            "latest_active_count": 1,
            "headroom_real": True,
            "dsr_honest": 0.0,
        },
        {
            "variant_name": "research_global_isotonic",
            "candidate_fix": True,
            "latest_active_count": 0,
            "headroom_real": False,
            "dsr_honest": 0.0,
        },
    ]

    result = classify_final_result(baseline_summary=baseline, challenger_summaries=challengers)

    assert result["classification"] == CLASS_FAMILY_WEAK
    assert result["decision"] == "abandon"


def test_classify_final_result_marks_fix_when_bounded_candidate_restores_headroom_and_dsr():
    baseline = {
        "dsr_honest": 0.0,
        "latest_active_count": 0,
        "headroom_real": False,
    }
    challengers = [
        {
            "variant_name": "research_global_isotonic",
            "candidate_fix": True,
            "latest_active_count": 1,
            "headroom_real": True,
            "dsr_honest": 0.2,
        },
    ]

    result = classify_final_result(baseline_summary=baseline, challenger_summaries=challengers)

    assert result["classification"] == CLASS_FIX_FOUND
    assert result["decision"] == "correct"
