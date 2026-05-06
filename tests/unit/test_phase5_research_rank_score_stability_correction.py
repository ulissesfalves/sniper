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

import phase5_research_rank_score_stability_correction as gate


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "combo": ["c1", "c1", "c1", "c1"],
            "date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02"],
            "symbol": ["AAA", "BBB", "AAA", "BBB"],
            "rank_score_stage_a": [0.7, 0.4, 0.8, 0.3],
            "hmm_prob_bull": [0.8, 0.95, 0.6, 0.99],
            "sigma_ewma": [0.1, 0.1, 0.1, 0.1],
            "stage_a_eligible": [False, True, False, True],
            "pnl_real": [0.1, -0.2, 0.3, -0.4],
            "slippage_frac": [0.0] * 4,
        }
    )


def test_select_correction_policy_keeps_score_gate_before_hmm_filter() -> None:
    selected = gate.select_correction_policy(
        _frame(),
        {"policy": "x", "score_threshold": 0.5, "hmm_threshold": 0.7, "sigma_quantile_max": None},
    )

    assert selected.sort_values("date")["symbol"].tolist() == ["AAA"]


def test_classify_corrections_abandons_when_stability_not_materially_improved() -> None:
    status, decision, classification, best = gate.classify_corrections(
        [
            {
                "policy": "weak",
                "median_active_days": 200,
                "max_cvar_95_loss_fraction": 0.01,
                "median_combo_sharpe": 0.34,
                "min_combo_sharpe": -3.25,
            }
        ],
        baseline_median_sharpe=0.331124,
        baseline_min_sharpe=-3.357339,
    )

    assert status == "FAIL"
    assert decision == "abandon"
    assert classification == "STABILITY_CORRECTION_DID_NOT_CLEAR_NEGATIVE_COMBO_OR_DSR_GAP"
    assert best["policy"] == "weak"


def test_classify_corrections_partial_for_material_improvement_without_promotion() -> None:
    status, decision, classification, best = gate.classify_corrections(
        [
            {
                "policy": "improved",
                "median_active_days": 200,
                "max_cvar_95_loss_fraction": 0.01,
                "median_combo_sharpe": 0.4,
                "min_combo_sharpe": -2.7,
            }
        ],
        baseline_median_sharpe=0.331124,
        baseline_min_sharpe=-3.357339,
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "MATERIAL_STABILITY_IMPROVEMENT_BUT_STILL_NOT_PROMOTABLE"
    assert best["policy"] == "improved"
