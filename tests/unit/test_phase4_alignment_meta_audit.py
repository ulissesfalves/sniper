from __future__ import annotations

import pandas as pd

from services.ml_engine.phase4_alignment_meta_audit import (
    CLASS_CHOKEPOINT,
    classify_blocker,
    compare_snapshot_lineage,
)


def test_compare_snapshot_lineage_detects_exact_match_against_latest_from_aggregated():
    aggregated = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-19", "2026-03-20", "2026-03-20"]),
            "symbol": ["AAA", "AAA", "BBB"],
            "p_meta_raw": [0.40, 0.55, 0.51],
            "p_meta_calibrated": [0.30, 0.45, 0.44],
            "mu_adj_meta": [0.0, 0.0, 0.0],
            "kelly_frac_meta": [0.0, 0.0, 0.0],
            "position_usdt_meta": [0.0, 0.0, 0.0],
        }
    )
    snapshot = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-20", "2026-03-20"]),
            "symbol": ["AAA", "BBB"],
            "p_meta_raw": [0.55, 0.51],
            "p_calibrated": [0.45, 0.44],
            "mu_adj_meta": [0.0, 0.0],
            "kelly_frac": [0.0, 0.0],
            "position_usdt": [0.0, 0.0],
        }
    )

    lineage = compare_snapshot_lineage(aggregated, snapshot)

    assert lineage["aligned"] is True
    assert lineage["merge_counts"]["both"] == 2
    assert lineage["field_mismatches"]["p_meta_calibrated"] == 0


def test_classify_blocker_flags_score_calibration_choke_when_lineage_is_aligned():
    lineage = {"paths_exist": True, "lineage_aligned": True}
    funnel_counts = {
        "latest_snapshot_p_meta_raw_gt_050": 3,
        "latest_snapshot_p_meta_calibrated_gt_050": 0,
        "latest_snapshot_mu_adj_meta_gt_0": 0,
        "latest_snapshot_kelly_frac_meta_gt_0": 0,
        "latest_snapshot_position_usdt_meta_gt_0": 0,
    }
    report = {"operational_path": {"choke_point": {"latest_snapshot_stage": "score_calibration"}}}

    result = classify_blocker(lineage, funnel_counts, report)

    assert result["classification"] == CLASS_CHOKEPOINT
    assert result["where_signal_dies"] == "score_calibration"
    assert result["needs_upstream_remediation"] is True
