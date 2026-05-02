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

import phase5_research_only_stage_a_nonzero_exposure_falsification as gate


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "combo": ["c1", "c1", "c1", "c1", "c2", "c2"],
            "date": ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02", "2025-01-01", "2025-01-01"],
            "symbol": ["AAA", "BBB", "AAA", "BBB", "AAA", "BBB"],
            "rank_score_stage_a": [0.1, 0.5, 0.9, 0.2, 0.3, 0.4],
            "stage_a_eligible": [True, False, False, True, True, False],
            "pnl_real": [0.1, -0.2, -0.3, 0.4, 0.05, -0.1],
            "slippage_frac": [0.0] * 6,
        }
    )


def test_safe_top1_does_not_use_realized_eligibility() -> None:
    selected = gate.select_safe_top1_by_score(_frame())

    picked = selected.sort_values(["combo", "date"])["symbol"].tolist()

    assert picked == ["BBB", "AAA", "BBB"]


def test_unsafe_top1_uses_realized_eligibility_only_for_diagnostic() -> None:
    selected = gate.select_unsafe_realized_eligible_top1(_frame())

    picked = selected.sort_values(["combo", "date"])["symbol"].tolist()

    assert picked == ["AAA", "BBB", "AAA"]


def test_classify_result_abandons_when_only_unsafe_policy_looks_good() -> None:
    status, decision, classification = gate.classify_result(
        safe_summary={
            "selected_events": 100,
            "selected_dates": 100,
            "median_combo_sharpe": -0.5,
            "min_combo_sharpe": -1.0,
        },
        unsafe_summary={"median_combo_sharpe": 10.0},
    )

    assert status == "FAIL"
    assert decision == "abandon"
    assert classification == "ONLY_REALIZED_ELIGIBILITY_LOOKS_GOOD_SAFE_POLICY_FAILS"
