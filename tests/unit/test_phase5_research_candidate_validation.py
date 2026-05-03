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

import phase5_research_candidate_decision as decision_gate
import phase5_research_candidate_falsification as falsification_gate
import phase5_research_candidate_global_reaudit as reaudit_gate
import phase5_research_candidate_stability as stability_gate
import phase5_research_candidate_validation as candidate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(12):
        for symbol, score, pnl in [
            ("HIGH_A", 0.95, -0.02),
            ("HIGH_B", 0.85, -0.01),
            ("MID", 0.65, 0.00),
            ("LOW", 0.20, 0.02),
        ]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": score,
                    "p_stage_a_raw": score,
                    "hmm_prob_bull": 0.8,
                    "sigma_ewma": 0.5,
                    "pnl_real": pnl,
                    "slippage_frac": 0.0,
                    "stage_a_eligible": symbol == "HIGH_A",
                }
            )
    return pd.DataFrame(rows)


def test_candidate_governance_rejects_forbidden_exante_selection_column() -> None:
    checks = candidate.candidate_governance_checks(candidate.candidate_config(score_col="pnl_real"))

    assert checks["uses_realized_variable_as_ex_ante_rule"] is True
    assert checks["forbidden_selection_columns_used"] == ["pnl_real"]


def test_evaluate_candidate_uses_short_sandbox_exposure_without_stage_a_eligible() -> None:
    evaluation = candidate.evaluate_candidate(_toy_predictions(), candidate.candidate_config(top_k=2))

    assert not evaluation["positions"].empty
    assert evaluation["positions"]["target_weight"].lt(0.0).all()
    assert "stage_a_eligible" not in evaluation["positions"].columns
    assert evaluation["summary"]["median_combo_sharpe"] > 0.0


def test_reaudit_classifies_valid_research_candidate_as_pass_not_promotable() -> None:
    summary = {
        "median_combo_sharpe": 1.0,
        "min_combo_sharpe": 0.2,
        "median_active_days": 200,
        "max_cvar_95_loss_fraction": 0.01,
    }
    governance = candidate.candidate_governance_checks()
    prior = {"governance": {"uses_pnl_real_only_as_realized_backtest_outcome": True}}

    status, decision, classification = reaudit_gate.classify_reaudit(
        summary=summary,
        governance=governance,
        prior_report=prior,
    )

    assert (status, decision) == ("PASS", "advance")
    assert classification == "CANDIDATE_GLOBAL_REAUDIT_PASS_RESEARCH_ONLY_NOT_PROMOTABLE"


def test_stability_classifies_failed_scenarios_as_partial() -> None:
    status, decision, classification = stability_gate.classify_stability(
        {"failed_scenarios": ["temporal_third_1"], "base_summary": {"min_combo_sharpe": 0.2}}
    )

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "CANDIDATE_STABILITY_PARTIAL_TEMPORAL_OR_STRESS_FRAGILITY"


def test_falsification_classifies_hard_falsifier_as_fail_abandon() -> None:
    status, decision, classification = falsification_gate.classify_falsification(
        {"hard_falsifiers": ["extra_cost_20bps_min_sharpe"]}
    )

    assert (status, decision) == ("FAIL", "abandon")
    assert classification == "RESEARCH_CANDIDATE_FALSIFIED_BY_TEMPORAL_OR_COST_STRESS"


def test_decision_gate_abandons_when_falsifiers_are_present() -> None:
    status, decision, classification, reason = decision_gate.classify_candidate(
        {"status": "PASS"},
        {"status": "PARTIAL"},
        {"status": "FAIL"},
        {"hard_falsifiers": ["temporal_subperiod_min_sharpe"]},
    )

    assert (status, decision) == ("PASS", "abandon")
    assert classification == "RESEARCH_CANDIDATE_FALSIFIED"
    assert reason == "hard_falsifiers_present"
