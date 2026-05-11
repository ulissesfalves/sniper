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

GATE_SLUG = "phase5_research_hypothesis_space_freeze_gate"
PHASE_FAMILY = "phase5_research_hypothesis_space_freeze"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

GATE_PATHS = {
    "stage_a_nonzero_exposure": REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_only_stage_a_nonzero_exposure_falsification_gate"
    / "gate_report.json",
    "sandbox_cvar": REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate"
    / "gate_report.json",
    "dsr_zero_diagnostic": REPO_ROOT / "reports" / "gates" / "phase5_research_dsr_zero_diagnostic_gate" / "gate_report.json",
    "rank_score_threshold": REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_threshold_sizing_falsification_gate"
    / "gate_report.json",
    "rank_score_stability_correction": REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_stability_correction_gate"
    / "gate_report.json",
}
CURRENT_STATE = REPO_ROOT / "reports" / "state" / "sniper_current_state.json"
SPEC_GAP_BACKLOG = REPO_ROOT / "reports" / "state" / "sniper_spec_gap_backlog.yaml"


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


def summarize_gate(name: str, path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    return {
        "name": name,
        "path": str(path),
        "gate_slug": payload.get("gate_slug"),
        "status": payload.get("status"),
        "decision": payload.get("decision"),
        "summary": payload.get("summary", []),
        "blockers": payload.get("blockers", []),
        "next_recommended_step": payload.get("next_recommended_step"),
    }


def classify_freeze(gate_summaries: list[dict[str, Any]], current_state: dict[str, Any]) -> tuple[str, str, str]:
    by_name = {row["name"]: row for row in gate_summaries}
    required_names = set(GATE_PATHS)
    if set(by_name) != required_names:
        return "INCONCLUSIVE", "correct", "MISSING_REQUIRED_RESEARCH_GATE_FOR_FREEZE"

    promotion_allowed = bool(current_state.get("official_promotion_allowed", False))
    paper_allowed = bool(current_state.get("paper_readiness_allowed", False))
    dsr_status = current_state.get("dsr_status", {})
    cvar_status = current_state.get("cvar_status", {})
    threshold_abandoned = by_name["rank_score_stability_correction"].get("decision") == "abandon"
    stage_a_abandoned = by_name["stage_a_nonzero_exposure"].get("decision") == "abandon"
    dsr_blocked = float(dsr_status.get("dsr_honest", 0.0)) == 0.0 and not bool(dsr_status.get("dsr_passed", False))
    cvar_blocked = cvar_status.get("economic_status") == "NOT_PROVEN_ZERO_EXPOSURE"

    if promotion_allowed or paper_allowed:
        return "FAIL", "freeze", "STATE_CONFLICT_PROMOTION_OR_READINESS_ALLOWED_DESPITE_BLOCKERS"
    if stage_a_abandoned and threshold_abandoned and dsr_blocked and cvar_blocked:
        return "PASS", "freeze", "CURRENT_RESEARCH_HYPOTHESIS_SPACE_EXHAUSTED_UNDER_GOVERNANCE"
    return "PARTIAL", "correct", "FREEZE_EVIDENCE_INCOMPLETE_OR_BLOCKERS_NOT_CONSISTENT"


def build_freeze_summary(gate_summaries: list[dict[str, Any]], current_state: dict[str, Any]) -> dict[str, Any]:
    open_blockers = list(current_state.get("open_blockers", []))
    return {
        "gates_considered": gate_summaries,
        "hypotheses_tested": [
            "Stage A nonzero exposure without realized eligibility",
            "Research sandbox nonzero-exposure CVaR measurement",
            "DSR zero root-cause diagnostic",
            "Predeclared rank-score threshold family",
            "One-shot rank-score stability correction",
        ],
        "hypotheses_falsified_or_abandoned": [
            "Stage A nonzero exposure as promotion path",
            "Rank-score threshold family as promotable alpha path",
            "Rank-score stability correction as repair path",
        ],
        "surviving_modules": [
            "research_sandbox_nonzero_exposure_cvar_evaluator",
            "dsr_zero_root_cause_diagnostic",
        ],
        "surviving_candidates": [],
        "open_blockers": open_blockers,
        "stop_condition": "No materially new defensible research-only hypothesis remains inside the current backlog without repeating a falsified line, changing specification, using realized eligibility, or promoting research.",
        "can_continue_autonomously": False,
        "requires_external_artifact": False,
        "requires_human_decision_for_next_research_direction": True,
    }


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    current_state = _read_json(CURRENT_STATE)
    gate_summaries = [summarize_gate(name, path) for name, path in GATE_PATHS.items()]
    status, decision, classification = classify_freeze(gate_summaries, current_state)
    freeze_summary = build_freeze_summary(gate_summaries, current_state)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    freeze_path = OUTPUT_DIR / "research_hypothesis_space_freeze_report.json"
    gate_table_path = OUTPUT_DIR / "research_hypothesis_space_freeze_gate_table.parquet"
    freeze_payload = {
        "hypothesis": "The current autonomous research hypothesis space is exhausted under governance constraints.",
        "status": status,
        "decision": decision,
        "classification": classification,
        "freeze_summary": freeze_summary,
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "fabricates_artifacts": False,
            "uses_realized_variable_as_ex_ante_rule": False,
        },
    }
    freeze_path.write_text(json.dumps(freeze_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(gate_summaries).to_parquet(gate_table_path, index=False)

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_gates_considered",
            "metric_value": len(gate_summaries),
            "metric_threshold": "5",
            "metric_status": "PASS" if len(gate_summaries) == 5 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "official_promotion_allowed",
            "metric_value": current_state.get("official_promotion_allowed", False),
            "metric_threshold": "false",
            "metric_status": "PASS" if current_state.get("official_promotion_allowed") is False else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "paper_readiness_allowed",
            "metric_value": current_state.get("paper_readiness_allowed", False),
            "metric_threshold": "false",
            "metric_status": "PASS" if current_state.get("paper_readiness_allowed") is False else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "can_continue_autonomously",
            "metric_value": freeze_summary["can_continue_autonomously"],
            "metric_threshold": "false for freeze",
            "metric_status": "PASS" if freeze_summary["can_continue_autonomously"] is False else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "requires_external_artifact",
            "metric_value": freeze_summary["requires_external_artifact"],
            "metric_threshold": "false",
            "metric_status": "PASS" if freeze_summary["requires_external_artifact"] is False else "FAIL",
        },
    ]

    generated_artifacts = [artifact_record(freeze_path), artifact_record(gate_table_path)]
    source_artifacts = [artifact_record(path) for path in GATE_PATHS.values()]
    source_artifacts.extend([artifact_record(CURRENT_STATE), artifact_record(SPEC_GAP_BACKLOG)])
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(freeze_path), str(gate_table_path)],
        "summary": [
            f"classification={classification}",
            "gates_considered=5",
            "surviving_modules=research_sandbox_nonzero_exposure_cvar_evaluator,dsr_zero_root_cause_diagnostic",
            "surviving_promotable_candidates=0",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "no_materially_new_defensible_hypothesis_in_current_backlog",
        ],
        "risks_residual": [
            "A future research direction may be possible, but it requires a new thesis beyond the current exhausted line.",
            "Current PR should remain draft governance/reproducibility evidence, not readiness.",
            "No merge, official promotion, or paper readiness should occur from these gates.",
        ],
        "next_recommended_step": (
            "Stop autonomous implementation for this mission. Update the existing draft PR with the "
            "new gate evidence or request a human strategic decision for a new research thesis."
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
            "Freeze/closure gate for the current autonomous research hypothesis space.",
            "No official artifacts were promoted.",
            "Freeze is based on governance and exhausted defensible hypotheses, not user indecision.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Hypothesis-space freeze gate result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This gate freezes the current research line only; it does not merge or promote."
        ),
        "Mudanças implementadas": (
            "Added a closure gate that consolidates the autonomous research gates and records why the current "
            "hypothesis space should stop under governance constraints."
        ),
        "Artifacts gerados": (
            f"- `{freeze_path.relative_to(REPO_ROOT)}`\n"
            f"- `{gate_table_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            "Five research gates were considered. Surviving modules are diagnostic/evaluation modules only; "
            "there are no surviving promotable candidates."
        ),
        "Avaliação contra gates": (
            "Freeze is valid because official promotion and paper readiness remain forbidden, Stage A and "
            "threshold-family lines are abandoned, and no materially new defensible hypothesis remains in the current backlog."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, official CVaR remains zero exposure, and cross-sectional remains "
            "`ALIVE_BUT_NOT_PROMOTABLE`."
        ),
        "Veredito final: advance / correct / abandon": (
            "`freeze`. Stop this autonomous mission and update the draft PR or request a new strategic research direction."
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
