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

import phase5_research_regime_specific_meta_disagreement as gate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(6):
        for symbol, p_bma, p_meta, hmm, pnl in [
            ("AAA", 0.70, 0.32, 0.50, -0.020),
            ("BBB", 0.62, 0.36, 0.52, -0.010),
            ("CCC", 0.70, 0.60, 0.80, 0.015),
        ]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": p_bma,
                    "p_meta_calibrated": p_meta,
                    "p_meta_raw": p_meta,
                    "sigma_ewma": 0.6,
                    "hmm_prob_bull": hmm,
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
        assert "hmm_prob_bull" in inputs
        assert "pnl_real" not in inputs
        assert "stage_a_eligible" not in inputs
        assert "avg_sl_train" not in inputs


def test_regime_mask_uses_exante_hmm_probability() -> None:
    work = gate.meta_gate.normalize_predictions(_toy_predictions())
    mask, score = gate.regime_mask_and_score(work, "neutral")

    assert mask.any()
    assert score.loc[mask].between(0, 1).all()


def test_select_policy_builds_regime_specific_sandbox_weights() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "regime_specific_meta_disagreement",
            "policy": "toy",
            "mode": "short_bma_high_meta_low",
            "regime": "neutral",
            "p_bma_min": 0.60,
            "p_meta_max": 0.40,
            "top_k": 2,
            "gross_exposure": 0.04,
            "sigma_max": 1.00,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma", "hmm_prob_bull"],
        },
    )

    assert not selected.empty
    assert selected.groupby(["combo", "date"])["target_weight"].sum().round(8).eq(-0.04).all()
    assert selected["target_weight"].lt(0).all()


def test_evaluate_config_uses_pnl_real_as_outcome_only() -> None:
    result = gate.evaluate_config(
        _toy_predictions(),
        {
            "family": "regime_specific_meta_disagreement",
            "policy": "toy",
            "mode": "short_bma_high_meta_low",
            "regime": "neutral",
            "p_bma_min": 0.60,
            "p_meta_max": 0.40,
            "top_k": 2,
            "gross_exposure": 0.04,
            "sigma_max": 1.00,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma", "hmm_prob_bull"],
        },
    )

    assert not result["positions"].empty
    assert not result["daily"].empty
    assert result["summary"]["max_exposure_fraction"] == 0.04


def test_classification_keeps_partial_for_positive_but_unstable_regime() -> None:
    status, decision, classification = gate.classify_regime_specific_meta_disagreement(
        {
            "median_combo_sharpe": 0.6,
            "min_combo_sharpe": -0.1,
            "median_active_days": 80,
            "max_cvar_95_loss_fraction": 0.002,
            "max_exposure_fraction": 0.04,
        },
        ["cost_20bps"],
        policy_grid_errors=[],
    )

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE"
