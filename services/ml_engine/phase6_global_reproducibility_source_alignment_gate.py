#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_FILE.parent) not in sys.path:
    sys.path.insert(0, str(THIS_FILE.parent))

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack
from services.ml_engine.sizing.kelly_cvar import CVAR_LIMIT, portfolio_stress_report

DEFAULT_GATE_SLUG = "phase6_global_reproducibility_source_alignment_gate"
GATE_SLUG = os.getenv("SNIPER_PHASE6_GATE_SLUG", DEFAULT_GATE_SLUG)
PHASE_FAMILY = "phase6_global_reproducibility_source_alignment"
DOC_PHASE4_MEMORY = REPO_ROOT / "docs" / "SNIPER_memoria_especificacao_controle_fase4R_v3.md"
DOC_HANDOFF = REPO_ROOT / "docs" / "SNIPER_openclaw_handoff.md"
DOC_REGENERATION = REPO_ROOT / "docs" / "SNIPER_regeneration_guide.md"
AUDIT_SUMMARY = REPO_ROOT / "reports" / "audits" / "global_spec_adherence" / "global_spec_adherence_summary.json"
AUDIT_NEXT_STEP = REPO_ROOT / "reports" / "audits" / "global_spec_adherence" / "next_step_recommendation.md"
MODEL_PATH = Path(os.getenv("SNIPER_MODEL_PATH", str(REPO_ROOT / "data" / "models")))
PHASE4_DIR = MODEL_PATH / "phase4"
PHASE4_SNAPSHOT = PHASE4_DIR / "phase4_execution_snapshot.parquet"
PHASE4_REPORT = PHASE4_DIR / "phase4_report_v4.json"
PHASE4_AGGREGATED_PREDICTIONS = PHASE4_DIR / "phase4_aggregated_predictions.parquet"
PHASE4_OOS_PREDICTIONS = PHASE4_DIR / "phase4_oos_predictions.parquet"
PHASE4_GATE_DIAGNOSTIC = PHASE4_DIR / "phase4_gate_diagnostic.json"
REGENERATION_BASELINE_DIR = MODEL_PATH / "research" / "phase4_cross_sectional_ranking_baseline"
REGENERATION_BASELINE_STAGE_A_PREDICTIONS = REGENERATION_BASELINE_DIR / "stage_a_predictions.parquet"
REGENERATION_BASELINE_STAGE_A_REPORT = REGENERATION_BASELINE_DIR / "stage_a_report.json"
REGENERATION_BASELINE_STAGE_A_MANIFEST = REGENERATION_BASELINE_DIR / "stage_a_manifest.json"
REGENERATION_BASELINE_STAGE_A_SNAPSHOT = REGENERATION_BASELINE_DIR / "stage_a_snapshot_proxy.parquet"
GATE_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
PHASE4_R4_CORRECTION_MARKER = "PHASE6_SOURCE_DOC_ALIGNMENT_CURRENT_SOURCE"

REQUIRED_PHASE4_ARTIFACTS = (
    PHASE4_REPORT,
    PHASE4_SNAPSHOT,
    PHASE4_AGGREGATED_PREDICTIONS,
    PHASE4_OOS_PREDICTIONS,
    PHASE4_GATE_DIAGNOSTIC,
)
REQUIRED_REGENERATION_BASELINE_ARTIFACTS = (
    REGENERATION_BASELINE_STAGE_A_PREDICTIONS,
    REGENERATION_BASELINE_STAGE_A_REPORT,
    REGENERATION_BASELINE_STAGE_A_MANIFEST,
    REGENERATION_BASELINE_STAGE_A_SNAPSHOT,
)

DOCUMENTED_PHASE4_MODULES = (
    "phase4_config.py",
    "phase4_data.py",
    "phase4_dsr.py",
    "phase4_backtest.py",
    "phase4_calibration.py",
    "phase4_cpcv.py",
    "phase4_stage_a_experiment.py",
)
TRACKED_PHASE4_SOURCE = (
    "phase4_cpcv.py",
    "phase4_gate_diagnostic.py",
    "phase4_stage_a_experiment.py",
)
REGENERATION_COMMAND = (
    "services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py"
)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _git_output(*args: str, cwd: Path = REPO_ROOT) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception as exc:
        return f"UNAVAILABLE: {exc}"


