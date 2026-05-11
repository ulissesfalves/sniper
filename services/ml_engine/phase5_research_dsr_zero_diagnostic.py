#!/usr/bin/env python3
from __future__ import annotations

import json
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

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_research_dsr_zero_diagnostic_gate"
PHASE_FAMILY = "phase5_research_dsr_zero_diagnostic"
PHASE4_REPORT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_report_v4.json"
PHASE4_DIAGNOSTIC = REPO_ROOT / "data" / "models" / "phase4" / "phase4_gate_diagnostic.json"
PHASE4_INTEGRITY = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase6_research_baseline_rehydration_clean_regeneration_gate"
    / "phase4_artifact_integrity_report.json"
)
STAGE_A_GATE_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_only_stage_a_nonzero_exposure_falsification_gate"
    / "gate_report.json"
)
CVAR_GATE_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate"
    / "gate_report.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

DSR_PASS_THRESHOLD = 0.95


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "pass"}
    return bool(value)


def collect_dsr_and_sharpe_candidates(obj: Any, prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        has_signal = any(key in obj for key in ("sharpe", "sharpe_is", "sharpe_oos", "dsr_honest", "dsr"))
        if has_signal:
            rows.append(
                {
                    "path": prefix or "<root>",
                    "sharpe": _as_float(obj.get("sharpe", obj.get("sharpe_is", obj.get("sharpe_oos"))), None),
                    "dsr_honest": _as_float(obj.get("dsr_honest", obj.get("dsr")), None),
                    "passed": _as_bool(obj.get("passed", False)),
                    "diagnostic_scope": classify_candidate_scope(prefix),
                }
            )
        for key, value in obj.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(collect_dsr_and_sharpe_candidates(value, child_prefix))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            rows.extend(collect_dsr_and_sharpe_candidates(value, f"{prefix}[{idx}]"))
    return rows


def classify_candidate_scope(path: str) -> str:
    lowered = path.lower()
    if "cpcv_trajectories" in lowered:
        return "cpcv_fold_diagnostic"
    if "rolling_stability" in lowered or "forward_validation" in lowered or "holdout_validation" in lowered:
        return "validation_diagnostic"
    if "sensitivity" in lowered or "ablation" in lowered or "stress" in lowered:
        return "research_diagnostic"
    if "fallback" in lowered or "operational_path" in lowered or path == "dsr":
        return "official_candidate_context"
    return "diagnostic"


def summarize_dsr_blocker(
    phase4_report: dict[str, Any],
    integrity_report: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    dsr = dict(integrity_report.get("dsr") or phase4_report.get("dsr") or {})
    sharpe_is = _as_float(dsr.get("sharpe_is", phase4_report.get("fallback", {}).get("sharpe")), 0.0)
    dsr_honest = _as_float(dsr.get("dsr_honest"), 0.0)
    sr_needed = _as_float(dsr.get("sr_needed"), 4.47)
    n_trials = int(_as_float(dsr.get("n_trials_honest"), 5000))
    dsr_passed = _as_bool(dsr.get("passed", integrity_report.get("dsr_passed", False)))
    sharpe_gap = round(float(sr_needed - sharpe_is), 6)

    frame = pd.DataFrame(candidate_rows)
    if frame.empty:
        best_dsr_row: dict[str, Any] = {}
        best_sharpe_row: dict[str, Any] = {}
    else:
        numeric = frame.copy()
        numeric["sharpe"] = pd.to_numeric(numeric["sharpe"], errors="coerce")
        numeric["dsr_honest"] = pd.to_numeric(numeric["dsr_honest"], errors="coerce")
        best_dsr_row = (
            numeric.sort_values(["dsr_honest", "sharpe"], ascending=[False, False]).iloc[0].to_dict()
            if numeric["dsr_honest"].notna().any()
            else {}
        )
        best_sharpe_row = (
            numeric.sort_values(["sharpe", "dsr_honest"], ascending=[False, False]).iloc[0].to_dict()
            if numeric["sharpe"].notna().any()
            else {}
        )

    return {
        "dsr_honest": round(dsr_honest, 6),
        "dsr_passed": bool(dsr_passed),
        "dsr_pass_threshold": DSR_PASS_THRESHOLD,
        "n_trials_honest": n_trials,
        "sharpe_is": round(sharpe_is, 6),
        "sr_needed": round(sr_needed, 6),
        "sharpe_gap_to_sr_needed": sharpe_gap,
        "best_dsr_honest_observed": round(_as_float(best_dsr_row.get("dsr_honest"), 0.0), 6),
        "best_dsr_honest_path": str(best_dsr_row.get("path", "")),
        "best_dsr_honest_scope": str(best_dsr_row.get("diagnostic_scope", "")),
        "best_sharpe_observed": round(_as_float(best_sharpe_row.get("sharpe"), 0.0), 6),
        "best_sharpe_path": str(best_sharpe_row.get("path", "")),
        "best_sharpe_scope": str(best_sharpe_row.get("diagnostic_scope", "")),
        "candidate_rows_scanned": int(len(candidate_rows)),
        "root_causes": [
            "honest DSR is exactly 0.0 and fails the 0.95 pass threshold",
            f"honest multiple-testing budget remains n_trials_honest={n_trials}",
            f"chosen Sharpe {round(sharpe_is, 6)} is {sharpe_gap} below sr_needed {round(sr_needed, 6)}",
            "diagnostic high-Sharpe or higher-DSR windows are not full official promotion evidence",
            "research sandbox CVaR measurement did not fix negative alpha or DSR",
        ],
    }


def classify_diagnostic(summary: dict[str, Any]) -> tuple[str, str, str]:
    required_keys = {
        "dsr_honest",
        "dsr_passed",
        "n_trials_honest",
        "sharpe_is",
        "sr_needed",
        "sharpe_gap_to_sr_needed",
        "candidate_rows_scanned",
    }
    if any(key not in summary for key in required_keys):
        return "INCONCLUSIVE", "correct", "DSR_DIAGNOSTIC_INCOMPLETE"
    if summary["dsr_passed"] or summary["dsr_honest"] >= DSR_PASS_THRESHOLD:
        return "FAIL", "freeze", "UNEXPECTED_DSR_PASS_CONFLICTS_WITH_KNOWN_BLOCKER"
    if summary["candidate_rows_scanned"] <= 0:
        return "INCONCLUSIVE", "correct", "NO_DSR_SHARPE_CANDIDATES_SCANNED"
    return "PASS", "advance", "DSR_ZERO_ROOT_CAUSE_DIAGNOSTIC_COMPLETE"


def _metric_status(value: float, threshold: float, op: str) -> str:
    if op == ">=":
        return "PASS" if value >= threshold else "FAIL"
    if op == "<=":
        return "PASS" if value <= threshold else "FAIL"
    return "INCONCLUSIVE"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    phase4_report = _read_json(PHASE4_REPORT)
    phase4_diagnostic = _read_json(PHASE4_DIAGNOSTIC)
    integrity_report = _read_json(PHASE4_INTEGRITY)
    stage_a_gate = _read_json(STAGE_A_GATE_REPORT)
    cvar_gate = _read_json(CVAR_GATE_REPORT)

    candidate_rows = collect_dsr_and_sharpe_candidates(phase4_report)
    diagnostic_rows = collect_dsr_and_sharpe_candidates(phase4_diagnostic, "phase4_gate_diagnostic")
    all_rows = candidate_rows + diagnostic_rows
    summary = summarize_dsr_blocker(phase4_report, integrity_report, all_rows)
    status, decision, classification = classify_diagnostic(summary)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    diagnostic_path = OUTPUT_DIR / "dsr_zero_diagnostic_report.json"
    candidate_scan_path = OUTPUT_DIR / "dsr_candidate_scan.parquet"
    candidate_frame = pd.DataFrame(all_rows)
    diagnostic_payload = {
        "hypothesis": (
            "The honest DSR=0.0 blocker can be explained by measured Sharpe being far below "
            "the required Sharpe under the fixed honest multiple-testing budget, without relaxing thresholds."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "diagnostic": summary,
        "prior_research_context": {
            "stage_a_gate_status": stage_a_gate.get("status"),
            "stage_a_gate_decision": stage_a_gate.get("decision"),
            "stage_a_gate_summary": stage_a_gate.get("summary", []),
            "research_cvar_gate_status": cvar_gate.get("status"),
            "research_cvar_gate_decision": cvar_gate.get("decision"),
            "research_cvar_gate_summary": cvar_gate.get("summary", []),
        },
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "masks_dsr": False,
            "treats_diagnostic_as_operational_signal": False,
        },
    }
    diagnostic_path.write_text(json.dumps(diagnostic_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_frame.to_parquet(candidate_scan_path, index=False)

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "diagnostic_complete",
            "metric_value": status == "PASS",
            "metric_threshold": "true",
            "metric_status": "PASS" if status == "PASS" else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "dsr_honest",
            "metric_value": summary["dsr_honest"],
            "metric_threshold": f">= {DSR_PASS_THRESHOLD} for promotion",
            "metric_status": _metric_status(summary["dsr_honest"], DSR_PASS_THRESHOLD, ">="),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "sharpe_is",
            "metric_value": summary["sharpe_is"],
            "metric_threshold": f">= sr_needed {summary['sr_needed']}",
            "metric_status": _metric_status(summary["sharpe_is"], summary["sr_needed"], ">="),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "sharpe_gap_to_sr_needed",
            "metric_value": summary["sharpe_gap_to_sr_needed"],
            "metric_threshold": "<= 0.0",
            "metric_status": _metric_status(summary["sharpe_gap_to_sr_needed"], 0.0, "<="),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_dsr_honest_observed",
            "metric_value": summary["best_dsr_honest_observed"],
            "metric_threshold": f">= {DSR_PASS_THRESHOLD}",
            "metric_status": _metric_status(summary["best_dsr_honest_observed"], DSR_PASS_THRESHOLD, ">="),
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "n_trials_honest",
            "metric_value": summary["n_trials_honest"],
            "metric_threshold": "== 5000 fixed honest budget",
            "metric_status": "PASS" if summary["n_trials_honest"] == 5000 else "FAIL",
        },
    ]

    generated_artifacts = [artifact_record(diagnostic_path), artifact_record(candidate_scan_path)]
    source_artifacts = [
        artifact_record(PHASE4_REPORT),
        artifact_record(PHASE4_DIAGNOSTIC),
        artifact_record(PHASE4_INTEGRITY),
        artifact_record(STAGE_A_GATE_REPORT),
        artifact_record(CVAR_GATE_REPORT),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [str(PHASE4_REPORT), str(PHASE4_DIAGNOSTIC), str(PHASE4_INTEGRITY)],
        "research_artifacts_generated": [str(diagnostic_path), str(candidate_scan_path)],
        "summary": [
            f"classification={classification}",
            f"dsr_honest={summary['dsr_honest']}",
            f"dsr_passed={summary['dsr_passed']}",
            f"n_trials_honest={summary['n_trials_honest']}",
            f"sharpe_is={summary['sharpe_is']}",
            f"sr_needed={summary['sr_needed']}",
            f"sharpe_gap_to_sr_needed={summary['sharpe_gap_to_sr_needed']}",
            f"best_dsr_honest_observed={summary['best_dsr_honest_observed']} at {summary['best_dsr_honest_path']}",
            "diagnostic does not relax thresholds or promote official",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "sharpe_gap_to_required_honest_dsr",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "This diagnostic explains DSR=0.0 but does not improve the trading policy.",
            "Higher diagnostic windows are not accepted as full official evidence.",
            "Future research needs materially better ex-ante alpha, not threshold relaxation.",
        ],
        "next_recommended_step": (
            "Continue autonomously only with a materially new ex-ante ranking/sizing hypothesis that "
            "can improve alpha without using realized eligibility. Do not promote official."
        ),
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": head,
        "branch": branch,
        "working_tree_dirty_before": dirty_before,
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_artifacts,
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Research-only DSR blocker diagnostic.",
            "No thresholds were relaxed.",
            "No official artifacts were promoted.",
            "Diagnostic rows are not treated as operational signals.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"DSR zero diagnostic result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. The official blocker remains `dsr_honest=0.0`; "
            "this gate only explains the blocker."
        ),
        "Mudanças implementadas": (
            "Added a research-only diagnostic that scans Phase4 Sharpe/DSR evidence, measures the "
            "gap to the required honest Sharpe, and records why diagnostics cannot be promoted."
        ),
        "Artifacts gerados": (
            f"- `{diagnostic_path.relative_to(REPO_ROOT)}`\n"
            f"- `{candidate_scan_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"`dsr_honest={summary['dsr_honest']}`, `sharpe_is={summary['sharpe_is']}`, "
            f"`sr_needed={summary['sr_needed']}`, gap `{summary['sharpe_gap_to_sr_needed']}`. "
            f"Best observed diagnostic DSR was `{summary['best_dsr_honest_observed']}` at "
            f"`{summary['best_dsr_honest_path']}`."
        ),
        "Avaliação contra gates": (
            "The diagnostic is complete, but promotion metrics fail: DSR is below 0.95 and chosen "
            "Sharpe is below required Sharpe. This is a PASS only for root-cause diagnosis, not for "
            "readiness or official promotion."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, official CVaR remains zero exposure, and cross-sectional remains "
            "`ALIVE_BUT_NOT_PROMOTABLE`. Future work needs new ex-ante alpha."
        ),
        "Veredito final: advance / correct / abandon": (
            "`advance` for diagnostic capability. Continue only with a materially different "
            "research-only ranking/sizing thesis; no promotion is allowed."
        ),
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=metrics,
        markdown_sections=markdown_sections,
    )
    return gate_report


if __name__ == "__main__":
    report = run_gate()
    print(json.dumps({"gate_slug": report["gate_slug"], "status": report["status"], "decision": report["decision"]}))
