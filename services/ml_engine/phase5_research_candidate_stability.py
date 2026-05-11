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

GATE_SLUG = "phase5_research_candidate_stability_gate"
PHASE_FAMILY = "phase5_research_candidate_stability"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
REAUDIT_REPORT = REPO_ROOT / "reports" / "gates" / "phase5_research_candidate_global_reaudit_gate" / "gate_report.json"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def build_stability_scenarios(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    base = candidate.evaluate_candidate(predictions)
    scenario_rows: list[dict[str, Any]] = []

    base_summary = base["summary"]
    base_flags = candidate.status_from_summary(base_summary)
    scenario_rows.append(
        candidate.scenario_record(
            "base_candidate",
            "base",
            base_summary,
            threshold="median/min Sharpe > 0, active days >= 120, CVaR <= 0.15",
            passed=(
                base_flags["positive_median_sharpe"]
                and base_flags["positive_min_sharpe"]
                and base_flags["active_days_sufficient"]
                and base_flags["cvar_within_research_limit"]
            ),
        )
    )

    for top_k in (1, 2, 3, 5):
        for p_threshold in (0.55, 0.60, 0.65):
            for hmm_threshold in (0.50, 0.70, 0.90):
                config = candidate.candidate_config(
                    top_k=top_k,
                    p_bma_threshold=p_threshold,
                    hmm_threshold=hmm_threshold,
                    policy=f"short_high_p_bma_k{top_k}_p{int(p_threshold * 100)}_h{int(hmm_threshold * 100)}",
                )
                evaluation = candidate.evaluate_candidate(predictions, config)
                summary = evaluation["summary"]
                flags = candidate.status_from_summary(summary)
                scenario_rows.append(
                    candidate.scenario_record(
                        config["policy"],
                        "parameter_sensitivity",
                        summary,
                        threshold="median Sharpe > 0, min Sharpe > 0, active days >= 120",
                        passed=(
                            flags["positive_median_sharpe"]
                            and flags["positive_min_sharpe"]
                            and flags["active_days_sufficient"]
                        ),
                        details={
                            "top_k": top_k,
                            "p_bma_threshold": p_threshold,
                            "hmm_threshold": hmm_threshold,
                        },
                    )
                )

    for extra_cost in (0.00025, 0.00050, 0.00100, 0.00200):
        evaluation = candidate.evaluate_candidate(predictions, extra_cost_per_position=extra_cost)
        summary = evaluation["summary"]
        flags = candidate.status_from_summary(summary)
        scenario_rows.append(
            candidate.scenario_record(
                f"extra_cost_{extra_cost:.5f}",
                "friction_sensitivity",
                summary,
                threshold="median Sharpe > 0, min Sharpe > 0 under added cost",
                passed=flags["positive_median_sharpe"] and flags["positive_min_sharpe"],
                details={"extra_cost_per_position": extra_cost},
            )
        )

    for universe_filter in ("drop_high_sigma_q80", "symbol_hash_even", "symbol_hash_odd"):
        evaluation = candidate.evaluate_candidate(predictions, universe_filter=universe_filter)
        summary = evaluation["summary"]
        flags = candidate.status_from_summary(summary)
        scenario_rows.append(
            candidate.scenario_record(
                universe_filter,
                "universe_sensitivity",
                summary,
                threshold="median Sharpe > 0, active days >= 120",
                passed=flags["positive_median_sharpe"] and flags["active_days_sufficient"],
                details={"universe_filter": universe_filter},
            )
        )

    positions = base["positions"]
    for label, mask in (
        ("hmm_band_0_70_to_0_80", positions["hmm_prob_bull"] < 0.80),
        ("hmm_band_ge_0_80", positions["hmm_prob_bull"] >= 0.80),
    ):
        regime_positions = positions.loc[mask].copy()
        if regime_positions.empty:
            continue
        daily, trades = candidate.build_daily_returns_with_cost(predictions, regime_positions)
        combo_metrics, policy_metrics = candidate.summarize_daily(daily, trades)
        metrics = pd.concat(
            [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
            ignore_index=True,
            sort=False,
        )
        summary = candidate.policy_summary(metrics)
        flags = candidate.status_from_summary(summary)
        scenario_rows.append(
            candidate.scenario_record(
                label,
                "regime_dependency",
                summary,
                threshold="median Sharpe > 0 and active days >= 120",
                passed=flags["positive_median_sharpe"] and flags["active_days_sufficient"],
            )
        )

    subperiods = candidate.temporal_subperiod_summaries(base["daily"], periods=3)
    for _, row in subperiods.iterrows():
        summary = row.to_dict()
        flags = candidate.status_from_summary(summary)
        scenario_rows.append(
            candidate.scenario_record(
                str(summary["scenario"]),
                "temporal_subperiod",
                summary,
                threshold="median Sharpe > 0, min Sharpe > 0, active days >= 80",
                passed=(
                    flags["positive_median_sharpe"]
                    and flags["positive_min_sharpe"]
                    and float(summary.get("median_active_days") or 0.0) >= 80
                ),
                details={"period_index": int(summary["period_index"])},
            )
        )

    scenario_frame = pd.DataFrame(candidate.json_safe(scenario_rows))
    diagnostics = {
        "scenario_count": int(len(scenario_frame)),
        "failed_scenarios": scenario_frame.loc[~scenario_frame["passed"].astype(bool), "scenario"].tolist(),
        "parameter_scenarios": int((scenario_frame["scenario_type"] == "parameter_sensitivity").sum()),
        "parameter_pass_rate": round(
            float(scenario_frame.loc[scenario_frame["scenario_type"] == "parameter_sensitivity", "passed"].mean()),
            6,
        ),
        "base_summary": base_summary,
    }
    return scenario_frame, subperiods, candidate.json_safe(diagnostics)


def classify_stability(diagnostics: dict[str, Any]) -> tuple[str, str, str]:
    failed = set(diagnostics.get("failed_scenarios", []))
    if "base_candidate" in failed:
        return "FAIL", "abandon", "CANDIDATE_BASE_METRICS_NO_LONGER_STABLE"
    if failed:
        return "PARTIAL", "correct", "CANDIDATE_STABILITY_PARTIAL_TEMPORAL_OR_STRESS_FRAGILITY"
    return "PASS", "advance", "CANDIDATE_STABILITY_PASS_RESEARCH_ONLY_NOT_PROMOTABLE"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = candidate.load_predictions()
    scenario_frame, subperiods, diagnostics = build_stability_scenarios(predictions)
    status, decision, classification = classify_stability(diagnostics)
    git_context = candidate.current_git_context()

    scenario_path = OUTPUT_DIR / "candidate_stability_scenarios.parquet"
    subperiod_path = OUTPUT_DIR / "candidate_stability_subperiods.parquet"
    report_path = OUTPUT_DIR / "candidate_stability_report.json"
    scenario_frame.to_parquet(scenario_path, index=False)
    subperiods.to_parquet(subperiod_path, index=False)

    payload = {
        "hypothesis": (
            "The surviving research-only short-high candidate should remain stable across subperiods, "
            "parameter neighborhoods, cost stress, regime slices and universe perturbations before deepening."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "candidate": candidate.CANDIDATE_POLICY,
        "diagnostics": diagnostics,
        "failed_scenarios": diagnostics["failed_scenarios"],
        "scenario_metrics": scenario_frame.to_dict(orient="records"),
        "governance": candidate.candidate_governance_checks(),
        "blockers": [
            "candidate_stability_fragility" if diagnostics["failed_scenarios"] else "candidate_requires_falsification_gate",
            "dsr_honest_zero_blocks_promotion",
            "short_exposure_research_sandbox_only",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
    }
    candidate.write_json(report_path, payload)

    failed_count = int((~scenario_frame["passed"].astype(bool)).sum())
    parameter_rows = scenario_frame.loc[scenario_frame["scenario_type"] == "parameter_sensitivity"]
    gate_metrics = [
        _metric("scenario_count", len(scenario_frame), ">= 10", len(scenario_frame) >= 10),
        _metric("failed_scenario_count", failed_count, "0 for PASS; >0 means PARTIAL/falsification needed", failed_count == 0),
        _metric("parameter_pass_rate", diagnostics["parameter_pass_rate"], ">= 0.50", diagnostics["parameter_pass_rate"] >= 0.50),
        _metric(
            "base_candidate_min_combo_sharpe",
            diagnostics["base_summary"].get("min_combo_sharpe"),
            "> 0",
            float(diagnostics["base_summary"].get("min_combo_sharpe") or 0.0) > 0.0,
        ),
        _metric(
            "worst_temporal_min_sharpe",
            scenario_frame.loc[scenario_frame["scenario_type"] == "temporal_subperiod", "min_combo_sharpe"].min(),
            "> 0 for full stability PASS",
            bool(
                (
                    scenario_frame.loc[
                        scenario_frame["scenario_type"] == "temporal_subperiod",
                        "min_combo_sharpe",
                    ]
                    > 0.0
                ).all()
            ),
        ),
        _metric("parameter_scenario_count", len(parameter_rows), ">= 20", len(parameter_rows) >= 20),
        _metric("official_promotion_allowed", False, "false", True),
    ]

    source_artifacts = [
        artifact_record(candidate.STAGE_A_PREDICTIONS),
        artifact_record(REAUDIT_REPORT),
    ]
    generated_artifacts = [
        artifact_record(scenario_path),
        artifact_record(subperiod_path),
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
        "research_artifacts_generated": [str(scenario_path), str(subperiod_path), str(report_path)],
        "summary": [
            f"classification={classification}",
            f"candidate_policy={candidate.CANDIDATE_POLICY}",
            f"scenario_count={len(scenario_frame)}",
            f"failed_scenario_count={failed_count}",
            f"failed_scenarios={','.join(diagnostics['failed_scenarios'])}",
            f"parameter_pass_rate={diagnostics['parameter_pass_rate']}",
            "candidate remains research/sandbox only",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": payload["blockers"],
        "risks_residual": [
            "Temporal and stress fragility must be treated as research falsification evidence, not as promotion evidence.",
            "Short exposure remains sandbox-only.",
            "DSR=0.0 and official zero-exposure CVaR remain blockers.",
        ],
        "next_recommended_step": (
            "Run phase5_research_candidate_falsification_gate to decide whether the fragility falsifies the candidate."
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
            "Autonomous stability gate for the research-only candidate.",
            "This gate does not alter official code or promotion status.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Candidate stability result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Candidate remains research-only."
        ),
        "MudanÃƒÂ§as implementadas": (
            "Added candidate stability sweeps for temporal subperiods, k/p/hmm sensitivity, friction, "
            "regime dependency and universe perturbations."
        ),
        "Artifacts gerados": (
            f"- `{scenario_path.relative_to(REPO_ROOT)}`\n"
            f"- `{subperiod_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Ran `{len(scenario_frame)}` scenarios. Failed scenarios: "
            f"`{', '.join(diagnostics['failed_scenarios']) or 'none'}`."
        ),
        "AvaliaÃƒÂ§ÃƒÂ£o contra gates": (
            "The base candidate remains valid, but failed stability scenarios require explicit falsification."
        ),
        "Riscos residuais": (
            "Fragility under time/cost/regime stress can invalidate the research candidate. No official promotion."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue to candidate falsification."
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
