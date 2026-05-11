from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_final_freeze_resource_and_opportunity_audit as gate


def test_read_yaml_conservative_fallback_parses_agenda(monkeypatch, tmp_path: Path) -> None:
    agenda = tmp_path / "agenda.yaml"
    agenda.write_text(
        """
hypotheses:
  - id: "AGENDA-H01"
    name: "already_run"
    priority: "HIGH"
    required_repo_data:
      - "data/models/phase4/phase4_oos_predictions.parquet"
    suggested_gate: "phase5_existing_gate"
  - id: "AGENDA-H06"
    name: "low_unlock"
    priority: "LOW"
    execution_status: "INCONCLUSIVE"
    required_repo_data:
      - "data/parquet/unlocks/**"
    suggested_gate: "phase5_unlock_gate"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "yaml", None)

    parsed = gate._read_yaml(agenda)

    assert parsed["hypotheses"][0]["id"] == "AGENDA-H01"
    assert parsed["hypotheses"][0]["priority"] == "HIGH"
    assert parsed["hypotheses"][0]["required_repo_data"] == ["data/models/phase4/phase4_oos_predictions.parquet"]
    assert parsed["hypotheses"][1]["execution_status"] == "INCONCLUSIVE"


def test_h06_preflight_requires_canonical_unlock_artifacts(tmp_path: Path) -> None:
    shadow_dir = tmp_path / "data" / "parquet" / "unlock_wayback" / "SOL"
    shadow_dir.mkdir(parents=True)
    (shadow_dir / "shadow.parquet").write_bytes(b"PAR1")

    assessment = gate.h06_preflight_assessment(tmp_path)

    assert assessment["preflight_executable"] is False
    assert assessment["status"] == "EXTERNAL_RESOURCE_REQUIRED"
    assert "data/parquet/unlocks/**" in assessment["missing_expected_patterns"]
    assert "data/parquet/unlock_diagnostics/unlock_quality_daily.parquet" in assessment["missing_expected_patterns"]
    assert assessment["shadow_or_noncanonical_artifacts"][0]["exists"] is True


def test_h06_preflight_passes_when_exact_artifacts_exist(tmp_path: Path) -> None:
    unlock_dir = tmp_path / "data" / "parquet" / "unlocks" / "SOL"
    unlock_dir.mkdir(parents=True)
    (unlock_dir / "unlock.parquet").write_bytes(b"PAR1")
    quality = tmp_path / "data" / "parquet" / "unlock_diagnostics" / "unlock_quality_daily.parquet"
    quality.parent.mkdir(parents=True)
    quality.write_bytes(b"PAR1")

    assessment = gate.h06_preflight_assessment(tmp_path)

    assert assessment["preflight_executable"] is True
    assert assessment["status"] == "PREFLIGHT_EXECUTABLE"
    assert assessment["missing_expected_patterns"] == []


def test_hypothesis_execution_rows_mark_executed_high_medium_not_remaining(tmp_path: Path) -> None:
    report = tmp_path / "reports" / "gates" / "phase5_test_gate" / "gate_report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    agenda = {
        "hypotheses": [
            {
                "id": "AGENDA-H01",
                "name": "already_run",
                "priority": "HIGH",
                "suggested_gate": "phase5_test_gate",
                "required_repo_data": [],
            },
            {
                "id": "AGENDA-H06",
                "name": "low_missing",
                "priority": "LOW",
                "suggested_gate": "phase5_low_gate",
                "required_repo_data": ["data/parquet/unlocks/**"],
            },
        ]
    }

    rows = gate.hypothesis_execution_rows(agenda, tmp_path)

    assert rows[0]["executed_or_closed"] is True
    assert rows[0]["is_remaining_high_medium_executable"] is False
    assert rows[1]["is_high_or_medium"] is False


def test_classify_final_audit_freezes_after_opportunity_audit_when_no_safe_action() -> None:
    h06 = {
        "preflight_executable": False,
        "missing_expected_patterns": ["data/parquet/unlocks/**"],
    }
    modules = [{"module": "data_quality_gate", "safe_next_action": False}]

    status, decision, classification = gate.classify_final_opportunity_audit(
        remaining_high_medium=[],
        h06_assessment=h06,
        modules=modules,
    )

    assert (status, decision) == ("PASS", "freeze")
    assert classification == "FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED"


def test_classify_final_audit_blocks_when_h06_preflight_is_available() -> None:
    h06 = {
        "preflight_executable": True,
        "missing_expected_patterns": [],
    }
    modules = [{"module": "h06_unlock_shadow_feature_ablation_preflight", "safe_next_action": True}]

    status, decision, classification = gate.classify_final_opportunity_audit(
        remaining_high_medium=[],
        h06_assessment=h06,
        modules=modules,
    )

    assert (status, decision) == ("PARTIAL", "correct")
    assert classification == "LOW_PRIORITY_PREFLIGHT_GATE_AVAILABLE"
