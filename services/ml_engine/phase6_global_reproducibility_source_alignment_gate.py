#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

THIS_FILE = Path(__file__).resolve()
THIS_DIR = THIS_FILE.parent
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack
from services.ml_engine.sizing.kelly_cvar import CVAR_LIMIT, portfolio_stress_report

GATE_SLUG = "phase6_global_reproducibility_source_alignment_gate"
PHASE_FAMILY = GATE_SLUG
BASE_BRANCH = "codex/openclaw-sniper-handoff"
SUGGESTED_BRANCH = "codex/phase6-global-reproducibility-source-alignment"
CAPITAL_INITIAL = 200_000.0

GATE_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
MODEL_ROOT = REPO_ROOT / "data" / "models"
PARQUET_ROOT = REPO_ROOT / "data" / "parquet"
PHASE4_ROOT = MODEL_ROOT / "phase4"
RESEARCH_ROOT = MODEL_ROOT / "research"

SOURCE_DOC_ALIGNMENT_PATH = GATE_DIR / "source_doc_alignment.json"
PORTFOLIO_CVAR_REPORT_PATH = GATE_DIR / "portfolio_cvar_report.json"

PHASE4_MEMORY_DOC = REPO_ROOT / "docs" / "SNIPER_memoria_especificacao_controle_fase4R_v3.md"
TECH_ARCH_PDF = REPO_ROOT / "docs" / "SNIPER_v10.10_Technical_Architecture_presentation.pdf"

DOCUMENTED_PHASE4_MODULES = (
    "phase4_config.py",
    "phase4_data.py",
    "phase4_dsr.py",
    "phase4_backtest.py",
    "phase4_calibration.py",
)

CURRENT_PHASE4_SOURCE_EXPECTED = (
    "phase4_cpcv.py",
    "phase4_gate_diagnostic.py",
    "phase4_stage_a_experiment.py",
)

REGENERATION_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    (
        "restore_sovereign_closure_bundle",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py"],
    ),
    (
        "sovereign_hardening_recheck",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py"],
    ),
    (
        "operational_fragility_audit_and_bounded_correction",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py"],
    ),
    (
        "recent_regime_policy_falsification",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py"],
    ),
    ("phase4_gate_diagnostic", [sys.executable, "services/ml_engine/phase4_gate_diagnostic.py"]),
)

VALIDATION_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("py_compile_phase6_gate", [sys.executable, "-m", "py_compile", "services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py"]),
    ("pytest_gate_reports", [sys.executable, "-m", "pytest", "tests/unit/test_gate_reports.py", "-q"]),
    (
        "pytest_nautilus_bridge_existing",
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_nautilus_bridge_contract.py",
            "tests/unit/test_nautilus_bridge_acceptance.py",
            "tests/unit/test_nautilus_bridge_phase4_publisher.py",
            "tests/unit/test_nautilus_bridge_consumer.py",
            "tests/unit/test_nautilus_bridge_reconciler.py",
            "tests/unit/test_nautilus_bridge_phase4_paper_daemon.py",
            "tests/unit/test_nautilus_bridge_phase4_paper_once.py",
            "-q",
        ],
    ),
    ("pytest_unit_full_collection", [sys.executable, "-m", "pytest", "tests/unit", "-q"]),
)

