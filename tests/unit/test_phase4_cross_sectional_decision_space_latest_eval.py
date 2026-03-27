from __future__ import annotations

import pandas as pd

from services.ml_engine.phase4_cross_sectional_decision_space_latest_eval import (
    CLASS_INCONCLUSIVE,
    CLASS_REJECTED,
    CLASS_VALIDATED,
    classify_eval_redesign,
    classify_space_status,
    count_dead_to_live_dates,
)


def test_classify_space_status_marks_dead_no_label_contest():
    result = classify_space_status(available_count=0, selected_count=0, active_count=0, space="label")
    assert result == "dead_no_label_contest"


def test_classify_space_status_marks_live_in_decision_space():
    result = classify_space_status(available_count=9, selected_count=2, active_count=2, space="decision")
    assert result == "live"


def test_count_dead_to_live_dates_counts_only_label_dead_and_decision_live_rows():
    frame = pd.DataFrame(
        [
            {"headroom_label_space": False, "headroom_decision_space": True},
            {"headroom_label_space": True, "headroom_decision_space": True},
            {"headroom_label_space": False, "headroom_decision_space": False},
        ]
    )
    assert count_dead_to_live_dates(frame) == 1


def test_classify_eval_redesign_validates_when_latest_turns_live_only_in_decision_space():
    result = classify_eval_redesign(
        latest_active_count_label_space=0,
        latest_active_count_decision_space=2,
        headroom_label_space=False,
        headroom_decision_space=True,
    )
    assert result["classification"] == CLASS_VALIDATED
    assert result["decision"] == "correct"


def test_classify_eval_redesign_rejects_when_decision_space_latest_stays_dead():
    result = classify_eval_redesign(
        latest_active_count_label_space=0,
        latest_active_count_decision_space=0,
        headroom_label_space=False,
        headroom_decision_space=False,
    )
    assert result["classification"] == CLASS_REJECTED
    assert result["decision"] == "abandon"


def test_classify_eval_redesign_returns_inconclusive_for_mixed_case():
    result = classify_eval_redesign(
        latest_active_count_label_space=1,
        latest_active_count_decision_space=1,
        headroom_label_space=True,
        headroom_decision_space=True,
    )
    assert result["classification"] == CLASS_INCONCLUSIVE
    assert result["decision"] == "correct"
