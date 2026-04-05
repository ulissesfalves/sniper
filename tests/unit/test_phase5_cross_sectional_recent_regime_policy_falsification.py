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

import phase5_cross_sectional_recent_regime_policy_falsification as rc5


def _base_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2024-01-01", "2025-12-30", "2025-12-30", "2025-12-30", "2026-03-20", "2026-03-20", "2026-03-20"],
            "symbol": ["OLD", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
            "decision_selected": [True, True, True, True, True, True, True],
            "decision_selected_local": [True, True, True, True, True, True, True],
            "decision_selected_fallback": [False, False, False, True, False, False, True],
            "decision_position_usdt": [100.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0],
            "decision_kelly_frac": [0.1] * 7,
            "decision_mu_adj": [0.01] * 7,
            "position_usdt_stage_a": [100.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0],
            "kelly_frac_stage_a": [0.1] * 7,
            "mu_adj_stage_a": [0.01] * 7,
            "decision_proxy_prob": [1.0] * 7,
            "p_stage_a_calibrated": [0.2, 0.44, 0.46, 0.30, 0.60, 0.41, 0.55],
            "rank_score_stage_a": [0.1, 0.7, 0.8, 0.2, 0.9, 0.6, 0.5],
            "decision_space_available": [True] * 7,
            "pnl_exec_stage_a": [0.01, -0.02, 0.03, -0.01, 0.04, -0.03, 0.02],
            "pnl_gross_before_friction_stage_a": [0.01, -0.02, 0.03, -0.01, 0.04, -0.03, 0.02],
        }
    )


def test_recent_mask_preserves_pre_365_dates() -> None:
    frame = _base_frame()
    mask = rc5._recent_mask(frame)

    assert bool(mask.iloc[0]) is False
    assert bool(mask.iloc[-1]) is True


def test_recent_edge_gate_zeros_only_recent_low_score_rows() -> None:
    frame = _base_frame()
    gated = rc5._apply_recent_edge_gate(frame, score_threshold=0.45)

    assert bool(gated.loc[0, "decision_selected"]) is True
    assert bool(gated.loc[1, "decision_selected"]) is False
    assert bool(gated.loc[2, "decision_selected"]) is True
    assert float(gated.loc[1, "decision_position_usdt"]) == 0.0
    assert float(gated.loc[0, "decision_position_usdt"]) == 100.0


def test_recent_edge_gate_plus_top2_caps_only_recent_dates() -> None:
    frame = _base_frame()
    capped = rc5._apply_recent_edge_gate_plus_top2(frame, score_threshold=0.0, n_per_date=2)

    assert int(capped.loc[capped["date"] == "2024-01-01", "decision_selected"].sum()) == 1
    assert int(capped.loc[capped["date"] == "2026-03-20", "decision_selected"].sum()) == 2


def test_classify_recent_fix_plausibility_returns_plausible() -> None:
    plausibility = rc5._classify_recent_fix_plausibility(
        baseline_row={
            "latest_365d_sharpe": -1.2,
            "latest_active_count_decision_space": 2,
            "headroom_decision_space": True,
            "sharpe_operational": -0.6,
        },
        fragility_stats={"regime_recent_loss_share": 0.42},
        rc4_summary={
            "challenger_metrics": {
                "x": {
                    "latest_active_count_decision_space": 1,
                    "headroom_decision_space": True,
                    "sharpe_operational": -0.35,
                }
            }
        },
    )

    assert plausibility == "RECENT_REGIME_POLICY_FIX_PLAUSIBLE"


def test_classify_round_returns_alive_but_not_promotable_when_dsr_stays_zero() -> None:
    status, decision, classification = rc5._classify_round(
        integrity_pass=True,
        plausibility="RECENT_REGIME_POLICY_FIX_PLAUSIBLE",
        baseline_row={
            "sharpe_operational": -0.56,
            "latest_365d_sharpe": -1.7,
        },
        challenger_rows=[
            {
                "latest_active_count_decision_space": 1,
                "headroom_decision_space": True,
                "sharpe_operational": -0.28,
                "dsr_honest": 0.0,
                "latest_365d_sharpe": -1.2,
            }
        ],
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "ALIVE_BUT_NOT_PROMOTABLE"
