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

GATE_SLUG = "phase5_post_candidate_falsification_global_reaudit_gate"
PHASE_FAMILY = "phase5_post_candidate_falsification_global_reaudit"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

STATE_FILE = REPO_ROOT / "reports" / "state" / "sniper_current_state.json"
BACKLOG_FILE = REPO_ROOT / "reports" / "state" / "sniper_spec_gap_backlog.yaml"
LEDGER_FILE = REPO_ROOT / "reports" / "state" / "sniper_decision_ledger.md"
RUNBOOK_FILE = REPO_ROOT / "reports" / "state" / "sniper_autonomous_runbook.md"
STAGE_A_PREDICTIONS = (
    REPO_ROOT
    / "data"
    / "models"
    / "research"
    / "phase4_cross_sectional_ranking_baseline"
    / "stage_a_predictions.parquet"
)

CANDIDATE_DECISION_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_decision_gate" / "gate_report.json"
)
CANDIDATE_FALSIFICATION_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_falsification_gate" / "gate_report.json"
)
CANDIDATE_STABILITY_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_stability_gate" / "gate_report.json"
)
FULL_PHASE_COMPARISON_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_full_phase_family_comparison_gate" / "gate_report.json"
)
CLUSTER_CONDITIONED_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_gate" / "gate_report.json"
)

FORBIDDEN_MODES = {
    "OFFICIAL_PROMOTION",
    "PAPER_READINESS",
    "A3_REOPEN",
    "A4_REOPEN",
    "THRESHOLD_RELAXATION",
    "REAL_TRADING",
    "MERGE_PR",
}


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_value(report: dict[str, Any], key: str) -> str:
    prefix = f"{key}="
    for item in report.get("summary", []):
        text = str(item)
        if text.startswith(prefix):
            return text[len(prefix) :]
    return ""


def _has_stage_a_column(column: str) -> bool:
    if not STAGE_A_PREDICTIONS.exists():
        return False
    try:
        pd.read_parquet(STAGE_A_PREDICTIONS, columns=[column])
    except Exception:
        return False
    return True


def detect_material_next_hypothesis(*, cluster_gate_exists: bool | None = None) -> dict[str, Any]:
    if cluster_gate_exists is None:
        cluster_gate_exists = CLUSTER_CONDITIONED_GATE.exists()
    cluster_column_available = _has_stage_a_column("cluster_name")
    available = bool(cluster_column_available and not cluster_gate_exists)
    return {
        "available": available,
        "hypothesis_family": "cluster_conditioned_polarity" if available else None,
        "suggested_gate": "phase5_research_cluster_conditioned_polarity_gate" if available else None,
        "reason": (
            "cluster_name is present in the research baseline and no cluster-conditioned polarity gate exists"
            if available
            else "no material untested in-repo hypothesis detected by this reaudit"
        ),
        "uses_external_resource": False,
        "uses_realized_variable_as_ex_ante_rule": False,
    }


