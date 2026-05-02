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

import phase5_research_alternative_exante_family as gate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(5):
        for symbol, p_bma, sigma in [("AAA", 0.65, 0.3), ("BBB", 0.54, 0.8), ("CCC", 0.61, 0.4)]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": p_bma,
                    "p_stage_a_raw": p_bma - 0.05,
                    "sigma_ewma": sigma,
                    "uniqueness": 0.5,
                    "hmm_prob_bull": 0.9,
                    "pnl_real": 0.01 if symbol == "AAA" else -0.002,
                    "slippage_frac": 0.0,
                    "stage_a_eligible": symbol == "AAA",
                    "avg_sl_train": 0.1,
                }
            )
    return pd.DataFrame(rows)


def test_select_policy_uses_exante_columns_and_builds_weights() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "volatility_targeted_topk",
            "policy": "toy",
            "top_k": 2,
            "p_bma_threshold": 0.55,
            "hmm_threshold": 0.5,
            "gross_exposure": 0.03,
            "score_mode": "edge_inverse_vol",
        },
    )

    assert not selected.empty
    assert "target_weight" in selected.columns
    assert selected.groupby(["combo", "date"])["target_weight"].sum().round(8).eq(0.03).all()
    assert "stage_a_eligible" in selected.columns


def test_build_daily_returns_keeps_pnl_real_as_outcome_only() -> None:
    predictions = _toy_predictions()
    selected = gate.select_policy(
        predictions,
        {
            "family": "volatility_targeted_topk",
            "policy": "toy",
            "top_k": 1,
            "p_bma_threshold": 0.55,
            "hmm_threshold": 0.5,
            "gross_exposure": 0.03,
            "score_mode": "edge_inverse_vol",
        },
    )
    daily, trades = gate.build_daily_returns(predictions, selected)

    assert not daily.empty
    assert not trades.empty
    assert daily["exposure_fraction"].max() == 0.03
    assert daily["daily_return_proxy"].sum() > 0.0


def test_classify_family_marks_positive_but_short_history_partial() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "family": "regime_filtered_defensive_ensemble",
                "policy": "ensemble",
                "median_active_days": 30,
                "median_combo_sharpe": 0.4,
                "min_combo_sharpe": -0.2,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = gate.classify_family(metrics)

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "POSITIVE_ALPHA_BUT_INSUFFICIENT_ACTIVE_HISTORY_OR_STABILITY"
    assert best["policy"] == "ensemble"
