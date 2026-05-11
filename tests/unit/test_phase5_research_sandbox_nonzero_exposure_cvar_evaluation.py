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

import phase5_research_sandbox_nonzero_exposure_cvar_evaluation as gate


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "combo": ["c1", "c1", "c1", "c1"],
            "date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02"],
            "symbol": ["AAA", "BBB", "AAA", "BBB"],
            "rank_score_stage_a": [0.1, 0.5, 0.9, 0.2],
            "stage_a_eligible": [True, False, False, True],
            "pnl_real": [0.1, -0.2, -0.3, 0.4],
            "slippage_frac": [0.0, 0.0, 0.0, 0.0],
        }
    )


def test_research_policy_selection_ignores_realized_eligibility() -> None:
    selected = gate.select_safe_top1_by_score(_frame())

    assert selected.sort_values("date")["symbol"].tolist() == ["BBB", "AAA"]


def test_daily_returns_apply_fixed_fraction_to_selected_pnl() -> None:
    selected = gate.select_safe_top1_by_score(_frame())
    daily = gate.build_daily_policy_returns(_frame(), selected, position_fraction=0.01).sort_values("date")

    assert daily["daily_return_proxy"].round(6).tolist() == [-0.002, -0.003]
    assert daily["exposure_fraction"].tolist() == [0.01, 0.01]


def test_empirical_cvar_uses_left_tail_losses() -> None:
    value_at_risk, conditional_var, tail_count = gate.empirical_var_cvar(
        pd.Series([0.02, -0.01, -0.03, -0.08, 0.01]),
        alpha=0.2,
    )

    assert round(value_at_risk, 6) == 0.04
    assert round(conditional_var, 6) == 0.08
    assert tail_count == 1


def test_classify_partial_when_cvar_passes_but_alpha_is_negative() -> None:
    status, decision, classification = gate.classify_result(
        {
            "active_combo_days": 1000,
            "median_active_days": 200,
            "max_cvar_95_loss_fraction": 0.01,
            "median_combo_sharpe": -0.5,
            "all_combos_cvar_within_limit": True,
        }
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "NONZERO_EXPOSURE_RESEARCH_CVAR_PASS_BUT_ALPHA_DSR_BLOCKERS_REMAIN"
