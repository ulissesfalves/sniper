from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_post_candidate_falsification_governed_freeze as gate


def test_classify_freeze_passes_when_requirements_and_no_hypotheses() -> None:
    requirements = {
        "post_falsification_reaudit_passed": True,
        "at_least_two_material_families_tested": True,
        "explicit_dsr_diagnostic_exists": True,
        "research_cvar_nonzero_exposure_evaluated": True,
        "family_comparison_recorded": True,
        "prior_candidate_falsified": True,
        "cluster_candidate_falsified": True,
        "no_official_promotion": True,
        "no_paper_readiness": True,
    }

    status, decision, classification = gate.classify_freeze(requirements, [])

    assert (status, decision) == ("PASS", "freeze")
    assert classification == "FULL_FREEZE_AFTER_REAUDIT"


def test_classify_freeze_blocks_when_material_hypothesis_remains() -> None:
    requirements = {
        "post_falsification_reaudit_passed": True,
        "at_least_two_material_families_tested": True,
    }

    status, decision, classification = gate.classify_freeze(
        requirements,
        [{"hypothesis": "new_safe_thesis"}],
    )

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "FREEZE_BLOCKED_BY_MATERIAL_HYPOTHESIS_OR_INCOMPLETE_REQUIREMENT"
