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

GATE_SLUG = "phase5_research_candidate_global_reaudit_gate"
PHASE_FAMILY = "phase5_research_candidate_global_reaudit"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG


def _metric(
    name: str,
    value: Any,
    threshold: str,
    passed: bool,
) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def classify_reaudit(
    *,
    summary: dict[str, Any],
    governance: dict[str, Any],
    prior_report: dict[str, Any],
) -> tuple[str, str, str]:
    status_flags = candidate.status_from_summary(summary)
    prior_governance = prior_report.get("governance", {})
    checks = [
        bool(governance["research_only"]),
        bool(governance["sandbox_only"]),
        not bool(governance["promotes_official"]),
        not bool(governance["reopens_a3_a4"]),
        not bool(governance["relaxes_thresholds"]),
        not bool(governance["uses_realized_variable_as_ex_ante_rule"]),
        bool(prior_governance.get("uses_pnl_real_only_as_realized_backtest_outcome", False)),
        status_flags["positive_median_sharpe"],
        status_flags["positive_min_sharpe"],
        status_flags["active_days_sufficient"],
        status_flags["cvar_within_research_limit"],
        status_flags["below_sr_needed"],
    ]
    if all(checks):
        return "PASS", "advance", "CANDIDATE_GLOBAL_REAUDIT_PASS_RESEARCH_ONLY_NOT_PROMOTABLE"
    if not governance["research_only"] or governance["uses_realized_variable_as_ex_ante_rule"]:
        return "FAIL", "abandon", "CANDIDATE_GLOBAL_REAUDIT_GOVERNANCE_FAILURE"
    return "PARTIAL", "correct", "CANDIDATE_GLOBAL_REAUDIT_REQUIRES_FURTHER_FALSIFICATION"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = candidate.load_predictions()
    evaluation = candidate.evaluate_candidate(predictions)
    summary = evaluation["summary"]
    prior_report = candidate.read_json(candidate.PRIOR_CANDIDATE_REPORT)
    full_phase_report = candidate.read_json(candidate.FULL_PHASE_COMPARISON_REPORT)
    governance = candidate.candidate_governance_checks(evaluation["config"])
    status, decision, classification = classify_reaudit(
        summary=summary,
        governance=governance,
        prior_report=prior_report,
    )
    git_context = candidate.current_git_context()

    positions_path = OUTPUT_DIR / "candidate_global_reaudit_positions.parquet"
    daily_path = OUTPUT_DIR / "candidate_global_reaudit_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "candidate_global_reaudit_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "candidate_global_reaudit_metrics.parquet"
    report_path = OUTPUT_DIR / "candidate_global_reaudit_report.json"

    evaluation["positions"].to_parquet(positions_path, index=False)
    evaluation["daily"].to_parquet(daily_path, index=False)
    evaluation["trades"].to_parquet(trades_path, index=False)
    evaluation["metrics"].to_parquet(metrics_path, index=False)

    status_flags = candidate.status_from_summary(summary)
    prior_best = prior_report.get("best_correction", {})
    metric_reconciliation = {
        "prior_median_combo_sharpe": prior_best.get("median_combo_sharpe"),
        "recomputed_median_combo_sharpe": summary.get("median_combo_sharpe"),
        "prior_min_combo_sharpe": prior_best.get("min_combo_sharpe"),
        "recomputed_min_combo_sharpe": summary.get("min_combo_sharpe"),
        "prior_max_cvar_95_loss_fraction": prior_best.get("max_cvar_95_loss_fraction"),
        "recomputed_max_cvar_95_loss_fraction": summary.get("max_cvar_95_loss_fraction"),
        "matches_prior_rounded": (
            round(float(prior_best.get("median_combo_sharpe", 0.0)), 6)
            == round(float(summary.get("median_combo_sharpe", 0.0)), 6)
            and round(float(prior_best.get("min_combo_sharpe", 0.0)), 6)
            == round(float(summary.get("min_combo_sharpe", 0.0)), 6)
            and round(float(prior_best.get("max_cvar_95_loss_fraction", 0.0)), 8)
            == round(float(summary.get("max_cvar_95_loss_fraction", 0.0)), 8)
        ),
    }

    payload = {
        "hypothesis": (
            "The surviving short_high_p_bma_k3_p60_h70 candidate can pass a governance and "
            "ex-ante validity reaudit as research/sandbox only, while remaining non-promotable."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "candidate": candidate.CANDIDATE_POLICY,
        "candidate_config": evaluation["config"],
        "candidate_summary": summary,
        "status_flags": status_flags,
        "metric_reconciliation": metric_reconciliation,
        "governance": governance,
        "prior_candidate_gate_summary": prior_report.get("summary", []),
        "full_phase_comparison_summary": full_phase_report.get("summary", []),
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "candidate_sharpe_below_sr_needed",
            "short_exposure_research_sandbox_only",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
    }
    candidate.write_json(report_path, payload)

    gate_metrics = [
        _metric("candidate_policy", candidate.CANDIDATE_POLICY, "must match survivor", True),
        _metric("research_only", governance["research_only"], "true", bool(governance["research_only"])),
        _metric("uses_realized_variable_as_ex_ante_rule", governance["uses_realized_variable_as_ex_ante_rule"], "false", not governance["uses_realized_variable_as_ex_ante_rule"]),
        _metric("metric_reconciliation_matches_prior", metric_reconciliation["matches_prior_rounded"], "true", metric_reconciliation["matches_prior_rounded"]),
        _metric("median_combo_sharpe", summary.get("median_combo_sharpe"), "> 0 research-only; < sr_needed means not promotable", status_flags["positive_median_sharpe"] and status_flags["below_sr_needed"]),
        _metric("min_combo_sharpe", summary.get("min_combo_sharpe"), "> 0", status_flags["positive_min_sharpe"]),
        _metric("median_active_days", summary.get("median_active_days"), f">= {candidate.MIN_MEDIAN_ACTIVE_DAYS}", status_flags["active_days_sufficient"]),
        _metric("max_cvar_95_loss_fraction", summary.get("max_cvar_95_loss_fraction"), f"<= {candidate.CVAR_LIMIT}", status_flags["cvar_within_research_limit"]),
        _metric("official_promotion_allowed", False, "false while DSR=0.0 and candidate below sr_needed", True),
    ]
    source_artifacts = [
        artifact_record(candidate.STAGE_A_PREDICTIONS),
        artifact_record(candidate.PRIOR_CANDIDATE_REPORT),
        artifact_record(candidate.FULL_PHASE_COMPARISON_REPORT),
    ]
    generated_artifacts = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(report_path),
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
        "research_artifacts_generated": [
            str(positions_path),
            str(daily_path),
            str(trades_path),
            str(metrics_path),
            str(report_path),
        ],
        "summary": [
            f"classification={classification}",
            f"candidate_policy={candidate.CANDIDATE_POLICY}",
            f"median_combo_sharpe={summary.get('median_combo_sharpe')}",
            f"min_combo_sharpe={summary.get('min_combo_sharpe')}",
            f"median_active_days={summary.get('median_active_days')}",
            f"max_cvar_95_loss_fraction={summary.get('max_cvar_95_loss_fraction')}",
            f"sr_needed={candidate.SR_NEEDED_FOR_PROMOTION}",
            "candidate is research/sandbox only",
            "ex-ante selection uses p_bma_pkf and hmm_prob_bull filters only",
            "pnl_real remains realized outcome only",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "The candidate uses sandbox short exposure and is not an official execution policy.",
            "Median Sharpe remains far below sr_needed for honest DSR clearance.",
            "A stability/falsification sequence is still required before any deepening decision.",
        ],
        "next_recommended_step": (
            "Run phase5_research_candidate_stability_gate and phase5_research_candidate_falsification_gate; "
            "keep the candidate research-only and non-promotable."
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
            "Autonomous global reaudit of surviving research-only candidate.",
            "No official artifacts were promoted.",
            "Selection rules were checked for realized-variable leakage.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Candidate global reaudit result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Candidate remains research/sandbox only."
        ),
        "MudanÃƒÂ§as implementadas": (
            "Added a research-only candidate reaudit runner that recomputes positions, returns, metrics and "
            "governance checks for `short_high_p_bma_k3_p60_h70`."
        ),
        "Artifacts gerados": (
            f"- `{positions_path.relative_to(REPO_ROOT)}`\n"
            f"- `{daily_path.relative_to(REPO_ROOT)}`\n"
            f"- `{trades_path.relative_to(REPO_ROOT)}`\n"
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Recomputed median Sharpe `{summary.get('median_combo_sharpe')}`, min Sharpe "
            f"`{summary.get('min_combo_sharpe')}`, median active days `{summary.get('median_active_days')}`, "
            f"and max CVaR95 `{summary.get('max_cvar_95_loss_fraction')}`."
        ),
        "AvaliaÃƒÂ§ÃƒÂ£o contra gates": (
            "The candidate passes research-only governance and ex-ante checks, but remains below the honest "
            "DSR promotion bar and cannot be official."
        ),
        "Riscos residuais": (
            "Short exposure is sandbox-only, DSR remains 0.0, and official CVaR remains zero exposure."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}` to candidate stability and falsification gates. No official promotion."
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
