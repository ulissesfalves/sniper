from __future__ import annotations

from pathlib import Path
import subprocess
import json

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase6_global_reproducibility_source_alignment_gate as phase6


def test_build_source_doc_alignment_flags_documented_missing_modules() -> None:
    alignment = phase6.build_source_doc_alignment()

    assert alignment["status"] == "ALIGNED"
    assert "phase4_config.py" not in alignment["missing_documented_modules"]
    assert "phase4_cpcv.py" not in alignment["missing_documented_modules"]


def test_build_portfolio_cvar_report_marks_zero_exposure_as_not_economic_pass(tmp_path: Path) -> None:
    missing_snapshot = tmp_path / "missing_snapshot.parquet"

    report = phase6.build_portfolio_cvar_report(snapshot_path=missing_snapshot)

    assert report["technical_persistence_status"] == "PASS_ZERO_EXPOSURE"
    assert report["economic_robustness_status"] == "NOT_PROVEN_ZERO_EXPOSURE"
    assert report["stress_report"]["cvar_ok"] is True


def test_build_portfolio_cvar_report_reads_positive_exposure(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.parquet"
    pd.DataFrame(
        {
            "date": ["2026-03-20", "2026-03-20"],
            "symbol": ["AAA", "BBB"],
            "position_usdt": [1000.0, 0.0],
            "sigma_entry": [0.04, 0.02],
        }
    ).to_parquet(snapshot)

    report = phase6.build_portfolio_cvar_report(snapshot_path=snapshot)

    assert report["technical_persistence_status"] == "MEASURED"
    assert report["positions"] == {"AAA": 1000.0}
    assert report["stress_report"]["n_positions"] == 1


def test_build_phase4_artifact_integrity_report_marks_dsr_zero_as_promotion_blocker(tmp_path: Path) -> None:
    phase4_dir = tmp_path / "phase4"
    phase4_dir.mkdir()
    (phase4_dir / "phase4_report_v4.json").write_text(
        json.dumps(
            {
                "dsr": {"dsr_honest": 0.0, "passed": False},
                "checks": {"DSR honesto > 0.95 [10]": False},
                "fallback": {"policy": "fixed_small_080_cooldown3"},
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "phase4_execution_snapshot.parquet",
        "phase4_aggregated_predictions.parquet",
        "phase4_oos_predictions.parquet",
        "phase4_gate_diagnostic.json",
    ):
        (phase4_dir / name).write_bytes(b"artifact")

    report = phase6.build_phase4_artifact_integrity_report(phase4_dir=phase4_dir)

    assert report["artifact_integrity_status"] == "PASS"
    assert report["dsr_honest"] == 0.0
    assert report["promotion_status"] == "BLOCKED_DSR_HONEST_ZERO"


def test_classify_gate_records_dsr_zero_blocker() -> None:
    status, decision, blockers = phase6.classify_gate(
        source_alignment={"status": "ALIGNED"},
        cvar_report={"economic_robustness_status": "MEASURED_ONLY"},
        environment_report={"all_required_probe_packages_available": True},
        regeneration_report={"clean_clone_or_equivalent": True, "returncode": 0},
        phase4_integrity_report={"artifact_integrity_status": "PASS", "promotion_status": "BLOCKED_DSR_HONEST_ZERO"},
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert blockers == ["dsr_honest_zero_blocks_promotion"]


def test_classify_gate_keeps_partial_until_clean_regeneration_is_proven() -> None:
    status, decision, blockers = phase6.classify_gate(
        source_alignment={"status": "ALIGNED"},
        cvar_report={"economic_robustness_status": "MEASURED_ONLY"},
        environment_report={"all_required_probe_packages_available": True},
        regeneration_report={"clean_clone_or_equivalent": False, "returncode": 0},
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert blockers == ["clean_regeneration_not_proven_in_clean_clone_or_equivalent"]


def test_run_regeneration_probe_preflights_missing_phase4_without_subprocess(tmp_path: Path) -> None:
    model_path = tmp_path / "models"

    report = phase6.run_regeneration_probe(model_path=model_path)

    assert report["command_executed"] is False
    assert report["returncode"] is None
    assert report["blocker"] == "MISSING_OFFICIAL_PHASE4_ARTIFACTS"
    assert report["preflight"]["classification"] == "MISSING_OFFICIAL_PHASE4_ARTIFACTS"


def test_run_regeneration_probe_preflights_missing_research_baseline_without_subprocess(tmp_path: Path) -> None:
    phase4_dir = tmp_path / "models" / "phase4"
    phase4_dir.mkdir(parents=True)
    for name in (
        "phase4_report_v4.json",
        "phase4_execution_snapshot.parquet",
        "phase4_aggregated_predictions.parquet",
        "phase4_oos_predictions.parquet",
        "phase4_gate_diagnostic.json",
    ):
        (phase4_dir / name).write_bytes(b"artifact")

    report = phase6.run_regeneration_probe(model_path=tmp_path / "models")

    assert report["command_executed"] is False
    assert report["returncode"] is None
    assert report["blocker"] == "MISSING_RESEARCH_BASELINE_ARTIFACTS"
    assert report["preflight"]["classification"] == "MISSING_RESEARCH_BASELINE_ARTIFACTS"
    assert report["preflight"]["missing_required_artifacts"] == []
    assert report["preflight"]["missing_regeneration_baseline_artifacts"]


def test_phase4_preflight_passes_with_research_baseline_artifacts(tmp_path: Path) -> None:
    model_path = tmp_path / "models"
    phase4_dir = model_path / "phase4"
    baseline_dir = model_path / "research" / "phase4_cross_sectional_ranking_baseline"
    phase4_dir.mkdir(parents=True)
    baseline_dir.mkdir(parents=True)
    for name in (
        "phase4_report_v4.json",
        "phase4_execution_snapshot.parquet",
        "phase4_aggregated_predictions.parquet",
        "phase4_oos_predictions.parquet",
        "phase4_gate_diagnostic.json",
    ):
        (phase4_dir / name).write_bytes(b"artifact")
    for name in (
        "stage_a_predictions.parquet",
        "stage_a_report.json",
        "stage_a_manifest.json",
        "stage_a_snapshot_proxy.parquet",
    ):
        (baseline_dir / name).write_bytes(b"artifact")

    preflight = phase6._phase4_preflight(model_path=model_path)

    assert preflight["classification"] == "PASS"
    assert preflight["missing_required_artifacts"] == []
    assert preflight["missing_regeneration_baseline_artifacts"] == []


def test_phase6_script_is_tracked_or_new_source_path_under_repo() -> None:
    path = phase6.THIS_FILE
    assert path.exists()
    assert path.is_relative_to(phase6.REPO_ROOT)
    result = subprocess.run(
        ["git", "status", "--short", "--", str(path.relative_to(phase6.REPO_ROOT))],
        cwd=phase6.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