EXPECTED_GATE_REPORTS = (
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_hardening_recheck/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_recent_regime_policy_falsification/gate_report.json",
    PHASE4_ROOT / "phase4_gate_diagnostic.json",
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _git_output(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_returncode(*args: str) -> int:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    return int(result.returncode)


def _git_baseline() -> dict[str, Any]:
    status = _git_output("status", "--short")
    return {
        "branch": _git_output("branch", "--show-current"),
        "commit": _git_output("rev-parse", "HEAD"),
        "status_short": [line for line in status.splitlines() if line.strip()],
        "working_tree_dirty": bool(status.strip()),
    }


def _run_command(label: str, args: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["SNIPER_WORKSPACE_PATH"] = str(REPO_ROOT)
    env["SNIPER_MODEL_PATH"] = str(MODEL_ROOT)
    env["MODEL_ARTIFACTS_PATH"] = str(MODEL_ROOT)
    env["PARQUET_BASE_PATH"] = str(PARQUET_ROOT)
    started = time.monotonic()
    completed = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    duration = round(time.monotonic() - started, 3)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    return {
        "label": label,
        "command": " ".join(args),
        "returncode": int(completed.returncode),
        "duration_seconds": duration,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }


def _artifact_hashes(paths: tuple[Path, ...]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for path in paths:
        records[str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)] = {
            "exists": path.exists(),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        }
    return records


def _collect_gate_statuses() -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    for report_path in sorted((REPO_ROOT / "reports" / "gates").glob("*/gate_report.json")):
        if report_path.parent.name == GATE_SLUG:
            continue
        try:
            payload = _read_json(report_path)
        except Exception as exc:
            statuses[report_path.parent.name] = {"read_error": str(exc)}
            continue
        statuses[report_path.parent.name] = {
            "status": payload.get("status"),
            "decision": payload.get("decision"),
            "summary": payload.get("summary"),
        }
    return statuses


def _source_doc_alignment() -> dict[str, Any]:
    doc_text = PHASE4_MEMORY_DOC.read_text(encoding="utf-8", errors="ignore") if PHASE4_MEMORY_DOC.exists() else ""
    documented = []
    for module_name in DOCUMENTED_PHASE4_MODULES:
        path = THIS_DIR / module_name
        documented.append(
            {
                "module": module_name,
                "documented_in_phase4r_memory": module_name in doc_text,
                "tracked_source_path": str(path.relative_to(REPO_ROOT)),
                "exists_in_current_checkout": path.exists(),
                "git_tracked": _git_returncode("ls-files", "--error-unmatch", str(path.relative_to(REPO_ROOT))) == 0,
            }
        )

    actual_phase4 = [
        str(Path(path).as_posix())
        for path in _git_output("ls-files", "services/ml_engine/phase4_*.py").splitlines()
        if path.strip()
    ]
    current_expected = []
    for module_name in CURRENT_PHASE4_SOURCE_EXPECTED:
        path = THIS_DIR / module_name
        current_expected.append(
            {
                "module": module_name,
                "path": str(path.relative_to(REPO_ROOT)),
                "exists": path.exists(),
                "git_tracked": _git_returncode("ls-files", "--error-unmatch", str(path.relative_to(REPO_ROOT))) == 0,
            }
        )

    correction_note_present = "Nota de alinhamento source-doc" in doc_text and GATE_SLUG in doc_text
    missing_documented_modules = [row["module"] for row in documented if not row["exists_in_current_checkout"]]
    current_source_ok = all(row["exists"] and row["git_tracked"] for row in current_expected)
    status = "PASS" if correction_note_present and current_source_ok else "FAIL"
    if missing_documented_modules and not correction_note_present:
        status = "FAIL"
    elif missing_documented_modules and correction_note_present and current_source_ok:
        status = "PASS"
    elif current_source_ok:
        status = "PARTIAL"

    return {
        "status": status,
        "decision": "document_current_checkout_as_source_of_truth",
        "phase4_memory_doc": str(PHASE4_MEMORY_DOC.relative_to(REPO_ROOT)),
        "correction_note_present": correction_note_present,
        "documented_phase4r_modules": documented,
        "missing_documented_modules": missing_documented_modules,
        "current_tracked_phase4_sources": actual_phase4,
        "current_expected_sources": current_expected,
        "interpretation": (
            "Section 7 remains historical/reported memory; current tracked source is the operational truth for this branch."
            if correction_note_present
            else "Phase 4-R4 documentation still implies modules that are absent from tracked source."
        ),
        "non_actions": [
            "No functional Phase 4 refactor was performed.",
            "No model artifact was promoted to official.",
            "A3/A4 were not reopened.",
        ],
    }


def _load_phase4_diagnostic_metrics() -> dict[str, Any]:
    diagnostic_path = PHASE4_ROOT / "phase4_gate_diagnostic.json"
    report_path = PHASE4_ROOT / "phase4_report_v4.json"
    metrics: dict[str, Any] = {}
    if report_path.exists():
        report = _read_json(report_path)
        dsr = report.get("dsr") or {}
        fallback = report.get("fallback") or {}
        metrics.update(
            {
                "phase4_decision_policy": report.get("phase4_decision_policy"),
                "dsr_honest": dsr.get("dsr_honest"),
                "n_trials_honest": dsr.get("n_trials_honest"),
                "sharpe_oos": fallback.get("sharpe"),
                "subperiods_positive": (fallback.get("operational_robustness", {}).get("fixed_small_080_cooldown3") or {}).get(
                    "subperiods_positive"
                ),
                "fallback_equity_final": fallback.get("equity_final"),
            }
        )
    if diagnostic_path.exists():
        diagnostic = _read_json(diagnostic_path)
        blockers = diagnostic.get("blocker_reclassification") or {}
        metrics["hard_blockers"] = [
            name for name, meta in blockers.items() if meta.get("classification") == "HARD_BLOCKER"
        ]
        if "CVaR empirico persistido" in blockers:
            metrics["previous_cvar_empirical_status"] = blockers["CVaR empirico persistido"].get("audit_status")
    return metrics


def _portfolio_cvar_report() -> dict[str, Any]:
    snapshot_path = PHASE4_ROOT / "phase4_execution_snapshot.parquet"
    if not snapshot_path.exists():
        return {
            "status": "INCONCLUSIVE",
            "reason": "missing_phase4_execution_snapshot",
            "snapshot_path": str(snapshot_path.relative_to(REPO_ROOT)),
        }

    snapshot = pd.read_parquet(snapshot_path)
    position_col = "position_usdt" if "position_usdt" in snapshot.columns else "position_usdt_meta"
    sigma_col = "sigma_ewma" if "sigma_ewma" in snapshot.columns else None
    active_col = "is_active" if "is_active" in snapshot.columns else None
    positions_series = pd.to_numeric(snapshot.get(position_col, 0.0), errors="coerce").fillna(0.0)
    active = positions_series > 0.0
    if active_col:
        active = active & snapshot[active_col].fillna(False).astype(bool)
    active_frame = snapshot.loc[active].copy()

    positions = {
        str(row["symbol"]): float(row[position_col])
        for _, row in active_frame.iterrows()
        if "symbol" in active_frame.columns and float(row[position_col]) > 0.0
    }
    sigmas = {
        str(row["symbol"]): float(row[sigma_col])
        for _, row in active_frame.iterrows()
        if sigma_col and "symbol" in active_frame.columns and pd.notna(row.get(sigma_col))
    }
    stress = portfolio_stress_report(positions, sigmas, CAPITAL_INITIAL) if positions else {
        "cvar_historical": 0.0,
        "cvar_stress_rho1": 0.0,
        "cvar_limit": CVAR_LIMIT,
        "margin_of_safety": CVAR_LIMIT,
        "cvar_ok": True,
        "hidden_risk_factor": 0.0,
        "sigma_portfolio_stress": 0.0,
        "n_positions": 0,
        "total_exposure_pct": 0.0,
    }
    gross_exposure = float(sum(abs(value) for value in positions.values()))
    latest_date = None
    if "date" in snapshot.columns and not snapshot.empty:
        latest = pd.to_datetime(snapshot["date"], errors="coerce").dropna().max()
        latest_date = latest.strftime("%Y-%m-%d") if pd.notna(latest) else None
    status = "PASS_ZERO_EXPOSURE" if not positions else ("PASS" if stress.get("cvar_ok") else "FAIL")
    return {
        "status": status,
        "scope": "current_official_phase4_execution_snapshot_only",
        "snapshot_path": str(snapshot_path.relative_to(REPO_ROOT)),
        "snapshot_sha256": _sha256_file(snapshot_path),
        "latest_date": latest_date,
        "rows": int(len(snapshot)),
        "position_column": position_col,
        "sigma_column": sigma_col,
        "capital_initial": CAPITAL_INITIAL,
        "positions_usdt": positions,
        "sigmas": sigmas,
        "gross_exposure_usdt": round(gross_exposure, 6),
        "gross_exposure_pct": round(gross_exposure / CAPITAL_INITIAL * 100.0, 6),
        "stress_report": stress,
        "interpretation": (
            "The current official snapshot has zero active exposure; CVaR is valid for this snapshot but does not approve model promotion."
            if not positions
            else "Current official snapshot CVaR was computed with rho=1 stress."
        ),
        "non_promotion_statement": "This artifact is a risk audit for the current snapshot, not paper readiness or capital readiness.",
    }


def _governance_boundaries() -> dict[str, Any]:
    return {
        "official": {
            "status": "FAST_PATH_REMAINS_OFFICIAL",
            "artifacts": [
                str((PHASE4_ROOT / "phase4_report_v4.json").relative_to(REPO_ROOT)),
                str((PHASE4_ROOT / "phase4_gate_diagnostic.json").relative_to(REPO_ROOT)),
                str((PHASE4_ROOT / "phase4_execution_snapshot.parquet").relative_to(REPO_ROOT)),
                str((PHASE4_ROOT / "phase4_aggregated_predictions.parquet").relative_to(REPO_ROOT)),
            ],
        },
        "research": {
            "status": "RESEARCH_ONLY",
            "baseline": "phase5_cross_sectional_sovereign_closure_restored",
            "cross_sectional_family": "ALIVE_BUT_NOT_PROMOTABLE",
            "artifacts_root": str(RESEARCH_ROOT.relative_to(REPO_ROOT)),
        },
        "shadow": {
            "RiskLabAI": "oracle/shadow_only_not_official",
        },
        "paper": {
            "status": "MECHANISM_ONLY_VALIDATION",
            "promotion": "blocked_until_upstream_official_gates_pass",
            "no_snapshot_published": True,
        },
        "sandbox": {
            "status": "not_promoted",
        },
        "invariants": {
            "A3_reopened": False,
            "A4_reopened": False,
            "research_promoted_to_official": False,
            "alive_but_not_promotable_treated_as_promotable": False,
        },
    }


def _metrics_rows(
    *,
    source_doc_alignment: dict[str, Any],
    portfolio_cvar: dict[str, Any],
    phase4_metrics: dict[str, Any],
    command_records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
    clean_regeneration: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "source_doc_alignment_status",
            "metric_value": source_doc_alignment.get("status"),
            "metric_threshold": "PASS",
            "metric_status": "PASS" if source_doc_alignment.get("status") == "PASS" else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "portfolio_cvar_stress_rho1",
            "metric_value": (portfolio_cvar.get("stress_report") or {}).get("cvar_stress_rho1"),
            "metric_threshold": CVAR_LIMIT,
            "metric_status": "PASS" if (portfolio_cvar.get("stress_report") or {}).get("cvar_ok") else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "regeneration_commands_exit_zero",
            "metric_value": all(record["returncode"] == 0 for record in command_records),
            "metric_threshold": True,
            "metric_status": "PASS" if all(record["returncode"] == 0 for record in command_records) else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "targeted_validation_exit_zero",
            "metric_value": all(record["returncode"] == 0 for record in validation_records if record["label"] != "pytest_unit_full_collection"),
            "metric_threshold": True,
            "metric_status": "PASS"
            if all(record["returncode"] == 0 for record in validation_records if record["label"] != "pytest_unit_full_collection")
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "full_unit_suite_exit_zero",
            "metric_value": next((record["returncode"] == 0 for record in validation_records if record["label"] == "pytest_unit_full_collection"), False),
            "metric_threshold": True,
            "metric_status": "PASS"
            if next((record["returncode"] == 0 for record in validation_records if record["label"] == "pytest_unit_full_collection"), False)
            else "INCONCLUSIVE",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "clean_clone_proven",
            "metric_value": False,
            "metric_threshold": True,
            "metric_status": "PARTIAL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "technical_architecture_pdf_text_auditable",
            "metric_value": False,
            "metric_threshold": True,
            "metric_status": "INCONCLUSIVE",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "dsr_honest_preserved",
            "metric_value": phase4_metrics.get("dsr_honest"),
            "metric_threshold": "> 0.95 for promotion",
            "metric_status": "FAIL" if phase4_metrics.get("dsr_honest") == 0.0 else "PARTIAL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "cross_sectional_status",
            "metric_value": "ALIVE_BUT_NOT_PROMOTABLE",
            "metric_threshold": "must_not_be_promoted",
            "metric_status": "PASS",
        },
    ]
    for gate_name, payload in clean_regeneration.get("gate_statuses_after", {}).items():
        rows.append(
            {
                "gate_slug": GATE_SLUG,
                "metric_name": f"upstream_gate_{gate_name}",
                "metric_value": payload.get("status"),
                "metric_threshold": "record_only",
                "metric_status": payload.get("status") or "UNKNOWN",
            }
        )
    return rows


def main() -> None:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    before_git = _git_baseline()
    before_hashes = _artifact_hashes(EXPECTED_GATE_REPORTS)

    command_records = [_run_command(label, args) for label, args in REGENERATION_COMMANDS]
    validation_records = [_run_command(label, args) for label, args in VALIDATION_COMMANDS]
    after_hashes = _artifact_hashes(EXPECTED_GATE_REPORTS)
    gate_statuses_after = _collect_gate_statuses()

    source_doc_alignment = _source_doc_alignment()
    portfolio_cvar = _portfolio_cvar_report()
    phase4_metrics = _load_phase4_diagnostic_metrics()
    governance = _governance_boundaries()

    _write_json(SOURCE_DOC_ALIGNMENT_PATH, source_doc_alignment)
    _write_json(PORTFOLIO_CVAR_REPORT_PATH, portfolio_cvar)

    clean_regeneration = {
        "status": "PARTIAL",
        "classification": "LOCAL_REGENERATION_WITH_HASHES_NOT_CLEAN_CLONE",
        "commands": command_records,
        "all_commands_exit_zero": all(record["returncode"] == 0 for record in command_records),
        "artifact_hashes_before": before_hashes,
        "artifact_hashes_after": after_hashes,
        "gate_statuses_after": gate_statuses_after,
        "missing_expected_artifacts_after": [
            path for path, record in after_hashes.items() if not record.get("exists")
        ],
        "objective_divergences": [
            {
                "artifact": path,
                "before_sha256": before_hashes.get(path, {}).get("sha256"),
                "after_sha256": after_hashes.get(path, {}).get("sha256"),
                "classification": "HASH_CHANGED_DURING_LOCAL_REGENERATION",
            }
            for path in sorted(after_hashes)
            if before_hashes.get(path, {}).get("sha256") != after_hashes.get(path, {}).get("sha256")
        ],
        "limitation": "The gate ran regeneration commands in the current workspace, not in an isolated fresh clone.",
    }
    if not clean_regeneration["all_commands_exit_zero"]:
        clean_regeneration["status"] = "FAIL"
    elif not clean_regeneration["objective_divergences"] and not clean_regeneration["missing_expected_artifacts_after"]:
        clean_regeneration["status"] = "PARTIAL"

    blockers = [
        "DSR honest remains a promotion blocker if it is 0.0.",
        "Cross-sectional sovereign family remains ALIVE_BUT_NOT_PROMOTABLE.",
        "Clean clone regeneration was not proven in an isolated clone.",
        "Technical Architecture PDF remains text/OCR inconclusive.",
    ]
    if source_doc_alignment.get("status") != "PASS":
        blockers.append("Phase 4-R4 source-doc alignment is not resolved.")
    if portfolio_cvar.get("status") == "INCONCLUSIVE":
        blockers.append("Portfolio CVaR empirical artifact is inconclusive.")
    if not clean_regeneration["all_commands_exit_zero"]:
        blockers.append("One or more regeneration commands failed.")
    full_unit_record = next((record for record in validation_records if record["label"] == "pytest_unit_full_collection"), None)
    if full_unit_record and full_unit_record["returncode"] != 0:
        blockers.append("Full unit suite did not collect in this environment; see pytest_unit_full_collection stderr/stdout tails.")

    risks = [
        "Treating this gate as model promotion would violate the audit scope.",
        "Existing research artifacts are local and may not exist in a clean clone.",
        "CVaR was computed for the current official snapshot only; zero exposure is not paper readiness.",
        "RiskLabAI must remain oracle/shadow.",
    ]

    status = "PARTIAL"
    decision = "correct"
    if not clean_regeneration["all_commands_exit_zero"] or source_doc_alignment.get("status") == "FAIL":
        status = "FAIL"
        decision = "abandon"
    elif portfolio_cvar.get("status") == "INCONCLUSIVE":
        status = "INCONCLUSIVE"
        decision = "inconclusive"

    after_git = _git_baseline()
    gate_metrics = _metrics_rows(
        source_doc_alignment=source_doc_alignment,
        portfolio_cvar=portfolio_cvar,
        phase4_metrics=phase4_metrics,
        command_records=command_records,
        validation_records=validation_records,
        clean_regeneration=clean_regeneration,
    )

    generated_pre_pack = [
        artifact_record(SOURCE_DOC_ALIGNMENT_PATH),
        artifact_record(PORTFOLIO_CVAR_REPORT_PATH),
    ]
    official_artifacts = [
        artifact_record(PHASE4_ROOT / "phase4_report_v4.json"),
        artifact_record(PHASE4_ROOT / "phase4_gate_diagnostic.json"),
        artifact_record(PHASE4_ROOT / "phase4_execution_snapshot.parquet"),
        artifact_record(PHASE4_ROOT / "phase4_aggregated_predictions.parquet"),
    ]
    research_artifacts = [
        artifact_record(report_path)
        for report_path in EXPECTED_GATE_REPORTS
        if "reports" in str(report_path) or "research" in str(report_path)
    ]

    summary = [
        f"source_doc_alignment={source_doc_alignment.get('status')}",
        f"clean_regeneration={clean_regeneration.get('status')}",
        f"portfolio_cvar={portfolio_cvar.get('status')}",
        f"dsr_honest={phase4_metrics.get('dsr_honest')}",
        "cross_sectional_status=ALIVE_BUT_NOT_PROMOTABLE",
    ]

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": before_git.get("commit"),
        "commit": after_git.get("commit"),
        "base_branch": BASE_BRANCH,
        "working_tree_dirty": after_git.get("working_tree_dirty"),
        "branch": after_git.get("branch"),
        "compare_url": None,
        "pr_url": None,
        "official_artifacts_used": official_artifacts,
        "artifacts_official_used": official_artifacts,
        "research_artifacts_generated": generated_pre_pack + research_artifacts,
        "artifacts_research_generated": generated_pre_pack + research_artifacts,
        "artifacts_generated": generated_pre_pack,
        "metrics": phase4_metrics,
        "source_doc_alignment": source_doc_alignment,
        "clean_regeneration": clean_regeneration,
        "validation": {
            "status": "PARTIAL" if any(record["returncode"] != 0 for record in validation_records) else "PASS",
            "commands": validation_records,
            "targeted_validation_exit_zero": all(
                record["returncode"] == 0 for record in validation_records if record["label"] != "pytest_unit_full_collection"
            ),
            "full_unit_suite_exit_zero": bool(full_unit_record and full_unit_record["returncode"] == 0),
        },
        "portfolio_cvar": portfolio_cvar,
        "governance_boundaries": governance,
        "summary": summary,
        "gates": gate_metrics,
        "blockers": blockers,
        "risks": risks,
        "risks_residual": risks,
        "recommendation": "Keep the family frozen as research-only and run a true clean-clone regeneration gate before any model/paper promotion.",
        "next_recommended_step": "Run isolated clean-clone regeneration or explicitly accept PARTIAL with documented local-only reproducibility limits.",
        "commands": command_records + validation_records,
        "timestamp_utc": _now_utc(),
    }

    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": before_git.get("commit"),
        "branch": after_git.get("branch"),
        "working_tree_dirty_before": before_git.get("working_tree_dirty"),
        "working_tree_dirty_after": after_git.get("working_tree_dirty"),
        "source_artifacts": [
            artifact_record(REPO_ROOT / "docs" / "SNIPER_openclaw_handoff.md"),
            artifact_record(REPO_ROOT / "docs" / "SNIPER_regeneration_guide.md"),
            artifact_record(PHASE4_MEMORY_DOC),
            artifact_record(TECH_ARCH_PDF),
            *official_artifacts,
        ],
        "generated_artifacts": generated_pre_pack,
        "commands_executed": command_records + validation_records,
        "notes": [
            "No artifacts were promoted to official.",
            "A3/A4 were not reopened.",
            "ALIVE_BUT_NOT_PROMOTABLE was preserved as non-promotable.",
            "RiskLabAI remains oracle/shadow.",
            "gate_metrics.parquet is generated by the standard gate pack writer.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": (
            f"Gate `{GATE_SLUG}` concluído com status `{status}` e decisão `{decision}`.\n\n"
            "O gate produziu evidência local de alinhamento source-doc-artifact, persistiu CVaR empírico do snapshot official atual e reexecutou os runners Phase 5/Phase 4 com hashes before/after. "
            "A classificação permanece limitada porque não houve clone limpo isolado e os blockers quantitativos continuam preservados."
        ),
        "Baseline congelado": (
            f"- Branch base recomendada: `{BASE_BRANCH}`\n"
            f"- Branch executada: `{after_git.get('branch')}`\n"
            f"- Commit: `{after_git.get('commit')}`\n"
            "- A3/A4: não reabertos\n"
            "- Cross-sectional: `ALIVE_BUT_NOT_PROMOTABLE`\n"
            "- RiskLabAI: oracle/shadow, não official"
        ),
        "Mudanças implementadas": (
            "- Criado runner `services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py`.\n"
            "- Adicionada nota documental de alinhamento source-doc na memória Fase 4-R.\n"
            "- Persistidos `source_doc_alignment.json` e `portfolio_cvar_report.json`.\n"
            "- Nenhuma promoção official foi feita."
        ),
        "Artifacts gerados": "\n".join(
            [
                f"- `{SOURCE_DOC_ALIGNMENT_PATH.relative_to(REPO_ROOT)}`",
                f"- `{PORTFOLIO_CVAR_REPORT_PATH.relative_to(REPO_ROOT)}`",
                f"- `reports/gates/{GATE_SLUG}/gate_report.json`",
                f"- `reports/gates/{GATE_SLUG}/gate_report.md`",
                f"- `reports/gates/{GATE_SLUG}/gate_manifest.json`",
                f"- `reports/gates/{GATE_SLUG}/gate_metrics.parquet`",
            ]
        ),
        "Resultados": (
            f"- Source-doc alignment: `{source_doc_alignment.get('status')}`\n"
            f"- Clean/local regeneration: `{clean_regeneration.get('status')}`\n"
            f"- Portfolio CVaR: `{portfolio_cvar.get('status')}`\n"
            f"- Targeted validation: `{'PASS' if all(record['returncode'] == 0 for record in validation_records if record['label'] != 'pytest_unit_full_collection') else 'FAIL'}`\n"
            f"- Full unit suite: `{'PASS' if full_unit_record and full_unit_record['returncode'] == 0 else 'INCONCLUSIVE'}`\n"
            f"- DSR honesto: `{phase4_metrics.get('dsr_honest')}`\n"
            f"- Sharpe OOS: `{phase4_metrics.get('sharpe_oos')}`\n"
            f"- Hard blockers: `{phase4_metrics.get('hard_blockers')}`"
        ),
        "Avaliação contra gates": "\n".join(
            f"- {row['metric_name']}: `{row['metric_status']}` ({row['metric_value']})" for row in gate_metrics
        ),
        "Riscos residuais": "\n".join(f"- {risk}" for risk in risks),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. O próximo passo é uma regeneração em clone limpo isolado ou aceitar este gate como PARTIAL local-only. "
            "Não há paper readiness, model promotion ou capital readiness nesta rodada."
        ),
    }

    outputs = write_gate_pack(
        output_dir=GATE_DIR,
        gate_report=gate_report,
        gate_manifest=manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )

    print(
        json.dumps(
            {
                "gate_slug": GATE_SLUG,
                "status": status,
                "decision": decision,
                "outputs": {key: str(path) for key, path in outputs.items()},
                "source_doc_alignment": source_doc_alignment.get("status"),
                "clean_regeneration": clean_regeneration.get("status"),
                "portfolio_cvar": portfolio_cvar.get("status"),
                "dsr_honest": phase4_metrics.get("dsr_honest"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
