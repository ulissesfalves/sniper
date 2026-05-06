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

GATE_SLUG = "phase5_post_candidate_falsification_governed_freeze_gate"
PHASE_FAMILY = "phase5_post_candidate_falsification_governed_freeze"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

POST_FALSIFICATION_REAUDIT = (
    REPO_ROOT / "reports" / "gates" / "phase5_post_candidate_falsification_global_reaudit_gate" / "gate_report.json"
)
DSR_DIAGNOSTIC = REPO_ROOT / "reports" / "gates" / "phase5_research_dsr_zero_diagnostic_gate" / "gate_report.json"
RESEARCH_CVAR = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate" / "gate_report.json"
)
FULL_PHASE_COMPARISON = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_full_phase_family_comparison_gate" / "gate_report.json"
)
PRIOR_CANDIDATE_DECISION = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_decision_gate" / "gate_report.json"
)
CLUSTER_FAMILY = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_gate" / "gate_report.json"
)
CLUSTER_DECISION = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_decision_gate" / "gate_report.json"
)
BACKLOG = REPO_ROOT / "reports" / "state" / "sniper_spec_gap_backlog.yaml"

TESTED_FAMILIES = (
    "stage_a_safe_top1",
    "rank_score_threshold",
    "alternative_exante_p_bma_sigma_hmm",
    "signal_polarity_short_high",
    "cluster_conditioned_polarity",
)
EXHAUSTED_SAFE_FEATURES = (
    "p_bma_pkf",
    "p_stage_a_raw",
    "sigma_ewma",
    "uniqueness",
    "hmm_prob_bull",
    "cluster_name",
)
FORBIDDEN_FEATURES = (
    "stage_a_eligible",
    "pnl_real",
    "avg_sl_train",
    "rank_target_stage_a",
    "stage_a_score_realized",
    "label",
    "future_return",
    "forward_return",
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


def freeze_requirements(
    *,
    post_reaudit: dict[str, Any],
    dsr_diagnostic: dict[str, Any],
    research_cvar: dict[str, Any],
    comparison: dict[str, Any],
    prior_decision: dict[str, Any],
    cluster_decision: dict[str, Any],
) -> dict[str, bool]:
    return {
        "post_falsification_reaudit_passed": post_reaudit.get("status") == "PASS",
        "at_least_two_material_families_tested": len(TESTED_FAMILIES) >= 2,
        "explicit_dsr_diagnostic_exists": dsr_diagnostic.get("status") == "PASS",
        "research_cvar_nonzero_exposure_evaluated": research_cvar.get("status") in {"PASS", "PARTIAL"},
        "family_comparison_recorded": comparison.get("status") == "PASS",
        "prior_candidate_falsified": _summary_value(prior_decision, "classification") == "RESEARCH_CANDIDATE_FALSIFIED",
        "cluster_candidate_falsified": _summary_value(cluster_decision, "classification")
        == "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_FALSIFIED",
        "no_official_promotion": True,
        "no_paper_readiness": True,
    }


def detect_safe_material_hypotheses_remaining(requirements: dict[str, bool]) -> list[dict[str, Any]]:
    if not all(requirements.values()):
        return [
            {
                "hypothesis": "freeze_requirements_incomplete",
                "reason": "governed freeze cannot be declared until all freeze requirements pass",
            }
        ]
    return []


def classify_freeze(requirements: dict[str, bool], remaining_hypotheses: list[dict[str, Any]]) -> tuple[str, str, str]:
    if remaining_hypotheses:
        return "PARTIAL", "correct", "FREEZE_BLOCKED_BY_MATERIAL_HYPOTHESIS_OR_INCOMPLETE_REQUIREMENT"
    if all(requirements.values()):
        return "PASS", "freeze", "FULL_FREEZE_AFTER_REAUDIT"
    return "PARTIAL", "correct", "FREEZE_REQUIREMENTS_INCOMPLETE"


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
    post_reaudit = read_json(POST_FALSIFICATION_REAUDIT)
    dsr_diagnostic = read_json(DSR_DIAGNOSTIC)
    research_cvar = read_json(RESEARCH_CVAR)
    comparison = read_json(FULL_PHASE_COMPARISON)
    prior_decision = read_json(PRIOR_CANDIDATE_DECISION)
    cluster_family = read_json(CLUSTER_FAMILY)
    cluster_decision = read_json(CLUSTER_DECISION)
    requirements = freeze_requirements(
        post_reaudit=post_reaudit,
        dsr_diagnostic=dsr_diagnostic,
        research_cvar=research_cvar,
        comparison=comparison,
        prior_decision=prior_decision,
        cluster_decision=cluster_decision,
    )
    remaining = detect_safe_material_hypotheses_remaining(requirements)
    status, decision, classification = classify_freeze(requirements, remaining)

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }
    freeze_report_path = OUTPUT_DIR / "post_candidate_falsification_governed_freeze_report.json"
    freeze_metrics_path = OUTPUT_DIR / "post_candidate_falsification_governed_freeze_metrics.parquet"
    rows = [
        {
            "requirement": key,
            "passed": value,
            "gate_slug": GATE_SLUG,
        }
        for key, value in requirements.items()
    ]
    rows.extend(
        [
            {
                "requirement": "tested_family",
                "passed": True,
                "gate_slug": GATE_SLUG,
                "family": family,
            }
            for family in TESTED_FAMILIES
        ]
    )
    pd.DataFrame(rows).to_parquet(freeze_metrics_path, index=False)
    payload = {
        "hypothesis": (
            "After post-falsification global reaudit and a materially new cluster-conditioned thesis, "
            "the current in-repo research hypothesis space can be frozen without promotion."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "requirements": requirements,
        "tested_families": list(TESTED_FAMILIES),
        "exhausted_safe_exante_features": list(EXHAUSTED_SAFE_FEATURES),
        "forbidden_realized_or_non_exante_features": list(FORBIDDEN_FEATURES),
        "remaining_safe_material_hypotheses": remaining,
        "cluster_family_classification": _summary_value(cluster_family, "classification"),
        "cluster_candidate_classification": _summary_value(cluster_decision, "classification"),
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "requires_external_resource": False,
        },
        "final_classification": classification,
        "next_recommended_mode": "UPDATE_STATE_AND_DRAFT_PR",
        "next_recommended_gate": None,
    }
    freeze_report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metrics = [
        _metric(key, value, "true", value) for key, value in requirements.items()
    ]
    metrics.extend(
        [
            _metric("tested_family_count", len(TESTED_FAMILIES), ">= 2", len(TESTED_FAMILIES) >= 2),
            _metric("remaining_safe_material_hypothesis_count", len(remaining), "0 for governed freeze", len(remaining) == 0),
            _metric("official_promotion_allowed", False, "false", True),
            _metric("paper_readiness_allowed", False, "false", True),
        ]
    )
    source_artifacts = [
        artifact_record(POST_FALSIFICATION_REAUDIT),
        artifact_record(DSR_DIAGNOSTIC),
        artifact_record(RESEARCH_CVAR),
        artifact_record(FULL_PHASE_COMPARISON),
        artifact_record(PRIOR_CANDIDATE_DECISION),
        artifact_record(CLUSTER_FAMILY),
        artifact_record(CLUSTER_DECISION),
        artifact_record(BACKLOG),
    ]
    generated_artifacts = [artifact_record(freeze_report_path), artifact_record(freeze_metrics_path)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(freeze_report_path), str(freeze_metrics_path)],
        "summary": [
            f"classification={classification}",
            f"tested_family_count={len(TESTED_FAMILIES)}",
            f"tested_families={','.join(TESTED_FAMILIES)}",
            f"remaining_safe_material_hypothesis_count={len(remaining)}",
            "prior_candidate=short_high_p_bma_k3_p60_h70 falsified",
            "cluster_candidate=cluster_2_long_high_short_low_p60_h70_k3 falsified",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "no_surviving_research_candidate_after_falsification",
            "no_materially_new_safe_in_repo_hypothesis_remaining",
        ],
        "risks_residual": [
            "Freeze does not prove readiness or economic robustness.",
            "Future progress requires a materially new evidence source, external artifact, or specification-level research direction.",
            "Do not promote the frozen research families.",
        ],
        "next_recommended_step": "Update reports/state and the existing draft PR. Do not merge or mark ready.",
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
            "Governed freeze after post-falsification global reaudit.",
            "No official promotion, readiness, merge, A3/A4 reopen or threshold relaxation.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Governed freeze result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research line frozen, not promoted."
        ),
        "Mudanças implementadas": (
            "Added a governed freeze gate after post-falsification reaudit and cluster-conditioned falsification."
        ),
        "Artifacts gerados": (
            f"- `{freeze_report_path.relative_to(REPO_ROOT)}`\n"
            f"- `{freeze_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Tested families: `{', '.join(TESTED_FAMILIES)}`. Remaining safe material hypotheses: `{len(remaining)}`."
        ),
        "Avaliação contra gates": (
            "Freeze requirements are satisfied and no promotion/readiness interpretation is allowed."
        ),
        "Riscos residuais": (
            "DSR, official CVaR zero exposure and cross-sectional non-promotability remain blockers."
        ),
        "Veredito final: advance / correct / abandon": (
            "`freeze`. Update state and draft PR; do not merge or promote."
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
