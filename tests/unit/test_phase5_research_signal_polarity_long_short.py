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

import phase5_research_signal_polarity_long_short as gate
import phase5_research_signal_polarity_stability_correction as correction


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(6):
        for symbol, score, pnl in [("HIGH", 0.9, -0.02), ("MID", 0.6, 0.00), ("LOW", 0.1, 0.02)]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": score,
                    "p_stage_a_raw": score,
                    "hmm_prob_bull": 0.8,
                    "sigma_ewma": 0.4,
                    "pnl_real": pnl,
                    "slippage_frac": 0.0,
                    "stage_a_eligible": symbol == "HIGH",
                }
            )
    return pd.DataFrame(rows)


def test_short_high_assigns_negative_weights_without_realized_filter() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "signal_polarity_short_high",
            "policy": "toy",
            "score_col": "p_bma_pkf",
            "mode": "short_high",
            "top_k": 1,
            "gross_exposure": 0.04,
        },
    )

    assert not selected.empty
    assert selected["target_weight"].lt(0.0).all()
    assert selected.groupby(["combo", "date"])["target_weight"].sum().round(8).eq(-0.04).all()


def test_short_high_daily_returns_can_capture_antipredictive_signal() -> None:
    predictions = _toy_predictions()
    selected = gate.select_policy(
        predictions,
        {
            "family": "signal_polarity_short_high",
            "policy": "toy",
            "score_col": "p_bma_pkf",
            "mode": "short_high",
            "top_k": 1,
            "gross_exposure": 0.04,
        },
    )
    daily, trades = gate.build_daily_returns(predictions, selected)

    assert not daily.empty
    assert not trades.empty
    assert daily["daily_return_proxy"].sum() > 0.0


def test_stability_correction_classifies_stable_positive_candidate_as_pass() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "family": "signal_polarity_stability_filtered",
                "policy": "stable",
                "median_active_days": 200,
                "median_combo_sharpe": 1.2,
                "min_combo_sharpe": 0.2,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = correction.classify_correction(metrics)

    assert (status, decision) == ("PASS", "advance")
    assert classification == "STABLE_SIGNAL_POLARITY_RESEARCH_CANDIDATE_BELOW_DSR_PROMOTION_BAR"
    assert best["policy"] == "stable"
