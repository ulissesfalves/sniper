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

from services.common.gate_reports import (  # noqa: E402
    GATE_REPORT_MARKDOWN_SECTIONS,
    artifact_record,
    utc_now_iso,
    write_gate_pack,
)

GATE_SLUG = "phase5_research_meta_disagreement_candidate_decision_gate"
PHASE_FAMILY = "phase5_research_meta_disagreement_candidate_decision"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
INITIAL_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_meta_disagreement_abstention_gate" / "gate_report.json"
STABILITY_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_meta_disagreement_stability_falsification_gate" / "gate_report.json"
)
STABILITY_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_meta_disagreement_stability_falsification_gate"
    / "meta_disagreement_stability_falsification_report.json"
)
AGENDA = REPO_ROOT / "reports" / "state" / "sniper_research_agenda.yaml"

CANDIDATE_POLICY = "short_bma_high_meta_low_p60_m40_k3"
NEXT_AGENDA_ID_IF_FALSIFIED = "AGENDA-H02"
NEXT_AGENDA_GATE_IF_FALSIFIED = "phase5_research_meta_uncertainty_abstention_gate"


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
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def classify_meta_disagreement_decision(
    initial_gate: dict[str, Any],
    stability_gate: dict[str, Any],
    stability_report: dict[str, Any],
) -> tuple[str, str, str, str]:
    if initial_gate.get("status") != "PASS" or initial_gate.get("decision") != "advance":
        return "FAIL", "abandon", "META_DISAGREEMENT_INITIAL_GATE_NOT_CANDIDATE", "initial_gate_not_pass_advance"
    if stability_gate.get("status") == "INCONCLUSIVE":
        return "PASS", "correct", "META_DISAGREEMENT_RESEARCH_CANDIDATE_NEEDS_EXTERNAL_DATA", "inconclusive_stability"
    hard_falsifiers = stability_report.get("hard_falsifiers", [])
    if hard_falsifiers or stability_gate.get("status") == "FAIL":
        return "PASS", "abandon", "META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED", "hard_falsifiers_present"
    if stability_gate.get("status") == "PASS":
        return "PASS", "advance", "META_DISAGREEMENT_RESEARCH_CANDIDATE_ALIVE_NOT_PROMOTABLE", "survived_falsification"
    return "PARTIAL", "correct", "META_DISAGREEMENT_RESEARCH_CANDIDATE_NEEDS_DEEPENING", "stability_not_final"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def _markdown_sections(values: dict[str, str]) -> dict[str, str]:
    sections = {section: "" for section in GATE_REPORT_MARKDOWN_SECTIONS}
    keys = list(GATE_REPORT_MARKDOWN_SECTIONS)
    sections.update(
        {
            keys[0]: values.get("summary", ""),
            keys[1]: values.get("baseline", ""),
            keys[2]: values.get("changes", ""),
            keys[3]: values.get("artifacts", ""),
            keys[4]: values.get("results", ""),
            keys[5]: values.get("evaluation", ""),
            keys[6]: values.get("risks", ""),
            keys[7]: values.get("verdict", ""),
        }
    )
    return sections


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    initial_gate = read_json(INITIAL_GATE)
    stability_gate = read_json(STABILITY_GATE)
    stability_report = read_json(STABILITY_REPORT)
    status, decision, classification, reason = classify_meta_disagreement_decision(
        initial_gate,
        stability_gate,
        stability_report,
    )
    hard_falsifiers = list(stability_report.get("hard_falsifiers", []))
    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    if classification.endswith("_FALSIFIED"):
        next_mode = "START_RESEARCH_ONLY_THESIS"
        next_gate = NEXT_AGENDA_GATE_IF_FALSIFIED
        next_agenda_id = NEXT_AGENDA_ID_IF_FALSIFIED
    elif classification.endswith("_ALIVE_NOT_PROMOTABLE"):
        next_mode = "UPDATE_STATE_AND_PR_DRAFT"
        next_gate = "draft_pr_update"
        next_agenda_id = None
    else:
        next_mode = "CONTINUE_AUTONOMOUS"
        next_gate = "meta_disagreement_deepening_or_correction_gate"
        next_agenda_id = None

    metrics_path = OUTPUT_DIR / "meta_disagreement_candidate_decision_metrics.parquet"
    report_path = OUTPUT_DIR / "meta_disagreement_candidate_decision_report.json"
    row = {
        "policy": CANDIDATE_POLICY,
        "initial_status": initial_gate.get("status"),
        "stability_status": stability_gate.get("status"),
        "classification": classification,
        "hard_falsifier_count": len(hard_falsifiers),
        "hard_falsifiers": ",".join(hard_falsifiers),
        "next_mode": next_mode,
        "next_gate": next_gate,
        "next_agenda_id": next_agenda_id or "",
        "promotion_allowed": False,
        "paper_readiness_allowed": False,
    }
    pd.DataFrame([row]).to_parquet(metrics_path, index=False)

    payload = {
        "hypothesis": (
            "The meta-disagreement candidate can be classified after initial PASS and autonomous "
            "stability/falsification without promotion or human thesis selection."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "classification_reason": reason,
        "candidate_policy": CANDIDATE_POLICY,
        "hard_falsifiers": hard_falsifiers,
        "initial_gate": {
            "status": initial_gate.get("status"),
            "decision": initial_gate.get("decision"),
            "summary": initial_gate.get("summary", []),
        },
        "stability_gate": {
            "status": stability_gate.get("status"),
            "decision": stability_gate.get("decision"),
            "summary": stability_gate.get("summary", []),
        },
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
        },
        "next_recommended_mode": next_mode,
        "next_recommended_gate": next_gate,
        "next_agenda_id_if_falsified": next_agenda_id,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "short_exposure_research_sandbox_only",
        ],
    }
    write_json(report_path, payload)

    gate_metrics = [
        _metric("initial_gate_passed", initial_gate.get("status") == "PASS", "true", initial_gate.get("status") == "PASS"),
        _metric(
            "stability_falsification_gate_status",
            stability_gate.get("status"),
            "PASS or FAIL or PARTIAL",
            stability_gate.get("status") in {"PASS", "FAIL", "PARTIAL"},
        ),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 for alive candidate", len(hard_falsifiers) == 0),
        _metric("final_classification", classification, "decision classification", True),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
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
        "research_artifacts_generated": [str(metrics_path), str(report_path)],
        "summary": [
            f"classification={classification}",
            f"classification_reason={reason}",
            f"candidate_policy={CANDIDATE_POLICY}",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            "candidate remains research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            f"next_recommended_mode={next_mode}",
            f"next_recommended_gate={next_gate}",
        ],
        "gates": gate_metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "No official promotion evidence was created.",
            "Paper readiness remains blocked by DSR=0.0 and official CVaR zero exposure.",
            "If the candidate is falsified, the next agenda hypothesis must be materially different and ex-ante.",
        ],
        "next_recommended_step": f"Execute the next safe in-repo action automatically: {next_mode} / {next_gate}.",
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": [
            artifact_record(INITIAL_GATE),
            artifact_record(STABILITY_GATE),
            artifact_record(STABILITY_REPORT),
            artifact_record(AGENDA),
        ],
        "generated_artifacts": [artifact_record(metrics_path), artifact_record(report_path)],
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Research-only meta-disagreement candidate decision gate.",
            "Classifies the candidate without official promotion, paper readiness, merge, A3/A4 reopen or threshold relaxation.",
        ],
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=_markdown_sections(
            {
                "summary": f"Meta-disagreement decision result: `{status}/{decision}`. Classification: `{classification}`.",
                "baseline": f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research/sandbox only.",
                "changes": "Added a candidate decision gate after meta-disagreement stability/falsification.",
                "artifacts": f"- `{metrics_path}`\n- `{report_path}`",
                "results": f"Hard falsifiers: `{', '.join(hard_falsifiers) or 'none'}`. Next gate: `{next_gate}`.",
                "evaluation": "The gate preserves research/official separation and keeps DSR/CVaR blockers active.",
                "risks": "The candidate cannot support official promotion or paper readiness.",
                "verdict": f"`{status}/{decision}`. Next mode `{next_mode}`.",
            }
        ),
    )
    return gate_report


def main() -> int:
    report = run_gate()
    print(json.dumps({"gate_slug": GATE_SLUG, "status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
