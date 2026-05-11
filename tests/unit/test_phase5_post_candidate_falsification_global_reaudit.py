from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_post_candidate_falsification_global_reaudit as gate


def _state() -> dict:
    return {
        "official_promotion_allowed": False,
        "paper_readiness_allowed": False,
        "human_decision_required": False,
        "allowed_next_modes": [
            "POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT",
            "START_RESEARCH_ONLY_THESIS",
            "FREEZE_LINE",
        ],
    }


def _decision_gate() -> dict:
    return {
        "status": "PASS",
        "decision": "abandon",
        "summary": [
            "classification=RESEARCH_CANDIDATE_FALSIFIED",
            "hard_falsifier_count=2",
        ],
    }


def test_classify_reaudit_advances_to_new_research_thesis_when_available() -> None:
    status, decision, classification, reason = gate.classify_post_falsification_reaudit(
        decision_gate=_decision_gate(),
        falsification_gate={"status": "FAIL", "decision": "abandon"},
        state=_state(),
        next_hypothesis={"available": True},
    )

    assert (status, decision) == ("PASS", "advance")
    assert classification == "POST_FALSIFICATION_REAUDIT_PASS_START_NEW_RESEARCH_THESIS"
    assert reason == "material_new_hypothesis_available"


def test_classify_reaudit_freezes_when_no_material_hypothesis_remains() -> None:
    status, decision, classification, reason = gate.classify_post_falsification_reaudit(
        decision_gate=_decision_gate(),
        falsification_gate={"status": "FAIL", "decision": "abandon"},
        state=_state(),
        next_hypothesis={"available": False},
    )

    assert (status, decision) == ("PASS", "freeze")
    assert classification == "POST_FALSIFICATION_REAUDIT_PASS_NO_MATERIAL_NEW_HYPOTHESIS"
    assert reason == "no_material_new_hypothesis"


def test_detect_next_hypothesis_reports_no_external_resource() -> None:
    next_hypothesis = gate.detect_material_next_hypothesis(cluster_gate_exists=True)

    assert next_hypothesis["available"] is False
    assert next_hypothesis["uses_external_resource"] is False
    assert next_hypothesis["uses_realized_variable_as_ex_ante_rule"] is False
