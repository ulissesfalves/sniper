from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_research_hypothesis_space_freeze_gate as gate


def test_classify_freeze_passes_when_research_lines_abandoned_and_blockers_remain() -> None:
    summaries = [
        {"name": "stage_a_nonzero_exposure", "decision": "abandon"},
        {"name": "sandbox_cvar", "decision": "correct"},
        {"name": "dsr_zero_diagnostic", "decision": "advance"},
        {"name": "rank_score_threshold", "decision": "correct"},
        {"name": "rank_score_stability_correction", "decision": "abandon"},
    ]
    state = {
        "official_promotion_allowed": False,
        "paper_readiness_allowed": False,
        "dsr_status": {"dsr_honest": 0.0, "dsr_passed": False},
        "cvar_status": {"economic_status": "NOT_PROVEN_ZERO_EXPOSURE"},
    }

    status, decision, classification = gate.classify_freeze(summaries, state)

    assert status == "PASS"
    assert decision == "freeze"
    assert classification == "CURRENT_RESEARCH_HYPOTHESIS_SPACE_EXHAUSTED_UNDER_GOVERNANCE"


def test_classify_freeze_fails_on_state_conflict() -> None:
    summaries = [{"name": name, "decision": "abandon"} for name in gate.GATE_PATHS]
    state = {
        "official_promotion_allowed": True,
        "paper_readiness_allowed": False,
        "dsr_status": {"dsr_honest": 0.0, "dsr_passed": False},
        "cvar_status": {"economic_status": "NOT_PROVEN_ZERO_EXPOSURE"},
    }

    status, decision, classification = gate.classify_freeze(summaries, state)

    assert status == "FAIL"
    assert decision == "freeze"
    assert classification == "STATE_CONFLICT_PROMOTION_OR_READINESS_ALLOWED_DESPITE_BLOCKERS"
