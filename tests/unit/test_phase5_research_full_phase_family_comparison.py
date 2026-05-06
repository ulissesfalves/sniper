from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_research_full_phase_family_comparison as gate


def test_summary_value_extracts_key_value_from_report() -> None:
    assert gate._summary_value({"summary": ["x=1", "best_policy=abc"]}, "best_policy") == "abc"


def test_classify_comparison_preserves_research_only_survivor() -> None:
    rows = [
        {
            "family": "old",
            "status": "FAIL",
            "decision": "abandon",
            "median_combo_sharpe": -1.0,
            "min_combo_sharpe": -2.0,
            "median_active_days": 300,
        },
        {
            "family": "survivor",
            "status": "PASS",
            "decision": "advance",
            "best_policy": "stable_short",
            "median_combo_sharpe": 1.3,
            "min_combo_sharpe": 0.2,
            "median_active_days": 400,
        },
    ]

    status, decision, classification, survivor, abandoned = gate.classify_comparison(rows)

    assert (status, decision) == ("PASS", "advance")
    assert classification == "RESEARCH_ONLY_SURVIVOR_IDENTIFIED_BELOW_DSR_PROMOTION_BAR"
    assert survivor["family"] == "survivor"
    assert abandoned == ["old"]
