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

import phase5_cross_sectional_operational_fragility_audit_and_bounded_correction as rc4


def test_estimate_pre_friction_pnl_adds_back_stop_slippage() -> None:
    frame = pd.DataFrame(
        {
            "label": [-1, 1],
            "barrier_sl": [90.0, 90.0],
            "p0": [100.0, 100.0],
            "decision_position_usdt": [1000.0, 1000.0],
            "pnl_exec_stage_a": [-0.11, 0.05],
        }
    )

    gross = rc4._estimate_pre_friction_pnl(frame)

    assert round(float(gross.iloc[0]), 6) == -0.1
    assert round(float(gross.iloc[1]), 6) == 0.05


def test_apply_top_n_cap_keeps_only_top_rank_per_date() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-03-20", "2026-03-20", "2026-03-20"],
            "symbol": ["AAA", "BBB", "CCC"],
            "decision_selected": [True, True, False],
            "decision_selected_local": [True, True, False],
            "decision_selected_fallback": [False, False, False],
            "rank_score_stage_a": [0.2, 0.9, 0.1],
            "decision_position_usdt": [100.0, 200.0, 0.0],
            "decision_kelly_frac": [0.1, 0.2, 0.0],
            "decision_mu_adj": [0.01, 0.02, 0.0],
            "position_usdt_stage_a": [100.0, 200.0, 0.0],
            "kelly_frac_stage_a": [0.1, 0.2, 0.0],
            "mu_adj_stage_a": [0.01, 0.02, 0.0],
            "decision_proxy_prob": [1.0, 1.0, 0.0],
            "pnl_exec_stage_a": [0.01, 0.02, 0.0],
            "pnl_gross_before_friction_stage_a": [0.01, 0.02, 0.0],
        }
    )

    capped = rc4._apply_top_n_cap(frame, n_per_date=1)

    assert capped["decision_selected"].tolist() == [False, True, False]
    assert capped["decision_position_usdt"].tolist() == [0.0, 200.0, 0.0]


def test_classify_operational_fragility_prefers_regime_when_recent_losses_dominate() -> None:
    regime_summary = {
        "latest_365d_sharpe": -1.7,
        "pre_latest_365d_sharpe": 0.1,
    }
    fragility_stats = {
        "gross_sharpe": -0.2,
        "net_sharpe": -0.5,
        "regime_recent_loss_share": 0.8,
        "sparse_contest_loss_share": 0.3,
        "concentration_loss_share": 0.25,
        "turnover_loss_share": 0.4,
    }

    assert rc4._classify_operational_fragility(
        fragility_stats=fragility_stats,
        regime_summary=regime_summary,
    ) == "REGIME_DEPENDENCE_DOMINANT"


def test_classify_round_returns_operational_fragility_persists_when_best_fix_still_has_zero_dsr() -> None:
    status, decision, classification = rc4._classify_round(
        integrity_pass=True,
        baseline_row={
            "sharpe_operational": -0.56,
            "subperiods_positive": 3,
        },
        challenger_rows=[
            {
                "latest_active_count_decision_space": 1,
                "headroom_decision_space": True,
                "sharpe_operational": -0.28,
                "dsr_honest": 0.0,
                "subperiods_positive": 4,
            }
        ],
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "OPERATIONAL_FRAGILITY_PERSISTS"
