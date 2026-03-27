from __future__ import annotations

from services.ml_engine.phase4_cross_sectional_no_contest_vs_ranking_failure import (
    CLASS_MISALIGNED,
    CLASS_NO_CONTEST,
    CLASS_RANKING_FAILURE,
    DATE_CLASS_MISALIGNMENT,
    DATE_CLASS_NO_CONTEST,
    DATE_CLASS_RANKING_FAILURE,
    classify_date_alignment,
    classify_final_audit,
)


def test_classify_date_alignment_marks_metric_misalignment_when_label_zero_but_operational_candidates_exist():
    result = classify_date_alignment(label_eligible_count=0, decision_available_count=9, label_selected_count=0, label_truth_hit_count=0)
    assert result == DATE_CLASS_MISALIGNMENT


def test_classify_date_alignment_marks_ranking_failure_when_label_selection_misses_truth():
    result = classify_date_alignment(label_eligible_count=3, decision_available_count=9, label_selected_count=1, label_truth_hit_count=0)
    assert result == DATE_CLASS_RANKING_FAILURE


def test_classify_final_audit_returns_misaligned_for_latest_metric_misalignment_case():
    result = classify_final_audit(latest_case=DATE_CLASS_MISALIGNMENT)
    assert result["classification"] == CLASS_MISALIGNED
    assert result["decision"] == "correct"


def test_classify_final_audit_returns_no_contest_when_no_operational_candidates_exist():
    result = classify_final_audit(latest_case=DATE_CLASS_NO_CONTEST)
    assert result["classification"] == CLASS_NO_CONTEST
    assert result["decision"] == "correct"


def test_classify_final_audit_returns_ranking_failure_when_latest_case_is_ranking_failure():
    result = classify_final_audit(latest_case=DATE_CLASS_RANKING_FAILURE)
    assert result["classification"] == CLASS_RANKING_FAILURE
    assert result["decision"] == "correct"
