#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
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

import phase5_research_rank_score_threshold_sizing_falsification as threshold_gate

GATE_SLUG = "phase5_research_rank_score_stability_correction_gate"
PHASE_FAMILY = "phase5_research_rank_score_stability_correction"
STAGE_A_PREDICTIONS = threshold_gate.STAGE_A_PREDICTIONS
THRESHOLD_GATE_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_threshold_sizing_falsification_gate"
    / "gate_report.json"
)
THRESHOLD_GATE_SUMMARY = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_threshold_sizing_falsification_gate"
    / "rank_score_threshold_family_summary.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

MIN_MEDIAN_ACTIVE_DAYS = threshold_gate.MIN_MEDIAN_ACTIVE_DAYS
CVAR_LIMIT = threshold_gate.CVAR_LIMIT
SR_NEEDED_FOR_PROMOTION = threshold_gate.SR_NEEDED_FOR_PROMOTION
MIN_MATERIAL_MIN_SHARPE_IMPROVEMENT = 0.50

PREDECLARED_CORRECTIONS: tuple[dict[str, Any], ...] = (
    {"policy": "score_0_50_hmm_0_70", "score_threshold": 0.50, "hmm_threshold": 0.70, "sigma_quantile_max": None},
    {"policy": "score_0_50_hmm_0_80", "score_threshold": 0.50, "hmm_threshold": 0.80, "sigma_quantile_max": None},
    {"policy": "score_0_50_hmm_0_90", "score_threshold": 0.50, "hmm_threshold": 0.90, "sigma_quantile_max": None},
    {"policy": "score_0_60", "score_threshold": 0.60, "hmm_threshold": None, "sigma_quantile_max": None},
    {"policy": "score_0_40_sigma_lte_q90", "score_threshold": 0.40, "hmm_threshold": None, "sigma_quantile_max": 0.90},
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_correction_policy(predictions: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = threshold_gate._normalize(predictions)
    mask = work["rank_score_stage_a"] >= float(config["score_threshold"])
    hmm_threshold = config.get("hmm_threshold")
    if hmm_threshold is not None:
        mask &= work["hmm_prob_bull"] >= float(hmm_threshold)
    sigma_quantile = config.get("sigma_quantile_max")
    if sigma_quantile is not None:
        sigma_cutoff = float(work["sigma_ewma"].quantile(float(sigma_quantile)))
        mask &= work["sigma_ewma"] <= sigma_cutoff
    filtered = work.loc[mask].sort_values(
        ["combo", "date", "rank_score_stage_a", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    if filtered.empty:
        return filtered.copy()
    return filtered.groupby(["combo", "date"], as_index=False).head(1).copy()


def evaluate_corrections(predictions: pd.DataFrame) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    combo_frames: list[pd.DataFrame] = []
    for config in PREDECLARED_CORRECTIONS:
        selected = select_correction_policy(predictions, config)
        summary = threshold_gate.summarize_policy(predictions, selected, policy_name=str(config["policy"]))
        summary["score_threshold"] = float(config["score_threshold"])
        summary["hmm_threshold"] = config.get("hmm_threshold")
        summary["sigma_quantile_max"] = config.get("sigma_quantile_max")
        summaries.append(summary)
        combo_frames.append(pd.DataFrame(summary["combo_metrics"]))
    combo_metrics = pd.concat(combo_frames, ignore_index=True) if combo_frames else pd.DataFrame()
    return summaries, combo_metrics


def classify_corrections(
    summaries: list[dict[str, Any]],
    *,
    baseline_median_sharpe: float,
    baseline_min_sharpe: float,
) -> tuple[str, str, str, dict[str, Any]]:
    eligible = [
        row
        for row in summaries
        if float(row.get("median_active_days", 0.0)) >= MIN_MEDIAN_ACTIVE_DAYS
        and float(row.get("max_cvar_95_loss_fraction", 0.0)) <= CVAR_LIMIT
    ]
    if not eligible:
        best_any = max(summaries, key=lambda row: float(row.get("median_combo_sharpe", 0.0)), default={})
        return "INCONCLUSIVE", "correct", "NO_CORRECTION_WITH_ENOUGH_ACTIVE_HISTORY_AND_CVAR_OK", best_any

    ranked = sorted(
        eligible,
        key=lambda row: (
            float(row.get("min_combo_sharpe", 0.0)),
            float(row.get("median_combo_sharpe", 0.0)),
        ),
        reverse=True,
    )
    best = ranked[0]
    best_median = float(best.get("median_combo_sharpe", 0.0))
    best_min = float(best.get("min_combo_sharpe", 0.0))
    min_improvement = best_min - baseline_min_sharpe

    if best_median >= SR_NEEDED_FOR_PROMOTION and best_min > 0.0:
        return "PASS", "advance", "STABILITY_CORRECTION_STRONG_RESEARCH_CANDIDATE_NOT_PROMOTED", best
    if best_median >= baseline_median_sharpe and min_improvement >= MIN_MATERIAL_MIN_SHARPE_IMPROVEMENT:
        return "PARTIAL", "correct", "MATERIAL_STABILITY_IMPROVEMENT_BUT_STILL_NOT_PROMOTABLE", best
    return "FAIL", "abandon", "STABILITY_CORRECTION_DID_NOT_CLEAR_NEGATIVE_COMBO_OR_DSR_GAP", best


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    threshold_gate_report = _read_json(THRESHOLD_GATE_REPORT)
    threshold_gate_summary = _read_json(THRESHOLD_GATE_SUMMARY)
    baseline = threshold_gate_summary.get("best_policy", {})
    baseline_median_sharpe = float(baseline.get("median_combo_sharpe", 0.0))
    baseline_min_sharpe = float(baseline.get("min_combo_sharpe", 0.0))

    summaries, combo_metrics = evaluate_corrections(predictions)
    status, decision, classification, best = classify_corrections(
        summaries,
        baseline_median_sharpe=baseline_median_sharpe,
        baseline_min_sharpe=baseline_min_sharpe,
    )

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    summary_path = OUTPUT_DIR / "rank_score_stability_correction_summary.json"
    combo_metrics_path = OUTPUT_DIR / "rank_score_stability_correction_combo_metrics.parquet"
    policy_metrics_path = OUTPUT_DIR / "rank_score_stability_correction_policy_metrics.parquet"
    min_sharpe_improvement = float(best.get("min_combo_sharpe", 0.0)) - baseline_min_sharpe if best else 0.0
    summary_payload = {
        "hypothesis": (
            "A predeclared ex-ante stability correction can repair the weak threshold-family candidate "
            "without using realized eligibility or choosing combos by realized performance."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "baseline_policy": baseline,
        "predeclared_corrections": list(PREDECLARED_CORRECTIONS),
        "best_correction": best,
        "min_combo_sharpe_improvement_vs_baseline": round(min_sharpe_improvement, 6),
        "policy_summaries": summaries,
        "prior_gate_summary": threshold_gate_report.get("summary", []),
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_stage_a_eligible_as_policy_input": False,
            "masks_dsr": False,
        },
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    combo_metrics.to_parquet(combo_metrics_path, index=False)
    pd.DataFrame([{k: v for k, v in row.items() if k != "combo_metrics"} for row in summaries]).to_parquet(
        policy_metrics_path, index=False
    )

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_policy",
            "metric_value": best.get("policy", ""),
            "metric_threshold": "predeclared correction family only",
            "metric_status": "PASS" if best else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "baseline_median_combo_sharpe",
            "metric_value": baseline_median_sharpe,
            "metric_threshold": "reference",
            "metric_status": "INCONCLUSIVE",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_median_combo_sharpe",
            "metric_value": best.get("median_combo_sharpe", 0.0),
            "metric_threshold": f">= {baseline_median_sharpe} and eventually >= {SR_NEEDED_FOR_PROMOTION}",
            "metric_status": "PASS"
            if float(best.get("median_combo_sharpe", 0.0)) >= baseline_median_sharpe
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "baseline_min_combo_sharpe",
            "metric_value": baseline_min_sharpe,
            "metric_threshold": "reference",
            "metric_status": "INCONCLUSIVE",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_min_combo_sharpe",
            "metric_value": best.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0 for stability correction success",
            "metric_status": "PASS" if float(best.get("min_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "min_combo_sharpe_improvement_vs_baseline",
            "metric_value": round(min_sharpe_improvement, 6),
            "metric_threshold": f">= {MIN_MATERIAL_MIN_SHARPE_IMPROVEMENT}",
            "metric_status": "PASS" if min_sharpe_improvement >= MIN_MATERIAL_MIN_SHARPE_IMPROVEMENT else "FAIL",
        },
    ]

    generated_artifacts = [
        artifact_record(summary_path),
        artifact_record(combo_metrics_path),
        artifact_record(policy_metrics_path),
    ]
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(THRESHOLD_GATE_REPORT), artifact_record(THRESHOLD_GATE_SUMMARY)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(summary_path), str(combo_metrics_path), str(policy_metrics_path)],
        "summary": [
            f"classification={classification}",
            f"best_correction_policy={best.get('policy', '')}",
            f"best_correction_median_combo_sharpe={best.get('median_combo_sharpe', 0.0)}",
            f"best_correction_min_combo_sharpe={best.get('min_combo_sharpe', 0.0)}",
            f"min_combo_sharpe_improvement_vs_baseline={round(min_sharpe_improvement, 6)}",
            f"best_correction_median_active_days={best.get('median_active_days', 0.0)}",
            f"best_correction_max_cvar_95_loss_fraction={best.get('max_cvar_95_loss_fraction', 0.0)}",
            "one allowed PARTIAL correction consumed for threshold-family line",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "threshold_family_stability_correction_failed",
            "best_correction_min_combo_sharpe_negative",
            "best_correction_below_required_dsr_sharpe",
            "dsr_honest_zero_blocks_promotion",
        ],
        "risks_residual": [
            "Predeclared stability guards did not clear negative cross-combo Sharpe.",
            "The threshold-family line should not be repeated under another name.",
            "Any further work needs a materially different hypothesis family.",
        ],
        "next_recommended_step": (
            "Abandon the rank-score threshold-family line. Continue only if a materially different "
            "research-only hypothesis remains within budget; otherwise freeze."
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
            "Research-only stability correction for prior PARTIAL threshold-family gate.",
            "This consumes the one allowed PARTIAL correction for the threshold-family line.",
            "No official artifacts were promoted.",
            "No realized eligibility fields are used as policy inputs.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Rank-score stability correction result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This correction remains research-only and does "
            "not change official policy."
        ),
        "Mudanças implementadas": (
            "Added a one-shot stability correction evaluator for the prior threshold-family PARTIAL, "
            "using only predeclared ex-ante guards."
        ),
        "Artifacts gerados": (
            f"- `{summary_path.relative_to(REPO_ROOT)}`\n"
            f"- `{combo_metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{policy_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Best correction `{best.get('policy', '')}` had median combo Sharpe "
            f"`{best.get('median_combo_sharpe', 0.0)}`, min combo Sharpe "
            f"`{best.get('min_combo_sharpe', 0.0)}`, and min-Sharpe improvement "
            f"`{round(min_sharpe_improvement, 6)}` versus baseline."
        ),
        "Avaliação contra gates": (
            "Correction success required removing negative min combo Sharpe or materially improving "
            "stability while preserving median alpha. The best correction did not meet that bar."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, cross-sectional remains not promotable, and the threshold-family line "
            "should be abandoned unless a materially different hypothesis is defined."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Abandon this threshold-family correction path; do not repeat it with a new name."
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
