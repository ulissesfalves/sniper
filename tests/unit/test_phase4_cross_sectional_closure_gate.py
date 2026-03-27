from __future__ import annotations

from services.ml_engine.phase4_cross_sectional_closure_gate import (
    CLASS_APPROVED,
    CLASS_INCONCLUSIVE,
    CLASS_REJECTED,
    classify_phase4_closure,
)


def test_classify_phase4_closure_approves_when_sovereign_metrics_are_all_green():
    result = classify_phase4_closure(
        sharpe_operational=16.6648,
        dsr_honest=1.0,
        latest_active_count_decision_space=2,
        headroom_decision_space=True,
        recent_live_dates_decision_space=8,
        recent_window_dates=8,
        historical_active_events_decision_space=3939,
        historical_active_events_legacy=1290,
    )
    assert result["classification"] == CLASS_APPROVED
    assert result["decision"] == "advance"


def test_classify_phase4_closure_rejects_when_sovereign_latest_stays_dead():
    result = classify_phase4_closure(
        sharpe_operational=1.0,
        dsr_honest=1.0,
        latest_active_count_decision_space=0,
        headroom_decision_space=False,
        recent_live_dates_decision_space=0,
        recent_window_dates=8,
        historical_active_events_decision_space=10,
        historical_active_events_legacy=1290,
    )
    assert result["classification"] == CLASS_REJECTED
    assert result["decision"] == "abandon"


def test_classify_phase4_closure_returns_inconclusive_for_mixed_case():
    result = classify_phase4_closure(
        sharpe_operational=1.0,
        dsr_honest=1.0,
        latest_active_count_decision_space=1,
        headroom_decision_space=True,
        recent_live_dates_decision_space=3,
        recent_window_dates=8,
        historical_active_events_decision_space=100,
        historical_active_events_legacy=1290,
    )
    assert result["classification"] == CLASS_INCONCLUSIVE
    assert result["decision"] == "correct"
