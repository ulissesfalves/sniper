from __future__ import annotations

import pandas as pd

from services.ml_engine.phase4_cross_sectional_latest_choke_audit import (
    CLASS_IDENTIFIED,
    CLASS_STRUCTURAL,
    classify_latest_audit,
)


def test_classify_latest_audit_identifies_latest_eligibility_choke_when_recent_window_is_live():
    latest = {"eligible_count": 0, "selected_proxy_count": 0, "position_gt_0": 0}
    recent = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-03-12"), "position_gt_0": 1},
            {"date": pd.Timestamp("2026-03-16"), "position_gt_0": 1},
            {"date": pd.Timestamp("2026-03-20"), "position_gt_0": 0},
        ]
    )
    result = classify_latest_audit(latest_summary=latest, recent_rows=recent)
    assert result["classification"] == CLASS_IDENTIFIED
    assert result["decision"] == "correct"


def test_classify_latest_audit_marks_structural_when_recent_window_is_dead_too():
    latest = {"eligible_count": 0, "selected_proxy_count": 0, "position_gt_0": 0}
    recent = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-03-12"), "position_gt_0": 0},
            {"date": pd.Timestamp("2026-03-16"), "position_gt_0": 0},
            {"date": pd.Timestamp("2026-03-20"), "position_gt_0": 0},
        ]
    )
    result = classify_latest_audit(latest_summary=latest, recent_rows=recent)
    assert result["classification"] == CLASS_STRUCTURAL
    assert result["decision"] == "abandon"
