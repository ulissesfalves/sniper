from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_research_cluster_conditioned_polarity as gate
import phase5_research_cluster_conditioned_polarity_decision as decision
import phase5_research_cluster_conditioned_polarity_falsification as falsification


def test_policy_grid_is_cluster_conditioned_and_exante() -> None:
    policies = gate.build_policy_grid()

    assert len(policies) >= 12
    assert all("cluster_name" in policy for policy in policies)
    assert {policy["score_col"] for policy in policies} == {"p_bma_pkf"}
    assert "pnl_real" not in {policy["score_col"] for policy in policies}
    assert "stage_a_eligible" not in {policy["score_col"] for policy in policies}


def test_classify_cluster_family_advances_positive_stable_candidate() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "policy": "cluster_2_candidate",
                "family": "cluster_conditioned_polarity",
                "median_active_days": 300,
                "median_combo_sharpe": 1.2,
                "min_combo_sharpe": 0.1,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = gate.classify_cluster_family(metrics)

    assert (status, decision) == ("PASS", "advance")
    assert classification == "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_NOT_PROMOTABLE"
    assert best["policy"] == "cluster_2_candidate"


def test_classify_cluster_family_abandons_negative_alpha() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "policy": "negative",
                "family": "cluster_conditioned_polarity",
                "median_active_days": 300,
                "median_combo_sharpe": -0.1,
                "min_combo_sharpe": -1.0,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = gate.classify_cluster_family(metrics)

    assert (status, decision) == ("FAIL", "abandon")
    assert classification == "CLUSTER_CONDITIONED_POLARITY_NO_POSITIVE_SAFE_ALPHA"
    assert best["policy"] == "negative"


def test_cluster_falsification_abandons_when_hard_falsifiers_exist() -> None:
    status, decision, classification = falsification.classify_falsification(["extra_cost_20bps"])

    assert (status, decision) == ("FAIL", "abandon")
    assert classification == "CLUSTER_CONDITIONED_CANDIDATE_FALSIFIED_BY_TEMPORAL_COST_OR_UNIVERSE_STRESS"


def test_cluster_falsification_advances_without_hard_falsifiers() -> None:
    status, decision, classification = falsification.classify_falsification([])

    assert (status, decision) == ("PASS", "advance")
    assert classification == "CLUSTER_CONDITIONED_CANDIDATE_SURVIVED_FALSIFICATION_RESEARCH_ONLY"


def test_cluster_decision_records_falsified_candidate() -> None:
    status, decision_value, classification, reason = decision.classify_cluster_decision(
        {"status": "PASS", "decision": "advance"},
        {"status": "FAIL", "decision": "abandon"},
        {"hard_falsifiers": ["temporal_third_1"]},
    )

    assert (status, decision_value) == ("PASS", "abandon")
    assert classification == "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_FALSIFIED"
    assert reason == "hard_falsifiers_present"