def _git_tracked(path: Path, *, repo_root: Path = REPO_ROOT) -> bool:
    relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=repo_root,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _contains(path: Path, token: str) -> bool:
    if not path.exists():
        return False
    return token in path.read_text(encoding="utf-8", errors="ignore")


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _phase4_module_documentation_state(doc_path: Path, module_name: str) -> str:
    if not doc_path.exists():
        return "missing_doc"
    text = doc_path.read_text(encoding="utf-8", errors="ignore")
    if module_name not in text:
        return "not_documented"
    marker = f"{PHASE4_R4_CORRECTION_MARKER}:{module_name}:historical_report_only"
    if marker in text:
        return "historical_report_only"
    return "current_source_required"


def build_source_doc_alignment(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    doc_path = repo_root / "docs" / "SNIPER_memoria_especificacao_controle_fase4R_v3.md"
    ml_engine = repo_root / "services" / "ml_engine"
    documented = []
    for module_name in DOCUMENTED_PHASE4_MODULES:
        source_path = ml_engine / module_name
        documentation_state = _phase4_module_documentation_state(doc_path, module_name)
        documented.append(
            {
                "module": module_name,
                "documented_in_phase4r_memory": documentation_state != "not_documented",
                "documentation_state": documentation_state,
                "source_path": source_path,
                "source_exists": source_path.exists(),
                "source_tracked": _git_tracked(source_path, repo_root=repo_root) if source_path.exists() else False,
            }
        )
    tracked = []
    for module_name in TRACKED_PHASE4_SOURCE:
        source_path = ml_engine / module_name
        tracked.append(
            {
                "module": module_name,
                "source_path": source_path,
                "source_exists": source_path.exists(),
                "source_tracked": _git_tracked(source_path, repo_root=repo_root) if source_path.exists() else False,
                "documented_in_phase4r_memory": _contains(doc_path, module_name),
            }
        )
    missing_documented_modules = [
        row["module"]
        for row in documented
        if row["documentation_state"] == "current_source_required" and not row["source_tracked"]
    ]
    return {
        "status": "DIVERGENT" if missing_documented_modules else "ALIGNED",
        "doc_path": doc_path,
        "documented_modules": documented,
        "tracked_phase4_source": tracked,
        "missing_documented_modules": missing_documented_modules,
        "decision": (
            "Keep Phase 4-R4 source-doc mismatch as blocker until modules are restored "
            "or documentation is corrected by an explicit gate."
            if missing_documented_modules
            else "Phase 4-R4 source-doc state is aligned with current tracked source and documented historical-only modules."
        ),
    }


def build_portfolio_cvar_report(*, snapshot_path: Path = PHASE4_SNAPSHOT) -> dict[str, Any]:
    positions: dict[str, float] = {}
    sigmas: dict[str, float] = {}
    capital = 100_000.0
    snapshot_status = "MISSING_OFFICIAL_SNAPSHOT"
    if snapshot_path.exists():
        frame = pd.read_parquet(snapshot_path)
        if {"symbol", "position_usdt"}.issubset(frame.columns):
            latest = frame.copy()
            if "date" in latest.columns:
                latest["date"] = pd.to_datetime(latest["date"], errors="coerce")
                latest_date = latest["date"].dropna().max()
                latest = latest.loc[latest["date"] == latest_date]
            for row in latest.to_dict("records"):
                symbol = str(row.get("symbol"))
                position = float(row.get("position_usdt") or 0.0)
                if position > 0.0:
                    positions[symbol] = position
                    sigmas[symbol] = float(row.get("sigma_entry") or row.get("sigma_ewma") or 0.02)
            snapshot_status = "LOADED_OFFICIAL_SNAPSHOT"
        else:
            snapshot_status = "SNAPSHOT_WITHOUT_POSITION_COLUMNS"
    stress = portfolio_stress_report(positions=positions, sigmas=sigmas, capital=capital)
    zero_exposure = len(positions) == 0
    return {
        "snapshot_path": snapshot_path,
        "snapshot_status": snapshot_status,
        "capital_assumption_usdt": capital,
        "positions": positions,
        "sigmas": sigmas,
        "stress_report": stress,
        "zero_exposure": zero_exposure,
        "technical_persistence_status": "PASS_ZERO_EXPOSURE" if zero_exposure else "MEASURED",
        "economic_robustness_status": "NOT_PROVEN_ZERO_EXPOSURE" if zero_exposure else "MEASURED_ONLY",
        "cvar_limit": CVAR_LIMIT,
    }


def build_phase4_artifact_integrity_report(*, phase4_dir: Path = PHASE4_DIR) -> dict[str, Any]:
    required = (
        phase4_dir / "phase4_report_v4.json",
        phase4_dir / "phase4_execution_snapshot.parquet",
        phase4_dir / "phase4_aggregated_predictions.parquet",
        phase4_dir / "phase4_oos_predictions.parquet",
        phase4_dir / "phase4_gate_diagnostic.json",
    )
    artifacts = [artifact_record(path) for path in required]
    missing = [str(row["path"]) for row in artifacts if not row["exists"]]
    report_payload: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    dsr_payload: dict[str, Any] = {}
    fallback_payload: dict[str, Any] = {}
    report_path = phase4_dir / "phase4_report_v4.json"
    if report_path.exists():
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        checks = dict(report_payload.get("checks") or {})
        dsr_payload = dict(report_payload.get("dsr") or {})
        fallback_payload = dict(report_payload.get("fallback") or {})

    dsr_honest = _coerce_float(dsr_payload.get("dsr_honest"))
    dsr_passed = bool(dsr_payload.get("passed")) if dsr_payload else False
    dsr_check_passed = checks.get("DSR honesto > 0.95 [10]")
    if missing:
        promotion_status = "UNKNOWN_MISSING_PHASE4_ARTIFACTS"
    elif dsr_honest == 0.0 or dsr_passed is False or dsr_check_passed is False:
        promotion_status = "BLOCKED_DSR_HONEST_ZERO"
    else:
        promotion_status = "NOT_BLOCKED_BY_DSR_REPORT"

    return {
        "phase4_dir": phase4_dir,
        "required_artifacts": artifacts,
        "missing_required_artifacts": missing,
        "artifact_integrity_status": "PASS" if not missing else "MISSING_REQUIRED_PHASE4_ARTIFACTS",
        "dsr": dsr_payload,
        "dsr_honest": dsr_honest,
        "dsr_passed": dsr_passed,
        "dsr_check_passed": dsr_check_passed,
        "promotion_status": promotion_status,
        "fallback_summary": {
            key: fallback_payload.get(key)
            for key in ("policy", "threshold", "sharpe", "cum_return", "max_dd", "n_active", "win_rate", "avg_alloc")
        },
        "checks": checks,
    }


def build_environment_report() -> dict[str, Any]:
    packages = {}
    for package_name in ("pytest", "pytest_asyncio", "hmmlearn", "polars", "pandas", "pyarrow", "matplotlib"):
        try:
            module = __import__(package_name)
            packages[package_name] = {
                "available": True,
                "version": getattr(module, "__version__", "unknown"),
            }
        except Exception as exc:
            packages[package_name] = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "packages": packages,
        "all_required_probe_packages_available": all(row["available"] for row in packages.values()),
    }


