from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from services.ml_engine.phase5_stage_a3_spec_hardening import (
    _classify_round,
    _compute_decision_space_metrics,
    _selected_target_payload,
)


def test_selected_target_payload_is_precommitted_to_q60():
    payload = _selected_target_payload(
        [
            {"candidate_id": "A3-q40", "precommitted_primary_candidate": False, "candidate_type": "two_stage_activation_utility", "quantile": 0.40},
            {"candidate_id": "A3-q60", "precommitted_primary_candidate": True, "candidate_type": "two_stage_activation_utility", "quantile": 0.60},
        ]
    )
    assert payload["selected_target_id"] == "A3-q60"
    assert payload["selection_basis"] == "ex_ante_precommitted"
    assert payload["selection_locked_before_final_rerun"] is True
    assert payload["downstream_performance_used_for_selection"] is False


def test_decision_space_metrics_follow_sovereign_definition():
    aggregated = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                    "2026-01-04",
                    "2026-01-05",
                    "2026-01-06",
                    "2026-01-07",
                    "2026-01-08",
                ]
            ),
            "decision_selected": [True, False, True, True, False, True, False, True],
            "position_usdt_stage_a": [100.0, 0.0, 50.0, 25.0, 0.0, 70.0, 0.0, 80.0],
        }
    )
    metrics = _compute_decision_space_metrics(aggregated)
    assert metrics["latest_active_count_decision_space"] == 1
    assert metrics["headroom_decision_space"] is True
    assert metrics["recent_live_dates_decision_space"] == 5
    assert metrics["historical_active_events_decision_space"] == 5


def test_classify_round_abandons_structural_failure():
    a3_row = {
        "positive_rate_oos": 0.04,
        "latest_active_count_decision_space": 0,
        "headroom_decision_space": False,
        "recent_live_dates_decision_space": 2,
        "historical_active_events_decision_space": 40,
        "sharpe_operational": 0.0,
        "dsr_honest": 0.0,
        "subperiods_positive": 0,
        "n_eff_mean": 150.0,
        "cpcv_trajectories": 15,
        "pbo": 0.05,
        "ece_calibrated": 0.03,
        "reliability_diagram_present": True,
    }
    integrity = {
        "no_leakage_proof_pass": True,
        "research_only_isolation_pass": True,
        "official_artifacts_unchanged": True,
        "target_is_non_circular": True,
        "decision_space_metrics_computed": True,
    }
    assert _classify_round(a3_row, integrity) == ("FAIL", "abandon")