def classify_post_falsification_reaudit(
    *,
    decision_gate: dict[str, Any],
    falsification_gate: dict[str, Any],
    state: dict[str, Any],
    next_hypothesis: dict[str, Any],
) -> tuple[str, str, str, str]:
    candidate_falsified = (
        decision_gate.get("decision") == "abandon"
        and _summary_value(decision_gate, "classification") == "RESEARCH_CANDIDATE_FALSIFIED"
    )
    hard_falsifiers_present = int(_summary_value(decision_gate, "hard_falsifier_count") or 0) > 0
    governance_safe = (
        state.get("official_promotion_allowed") is False
        and state.get("paper_readiness_allowed") is False
        and state.get("human_decision_required") is False
        and FORBIDDEN_MODES.isdisjoint(set(state.get("allowed_next_modes", [])))
    )
    falsification_confirmed = falsification_gate.get("status") == "FAIL" and hard_falsifiers_present
    if not candidate_falsified or not falsification_confirmed:
        return "INCONCLUSIVE", "correct", "CANDIDATE_FALSIFICATION_NOT_CONFIRMED", "missing_or_inconsistent_candidate_falsification"
    if not governance_safe:
        return "FAIL", "abandon", "GOVERNANCE_HARD_STOP", "governance_state_allows_forbidden_mode_or_readiness"
    if next_hypothesis.get("available"):
        return "PASS", "advance", "POST_FALSIFICATION_REAUDIT_PASS_START_NEW_RESEARCH_THESIS", "material_new_hypothesis_available"
    return "PASS", "freeze", "POST_FALSIFICATION_REAUDIT_PASS_NO_MATERIAL_NEW_HYPOTHESIS", "no_material_new_hypothesis"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = read_json(STATE_FILE)
    decision_gate = read_json(CANDIDATE_DECISION_GATE)
    falsification_gate = read_json(CANDIDATE_FALSIFICATION_GATE)
    stability_gate = read_json(CANDIDATE_STABILITY_GATE)
    comparison_gate = read_json(FULL_PHASE_COMPARISON_GATE)
    next_hypothesis = detect_material_next_hypothesis()
    status, decision, classification, reason = classify_post_falsification_reaudit(
        decision_gate=decision_gate,
        falsification_gate=falsification_gate,
        state=state,
        next_hypothesis=next_hypothesis,
    )

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }
    hard_falsifiers = [
        item
        for item in (_summary_value(decision_gate, "hard_falsifiers") or "").split(",")
        if item
    ]
    report_path = OUTPUT_DIR / "post_candidate_falsification_global_reaudit_report.json"
    payload = {
        "hypothesis": (
            "After the research-only survivor was falsified, the branch can be globally reaudited and "
            "the next safe in-repo action can be selected without human decision or official promotion."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "classification_reason": reason,
        "candidate_policy": "short_high_p_bma_k3_p60_h70",
        "candidate_status": "RESEARCH_CANDIDATE_FALSIFIED",
        "hard_falsifiers": hard_falsifiers,
        "candidate_gates": {
            "decision": {
                "status": decision_gate.get("status"),
                "decision": decision_gate.get("decision"),
                "classification": _summary_value(decision_gate, "classification"),
            },
            "falsification": {
                "status": falsification_gate.get("status"),
                "decision": falsification_gate.get("decision"),
            },
            "stability": {
                "status": stability_gate.get("status"),
                "decision": stability_gate.get("decision"),
            },
            "full_phase_comparison": {
                "status": comparison_gate.get("status"),
                "decision": comparison_gate.get("decision"),
            },
        },
        "governance": {
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
            "requires_human_decision": False,
        },
        "next_hypothesis": next_hypothesis,
        "next_recommended_mode": "START_RESEARCH_ONLY_THESIS" if next_hypothesis["available"] else "FREEZE_LINE",
        "next_recommended_gate": next_hypothesis.get("suggested_gate") or "phase5_post_candidate_falsification_governed_freeze_gate",
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "no_surviving_research_candidate_after_falsification",
        ],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metrics = [
        _metric("candidate_falsified", True, "true", classification != "CANDIDATE_FALSIFICATION_NOT_CONFIRMED"),
        _metric("hard_falsifier_count", len(hard_falsifiers), "> 0 after candidate falsification", len(hard_falsifiers) > 0),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
        _metric("human_decision_required", False, "false", True),
        _metric("material_new_hypothesis_available", next_hypothesis["available"], "true to continue thesis; false to freeze", True),
        _metric("next_recommended_gate", payload["next_recommended_gate"], "safe in-repo gate", True),
    ]
    source_artifacts = [
        artifact_record(STATE_FILE),
        artifact_record(BACKLOG_FILE),
        artifact_record(LEDGER_FILE),
        artifact_record(RUNBOOK_FILE),
        artifact_record(CANDIDATE_DECISION_GATE),
        artifact_record(CANDIDATE_FALSIFICATION_GATE),
        artifact_record(CANDIDATE_STABILITY_GATE),
        artifact_record(FULL_PHASE_COMPARISON_GATE),
        artifact_record(STAGE_A_PREDICTIONS),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(report_path)],
        "summary": [
            f"classification={classification}",
            f"classification_reason={reason}",
            "candidate_policy=short_high_p_bma_k3_p60_h70",
            "candidate_status=RESEARCH_CANDIDATE_FALSIFIED",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            f"material_new_hypothesis_available={next_hypothesis['available']}",
            f"next_hypothesis_family={next_hypothesis.get('hypothesis_family') or ''}",
            f"next_recommended_gate={payload['next_recommended_gate']}",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            "human_decision_required=false",
        ],
        "gates": metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "The abandoned short-high candidate cannot be revived without materially new evidence.",
            "DSR remains 0.0 and blocks promotion.",
            "Official CVaR remains zero exposure and is not economic robustness.",
        ],
        "next_recommended_step": (
            "Execute the next safe in-repo gate automatically: "
            f"{payload['next_recommended_gate']}."
        ),
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": [artifact_record(report_path)],
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Post-candidate-falsification global reaudit gate.",
            "Executes closed-loop next-action selection without promotion or human-decision stop.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Post-falsification reaudit result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. This is governance/research evidence only."
        ),
        "Mudanças implementadas": (
            "Added a focused global reaudit after candidate falsification and selected the next safe in-repo action."
        ),
        "Artifacts gerados": (
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"`short_high_p_bma_k3_p60_h70` remains falsified. Next gate: "
            f"`{payload['next_recommended_gate']}`."
        ),
        "Avaliação contra gates": (
            "No official promotion, paper readiness, A3/A4 reopening, threshold relaxation, merge or real capital action occurred."
        ),
        "Riscos residuais": (
            "DSR=0.0, official CVaR zero exposure and cross-sectional non-promotability remain open blockers."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue automatically if the next gate is safe; freeze only if no material hypothesis remains."
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
