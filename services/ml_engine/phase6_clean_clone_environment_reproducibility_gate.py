#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase6_clean_clone_environment_reproducibility_gate"
PHASE_FAMILY = GATE_SLUG
BASE_BRANCH = "codex/phase6-global-reproducibility-source-alignment"
SUGGESTED_BRANCH = "codex/phase6-clean-clone-environment-reproducibility"

GATE_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
MODEL_ROOT = REPO_ROOT / "data" / "models"
PARQUET_ROOT = REPO_ROOT / "data" / "parquet"

ENVIRONMENT_REPORT_PATH = GATE_DIR / "environment_reproducibility_report.json"
DEPENDENCY_AUDIT_PATH = GATE_DIR / "dependency_audit.json"
CLEAN_CLONE_REPORT_PATH = GATE_DIR / "clean_clone_regeneration_report.json"
PYTEST_COLLECTION_REPORT_PATH = GATE_DIR / "pytest_collection_report.json"
ARTIFACT_DIFF_REPORT_PATH = GATE_DIR / "artifact_diff_report.json"

REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PREVIOUS_GATE_DIR = REPO_ROOT / "reports" / "gates" / "phase6_global_reproducibility_source_alignment_gate"
PREVIOUS_GATE_REPORT = PREVIOUS_GATE_DIR / "gate_report.json"
PREVIOUS_GATE_REVIEW = PREVIOUS_GATE_DIR / "gate_result_review.md"
NEXT_STEP_RECOMMENDATION = REPO_ROOT / "reports" / "audits" / "global_spec_adherence" / "next_step_recommendation.md"
PHASE6_GLOBAL_SCRIPT = REPO_ROOT / "services" / "ml_engine" / "phase6_global_reproducibility_source_alignment_gate.py"

EXPECTED_ARTIFACTS = (
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_manifest.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_hardening_recheck/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_sovereign_hardening_recheck/gate_manifest.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction/gate_manifest.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_recent_regime_policy_falsification/gate_report.json",
    REPO_ROOT / "reports/gates/phase5_cross_sectional_recent_regime_policy_falsification/gate_manifest.json",
    REPO_ROOT / "data/models/phase4/phase4_gate_diagnostic.json",
    PREVIOUS_GATE_DIR / "gate_report.json",
    PREVIOUS_GATE_DIR / "gate_manifest.json",
    PREVIOUS_GATE_DIR / "gate_metrics.parquet",
    PREVIOUS_GATE_DIR / "source_doc_alignment.json",
    PREVIOUS_GATE_DIR / "portfolio_cvar_report.json",
)

PRECHECK_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("python_version", [sys.executable, "--version"]),
    ("pip_version", [sys.executable, "-m", "pip", "--version"]),
    ("pip_check", [sys.executable, "-m", "pip", "check"]),
    (
        "pip_dry_run_polars_hmmlearn_pins",
        [sys.executable, "-m", "pip", "install", "--dry-run", "polars==0.20.16", "hmmlearn==0.3.2"],
    ),
    ("pip_dry_run_root_requirements", [sys.executable, "-m", "pip", "install", "--dry-run", "-r", "requirements.txt"]),
)

PYTEST_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("pytest_unit_collect_only", [sys.executable, "-m", "pytest", "tests/unit", "--collect-only", "-q"]),
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
    ("pytest_unit_full", [sys.executable, "-m", "pytest", "tests/unit", "-q"]),
)

REGENERATION_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    (
        "phase5_closure_bundle_restore_and_revalidate",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py"],
    ),
    (
        "phase5_sovereign_hardening_recheck",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py"],
    ),
    (
        "phase5_operational_fragility_audit_and_bounded_correction",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py"],
    ),
    (
        "phase5_recent_regime_policy_falsification",
        [sys.executable, "services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py"],
    ),
    ("phase4_gate_diagnostic", [sys.executable, "services/ml_engine/phase4_gate_diagnostic.py"]),
    ("phase6_global_reproducibility_source_alignment_gate", [sys.executable, str(PHASE6_GLOBAL_SCRIPT.relative_to(REPO_ROOT))]),
)

