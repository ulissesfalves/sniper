#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
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

import phase5_research_candidate_validation as candidate

GATE_SLUG = "phase5_research_candidate_decision_gate"
PHASE_FAMILY = "phase5_research_candidate_decision"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
REAUDIT_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_global_reaudit_gate" / "gate_report.json"
STABILITY_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_stability_gate" / "gate_report.json"
FALSIFICATION_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_falsification_gate" / "gate_report.json"
FALSIFICATION_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_candidate_falsification_gate"
    / "candidate_falsification_report.json"
)


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def classify_candidate(
    reaudit_gate: dict[str, Any],
    stability_gate: dict[str, Any],
    falsification_gate: dict[str, Any],
    falsification_report: dict[str, Any],
) -> tuple[str, str, str, str]:
    if falsification_gate.get("status") == "INCONCLUSIVE":
        return "PASS", "correct", "RESEARCH_CANDIDATE_NEEDS_EXTERNAL_DATA", "external_or_missing_evidence"
    if falsification_report.get("hard_falsifiers"):
        return "PASS", "abandon", "RESEARCH_CANDIDATE_FALSIFIED", "hard_falsifiers_present"
    if stability_gate.get("status") == "PASS" and reaudit_gate.get("status") == "PASS":
        return "PASS", "advance", "RESEARCH_CANDIDATE_ALIVE_NOT_PROMOTABLE", "survived_reaudit_stability_falsification"
    return "PARTIAL", "correct", "RESEARCH_CANDIDATE_READY_FOR_DEEPENING", "requires_deepening_before_any_promotion"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    reaudit_gate = candidate.read_json(REAUDIT_GATE)
    stability_gate = candidate.read_json(STABILITY_GATE)
    falsification_gate = candidate.read_json(FALSIFICATION_GATE)
    falsification_report = candidate.read_json(FALSIFICATION_REPORT)
    status, decision, classification, reason = classify_candidate(
        reaudit_gate,
        stability_gate,
        falsification_gate,
        falsification_report,
    )
    git_context = candidate.current_git_context()

    metrics_rows = [
        {
            "candidate": candidate.CANDIDATE_POLICY,
            "reaudit_status": reaudit_gate.get("status"),
            "stability_status": stability_gate.get("status"),
            "falsification_status": falsification_gate.get("status"),
            "hard_falsifiers": ",".join(falsification_report.get("hard_falsifiers", [])),
            "final_classification": classification,
            "promotion_allowed": False,
            "paper_readiness_allowed": False,
        }
    ]
    metrics_path = OUTPUT_DIR / "candidate_decision_metrics.parquet"
    report_path = OUTPUT_DIR / "candidate_decision_report.json"
    pd.DataFrame(metrics_rows).to_parquet(metrics_path, index=False)

    payload = {
        "hypothesis": (
            "The surviving short_high_p_bma_k3_p60_h70 candidate can be classified after autonomous "
            "reaudit, stability testing and falsification without human decision or official promotion."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "classification_reason": reason,
        "candidate": candidate.CANDIDATE_POLICY,
        "reaudit_gate": {
            "status": reaudit_gate.get("status"),
            "decision": reaudit_gate.get("decision"),
            "summary": reaudit_gate.get("summary", []),
        },
        "stability_gate": {
            "status": stability_gate.get("status"),
            "decision": stability_gate.get("decision"),
            "summary": stability_gate.get("summary", []),
        },
        "falsification_gate": {
            "status": falsification_gate.get("status"),
            "decision": falsification_gate.get("decision"),
            "summary": falsification_gate.get("summary", []),
            "hard_falsifiers": falsification_report.get("hard_falsifiers", []),
        },
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "short_exposure_is_research_sandbox_only": True,
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
        },
        "next_recommended_mode": "RUN_GLOBAL_REAUDIT" if classification == "RESEARCH_CANDIDATE_FALSIFIED" else "UPDATE_DRAFT_PR",
        "next_recommended_gate": "post_candidate_falsification_global_reaudit" if classification == "RESEARCH_CANDIDATE_FALSIFIED" else "draft_pr_update",
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "short_exposure_research_sandbox_only",
        ],
    }
    candidate.write_json(report_path, payload)

    hard_falsifiers = falsification_report.get("hard_falsifiers", [])
    gate_metrics = [
        _metric("reaudit_gate_passed", reaudit_gate.get("status") == "PASS", "true", reaudit_gate.get("status") == "PASS"),
        _metric("stability_gate_status", stability_gate.get("status"), "PASS or PARTIAL accepted for falsification decision", stability_gate.get("status") in {"PASS", "PARTIAL"}),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 for alive candidate", len(hard_falsifiers) == 0),
        _metric("final_classification", classification, "one of decision classifications", True),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]
    source_artifacts = [
        artifact_record(REAUDIT_GATE),
        artifact_record(STABILITY_GATE),
        artifact_record(FALSIFICATION_GATE),
        artifact_record(FALSIFICATION_REPORT),
    ]
    generated_artifacts = [artifact_record(metrics_path), artifact_record(report_path)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(metrics_path), str(report_path)],
        "summary": [
            f"classification={classification}",
            f"classification_reason={reason}",
            f"candidate_policy={candidate.CANDIDATE_POLICY}",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            "candidate remains research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
        ],
        "gates": gate_metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "DSR remains 0.0 and blocks promotion.",
            "Official CVaR remains zero exposure and is not economic robustness.",
            "No official short exposure support is established by this research gate.",
        ],
        "next_recommended_step": (
            "Update reports/state and the existing draft PR with the candidate decision; run a global reaudit "
            "before any new broad mission."
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
        "generated_artifacts": generated_artifacts,
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Autonomous candidate decision gate.",
            "Classifies the candidate without promoting official, declaring readiness, or reopening A3/A4.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Candidate decision result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Decision is research/governance only."
        ),
        "MudanÃƒÂ§as implementadas": (
            "Added a decision gate combining candidate reaudit, stability and falsification evidence."
        ),
        "Artifacts gerados": (
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Final classification `{classification}` with reason `{reason}`."
        ),
        "AvaliaÃƒÂ§ÃƒÂ£o contra gates": (
            "The decision preserves research/official separation and keeps DSR/CVaR/promotability blockers active."
        ),
        "Riscos residuais": (
            "No official readiness evidence was created. The next broad action should be global reaudit or a new thesis."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}` for this candidate line. No promotion."
        ),
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    return gate_report


if __name__ == "__main__":
    report = run_gate()
    print(json.dumps({"gate_slug": report["gate_slug"], "status": report["status"], "decision": report["decision"]}))
