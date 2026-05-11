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

import phase5_research_feature_family_ablation_blocker_decomposition as gate


def _toy_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "p_bma_pkf": [0.6, 0.4, 0.7, 0.3],
            "p_meta_calibrated": [0.55, 0.35, 0.60, 0.30],
            "p_meta_raw": [0.5, 0.4, 0.7, 0.2],
            "sigma_ewma": [0.5, 1.0, 0.7, 1.2],
            "hmm_prob_bull": [0.8, 0.2, 0.7, 0.3],
            "pnl_real": [0.02, -0.01, 0.03, -0.02],
            "avg_sl_train": [0.1, 0.2, 0.1, 0.2],
            "rank_score_stage_a": [1.0, 0.2, 0.9, 0.1],
        }
    )


def test_feature_diagnostic_marks_forbidden_operational_inputs() -> None:
    row = gate.feature_diagnostic(_toy_frame(), "avg_sl_train")

    assert row["exists"] is True
    assert row["forbidden_as_operational_input"] is True
    assert "spearman_to_pnl_real_diagnostic" in row


def test_family_diagnostics_are_diagnostic_only() -> None:
    family_frame = gate.family_diagnostics(_toy_frame())

    assert not family_frame.empty
    assert "operational_allowed" in family_frame.columns
    assert family_frame["contains_forbidden_operational_input"].any()


def test_leakage_assessment_does_not_create_operational_signal() -> None:
    family_frame = gate.family_diagnostics(_toy_frame())
    assessment = gate.leakage_and_agenda_assessment(family_frame)

    assert assessment["diagnostic_output_is_operational_signal"] is False
    assert assessment["official_promotion_allowed"] is False
    assert assessment["paper_readiness_allowed"] is False


def test_classification_passes_governed_exhaustion_diagnostic() -> None:
    family_frame = gate.family_diagnostics(_toy_frame())
    assessment = gate.leakage_and_agenda_assessment(family_frame)
    status, decision, classification = gate.classify_feature_family_decomposition(assessment, family_frame)

    assert (status, decision) == ("PASS", "advance")
    assert classification == "FEATURE_FAMILY_ABLATION_COMPLETE_NO_HIGH_MEDIUM_EXECUTABLE_FAMILY"
