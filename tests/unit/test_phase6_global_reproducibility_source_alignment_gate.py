from __future__ import annotations

from pathlib import Path
import subprocess

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase6_global_reproducibility_source_alignment_gate as phase6


def test_build_source_doc_alignment_flags_documented_missing_modules() -> None:
    alignment = phase6.build_source_doc_alignment()

    assert alignment["status"] == "DIVERGENT"
    assert "phase4_config.py" in alignment["missing_documented_modules"]
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