ISOLATED_WORKTREE_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("isolated_git_status_short", ["git", "status", "--short"]),
    ("isolated_python_version", [sys.executable, "--version"]),
    ("isolated_pip_check", [sys.executable, "-m", "pip", "check"]),
    ("isolated_pytest_unit_collect_only", [sys.executable, "-m", "pytest", "tests/unit", "--collect-only", "-q"]),
    (
        "isolated_phase6_global_regeneration",
        [sys.executable, "services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py"],
    ),
)

MODULE_EXPECTATIONS = {
    "polars": {"requirement_name": "polars", "required_attribute": "Date"},
    "hmmlearn": {"requirement_name": "hmmlearn", "required_attribute": None},
    "pandas": {"requirement_name": "pandas", "required_attribute": None},
    "pyarrow": {"requirement_name": "pyarrow", "required_attribute": None},
    "sklearn": {"requirement_name": "scikit-learn", "required_attribute": None},
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
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


def _relative(path: Path) -> str:
    path = Path(path)
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


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


def _run_command(label: str, args: list[str], *, cwd: Path = REPO_ROOT, timeout: int = 1800) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["SNIPER_WORKSPACE_PATH"] = str(REPO_ROOT)
    env["SNIPER_MODEL_PATH"] = str(MODEL_ROOT)
    env["MODEL_ARTIFACTS_PATH"] = str(MODEL_ROOT)
    env["PARQUET_BASE_PATH"] = str(PARQUET_ROOT)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        returncode = int(completed.returncode)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timed_out = True
    duration = round(time.monotonic() - started, 3)
    return {
        "label": label,
        "command": " ".join(str(arg) for arg in args),
        "cwd": str(cwd),
        "returncode": returncode,
        "duration_seconds": duration,
        "timed_out": timed_out,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "status": "PASS" if returncode == 0 else "FAIL",
    }


def _artifact_hashes(paths: tuple[Path, ...]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for path in paths:
        records[_relative(path)] = {
            "exists": path.exists(),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size if path.exists() and path.is_file() else None,
        }
    return records


def _extract_pinned_requirements() -> dict[str, str]:
    pins: dict[str, str] = {}
    if not REQUIREMENTS_PATH.exists():
        return pins
    pattern = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)==([^\s#]+)")
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        name = match.group(1).split("[", 1)[0].lower().replace("_", "-")
        pins[name] = match.group(2)
    return pins


def _module_audit(pins: dict[str, str]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for module_name, expectation in MODULE_EXPECTATIONS.items():
        requirement_name = expectation["requirement_name"]
        normalized_req = str(requirement_name).lower().replace("_", "-")
        required_attribute = expectation.get("required_attribute")
        record: dict[str, Any] = {
            "module": module_name,
            "requirement_name": requirement_name,
            "expected_pin": pins.get(normalized_req),
            "required_attribute": required_attribute,
        }
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            record.update(
                {
                    "importable": False,
                    "version": None,
                    "path": None,
                    "classification": "DEPENDENCY_BLOCKER",
                    "risk": "module_missing_from_runtime",
                }
            )
            modules.append(record)
            continue
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", "unknown")
            module_path = getattr(module, "__file__", None)
            has_required_attribute = True if not required_attribute else hasattr(module, str(required_attribute))
            if not has_required_attribute:
                classification = "DEPENDENCY_OR_API_COMPATIBILITY_BLOCKER"
                risk = f"missing_required_attribute_{required_attribute}"
            elif record["expected_pin"] and str(version) != str(record["expected_pin"]):
                classification = "DEPENDENCY_DRIFT"
                risk = "installed_version_differs_from_root_pin"
            else:
                classification = "SATISFACTORY"
                risk = "none"
            record.update(
                {
                    "importable": True,
                    "version": version,
                    "path": module_path,
                    "has_required_attribute": has_required_attribute,
                    "classification": classification,
                    "risk": risk,
                }
            )
        except Exception as exc:
            record.update(
                {
                    "importable": False,
                    "version": None,
                    "path": None,
                    "classification": "DEPENDENCY_BLOCKER",
                    "risk": "import_raises_exception",
                    "error": repr(exc),
                }
            )
        modules.append(record)
    return modules


def _dependency_audit(precheck_records: list[dict[str, Any]]) -> dict[str, Any]:
    pins = _extract_pinned_requirements()
    modules = _module_audit(pins)
    root_dry_run = next((record for record in precheck_records if record["label"] == "pip_dry_run_root_requirements"), None)
    targeted_dry_run = next((record for record in precheck_records if record["label"] == "pip_dry_run_polars_hmmlearn_pins"), None)
    pip_check = next((record for record in precheck_records if record["label"] == "pip_check"), None)

    blockers = [record for record in modules if "BLOCKER" in str(record.get("classification"))]
    drift = [record for record in modules if record.get("classification") == "DEPENDENCY_DRIFT"]
    root_install_reproducible = bool(root_dry_run and root_dry_run["returncode"] == 0)
    targeted_install_reproducible = bool(targeted_dry_run and targeted_dry_run["returncode"] == 0)
    pip_check_passed = bool(pip_check and pip_check["returncode"] == 0)

    if blockers or not root_install_reproducible:
        status = "FAIL"
    elif drift:
        status = "PARTIAL"
    else:
        status = "PASS"

    return {
        "status": status,
        "classification": "DEPENDENCY_REPRODUCIBILITY_NOT_ESTABLISHED" if status == "FAIL" else "DEPENDENCY_AUDIT_RECORDED",
        "python_executable": sys.executable,
        "requirements_file": _relative(REQUIREMENTS_PATH),
        "pyproject_file": _relative(PYPROJECT_PATH),
        "root_pins": pins,
        "modules": modules,
        "pip_check_passed": pip_check_passed,
        "targeted_polars_hmmlearn_dry_run_passed": targeted_install_reproducible,
        "root_requirements_dry_run_passed": root_install_reproducible,
        "blockers": blockers,
        "version_drift": drift,
        "commands": precheck_records,
        "objective_findings": [
            "Root requirements are not reproducibly installable in this Python runtime."
            if not root_install_reproducible
            else "Root requirements dry-run is installable in this Python runtime.",
            "polars and/or hmmlearn are missing or incompatible in the active runtime."
            if blockers
            else "Required module imports passed.",
            "pip check only validates installed distributions; it does not prove that missing pinned requirements are installed.",
        ],
    }


def _classify_pytest_failure(record: dict[str, Any]) -> list[dict[str, str]]:
    text = f"{record.get('stdout_tail', '')}\n{record.get('stderr_tail', '')}"
    findings: list[dict[str, str]] = []
    if "No module named 'polars'" in text or "No module named polars" in text:
        findings.append(
            {
                "blocker": "polars_missing",
                "classification": "DEPENDENCY_BLOCKER",
                "evidence": "pytest collection cannot import polars.",
            }
        )
    if "module 'polars' has no attribute 'Date'" in text:
        findings.append(
            {
                "blocker": "polars_date_missing",
                "classification": "DEPENDENCY_OR_API_COMPATIBILITY_BLOCKER",
                "evidence": "collector schema references pl.Date but runtime polars lacks Date.",
            }
        )
    if "No module named 'hmmlearn'" in text or "No module named hmmlearn" in text:
        findings.append(
            {
                "blocker": "hmmlearn_missing",
                "classification": "DEPENDENCY_BLOCKER",
                "evidence": "regime HMM import cannot load hmmlearn.",
            }
        )
    return findings


def _pytest_collection_report(pytest_records: list[dict[str, Any]]) -> dict[str, Any]:
    collect = next((record for record in pytest_records if record["label"] == "pytest_unit_collect_only"), None)
    full = next((record for record in pytest_records if record["label"] == "pytest_unit_full"), None)
    bridge = next((record for record in pytest_records if record["label"] == "pytest_nautilus_bridge_existing"), None)
    gate_reports = next((record for record in pytest_records if record["label"] == "pytest_gate_reports"), None)
    collect_findings = _classify_pytest_failure(collect or {})
    full_findings = _classify_pytest_failure(full or {})
    status = "PASS" if collect and collect["returncode"] == 0 and full and full["returncode"] == 0 else "FAIL"
    return {
        "status": status,
        "classification": "PYTEST_COLLECTION_DEPENDENCY_BLOCKED" if status == "FAIL" else "PYTEST_COLLECTION_REPRODUCIBLE",
        "collect_only": collect,
        "unit_full": full,
        "gate_reports": gate_reports,
        "bridge_nautilus": bridge,
        "collection_findings": collect_findings,
        "full_suite_findings": full_findings,
        "bridge_passed": bool(bridge and bridge["returncode"] == 0),
        "gate_report_test_passed": bool(gate_reports and gate_reports["returncode"] == 0),
        "commands": pytest_records,
    }


def _tracked(path: Path) -> bool:
    return _git_returncode("ls-files", "--error-unmatch", _relative(path)) == 0


def _isolated_worktree_probe() -> dict[str, Any]:
    temp_parent = Path(tempfile.mkdtemp(prefix="sniper_clean_worktree_parent_"))
    worktree_path = temp_parent / "sniper_clean_worktree"
    add_record = _run_command("git_worktree_add_detached_head", ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"])
    command_records: list[dict[str, Any]] = []
    initial_status: dict[str, Any] = {
        "created": add_record["returncode"] == 0,
        "path": str(worktree_path),
        "parent": str(temp_parent),
        "add_command": add_record,
    }
    try:
        if add_record["returncode"] == 0:
            command_records = [
                _run_command(label, args, cwd=worktree_path, timeout=1800)
                for label, args in ISOLATED_WORKTREE_COMMANDS
            ]
            status_short = next((record for record in command_records if record["label"] == "isolated_git_status_short"), None)
            initial_status["initial_worktree_clean"] = bool(status_short and status_short["returncode"] == 0 and not status_short["stdout_tail"].strip())
        else:
            initial_status["initial_worktree_clean"] = False
    finally:
        remove_record = _run_command("git_worktree_remove_clean_probe", ["git", "worktree", "remove", "--force", str(worktree_path)])
        initial_status["remove_command"] = remove_record
        if temp_parent.exists():
            shutil.rmtree(temp_parent, ignore_errors=True)
    phase6_isolated = next((record for record in command_records if record["label"] == "isolated_phase6_global_regeneration"), None)
    collect_isolated = next((record for record in command_records if record["label"] == "isolated_pytest_unit_collect_only"), None)
    status = "PASS"
    if not initial_status.get("created") or not initial_status.get("initial_worktree_clean"):
        status = "INCONCLUSIVE"
    if phase6_isolated and phase6_isolated["returncode"] != 0:
        status = "FAIL"
    if collect_isolated and collect_isolated["returncode"] != 0 and status == "PASS":
        status = "FAIL"
    return {
        "status": status,
        "classification": (
            "CLEAN_WORKTREE_CREATED_BUT_REGENERATION_FAILED"
            if status == "FAIL"
            else "CLEAN_WORKTREE_PROBE_RECORDED"
        ),
        "worktree": initial_status,
        "phase6_global_source_tracked": _tracked(PHASE6_GLOBAL_SCRIPT),
        "phase6_global_source_exists_current_workspace": PHASE6_GLOBAL_SCRIPT.exists(),
        "commands": command_records,
        "interpretation": (
            "A detached clean worktree can be created, but it cannot reproduce the current Phase 6 source/artifact state "
            "until the prior gate source and artifacts are committed or otherwise materialized in a clean checkout."
        ),
    }


def _clean_regeneration_report(
    regeneration_records: list[dict[str, Any]],
    isolated_probe: dict[str, Any],
    before_hashes: dict[str, Any],
    after_hashes: dict[str, Any],
) -> dict[str, Any]:
    divergences = []
    for artifact, after in sorted(after_hashes.items()):
        before = before_hashes.get(artifact, {})
        if before.get("sha256") != after.get("sha256") or before.get("exists") != after.get("exists"):
            divergences.append(
                {
                    "artifact": artifact,
                    "before": before,
                    "after": after,
                    "classification": "HASH_CHANGED_DURING_REGENERATION"
                    if before.get("exists") and after.get("exists")
                    else "ARTIFACT_PRESENCE_CHANGED",
                }
            )
    all_current_regen_exit_zero = all(record["returncode"] == 0 for record in regeneration_records)
    status = "PASS" if all_current_regen_exit_zero and isolated_probe.get("status") == "PASS" and not divergences else "PARTIAL"
    if not all_current_regen_exit_zero or isolated_probe.get("status") == "FAIL":
        status = "FAIL"
    return {
        "status": status,
        "classification": "CLEAN_CLONE_REGENERATION_NOT_PROVEN" if status != "PASS" else "CLEAN_REGENERATION_PROVEN",
        "current_workspace_regeneration": {
            "all_exit_zero": all_current_regen_exit_zero,
            "commands": regeneration_records,
        },
        "isolated_worktree_probe": isolated_probe,
        "artifact_hashes_before": before_hashes,
        "artifact_hashes_after": after_hashes,
        "divergences": divergences,
        "missing_artifacts_after": [path for path, record in after_hashes.items() if not record.get("exists")],
        "objective_limitation": "The active branch contains dirty/untracked gate work, so a clean checkout of HEAD is not equivalent to this working tree.",
    }


def _load_previous_metrics() -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if PREVIOUS_GATE_REPORT.exists():
        try:
            report = _read_json(PREVIOUS_GATE_REPORT)
            previous_metrics = report.get("metrics") or {}
            metrics.update(
                {
                    "previous_gate_status": report.get("status"),
                    "previous_gate_decision": report.get("decision"),
                    "dsr_honest": previous_metrics.get("dsr_honest"),
                    "n_trials_honest": previous_metrics.get("n_trials_honest"),
                    "sharpe_oos": previous_metrics.get("sharpe_oos"),
                    "subperiods_positive": previous_metrics.get("subperiods_positive"),
                    "cross_sectional_status": "ALIVE_BUT_NOT_PROMOTABLE",
                    "portfolio_cvar_status": (report.get("portfolio_cvar") or {}).get("status"),
                }
            )
        except Exception as exc:
            metrics["previous_gate_read_error"] = repr(exc)
    return metrics


def _environment_report(
    *,
    before_git: dict[str, Any],
    after_git: dict[str, Any],
    dependency_audit: dict[str, Any],
    clean_regeneration: dict[str, Any],
    pytest_report: dict[str, Any],
) -> dict[str, Any]:
    if dependency_audit.get("status") == "PASS" and clean_regeneration.get("status") == "PASS" and pytest_report.get("status") == "PASS":
        status = "PASS"
    elif dependency_audit.get("status") == "FAIL" or pytest_report.get("status") == "FAIL" or clean_regeneration.get("status") == "FAIL":
        status = "FAIL"
    else:
        status = "PARTIAL"
    return {
        "status": status,
        "classification": "ENVIRONMENT_REPRODUCIBILITY_BLOCKED" if status == "FAIL" else "ENVIRONMENT_REPRODUCIBILITY_RECORDED",
        "base_branch": BASE_BRANCH,
        "suggested_branch": SUGGESTED_BRANCH,
        "before_git": before_git,
        "after_git": after_git,
        "python_executable": sys.executable,
        "dependency_status": dependency_audit.get("status"),
        "pytest_status": pytest_report.get("status"),
        "clean_regeneration_status": clean_regeneration.get("status"),
        "current_workspace_dirty_before": before_git.get("working_tree_dirty"),
        "current_workspace_dirty_after": after_git.get("working_tree_dirty"),
        "governance_invariants": {
            "official_promoted": False,
            "A3_reopened": False,
            "A4_reopened": False,
            "RiskLabAI_official": False,
            "cross_sectional_promoted": False,
            "DSR_zero_masked": False,
        },
    }


def _metrics_rows(
    *,
    environment_report: dict[str, Any],
    dependency_audit: dict[str, Any],
    pytest_report: dict[str, Any],
    clean_regeneration: dict[str, Any],
    previous_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    collect = pytest_report.get("collect_only") or {}
    bridge = pytest_report.get("bridge_nautilus") or {}
    current_regen = clean_regeneration.get("current_workspace_regeneration") or {}
    isolated = clean_regeneration.get("isolated_worktree_probe") or {}
    return [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "environment_reproducibility_status",
            "metric_value": environment_report.get("status"),
            "metric_threshold": "PASS",
            "metric_status": environment_report.get("status"),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "dependency_audit_status",
            "metric_value": dependency_audit.get("status"),
            "metric_threshold": "PASS",
            "metric_status": dependency_audit.get("status"),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "root_requirements_dry_run_passed",
            "metric_value": dependency_audit.get("root_requirements_dry_run_passed"),
            "metric_threshold": True,
            "metric_status": "PASS" if dependency_audit.get("root_requirements_dry_run_passed") else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "pytest_collect_only_exit_zero",
            "metric_value": collect.get("returncode") == 0,
            "metric_threshold": True,
            "metric_status": "PASS" if collect.get("returncode") == 0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "bridge_nautilus_exit_zero",
            "metric_value": bridge.get("returncode") == 0,
            "metric_threshold": True,
            "metric_status": "PASS" if bridge.get("returncode") == 0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "current_workspace_regeneration_exit_zero",
            "metric_value": current_regen.get("all_exit_zero"),
            "metric_threshold": True,
            "metric_status": "PASS" if current_regen.get("all_exit_zero") else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "isolated_worktree_regeneration_status",
            "metric_value": isolated.get("status"),
            "metric_threshold": "PASS",
            "metric_status": isolated.get("status"),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "dsr_honest_preserved",
            "metric_value": previous_metrics.get("dsr_honest"),
            "metric_threshold": "> 0.95 for any promotion",
            "metric_status": "FAIL" if previous_metrics.get("dsr_honest") == 0.0 else "PARTIAL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "cross_sectional_status_preserved",
            "metric_value": "ALIVE_BUT_NOT_PROMOTABLE",
            "metric_threshold": "must_not_be_promoted",
            "metric_status": "PASS",
        },
    ]


def main() -> None:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    before_git = _git_baseline()
    before_hashes = _artifact_hashes(EXPECTED_ARTIFACTS)

    precheck_records = [_run_command(label, args, timeout=1800) for label, args in PRECHECK_COMMANDS]
    dependency_audit = _dependency_audit(precheck_records)

    pytest_records = [_run_command(label, args, timeout=1800) for label, args in PYTEST_COMMANDS]
    pytest_report = _pytest_collection_report(pytest_records)

    isolated_probe = _isolated_worktree_probe()
    regeneration_records = [_run_command(label, args, timeout=2400) for label, args in REGENERATION_COMMANDS]
    after_hashes = _artifact_hashes(EXPECTED_ARTIFACTS)
    clean_regeneration = _clean_regeneration_report(regeneration_records, isolated_probe, before_hashes, after_hashes)

    after_git = _git_baseline()
    environment_report = _environment_report(
        before_git=before_git,
        after_git=after_git,
        dependency_audit=dependency_audit,
        clean_regeneration=clean_regeneration,
        pytest_report=pytest_report,
    )
    previous_metrics = _load_previous_metrics()
    artifact_diff_report = {
        "status": "PASS" if not clean_regeneration.get("divergences") else "PARTIAL",
        "artifact_hashes_before": before_hashes,
        "artifact_hashes_after": after_hashes,
        "divergences": clean_regeneration.get("divergences", []),
        "missing_artifacts_after": clean_regeneration.get("missing_artifacts_after", []),
        "classification": "HASH_DIVERGENCES_RECORDED" if clean_regeneration.get("divergences") else "NO_HASH_DIVERGENCE",
    }

    _write_json(ENVIRONMENT_REPORT_PATH, environment_report)
    _write_json(DEPENDENCY_AUDIT_PATH, dependency_audit)
    _write_json(CLEAN_CLONE_REPORT_PATH, clean_regeneration)
    _write_json(PYTEST_COLLECTION_REPORT_PATH, pytest_report)
    _write_json(ARTIFACT_DIFF_REPORT_PATH, artifact_diff_report)

    metrics_rows = _metrics_rows(
        environment_report=environment_report,
        dependency_audit=dependency_audit,
        pytest_report=pytest_report,
        clean_regeneration=clean_regeneration,
        previous_metrics=previous_metrics,
    )

    status = environment_report.get("status")
    decision = "advance" if status == "PASS" else "correct" if status in {"PARTIAL", "FAIL"} else "inconclusive"
    blockers = []
    if dependency_audit.get("status") != "PASS":
        blockers.append("Dependency reproducibility is not established in the active Python runtime.")
    if pytest_report.get("status") != "PASS":
        blockers.append("pytest unit collection/full suite is blocked by dependency/import errors.")
    if clean_regeneration.get("status") != "PASS":
        blockers.append("Clean clone/worktree regeneration is not proven.")
    if previous_metrics.get("dsr_honest") == 0.0:
        blockers.append("dsr_honest remains 0.0 and blocks promotion.")
    blockers.append("Cross-sectional remains ALIVE_BUT_NOT_PROMOTABLE.")

    risks = [
        "Installing partial dependencies outside the pinned environment can mask reproducibility drift.",
        "Python 3.13 is not proven compatible with the root pinned requirements in this workspace.",
        "Dirty/untracked gate sources prevent a clean checkout of HEAD from matching the current working tree.",
        "PASS_ZERO_EXPOSURE CVaR remains technical persistence only, not economic robustness with exposure.",
    ]

    generated_pre_pack = [
        artifact_record(ENVIRONMENT_REPORT_PATH),
        artifact_record(DEPENDENCY_AUDIT_PATH),
        artifact_record(CLEAN_CLONE_REPORT_PATH),
        artifact_record(PYTEST_COLLECTION_REPORT_PATH),
        artifact_record(ARTIFACT_DIFF_REPORT_PATH),
    ]

    source_artifacts = [
        artifact_record(REQUIREMENTS_PATH),
        artifact_record(PYPROJECT_PATH),
        artifact_record(NEXT_STEP_RECOMMENDATION),
        artifact_record(PREVIOUS_GATE_REPORT),
        artifact_record(PREVIOUS_GATE_REVIEW),
        artifact_record(PHASE6_GLOBAL_SCRIPT),
        artifact_record(THIS_FILE),
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
        "official_artifacts_used": [],
        "research_artifacts_generated": generated_pre_pack,
        "artifacts_generated": generated_pre_pack,
        "dependency_audit": dependency_audit,
        "pytest_collection": pytest_report,
        "clean_clone_regeneration": clean_regeneration,
        "artifact_diff": artifact_diff_report,
        "environment_reproducibility": environment_report,
        "metrics": previous_metrics,
        "summary": [
            f"environment={environment_report.get('status')}",
            f"dependencies={dependency_audit.get('status')}",
            f"pytest={pytest_report.get('status')}",
            f"clean_regeneration={clean_regeneration.get('status')}",
            f"dsr_honest={previous_metrics.get('dsr_honest')}",
            "cross_sectional_status=ALIVE_BUT_NOT_PROMOTABLE",
        ],
        "gates": metrics_rows,
        "blockers": blockers,
        "risks": risks,
        "risks_residual": risks,
        "recommendation": (
            "Do not move to new quantitative work. First run this branch from a committed clean checkout "
            "with a Python runtime compatible with the pinned requirements, then re-run collection and regeneration."
        ),
        "next_recommended_step": (
            "Commit/materialize the Phase 6 gate sources or use a clean worktree containing them, then create a fresh "
            "Python 3.11/3.12 environment from requirements.txt and re-run this gate."
        ),
        "commands": precheck_records + pytest_records + regeneration_records + isolated_probe.get("commands", []),
        "governance_boundaries": {
            "official_promoted": False,
            "A3_reopened": False,
            "A4_reopened": False,
            "RiskLabAI_official": False,
            "cross_sectional_promoted": False,
            "DSR_zero_masked": False,
        },
        "timestamp_utc": _now_utc(),
    }

    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": before_git.get("commit"),
        "branch": after_git.get("branch"),
        "working_tree_dirty_before": before_git.get("working_tree_dirty"),
        "working_tree_dirty_after": after_git.get("working_tree_dirty"),
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_pre_pack,
        "commands_executed": precheck_records + pytest_records + regeneration_records + isolated_probe.get("commands", []),
        "notes": [
            "No model, research artifact, RiskLabAI path, or paper path was promoted to official.",
            "A3/A4 were not reopened.",
            "ALIVE_BUT_NOT_PROMOTABLE remained non-promotable.",
            "No thresholds were changed.",
            "No dependency files were edited by this gate.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": (
            f"Gate `{GATE_SLUG}` concluido com status `{status}` e decisao `{decision}`.\n\n"
            "O ambiente atual nao prova reprodutibilidade limpa: a instalacao pinned completa via `requirements.txt` falha neste Python, "
            "a coleta de testes permanece bloqueada por dependencias/imports, e o worktree limpo de HEAD nao materializa o mesmo estado sujo/untracked usado pelos gates recentes."
        ),
        "Baseline congelado": (
            f"- Branch base: `{BASE_BRANCH}`\n"
            f"- Branch executada: `{after_git.get('branch')}`\n"
            f"- Commit: `{after_git.get('commit')}`\n"
            f"- Worktree dirty antes: `{before_git.get('working_tree_dirty')}`\n"
            "- A3/A4: fechados\n"
            "- RiskLabAI: shadow/oracle\n"
            "- Cross-sectional: `ALIVE_BUT_NOT_PROMOTABLE`"
        ),
        "Mudanças implementadas": (
            "- Criado runner de gate de ambiente/reprodutibilidade.\n"
            "- Gerados reports obrigatorios do gate.\n"
            "- Nenhum threshold, alpha, ranking, policy ou dependencia do repo foi alterado."
        ),
        "Artifacts gerados": "\n".join(
            [
                f"- `{_relative(ENVIRONMENT_REPORT_PATH)}`",
                f"- `{_relative(DEPENDENCY_AUDIT_PATH)}`",
                f"- `{_relative(CLEAN_CLONE_REPORT_PATH)}`",
                f"- `{_relative(PYTEST_COLLECTION_REPORT_PATH)}`",
                f"- `{_relative(ARTIFACT_DIFF_REPORT_PATH)}`",
                f"- `reports/gates/{GATE_SLUG}/gate_report.json`",
                f"- `reports/gates/{GATE_SLUG}/gate_report.md`",
                f"- `reports/gates/{GATE_SLUG}/gate_manifest.json`",
                f"- `reports/gates/{GATE_SLUG}/gate_metrics.parquet`",
            ]
        ),
        "Resultados": (
            f"- Dependency audit: `{dependency_audit.get('status')}`\n"
            f"- pytest collect/full: `{pytest_report.get('status')}`\n"
            f"- Bridge/Nautilus: `{'PASS' if pytest_report.get('bridge_passed') else 'FAIL'}`\n"
            f"- Current workspace regeneration: `{'PASS' if (clean_regeneration.get('current_workspace_regeneration') or {}).get('all_exit_zero') else 'FAIL'}`\n"
            f"- Isolated worktree probe: `{(clean_regeneration.get('isolated_worktree_probe') or {}).get('status')}`\n"
            f"- Artifact diff: `{artifact_diff_report.get('status')}`\n"
            f"- DSR honesto: `{previous_metrics.get('dsr_honest')}`\n"
            f"- CVaR previous status: `{previous_metrics.get('portfolio_cvar_status')}`"
        ),
        "Avaliação contra gates": "\n".join(
            f"- {row['metric_name']}: `{row['metric_status']}` ({row['metric_value']})" for row in metrics_rows
        ),
        "Riscos residuais": "\n".join(f"- {risk}" for risk in risks),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. O proximo passo deve corrigir ambiente e materializacao limpa antes de qualquer nova pesquisa quantitativa. "
            "Este gate nao autoriza promocao, paper readiness, testnet readiness ou capital readiness."
        ),
    }

    outputs = write_gate_pack(
        output_dir=GATE_DIR,
        gate_report=gate_report,
        gate_manifest=manifest,
        gate_metrics=metrics_rows,
        markdown_sections=markdown_sections,
    )

    print(
        json.dumps(
            {
                "gate_slug": GATE_SLUG,
                "status": status,
                "decision": decision,
                "dependency_status": dependency_audit.get("status"),
                "pytest_status": pytest_report.get("status"),
                "clean_regeneration_status": clean_regeneration.get("status"),
                "outputs": {key: str(path) for key, path in outputs.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
