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

import phase5_research_cvar_constrained_meta_sizing as gate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(6):
        for symbol, p_bma, p_meta, sigma, pnl in [
            ("AAA", 0.72, 0.60, 0.50, 0.020),
            ("BBB", 0.32, 0.35, 0.70, 0.015),
            ("CCC", 0.51, 0.51, 1.60, -0.015),
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


def test_select_policy_builds_risk_budgeted_signed_weights() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "cvar_constrained_meta_sizing",
            "policy": "toy",
            "mode": "signed_meta_edge_budget",
            "edge_min": 0.02,
            "sigma_max": 1.20,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_meta_calibrated", "sigma_ewma"],
        },
    )

    assert not selected.empty
    assert selected.groupby(["combo", "date"])["target_weight"].apply(lambda value: value.abs().sum()).round(8).eq(0.04).all()
    assert selected["target_weight"].abs().gt(0).all()
    assert selected["score"].gt(0).all()


def test_evaluate_config_uses_pnl_real_as_outcome_only() -> None:
    result = gate.evaluate_config(
        _toy_predictions(),
        {
            "family": "cvar_constrained_meta_sizing",
            "policy": "toy",
            "mode": "signed_meta_edge_budget",
            "edge_min": 0.02,
            "sigma_max": 1.20,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_meta_calibrated", "sigma_ewma"],
        },
    )

    assert not result["positions"].empty
    assert not result["daily"].empty
    assert result["summary"]["max_exposure_fraction"] == 0.04


def test_classification_preserves_partial_when_cvar_holds_but_alpha_unstable() -> None:
    status, decision, classification = gate.classify_cvar_constrained_meta_sizing(
        {
            "median_combo_sharpe": 0.7,
            "min_combo_sharpe": -0.2,
            "median_active_days": 250,
            "max_cvar_95_loss_fraction": 0.003,
            "median_turnover_fraction": 0.02,
            "max_exposure_fraction": 0.04,
        },
        ["cost_20bps"],
        policy_grid_errors=[],
    )

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "CVAR_CONSTRAINED_META_SIZING_CVAR_PASS_ALPHA_UNSTABLE"


def test_classification_advances_only_stable_research_candidate() -> None:
    status, decision, classification = gate.classify_cvar_constrained_meta_sizing(
        {
            "median_combo_sharpe": 0.7,
            "min_combo_sharpe": 0.2,
            "median_active_days": 250,
            "max_cvar_95_loss_fraction": 0.003,
            "median_turnover_fraction": 0.02,
            "max_exposure_fraction": 0.04,
        },
        [],
        policy_grid_errors=[],
    )

    assert (status, decision) == ("PASS", "advance")
    assert classification == "CVAR_CONSTRAINED_META_SIZING_RESEARCH_CANDIDATE_NOT_PROMOTABLE"
