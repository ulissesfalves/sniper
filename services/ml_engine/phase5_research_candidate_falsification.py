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

GATE_SLUG = "phase5_research_candidate_falsification_gate"
PHASE_FAMILY = "phase5_research_candidate_falsification"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
STABILITY_DIR = REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_stability_gate"
STABILITY_REPORT = STABILITY_DIR / "candidate_stability_report.json"
STABILITY_SCENARIOS = STABILITY_DIR / "candidate_stability_scenarios.parquet"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def build_falsification_evidence(predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    stability = pd.read_parquet(STABILITY_SCENARIOS)
    base = candidate.evaluate_candidate(predictions)
    null_control = candidate.evaluate_null_control(predictions)
    severe_cost = candidate.evaluate_candidate(predictions, extra_cost_per_position=0.005)

    temporal_rows = stability.loc[stability["scenario_type"] == "temporal_subperiod"].copy()
    cost20 = stability.loc[stability["scenario"] == "extra_cost_0.00200"].copy()
    universe_rows = stability.loc[stability["scenario_type"] == "universe_sensitivity"].copy()

    base_summary = base["summary"]
    null_summary = null_control["summary"]
    severe_cost_summary = severe_cost["summary"]
    governance = candidate.candidate_governance_checks()

    rows: list[dict[str, Any]] = []
    temporal_min = float(temporal_rows["min_combo_sharpe"].min()) if not temporal_rows.empty else 0.0
    temporal_pass = temporal_min > 0.0
    rows.append(
        {
            "test": "temporal_subperiod_min_sharpe",
            "test_type": "temporal_oos",
            "observed_value": round(temporal_min, 6),
            "threshold": "> 0.0 across temporal thirds",
            "passed": temporal_pass,
            "falsifier": not temporal_pass,
        }
    )

    cost20_min = float(cost20["min_combo_sharpe"].iloc[0]) if not cost20.empty else 0.0
    cost20_pass = cost20_min > 0.0
    rows.append(
        {
            "test": "extra_cost_20bps_min_sharpe",
            "test_type": "friction_stress",
            "observed_value": round(cost20_min, 6),
            "threshold": "> 0.0 under 0.002 extra cost per position",
            "passed": cost20_pass,
            "falsifier": not cost20_pass,
        }
    )

    severe_cost_min = float(severe_cost_summary.get("min_combo_sharpe") or 0.0)
    severe_cost_pass = severe_cost_min > 0.0
    rows.append(
        {
            "test": "extra_cost_50bps_min_sharpe",
            "test_type": "severe_friction_stress",
            "observed_value": round(severe_cost_min, 6),
            "threshold": "> 0.0 under 0.005 extra cost per position",
            "passed": severe_cost_pass,
            "falsifier": not severe_cost_pass,
        }
    )

    universe_min = float(universe_rows["median_combo_sharpe"].min()) if not universe_rows.empty else 0.0
    universe_pass = universe_min > 0.0
    rows.append(
        {
            "test": "universe_perturbation_min_median_sharpe",
            "test_type": "universe_reduction",
            "observed_value": round(universe_min, 6),
            "threshold": "> 0.0 median Sharpe under universe perturbations",
            "passed": universe_pass,
            "falsifier": not universe_pass,
        }
    )

    null_median = float(null_summary.get("median_combo_sharpe") or 0.0)
    base_median = float(base_summary.get("median_combo_sharpe") or 0.0)
    null_pass = base_median > null_median
    rows.append(
        {
            "test": "null_symbol_hash_control",
            "test_type": "baseline_control",
            "observed_value": round(base_median - null_median, 6),
            "threshold": "candidate median Sharpe must exceed null control median Sharpe",
            "passed": null_pass,
            "falsifier": not null_pass,
            "candidate_median_sharpe": round(base_median, 6),
            "null_median_sharpe": round(null_median, 6),
        }
    )

    leakage_pass = not bool(governance["uses_realized_variable_as_ex_ante_rule"])
    rows.append(
        {
            "test": "leakage_control",
            "test_type": "ex_ante_validity",
            "observed_value": ",".join(governance["forbidden_selection_columns_used"]) or "none",
            "threshold": "no forbidden realized columns in selection rule",
            "passed": leakage_pass,
            "falsifier": not leakage_pass,
        }
    )

    turnover = float(base_summary.get("median_turnover_fraction") or 0.0)
    turnover_pass = turnover <= 0.05
    rows.append(
        {
            "test": "turnover_stress",
            "test_type": "implementation_risk",
            "observed_value": round(turnover, 8),
            "threshold": "<= 0.05 median turnover fraction",
            "passed": turnover_pass,
            "falsifier": not turnover_pass,
        }
    )

    drawdown = float(base_summary.get("max_drawdown_proxy") or 0.0)
    drawdown_pass = drawdown <= 0.10
    rows.append(
        {
            "test": "drawdown_stress",
            "test_type": "risk_stress",
            "observed_value": round(drawdown, 8),
            "threshold": "<= 0.10 max drawdown proxy",
            "passed": drawdown_pass,
            "falsifier": not drawdown_pass,
        }
    )

    evidence = {
        "base_summary": base_summary,
        "null_summary": null_summary,
        "severe_cost_summary": severe_cost_summary,
        "governance": governance,
        "hard_falsifiers": [
            row["test"]
            for row in rows
            if row["falsifier"] and row["test"] in {"temporal_subperiod_min_sharpe", "extra_cost_20bps_min_sharpe"}
        ],
    }
    return pd.DataFrame(candidate.json_safe(rows)), candidate.json_safe(evidence)


def classify_falsification(evidence: dict[str, Any]) -> tuple[str, str, str]:
    hard_falsifiers = evidence.get("hard_falsifiers", [])
    if hard_falsifiers:
        return "FAIL", "abandon", "RESEARCH_CANDIDATE_FALSIFIED_BY_TEMPORAL_OR_COST_STRESS"
    return "PASS", "advance", "RESEARCH_CANDIDATE_SURVIVED_FALSIFICATION_NOT_PROMOTABLE"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = candidate.load_predictions()
    falsification_frame, evidence = build_falsification_evidence(predictions)
    status, decision, classification = classify_falsification(evidence)
    git_context = candidate.current_git_context()

    metrics_path = OUTPUT_DIR / "candidate_falsification_metrics.parquet"
    report_path = OUTPUT_DIR / "candidate_falsification_report.json"
    parquet_frame = falsification_frame.copy()
    parquet_frame["observed_value"] = parquet_frame["observed_value"].map(str)
    parquet_frame.to_parquet(metrics_path, index=False)

    hard_falsifiers = evidence["hard_falsifiers"]
    payload = {
        "hypothesis": (
            "The surviving candidate should withstand autonomous falsification through temporal splits, "
            "cost stress, universe reductions, null controls, turnover, drawdown and leakage checks."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "candidate": candidate.CANDIDATE_POLICY,
        "falsification_tests": falsification_frame.to_dict(orient="records"),
        "evidence": evidence,
        "hard_falsifiers": hard_falsifiers,
        "governance": evidence["governance"],
        "blockers": [
            "candidate_falsified_by_temporal_or_cost_stress" if hard_falsifiers else "candidate_alive_not_promotable",
            "dsr_honest_zero_blocks_promotion",
            "short_exposure_research_sandbox_only",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
    }
    candidate.write_json(report_path, payload)

    gate_metrics = [
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 to survive falsification", len(hard_falsifiers) == 0),
        _metric("temporal_subperiod_min_sharpe", falsification_frame.loc[falsification_frame["test"] == "temporal_subperiod_min_sharpe", "observed_value"].iloc[0], "> 0", "temporal_subperiod_min_sharpe" not in hard_falsifiers),
        _metric("extra_cost_20bps_min_sharpe", falsification_frame.loc[falsification_frame["test"] == "extra_cost_20bps_min_sharpe", "observed_value"].iloc[0], "> 0", "extra_cost_20bps_min_sharpe" not in hard_falsifiers),
        _metric("leakage_control", evidence["governance"]["uses_realized_variable_as_ex_ante_rule"], "false", not evidence["governance"]["uses_realized_variable_as_ex_ante_rule"]),
        _metric("official_promotion_allowed", False, "false", True),
    ]

    source_artifacts = [
        artifact_record(candidate.STAGE_A_PREDICTIONS),
        artifact_record(STABILITY_REPORT),
        artifact_record(STABILITY_SCENARIOS),
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
            f"candidate_policy={candidate.CANDIDATE_POLICY}",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            f"candidate_median_combo_sharpe={evidence['base_summary'].get('median_combo_sharpe')}",
            f"candidate_min_combo_sharpe={evidence['base_summary'].get('min_combo_sharpe')}",
            "candidate remains research/sandbox only",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "Falsification evidence invalidates the current survivor as a robust research candidate."
            if hard_falsifiers
            else "The candidate remains research-only and still cannot be promoted.",
            "DSR remains 0.0 and official CVaR remains zero exposure.",
            "Short exposure is sandbox-only and cannot be treated as official support.",
        ],
        "next_recommended_step": (
            "Run phase5_research_candidate_decision_gate to record the candidate decision and update state."
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
            "Autonomous falsification gate for the research-only candidate.",
            "FAIL/abandon means the candidate is abandoned, not that official promotion was attempted.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Candidate falsification result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Candidate remains research-only."
        ),
        "MudanÃƒÂ§as implementadas": (
            "Added falsification checks for temporal splits, cost stress, universe perturbation, null control, "
            "leakage, turnover and drawdown."
        ),
        "Artifacts gerados": (
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Hard falsifiers: `{', '.join(hard_falsifiers) or 'none'}`."
        ),
        "AvaliaÃƒÂ§ÃƒÂ£o contra gates": (
            "Temporal or cost stress falsifies the candidate if any hard falsifier is present. "
            "No official promotion is allowed either way."
        ),
        "Riscos residuais": (
            "DSR=0.0, official zero-exposure CVaR and sandbox-only short exposure remain blockers."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue to the decision gate."
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
