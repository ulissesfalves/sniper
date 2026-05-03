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

import phase5_research_meta_disagreement_abstention as gate


def _toy_predictions() -> pd.DataFrame:
    rows = []
    for day in range(5):
        for symbol, p_bma, p_meta, pnl in [
            ("AAA", 0.72, 0.22, -0.02),
            ("BBB", 0.66, 0.35, -0.01),
            ("CCC", 0.51, 0.50, 0.03),
        ]:
            rows.append(
                {
                    "combo": "(0, 1)",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "symbol": symbol,
                    "p_bma_pkf": p_bma,
                    "p_meta_calibrated": p_meta,
                    "p_meta_raw": p_meta,
                    "sigma_ewma": 0.5,
                    "hmm_prob_bull": 0.9,
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
        assert "p_bma_pkf" in inputs or "p_meta_calibrated" in inputs
        assert "pnl_real" not in inputs
        assert "stage_a_eligible" not in inputs
        assert "avg_sl_train" not in inputs


def test_select_policy_builds_short_sandbox_weights_from_disagreement() -> None:
    selected = gate.select_policy(
        _toy_predictions(),
        {
            "family": "meta_calibration_disagreement_abstention",
            "policy": "toy",
            "mode": "short_bma_high_meta_low",
            "p_bma_min": 0.60,
            "p_meta_max": 0.40,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
        },
    )

    assert not selected.empty
    assert selected.groupby(["combo", "date"])["target_weight"].sum().round(8).eq(-0.04).all()
    assert selected["target_weight"].lt(0).all()
    assert selected["score"].gt(0).all()


def test_daily_returns_use_pnl_real_as_outcome_only() -> None:
    predictions = _toy_predictions()
    selected = gate.select_policy(
        predictions,
        {
            "family": "meta_calibration_disagreement_abstention",
            "policy": "toy",
            "mode": "short_bma_high_meta_low",
            "p_bma_min": 0.60,
            "p_meta_max": 0.40,
            "top_k": 2,
            "gross_exposure": 0.04,
            "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
        },
    )
    daily, trades = gate.build_daily_returns(predictions, selected)

    assert not daily.empty
    assert not trades.empty
    assert daily["exposure_fraction"].max() == 0.04
    assert daily["daily_return_proxy"].sum() > 0.0


def test_classify_family_preserves_positive_stable_candidate_as_research_only() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "family": "meta_calibration_disagreement_abstention",
                "policy": "candidate",
                "median_active_days": 250,
                "median_combo_sharpe": 0.8,
                "min_combo_sharpe": 0.2,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = gate.classify_family(metrics)

    assert (status, decision) == ("PASS", "advance")
    assert classification == "META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTABLE"
    assert best["policy"] == "candidate"


def test_classify_family_abandons_negative_alpha() -> None:
    metrics = pd.DataFrame(
        [
            {
                "metric_level": "policy",
                "family": "meta_calibration_disagreement_abstention",
                "policy": "bad",
                "median_active_days": 250,
                "median_combo_sharpe": -0.2,
                "min_combo_sharpe": -0.8,
                "max_cvar_95_loss_fraction": 0.01,
            }
        ]
    )

    status, decision, classification, best = gate.classify_family(metrics)

    assert (status, decision) == ("FAIL", "abandon")
    assert classification == "META_DISAGREEMENT_NO_POSITIVE_SAFE_ALPHA"
    assert best["policy"] == "bad"
