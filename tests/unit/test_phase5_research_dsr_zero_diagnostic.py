from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_research_dsr_zero_diagnostic as gate


def test_collect_candidates_preserves_scope() -> None:
    payload = {
        "fallback": {"sharpe": 0.88, "dsr_honest": 0.0},
        "fallback": {
            "rolling_stability": {
                "best_window": {"sharpe": 3.85, "dsr_honest": 0.69},
            }
        },
    }

    rows = gate.collect_dsr_and_sharpe_candidates(payload)

    assert any(row["diagnostic_scope"] == "validation_diagnostic" for row in rows)


def test_summarize_dsr_blocker_computes_gap() -> None:
    integrity = {
        "dsr": {
            "sharpe_is": 0.8808,
            "dsr_honest": 0.0,
            "passed": False,
            "sr_needed": 4.47,
            "n_trials_honest": 5000,
        }
    }
    rows = [{"path": "fallback", "sharpe": 0.8808, "dsr_honest": 0.0, "diagnostic_scope": "official"}]

    summary = gate.summarize_dsr_blocker({}, integrity, rows)

    assert summary["sharpe_gap_to_sr_needed"] == 3.5892
    assert summary["dsr_passed"] is False
    assert summary["n_trials_honest"] == 5000


def test_classify_diagnostic_passes_only_as_root_cause_diagnostic() -> None:
    status, decision, classification = gate.classify_diagnostic(
        {
            "dsr_honest": 0.0,
            "dsr_passed": False,
            "n_trials_honest": 5000,
            "sharpe_is": 0.8808,
            "sr_needed": 4.47,
            "sharpe_gap_to_sr_needed": 3.5892,
            "candidate_rows_scanned": 10,
        }
    )

    assert status == "PASS"
    assert decision == "advance"
    assert classification == "DSR_ZERO_ROOT_CAUSE_DIAGNOSTIC_COMPLETE"