def _phase4_preflight(model_path: Path = MODEL_PATH) -> dict[str, Any]:
    phase4_dir = model_path / "phase4"
    research_baseline_dir = model_path / "research" / "phase4_cross_sectional_ranking_baseline"
    required_phase4 = (
        phase4_dir / "phase4_report_v4.json",
        phase4_dir / "phase4_execution_snapshot.parquet",
        phase4_dir / "phase4_aggregated_predictions.parquet",
        phase4_dir / "phase4_oos_predictions.parquet",
        phase4_dir / "phase4_gate_diagnostic.json",
    )
    required_regeneration_baseline = (
        research_baseline_dir / "stage_a_predictions.parquet",
        research_baseline_dir / "stage_a_report.json",
        research_baseline_dir / "stage_a_manifest.json",
        research_baseline_dir / "stage_a_snapshot_proxy.parquet",
    )
    missing_phase4 = [
        str(path)
        for path in (phase4_dir, *required_phase4)
        if not path.exists()
    ]
    missing_regeneration_baseline = [str(path) for path in required_regeneration_baseline if not path.exists()]
    if missing_phase4:
        classification = "MISSING_OFFICIAL_PHASE4_ARTIFACTS"
    elif missing_regeneration_baseline:
        classification = "MISSING_RESEARCH_BASELINE_ARTIFACTS"
    else:
        classification = "PASS"
    return {
        "model_path": model_path,
        "model_path_exists": model_path.exists(),
        "phase4_dir": phase4_dir,
        "phase4_dir_exists": phase4_dir.exists(),
        "required_phase4_artifacts": required_phase4,
        "missing_required_artifacts": missing_phase4,
        "research_baseline_dir": research_baseline_dir,
        "research_baseline_dir_exists": research_baseline_dir.exists(),
        "required_regeneration_baseline_artifacts": required_regeneration_baseline,
        "missing_regeneration_baseline_artifacts": missing_regeneration_baseline,
        "classification": classification,
    }


