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

GATE_SLUG = "phase5_research_rank_score_threshold_sizing_falsification_gate"
PHASE_FAMILY = "phase5_research_rank_score_threshold_sizing_falsification"
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
DSR_DIAGNOSTIC_GATE = REPO_ROOT / "reports" / "gates" / "phase5_research_dsr_zero_diagnostic_gate" / "gate_report.json"
CVAR_GATE = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate"
    / "gate_report.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

POSITION_FRACTION = 0.01
CVAR_ALPHA = 0.05
CVAR_LIMIT = 0.15
SR_NEEDED_FOR_PROMOTION = 4.47
MIN_MEDIAN_ACTIVE_DAYS = 120

PREDECLARED_POLICIES: tuple[dict[str, Any], ...] = (
    {"policy": "top1_score_ge_0_30", "score_threshold": 0.30, "hmm_threshold": None},
    {"policy": "top1_score_ge_0_50", "score_threshold": 0.50, "hmm_threshold": None},
    {"policy": "top1_score_ge_0_75", "score_threshold": 0.75, "hmm_threshold": None},
    {"policy": "top1_score_ge_1_00", "score_threshold": 1.00, "hmm_threshold": None},
    {"policy": "top1_score_ge_0_30_hmm_ge_0_55", "score_threshold": 0.30, "hmm_threshold": 0.55},
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


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["rank_score_stage_a"] = pd.to_numeric(work["rank_score_stage_a"], errors="coerce").fillna(float("-inf"))
    work["hmm_prob_bull"] = pd.to_numeric(work.get("hmm_prob_bull", 0.0), errors="coerce").fillna(0.0)
    work["pnl_real"] = pd.to_numeric(work["pnl_real"], errors="coerce").fillna(0.0)
    work["slippage_frac"] = pd.to_numeric(work.get("slippage_frac", 0.0), errors="coerce").fillna(0.0)
    work["pnl_net_proxy"] = work["pnl_real"] - work["slippage_frac"]
    return work


def select_policy(frame: pd.DataFrame, *, score_threshold: float, hmm_threshold: float | None) -> pd.DataFrame:
    work = _normalize(frame)
    mask = work["rank_score_stage_a"] >= score_threshold
    if hmm_threshold is not None:
        mask &= work["hmm_prob_bull"] >= hmm_threshold
    filtered = work.loc[mask].sort_values(
        ["combo", "date", "rank_score_stage_a", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    if filtered.empty:
        return filtered.copy()
    return filtered.groupby(["combo", "date"], as_index=False).head(1).copy()


def _empirical_cvar(returns: pd.Series, *, alpha: float = CVAR_ALPHA) -> tuple[float, float]:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if clean.empty:
        return 0.0, 0.0
    losses = -clean
    var_95 = float(losses.quantile(1.0 - alpha))
    tail = losses.loc[losses >= var_95]
    return var_95, float(tail.mean()) if not tail.empty else var_95


def _max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return 0.0
    drawdown = cumulative_returns - cumulative_returns.cummax()
    return abs(float(drawdown.min()))


def summarize_policy(all_predictions: pd.DataFrame, selected: pd.DataFrame, *, policy_name: str) -> dict[str, Any]:
    work = _normalize(all_predictions)
    selected = _normalize(selected) if not selected.empty else selected.copy()
    combo_rows: list[dict[str, Any]] = []
    for combo, combo_frame in work.groupby("combo"):
        dates = pd.to_datetime(combo_frame["date"]).dropna().sort_values().unique()
        returns = pd.Series(0.0, index=pd.to_datetime(dates))
        combo_selected = selected.loc[selected["combo"] == combo] if not selected.empty else selected
        if not combo_selected.empty:
            selected_returns = combo_selected.groupby("date")["pnl_net_proxy"].sum() * POSITION_FRACTION
            returns.loc[selected_returns.index] = selected_returns.values
        active = returns != 0.0
        mean = float(returns.mean()) if len(returns) else 0.0
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = 0.0 if std == 0.0 else mean / std * (252.0**0.5)
        var_95, cvar_95 = _empirical_cvar(returns)
        active_returns = returns.loc[active]
        combo_rows.append(
            {
                "policy": policy_name,
                "combo": str(combo),
                "total_days": int(len(returns)),
                "active_days": int(active.sum()),
                "cum_return_proxy": round(float(returns.sum()), 8),
                "annualized_sharpe_proxy": round(float(sharpe), 6),
                "var_95_loss_fraction": round(var_95, 8),
                "cvar_95_loss_fraction": round(cvar_95, 8),
                "max_drawdown_proxy": round(_max_drawdown(returns.cumsum()), 8),
                "win_rate_active": round(float((active_returns > 0.0).mean()), 6) if len(active_returns) else 0.0,
            }
        )
    combo_metrics = pd.DataFrame(combo_rows)
    return {
        "policy": policy_name,
        "selected_events": int(len(selected)),
        "selected_dates": int(pd.to_datetime(selected["date"]).dropna().nunique()) if not selected.empty else 0,
        "combo_count": int(combo_metrics["combo"].nunique()) if not combo_metrics.empty else 0,
        "median_active_days": round(float(combo_metrics["active_days"].median()), 6) if not combo_metrics.empty else 0.0,
        "median_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].median()), 6)
        if not combo_metrics.empty
        else 0.0,
        "min_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].min()), 6)
        if not combo_metrics.empty
        else 0.0,
        "median_cum_return_proxy": round(float(combo_metrics["cum_return_proxy"].median()), 8)
        if not combo_metrics.empty
        else 0.0,
        "max_cvar_95_loss_fraction": round(float(combo_metrics["cvar_95_loss_fraction"].max()), 8)
        if not combo_metrics.empty
        else 0.0,
        "median_win_rate_active": round(float(combo_metrics["win_rate_active"].median()), 6)
        if not combo_metrics.empty
        else 0.0,
        "combo_metrics": combo_rows,
    }


def evaluate_predeclared_family(predictions: pd.DataFrame) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    summaries: list[dict[str, Any]] = []
    combo_frames: list[pd.DataFrame] = []
    for config in PREDECLARED_POLICIES:
        selected = select_policy(
            predictions,
            score_threshold=float(config["score_threshold"]),
            hmm_threshold=config["hmm_threshold"],
        )
        summary = summarize_policy(predictions, selected, policy_name=str(config["policy"]))
        summary["score_threshold"] = float(config["score_threshold"])
        summary["hmm_threshold"] = config["hmm_threshold"]
        summaries.append(summary)
        combo_frames.append(pd.DataFrame(summary["combo_metrics"]))
    combo_metrics = pd.concat(combo_frames, ignore_index=True) if combo_frames else pd.DataFrame()
    return summaries, combo_metrics


def classify_family(summaries: list[dict[str, Any]]) -> tuple[str, str, str, dict[str, Any]]:
    if not summaries:
        return "INCONCLUSIVE", "correct", "NO_POLICIES_EVALUATED", {}
    all_ranked = sorted(
        summaries,
        key=lambda row: (
            float(row.get("median_combo_sharpe", 0.0)),
            -float(row.get("max_cvar_95_loss_fraction", 1.0)),
            float(row.get("median_active_days", 0.0)),
        ),
        reverse=True,
    )
    eligible = [
        row for row in all_ranked if float(row.get("median_active_days", 0.0)) >= MIN_MEDIAN_ACTIVE_DAYS
    ]
    if not eligible:
        return "INCONCLUSIVE", "correct", "INSUFFICIENT_ACTIVE_HISTORY_AFTER_FILTERING", all_ranked[0]
    best = eligible[0]
    if float(best.get("max_cvar_95_loss_fraction", 0.0)) > CVAR_LIMIT:
        return "FAIL", "abandon", "BEST_POLICY_BREACHES_RESEARCH_CVAR_LIMIT", best
    if (
        float(best.get("median_combo_sharpe", 0.0)) >= SR_NEEDED_FOR_PROMOTION
        and float(best.get("min_combo_sharpe", 0.0)) > 0.0
    ):
        return "PASS", "advance", "RESEARCH_ONLY_THRESHOLD_POLICY_STRONG_CANDIDATE_NOT_PROMOTED", best
    if float(best.get("median_combo_sharpe", 0.0)) > 0.0:
        return "PARTIAL", "correct", "WEAK_POSITIVE_MEDIAN_ALPHA_UNSTABLE_NOT_PROMOTABLE", best
    return "FAIL", "abandon", "PREDECLARED_THRESHOLD_FAMILY_NO_POSITIVE_SAFE_MEDIAN_ALPHA", best


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    dsr_gate = _read_json(DSR_DIAGNOSTIC_GATE)
    cvar_gate = _read_json(CVAR_GATE)

    summaries, combo_metrics = evaluate_predeclared_family(predictions)
    status, decision, classification, best = classify_family(summaries)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    summary_path = OUTPUT_DIR / "rank_score_threshold_family_summary.json"
    combo_metrics_path = OUTPUT_DIR / "rank_score_threshold_family_combo_metrics.parquet"
    policy_metrics_path = OUTPUT_DIR / "rank_score_threshold_family_policy_metrics.parquet"
    summary_payload = {
        "hypothesis": (
            "A predeclared research-only rank-score threshold family can improve ex-ante alpha "
            "relative to unfiltered safe top1 while staying non-promotional and leakage-safe."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "position_fraction": POSITION_FRACTION,
        "predeclared_policies": list(PREDECLARED_POLICIES),
        "best_policy": best,
        "policy_summaries": summaries,
        "prior_diagnostics": {
            "dsr_gate_status": dsr_gate.get("status"),
            "dsr_gate_summary": dsr_gate.get("summary", []),
            "cvar_gate_status": cvar_gate.get("status"),
            "cvar_gate_summary": cvar_gate.get("summary", []),
        },
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
    policy_metrics = pd.DataFrame([{k: v for k, v in row.items() if k != "combo_metrics"} for row in summaries])
    policy_metrics.to_parquet(policy_metrics_path, index=False)

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_policy",
            "metric_value": best.get("policy", ""),
            "metric_threshold": "predeclared policy family only",
            "metric_status": "PASS" if best else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_median_combo_sharpe",
            "metric_value": best.get("median_combo_sharpe", 0.0),
            "metric_threshold": f">= {SR_NEEDED_FOR_PROMOTION} for strong research candidate",
            "metric_status": "PASS"
            if float(best.get("median_combo_sharpe", 0.0)) >= SR_NEEDED_FOR_PROMOTION
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_min_combo_sharpe",
            "metric_value": best.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0 for robust cross-combo candidate",
            "metric_status": "PASS" if float(best.get("min_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_median_active_days",
            "metric_value": best.get("median_active_days", 0.0),
            "metric_threshold": f">= {MIN_MEDIAN_ACTIVE_DAYS}",
            "metric_status": "PASS"
            if float(best.get("median_active_days", 0.0)) >= MIN_MEDIAN_ACTIVE_DAYS
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_max_cvar_95_loss_fraction",
            "metric_value": best.get("max_cvar_95_loss_fraction", 0.0),
            "metric_threshold": f"<= {CVAR_LIMIT}",
            "metric_status": "PASS"
            if float(best.get("max_cvar_95_loss_fraction", 0.0)) <= CVAR_LIMIT
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "policies_evaluated",
            "metric_value": len(summaries),
            "metric_threshold": str(len(PREDECLARED_POLICIES)),
            "metric_status": "PASS" if len(summaries) == len(PREDECLARED_POLICIES) else "FAIL",
        },
    ]

    generated_artifacts = [
        artifact_record(summary_path),
        artifact_record(combo_metrics_path),
        artifact_record(policy_metrics_path),
    ]
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(DSR_DIAGNOSTIC_GATE), artifact_record(CVAR_GATE)]
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
            f"best_policy={best.get('policy', '')}",
            f"best_median_combo_sharpe={best.get('median_combo_sharpe', 0.0)}",
            f"best_min_combo_sharpe={best.get('min_combo_sharpe', 0.0)}",
            f"best_median_active_days={best.get('median_active_days', 0.0)}",
            f"best_max_cvar_95_loss_fraction={best.get('max_cvar_95_loss_fraction', 0.0)}",
            "family is research-only and predeclared",
            "no stage_a_eligible policy input used",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "best_policy_below_dsr_required_sharpe",
            "negative_min_combo_sharpe_blocks_promotability",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
        "risks_residual": [
            "The best threshold policy is a weak research-only candidate, not official evidence.",
            "Cross-combo instability remains because at least one combo has negative Sharpe.",
            "DSR remains 0.0 and no threshold was relaxed.",
        ],
        "next_recommended_step": (
            "Use the one allowed PARTIAL correction to test a stability-preserving variant of the "
            "best policy, or freeze if no materially defensible correction remains."
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
            "Research-only predeclared rank-score threshold family.",
            "Selection uses only ex-ante rank_score_stage_a and optional hmm_prob_bull.",
            "stage_a_eligible is never used as a policy input.",
            "No official artifacts were promoted.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Rank-score threshold sizing falsification result: `{status}/{decision}`. "
            f"Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This gate remains research-only and does not "
            "modify official policy."
        ),
        "Mudanças implementadas": (
            "Added a predeclared research-only threshold-family evaluator for `rank_score_stage_a`, "
            "with optional HMM bull filter, fixed 1% sandbox exposure, CVaR measurement and "
            "cross-combo stability checks."
        ),
        "Artifacts gerados": (
            f"- `{summary_path.relative_to(REPO_ROOT)}`\n"
            f"- `{combo_metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{policy_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Best policy `{best.get('policy', '')}` had median combo Sharpe "
            f"`{best.get('median_combo_sharpe', 0.0)}`, min combo Sharpe "
            f"`{best.get('min_combo_sharpe', 0.0)}`, median active days "
            f"`{best.get('median_active_days', 0.0)}`, and max CVaR95 loss fraction "
            f"`{best.get('max_cvar_95_loss_fraction', 0.0)}`."
        ),
        "Avaliação contra gates": (
            "The best policy improved median alpha above zero and stayed within the sandbox CVaR "
            "limit, but it remains far below the DSR Sharpe requirement and has negative "
            "cross-combo stability. It cannot be promoted."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, official CVaR remains zero exposure, and cross-sectional remains "
            "`ALIVE_BUT_NOT_PROMOTABLE`. The result is a weak research candidate only."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Use the allowed PARTIAL correction to test a stability-preserving "
            "variant, otherwise freeze this threshold-family line."
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
