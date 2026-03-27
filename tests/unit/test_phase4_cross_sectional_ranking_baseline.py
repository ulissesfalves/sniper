from __future__ import annotations

from services.ml_engine.phase4_cross_sectional_ranking_baseline import (
    CLASS_INCONCLUSIVE,
    CLASS_PROMISING,
    CLASS_WEAK,
    _compare,
    classify_baseline_result,
)


def test_classify_baseline_result_marks_promising_when_latest_and_operational_signal_exist():
    current = {"sharpe_operational": 1.5, "dsr_honest": 0.2, "latest_active_count": 1, "headroom_real": True, "historical_active_events": 120, "top1_hit_rate": 0.62, "naive_top1_hit_rate": 0.50}
    abandoned = {"sharpe_operational": 1.0315, "dsr_honest": 0.0, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 60}
    result = classify_baseline_result(current_summary=current, abandoned_summary=abandoned)
    assert result["classification"] == CLASS_PROMISING
    assert result["decision"] == "correct"


def test_classify_baseline_result_marks_inconclusive_when_history_improves_but_latest_stays_dead():
    current = {"sharpe_operational": 2.0, "dsr_honest": 0.8, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 500, "top1_hit_rate": 0.70, "naive_top1_hit_rate": 0.60}
    abandoned = {"sharpe_operational": 1.0315, "dsr_honest": 0.0, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 60}
    result = classify_baseline_result(current_summary=current, abandoned_summary=abandoned)
    assert result["classification"] == CLASS_INCONCLUSIVE
    assert result["decision"] == "correct"


def test_classify_baseline_result_marks_weak_when_it_does_not_beat_abandoned_family():
    current = {"sharpe_operational": 0.2, "dsr_honest": 0.0, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 10, "top1_hit_rate": 0.40, "naive_top1_hit_rate": 0.50}
    abandoned = {"sharpe_operational": 1.0315, "dsr_honest": 0.0, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 60}
    result = classify_baseline_result(current_summary=current, abandoned_summary=abandoned)
    assert result["classification"] == CLASS_WEAK
    assert result["decision"] == "abandon"


def test_compare_reports_deltas_and_improvement_flags():
    comparison = _compare(
        {"sharpe_operational": 2.0, "dsr_honest": 0.5, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 500},
        {"sharpe_operational": 1.0, "dsr_honest": 0.0, "latest_active_count": 0, "headroom_real": False, "historical_active_events": 60},
    )
    assert comparison["delta"]["sharpe_operational"] == 1.0
    assert comparison["delta"]["dsr_honest"] == 0.5
    assert comparison["better_than_abandoned_family"] is True