def run_regeneration_probe(*, repo_root: Path = REPO_ROOT, model_path: Path = MODEL_PATH) -> dict[str, Any]:
    command = [sys.executable, REGENERATION_COMMAND]
    preflight = _phase4_preflight(model_path)
    if preflight["classification"] != "PASS":
        return {
            "mode": "preflight_only_not_clean_clone",
            "clean_clone_or_equivalent": False,
            "command": " ".join(command),
            "command_executed": False,
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "preflight": preflight,
            "classification": "INCONCLUSIVE",
            "blocker": preflight["classification"],
        }
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "mode": "local_regeneration_probe_not_clean_clone",
        "clean_clone_or_equivalent": False,
        "command": " ".join(command),
        "command_executed": True,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "preflight": preflight,
        "classification": "PASS" if completed.returncode == 0 else "INCONCLUSIVE",
        "blocker": None if completed.returncode == 0 else "local regeneration probe failed",
    }


def classify_gate(
    *,
    source_alignment: dict[str, Any],
    cvar_report: dict[str, Any],
    environment_report: dict[str, Any],
    regeneration_report: dict[str, Any],
    phase4_integrity_report: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    blockers: list[str] = []
    if source_alignment.get("status") != "ALIGNED":
        blockers.append("phase4_r4_source_doc_mismatch")
    if phase4_integrity_report and phase4_integrity_report.get("artifact_integrity_status") != "PASS":
        blockers.append("official_phase4_artifacts_missing")
    if phase4_integrity_report and phase4_integrity_report.get("promotion_status") == "BLOCKED_DSR_HONEST_ZERO":
        blockers.append("dsr_honest_zero_blocks_promotion")
    if cvar_report.get("economic_robustness_status") == "NOT_PROVEN_ZERO_EXPOSURE":
        blockers.append("cvar_zero_exposure_not_economic_robustness")
    if not environment_report.get("all_required_probe_packages_available"):
        blockers.append("test_environment_missing_probe_packages")
    if not regeneration_report.get("clean_clone_or_equivalent"):
        blockers.append("clean_regeneration_not_proven_in_clean_clone_or_equivalent")
    if regeneration_report.get("blocker") == "MISSING_OFFICIAL_PHASE4_ARTIFACTS":
        if "official_phase4_artifacts_missing" not in blockers:
            blockers.append("official_phase4_artifacts_missing")
    elif regeneration_report.get("blocker") == "MISSING_RESEARCH_BASELINE_ARTIFACTS":
        blockers.append("research_regeneration_baseline_artifacts_missing")
    elif regeneration_report.get("returncode") != 0:
        blockers.append("local_regeneration_probe_failed")
    if blockers:
        return "PARTIAL", "correct", blockers
    return "PASS", "advance", []


def run_phase6_global_reproducibility_source_alignment_gate() -> dict[str, Any]:
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    working_tree_before = _git_output("status", "--short", "--untracked-files=all")

    source_alignment = build_source_doc_alignment()
    phase4_integrity_report = build_phase4_artifact_integrity_report()
    cvar_report = build_portfolio_cvar_report()
    environment_report = build_environment_report()
    regeneration_report = run_regeneration_probe()

    _write_json(GATE_DIR / "source_doc_alignment.json", source_alignment)
    _write_json(GATE_DIR / "phase4_artifact_integrity_report.json", phase4_integrity_report)
    _write_json(GATE_DIR / "portfolio_cvar_report.json", cvar_report)
    _write_json(GATE_DIR / "environment_report.json", environment_report)
    _write_json(GATE_DIR / "clean_regeneration_report.json", regeneration_report)
    _write_json(GATE_DIR / "clean_regeneration_preflight.json", regeneration_report)

    status, decision, blockers = classify_gate(
        source_alignment=source_alignment,
        cvar_report=cvar_report,
        environment_report=environment_report,
        regeneration_report=regeneration_report,
        phase4_integrity_report=phase4_integrity_report,
    )
    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "source_doc_alignment",
            "metric_value": source_alignment["status"],
            "metric_threshold": "ALIGNED",
            "metric_status": "PASS" if source_alignment["status"] == "ALIGNED" else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "phase4_artifact_integrity",
            "metric_value": phase4_integrity_report["artifact_integrity_status"],
            "metric_threshold": "PASS",
            "metric_status": "PASS" if phase4_integrity_report["artifact_integrity_status"] == "PASS" else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "phase4_dsr_honest",
            "metric_value": phase4_integrity_report["dsr_honest"],
            "metric_threshold": "> 0.95 and passed=true for promotion",
            "metric_status": "FAIL"
            if phase4_integrity_report["promotion_status"] == "BLOCKED_DSR_HONEST_ZERO"
            else "INCONCLUSIVE"
            if phase4_integrity_report["promotion_status"] == "UNKNOWN_MISSING_PHASE4_ARTIFACTS"
            else "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "cvar_artifact_persisted",
            "metric_value": cvar_report["technical_persistence_status"],
            "metric_threshold": "persisted with explicit zero-exposure caveat",
            "metric_status": "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "cvar_economic_robustness",
            "metric_value": cvar_report["economic_robustness_status"],
            "metric_threshold": "MEASURED_NONZERO_EXPOSURE_OR_APPROVED_STRESS",
            "metric_status": "INCONCLUSIVE"
            if cvar_report["economic_robustness_status"] == "NOT_PROVEN_ZERO_EXPOSURE"
            else "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "test_environment_probe",
            "metric_value": environment_report["all_required_probe_packages_available"],
            "metric_threshold": True,
            "metric_status": "PASS" if environment_report["all_required_probe_packages_available"] else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "local_regeneration_probe",
            "metric_value": regeneration_report["returncode"],
            "metric_threshold": 0,
            "metric_status": "PASS"
            if regeneration_report["returncode"] == 0
            else "INCONCLUSIVE"
            if str(regeneration_report.get("blocker") or "").startswith("MISSING_")
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "clean_regeneration_proof",
            "metric_value": regeneration_report["clean_clone_or_equivalent"],
            "metric_threshold": True,
            "metric_status": "PASS" if regeneration_report["clean_clone_or_equivalent"] else "INCONCLUSIVE",
        },
    ]

    branch = _git_output("branch", "--show-current")
    commit = _git_output("rev-parse", "HEAD")
    compare_url = f"https://github.com/ulissesfalves/sniper/compare/codex/openclaw-sniper-handoff...{branch}"
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": commit,
        "working_tree_dirty": bool(working_tree_before),
        "branch": branch,
        "compare_url": compare_url,
        "official_artifacts_used": [str(path) for path in REQUIRED_PHASE4_ARTIFACTS],
        "research_artifacts_generated": [
            str(GATE_DIR / "source_doc_alignment.json"),
            str(GATE_DIR / "phase4_artifact_integrity_report.json"),
            str(GATE_DIR / "portfolio_cvar_report.json"),
            str(GATE_DIR / "environment_report.json"),
            str(GATE_DIR / "clean_regeneration_report.json"),
            str(GATE_DIR / "clean_regeneration_preflight.json"),
        ],
        "summary": [
            f"source_doc_alignment={source_alignment['status']}",
            f"phase4_artifact_integrity={phase4_integrity_report['artifact_integrity_status']}",
            f"dsr_honest={phase4_integrity_report['dsr_honest']}",
            f"phase4_promotion_status={phase4_integrity_report['promotion_status']}",
            f"missing_documented_modules={source_alignment['missing_documented_modules']}",
            f"cvar_technical_status={cvar_report['technical_persistence_status']}",
            f"cvar_economic_status={cvar_report['economic_robustness_status']}",
            f"regeneration_returncode={regeneration_report['returncode']}",
            f"regeneration_blocker={regeneration_report.get('blocker')}",
            "missing_regeneration_baseline_artifacts="
            f"{regeneration_report.get('preflight', {}).get('missing_regeneration_baseline_artifacts', [])}",
        ],
        "gates": gate_metrics,
        "blockers": blockers,
        "risks_residual": [
            "A3/A4 remain closed; no promotion attempted.",
            "RiskLabAI remains oracle/shadow only.",
            "Cross-sectional family remains ALIVE_BUT_NOT_PROMOTABLE.",
            "DSR=0.0 remains a promotion blocker; this gate does not alter thresholds.",
        ],
        "next_recommended_step": (
            "Provide the research regeneration baseline artifacts under "
            "data/models/research/phase4_cross_sectional_ranking_baseline/ to prove clean regeneration. "
            "Even after that, stop promotion/readiness escalation while DSR honest remains 0.0 and CVaR has zero exposure."
        ),
    }
    gate_command = (
        ".\\.venv\\Scripts\\python.exe services\\ml_engine\\phase6_global_reproducibility_source_alignment_gate.py"
        if GATE_SLUG == DEFAULT_GATE_SLUG
        else "$env:SNIPER_PHASE6_GATE_SLUG='"
        + GATE_SLUG
        + "'; .\\.venv\\Scripts\\python.exe services\\ml_engine\\phase6_global_reproducibility_source_alignment_gate.py; "
        "Remove-Item Env:SNIPER_PHASE6_GATE_SLUG"
    )
    commands_executed = [
        ".\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q",
        gate_command,
    ]
    if regeneration_report.get("command_executed"):
        commands_executed.append(str(regeneration_report.get("command")))
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": commit,
        "branch": branch,
        "working_tree_dirty_before": bool(working_tree_before),
        "working_tree_dirty_after": bool(_git_output("status", "--short", "--untracked-files=all")),
        "source_artifacts": [
            artifact_record(DOC_PHASE4_MEMORY),
            artifact_record(DOC_HANDOFF),
            artifact_record(DOC_REGENERATION),
            artifact_record(AUDIT_SUMMARY),
            artifact_record(AUDIT_NEXT_STEP),
            *[artifact_record(path) for path in REQUIRED_PHASE4_ARTIFACTS],
            *[artifact_record(path) for path in REQUIRED_REGENERATION_BASELINE_ARTIFACTS],
            artifact_record(THIS_FILE),
            artifact_record(REPO_ROOT / "services" / "ml_engine" / "sizing" / "kelly_cvar.py"),
        ],
        "generated_artifacts": [
            artifact_record(GATE_DIR / "source_doc_alignment.json"),
            artifact_record(GATE_DIR / "phase4_artifact_integrity_report.json"),
            artifact_record(GATE_DIR / "portfolio_cvar_report.json"),
            artifact_record(GATE_DIR / "environment_report.json"),
            artifact_record(GATE_DIR / "clean_regeneration_report.json"),
            artifact_record(GATE_DIR / "clean_regeneration_preflight.json"),
        ],
        "commands_executed": commands_executed,
        "notes": [
            "No official promotion, A3/A4 reopening, merge, force push, credential use, or real-capital operation.",
            "Clean regeneration remains inconclusive unless the required research baseline artifacts exist and an isolated clone/equivalent proof is documented.",
            "DSR honest is read from phase4_report_v4.json and blocks any promotion when equal to 0.0.",
            "The Phase5 restore command is skipped when preflight reports missing regeneration baseline artifacts.",
        ],
    }
    section_bodies = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}`.",
                f"- Source-doc alignment: `{source_alignment['status']}`.",
                f"- Phase4 artifact integrity: `{phase4_integrity_report['artifact_integrity_status']}`.",
                f"- DSR honest: `{phase4_integrity_report['dsr_honest']}`; promotion status `{phase4_integrity_report['promotion_status']}`.",
                f"- Clean regeneration proof: `{regeneration_report['clean_clone_or_equivalent']}`; local probe rc `{regeneration_report['returncode']}`.",
                f"- CVaR persisted as `{cvar_report['technical_persistence_status']}` with economic status `{cvar_report['economic_robustness_status']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                "- A3/A4 remain closed.",
                "- RiskLabAI remains oracle/shadow, not official.",
                "- Fast path remains official; cross-sectional remains ALIVE_BUT_NOT_PROMOTABLE.",
                "- DSR honest 0.0 remains a promotion blocker.",
            ]
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- Added a Phase 6 gate runner for source-doc-artifact alignment.",
                "- Added official Phase 4 artifact integrity and DSR evidence.",
                "- Added persisted CVaR evidence with an explicit zero-exposure caveat.",
                "- Added environment and local regeneration probe artifacts.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [
                f"- `{GATE_DIR / 'source_doc_alignment.json'}`",
                f"- `{GATE_DIR / 'phase4_artifact_integrity_report.json'}`",
                f"- `{GATE_DIR / 'portfolio_cvar_report.json'}`",
                f"- `{GATE_DIR / 'environment_report.json'}`",
                f"- `{GATE_DIR / 'clean_regeneration_report.json'}`",
                f"- `{GATE_DIR / 'clean_regeneration_preflight.json'}`",
                f"- `{GATE_DIR / 'gate_report.json'}`",
                f"- `{GATE_DIR / 'gate_report.md'}`",
                f"- `{GATE_DIR / 'gate_manifest.json'}`",
                f"- `{GATE_DIR / 'gate_metrics.parquet'}`",
            ]
        ),
        "Resultados": "\n".join(
            [
                f"- Missing documented Phase 4-R4 modules: `{source_alignment['missing_documented_modules']}`.",
                f"- Missing official Phase4 artifacts: `{phase4_integrity_report['missing_required_artifacts']}`.",
                f"- DSR honest/pass: `{phase4_integrity_report['dsr_honest']}` / `{phase4_integrity_report['dsr_passed']}`.",
                f"- Environment packages available: `{environment_report['all_required_probe_packages_available']}`.",
                f"- Regeneration blocker: `{regeneration_report.get('blocker')}`.",
                "- Missing regeneration baseline artifacts: "
                f"`{regeneration_report.get('preflight', {}).get('missing_regeneration_baseline_artifacts', [])}`.",
                f"- CVaR stress report: `{cvar_report['stress_report']}`.",
            ]
        ),
        "Avaliação contra gates": "\n".join(
            [f"- {row['metric_name']}: `{row['metric_status']}` (value `{row['metric_value']}`)" for row in gate_metrics]
        ),
        "Riscos residuais": "\n".join([f"- {risk}" for risk in gate_report["risks_residual"]]),
        "Veredito final: advance / correct / abandon": f"- `{status}` -> `{decision}`. Blockers: `{blockers}`.",
    }
    outputs = write_gate_pack(
        output_dir=GATE_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=section_bodies,
    )
    return {
        "status": status,
        "decision": decision,
        "blockers": blockers,
        "gate_path": str(GATE_DIR),
        "gate_outputs": {key: str(path) for key, path in outputs.items()},
    }


def main() -> None:
    result = run_phase6_global_reproducibility_source_alignment_gate()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
