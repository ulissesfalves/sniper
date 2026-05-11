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

import phase5_research_rank_score_threshold_sizing_falsification as gate


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "combo": ["c1", "c1", "c1", "c1", "c1", "c1"],
            "date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"],
            "symbol": ["AAA", "BBB", "AAA", "BBB", "AAA", "BBB"],
            "rank_score_stage_a": [0.2, 0.6, 0.9, 0.4, 0.2, 0.1],
            "hmm_prob_bull": [0.9, 0.9, 0.4, 0.8, 0.9, 0.9],
            "stage_a_eligible": [True, False, False, True, True, False],
            "pnl_real": [0.1, -0.2, -0.3, 0.4, 0.2, -0.1],
            "slippage_frac": [0.0] * 6,
        }
    )


def test_select_policy_uses_score_threshold_not_realized_eligibility() -> None:
    selected = gate.select_policy(_frame(), score_threshold=0.5, hmm_threshold=None)

    assert selected.sort_values("date")["symbol"].tolist() == ["BBB", "AAA"]


def test_select_policy_applies_optional_hmm_filter() -> None:
    selected = gate.select_policy(_frame(), score_threshold=0.3, hmm_threshold=0.7)

    assert selected.sort_values("date")["symbol"].tolist() == ["BBB", "BBB"]


def test_classify_family_marks_weak_positive_candidate_partial() -> None:
    status, decision, classification, best = gate.classify_family(
        [
            {
                "policy": "p1",
                "median_active_days": 200,
                "max_cvar_95_loss_fraction": 0.01,
                "median_combo_sharpe": 0.5,
                "min_combo_sharpe": -1.0,
            }
        ]
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "WEAK_POSITIVE_MEDIAN_ALPHA_UNSTABLE_NOT_PROMOTABLE"
    assert best["policy"] == "p1"


def test_classify_family_ignores_high_sharpe_policy_without_enough_history() -> None:
    status, decision, classification, best = gate.classify_family(
        [
            {
                "policy": "too_sparse",
                "median_active_days": 5,
                "max_cvar_95_loss_fraction": 0.01,
                "median_combo_sharpe": 2.0,
                "min_combo_sharpe": 1.0,
            },
            {
                "policy": "usable",
                "median_active_days": 200,
                "max_cvar_95_loss_fraction": 0.01,
                "median_combo_sharpe": 0.5,
                "min_combo_sharpe": -1.0,
            },
        ]
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "WEAK_POSITIVE_MEDIAN_ALPHA_UNSTABLE_NOT_PROMOTABLE"
    assert best["policy"] == "usable"
