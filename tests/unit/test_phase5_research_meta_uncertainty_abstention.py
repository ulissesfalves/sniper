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

import phase5_research_meta_uncertainty_abstention as gate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(6):
        for symbol, p_bma, p_meta, sigma, pnl in [
            ("AAA", 0.72, 0.58, 0.50, 0.020),
            ("BBB", 0.66, 0.53, 0.70, 0.010),
            ("CCC", 0.40, 0.42, 1.30, -0.015),
        ]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": p_bma,
                    "p_meta_calibrated": p_meta,
                    "p_meta_raw": p_meta,
                    "sigma_ewma": sigma,
                    "hmm_prob_bull": 0.8,
                    "pnl_real": pnl,
                    "slippage_exec_meta": 0.0,
                    "stage_a_eligible": symbol == "AAA",
                    "avg_sl_train": 0.1,
                }
            )
    return pd.DataFrame(rows)


def test_policy_grid_uses_only_exante_selection_inputs() -> None:
    assert gate.validate_policy_grid() == []
    for policy in gate.PREDECLARED_POLICIES:
        inputs = set(policy["selection_inputs"])
        assert "pnl_real" not in inputs
        assert "stage_a_eligible" not in inputs
        assert "avg_sl_train" not in inputs


def test_select_policy_builds_long_only_research_weights() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "meta_uncertainty_abstention_long_only",
            "policy": "toy",
            "mode": "long_bma_meta_agree_low_sigma",
            "p_bma_min": 0.60,
            "p_meta_min": 0.50,
            "sigma_max": 1.00,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
        },
    )

    assert not selected.empty
    assert selected.groupby(["combo", "date"])["target_weight"].sum().round(8).eq(0.04).all()
    assert selected["target_weight"].gt(0).all()
    assert selected["score"].gt(0).all()


def test_evaluate_config_uses_pnl_real_as_outcome_only() -> None:
    result = gate.evaluate_config(
        _toy_predictions(),
        {
            "family": "meta_uncertainty_abstention_long_only",
            "policy": "toy",
            "mode": "long_bma_meta_agree_low_sigma",
            "p_bma_min": 0.60,
            "p_meta_min": 0.50,
            "sigma_max": 1.00,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
        },
    )

    assert not result["positions"].empty
    assert not result["daily"].empty
    assert result["summary"]["max_exposure_fraction"] == 0.04


def test_classification_falsifies_hard_sensitivity_failures() -> None:
    status, decision, classification = gate.classify_meta_uncertainty(
        {
            "median_combo_sharpe": 0.7,
            "min_combo_sharpe": 0.2,
            "median_active_days": 250,
            "max_cvar_95_loss_fraction": 0.01,
            "max_exposure_fraction": 0.04,
        },
        ["cost_20bps"],
        policy_grid_errors=[],
    )

    assert (status, decision) == ("FAIL", "abandon")
    assert classification == "META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS"


def test_classification_advances_stable_candidate_as_not_promotable() -> None:
    status, decision, classification = gate.classify_meta_uncertainty(
        {
            "median_combo_sharpe": 0.7,
            "min_combo_sharpe": 0.2,
            "median_active_days": 250,
            "max_cvar_95_loss_fraction": 0.01,
            "max_exposure_fraction": 0.04,
        },
        [],
        policy_grid_errors=[],
    )

    assert (status, decision) == ("PASS", "advance")
    assert classification == "META_UNCERTAINTY_RESEARCH_CANDIDATE_NOT_PROMOTABLE"
