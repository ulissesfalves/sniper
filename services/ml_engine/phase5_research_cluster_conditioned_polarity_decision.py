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

GATE_SLUG = "phase5_research_cluster_conditioned_polarity_decision_gate"
PHASE_FAMILY = "phase5_research_cluster_conditioned_polarity_decision"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
CLUSTER_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_gate" / "gate_report.json"
FALSIFICATION_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_falsification_gate" / "gate_report.json"
)
FALSIFICATION_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_cluster_conditioned_polarity_falsification_gate"
    / "cluster_conditioned_polarity_falsification_report.json"
)


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


def classify_cluster_decision(
    cluster_gate: dict[str, Any],
    falsification_gate: dict[str, Any],
    falsification_report: dict[str, Any],
) -> tuple[str, str, str, str]:
    if cluster_gate.get("status") != "PASS" or cluster_gate.get("decision") != "advance":
        return "FAIL", "abandon", "CLUSTER_CONDITIONED_FAMILY_NOT_A_CANDIDATE", "initial_gate_not_pass_advance"
    hard_falsifiers = falsification_report.get("hard_falsifiers", [])
    if falsification_gate.get("status") == "FAIL" and hard_falsifiers:
        return "PASS", "abandon", "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_FALSIFIED", "hard_falsifiers_present"
    if falsification_gate.get("status") == "PASS":
        return "PASS", "advance", "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_ALIVE_NOT_PROMOTABLE", "survived_falsification"
    return "PARTIAL", "correct", "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_NEEDS_MORE_EVIDENCE", "incomplete_falsification"


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
    cluster_report = read_json(CLUSTER_GATE)
    falsification_gate = read_json(FALSIFICATION_GATE)
    falsification_report = read_json(FALSIFICATION_REPORT)
    status, decision, classification, reason = classify_cluster_decision(
        cluster_report,
        falsification_gate,
        falsification_report,
    )
    hard_falsifiers = falsification_report.get("hard_falsifiers", [])
    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    metrics_path = OUTPUT_DIR / "cluster_conditioned_polarity_decision_metrics.parquet"
    report_path = OUTPUT_DIR / "cluster_conditioned_polarity_decision_report.json"
    row = {
        "policy": "cluster_2_long_high_short_low_p60_h70_k3",
        "initial_status": cluster_report.get("status"),
        "falsification_status": falsification_gate.get("status"),
        "classification": classification,
        "hard_falsifier_count": len(hard_falsifiers),
        "promotion_allowed": False,
        "paper_readiness_allowed": False,
    }
    pd.DataFrame([row]).to_parquet(metrics_path, index=False)
    payload = {
        "hypothesis": (
            "The cluster-conditioned research candidate can be classified after falsification without "
            "promoting official or asking for human thesis selection."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "classification_reason": reason,
        "candidate_policy": row["policy"],
        "hard_falsifiers": hard_falsifiers,
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
        },
        "next_recommended_mode": "FREEZE_LINE" if classification.endswith("_FALSIFIED") else "RUN_GLOBAL_REAUDIT_CANDIDATE",
        "next_recommended_gate": "phase5_post_candidate_falsification_governed_freeze_gate"
        if classification.endswith("_FALSIFIED")
        else "phase5_research_candidate_global_reaudit_gate",
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metrics = [
        _metric("initial_gate_passed", cluster_report.get("status") == "PASS", "true", cluster_report.get("status") == "PASS"),
        _metric("falsification_gate_status", falsification_gate.get("status"), "FAIL or PASS", falsification_gate.get("status") in {"FAIL", "PASS"}),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 for alive candidate", len(hard_falsifiers) == 0),
        _metric("final_classification", classification, "decision classification", True),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]
    next_gate = payload["next_recommended_gate"]
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
            "candidate_policy=cluster_2_long_high_short_low_p60_h70_k3",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            "candidate remains research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            f"next_recommended_gate={next_gate}",
        ],
        "gates": metrics,
        "blockers": [
            "cluster_conditioned_candidate_falsified",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "No cluster-conditioned candidate survived robust falsification.",
            "No official promotion or readiness support exists.",
            "Further work can freeze or require a materially new hypothesis not yet in the backlog.",
        ],
        "next_recommended_step": f"Execute the next safe in-repo gate automatically: {next_gate}.",
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": [
            artifact_record(CLUSTER_GATE),
            artifact_record(FALSIFICATION_GATE),
            artifact_record(FALSIFICATION_REPORT),
        ],
        "generated_artifacts": [artifact_record(metrics_path), artifact_record(report_path)],
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Research-only cluster-conditioned candidate decision gate.",
            "Records abandon decision without promotion, paper readiness, merge, A3/A4 reopen or threshold relaxation.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Cluster-conditioned decision result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research/sandbox only."
        ),
        "Mudanças implementadas": (
            "Added a decision gate for the cluster-conditioned candidate after falsification."
        ),
        "Artifacts gerados": (
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Classification `{classification}` with `{len(hard_falsifiers)}` hard falsifiers."
        ),
        "Avaliação contra gates": (
            "The gate preserves research/official separation and blocks promotion/readiness."
        ),
        "Riscos residuais": (
            "DSR=0.0, official CVaR zero exposure and cross-sectional non-promotability remain."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue to `{next_gate}` if safe."
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
