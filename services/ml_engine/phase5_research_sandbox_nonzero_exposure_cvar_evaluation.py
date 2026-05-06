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

GATE_SLUG = "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate"
PHASE_FAMILY = "phase5_research_sandbox_nonzero_exposure_cvar_evaluation"
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
OFFICIAL_SNAPSHOT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_execution_snapshot.parquet"
OFFICIAL_REPORT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_report_v4.json"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

POSITION_FRACTION = 0.01
CVAR_ALPHA = 0.05
CVAR_LIMIT = 0.15
MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR = 120


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


def _normalize_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["rank_score_stage_a"] = pd.to_numeric(work["rank_score_stage_a"], errors="coerce").fillna(float("-inf"))
    work["pnl_real"] = pd.to_numeric(work["pnl_real"], errors="coerce").fillna(0.0)
    if "slippage_frac" in work.columns:
        work["slippage_frac"] = pd.to_numeric(work["slippage_frac"], errors="coerce").fillna(0.0)
    else:
        work["slippage_frac"] = 0.0
    work["pnl_net_proxy"] = work["pnl_real"] - work["slippage_frac"]
    return work


def select_safe_top1_by_score(frame: pd.DataFrame) -> pd.DataFrame:
    """Research/sandbox policy: top rank per combo/date, without realized eligibility."""
    work = _normalize_predictions(frame)
    ranked = work.sort_values(
        ["combo", "date", "rank_score_stage_a", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    return ranked.groupby(["combo", "date"], as_index=False).head(1).copy()


def build_daily_policy_returns(
    all_predictions: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    position_fraction: float = POSITION_FRACTION,
) -> pd.DataFrame:
    work = _normalize_predictions(all_predictions)
    selected = _normalize_predictions(selected) if not selected.empty else selected.copy()
    rows: list[dict[str, Any]] = []
    for combo, combo_frame in work.groupby("combo"):
        dates = pd.to_datetime(combo_frame["date"]).dropna().sort_values().unique()
        returns = pd.Series(0.0, index=pd.to_datetime(dates))
        exposure = pd.Series(0.0, index=pd.to_datetime(dates))
        combo_selected = selected.loc[selected["combo"] == combo] if not selected.empty else selected
        if not combo_selected.empty:
            selected_returns = combo_selected.groupby("date")["pnl_net_proxy"].sum() * position_fraction
            returns.loc[selected_returns.index] = selected_returns.values
            exposure.loc[selected_returns.index] = position_fraction
        for date_value in returns.index:
            rows.append(
                {
                    "combo": str(combo),
                    "date": date_value,
                    "daily_return_proxy": float(returns.loc[date_value]),
                    "exposure_fraction": float(exposure.loc[date_value]),
                }
            )
    return pd.DataFrame(rows)


def empirical_var_cvar(returns: pd.Series, *, alpha: float = CVAR_ALPHA) -> tuple[float, float, int]:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if clean.empty:
        return 0.0, 0.0, 0
    losses = -clean
    var_level = 1.0 - alpha
    value_at_risk = float(losses.quantile(var_level))
    tail = losses.loc[losses >= value_at_risk]
    conditional_var = float(tail.mean()) if not tail.empty else value_at_risk
    return value_at_risk, conditional_var, int(len(tail))


def _max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return 0.0
    drawdown = cumulative_returns - cumulative_returns.cummax()
    return abs(float(drawdown.min()))


def summarize_research_cvar(daily: pd.DataFrame) -> dict[str, Any]:
    combo_rows: list[dict[str, Any]] = []
    for combo, combo_daily in daily.groupby("combo"):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        exposure = pd.to_numeric(combo_daily["exposure_fraction"], errors="coerce").fillna(0.0)
        active = exposure > 0.0
        mean = float(returns.mean()) if len(returns) else 0.0
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = 0.0 if std == 0.0 else mean / std * (252.0**0.5)
        value_at_risk, conditional_var, tail_count = empirical_var_cvar(returns)
        combo_rows.append(
            {
                "combo": str(combo),
                "total_days": int(len(returns)),
                "active_days": int(active.sum()),
                "active_ratio": round(float(active.mean()), 6) if len(active) else 0.0,
                "mean_exposure_fraction": round(float(exposure.mean()), 8) if len(exposure) else 0.0,
                "max_exposure_fraction": round(float(exposure.max()), 8) if len(exposure) else 0.0,
                "cum_return_proxy": round(float(returns.sum()), 8),
                "annualized_sharpe_proxy": round(float(sharpe), 6),
                "var_95_loss_fraction": round(float(value_at_risk), 8),
                "cvar_95_loss_fraction": round(float(conditional_var), 8),
                "tail_count": tail_count,
                "max_drawdown_proxy": round(_max_drawdown(returns.cumsum()), 8),
                "cvar_within_limit": bool(conditional_var <= CVAR_LIMIT),
            }
        )
    combo_metrics = pd.DataFrame(combo_rows)
    if combo_metrics.empty:
        return {
            "combo_count": 0,
            "total_combo_days": 0,
            "active_combo_days": 0,
            "median_cvar_95_loss_fraction": 0.0,
            "max_cvar_95_loss_fraction": 0.0,
            "median_combo_sharpe": 0.0,
            "all_combos_cvar_within_limit": False,
            "combo_metrics": [],
        }
    return {
        "combo_count": int(combo_metrics["combo"].nunique()),
        "total_combo_days": int(combo_metrics["total_days"].sum()),
        "active_combo_days": int(combo_metrics["active_days"].sum()),
        "median_active_days": round(float(combo_metrics["active_days"].median()), 6),
        "median_cvar_95_loss_fraction": round(float(combo_metrics["cvar_95_loss_fraction"].median()), 8),
        "max_cvar_95_loss_fraction": round(float(combo_metrics["cvar_95_loss_fraction"].max()), 8),
        "median_var_95_loss_fraction": round(float(combo_metrics["var_95_loss_fraction"].median()), 8),
        "median_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].median()), 6),
        "min_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].min()), 6),
        "max_combo_drawdown_proxy": round(float(combo_metrics["max_drawdown_proxy"].max()), 8),
        "all_combos_cvar_within_limit": bool(combo_metrics["cvar_within_limit"].all()),
        "combo_metrics": combo_rows,
    }


def classify_result(summary: dict[str, Any]) -> tuple[str, str, str]:
    active_combo_days = int(summary.get("active_combo_days", 0))
    median_active_days = float(summary.get("median_active_days", 0.0))
    max_cvar = float(summary.get("max_cvar_95_loss_fraction", 0.0))
    median_sharpe = float(summary.get("median_combo_sharpe", 0.0))
    all_cvar_ok = bool(summary.get("all_combos_cvar_within_limit", False))

    if active_combo_days == 0 or median_active_days <= 0:
        return "FAIL", "abandon", "RESEARCH_SANDBOX_POLICY_ZERO_EXPOSURE"
    if median_active_days < MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR:
        return "INCONCLUSIVE", "correct", "INSUFFICIENT_ACTIVE_HISTORY_FOR_RESEARCH_CVAR"
    if not all_cvar_ok or max_cvar > CVAR_LIMIT:
        return "FAIL", "abandon", "NONZERO_EXPOSURE_RESEARCH_CVAR_BREACH"
    if median_sharpe > 0.0:
        return "PASS", "advance", "NONZERO_EXPOSURE_RESEARCH_CVAR_PASS_WITH_POSITIVE_ALPHA_CANDIDATE"
    return "PARTIAL", "correct", "NONZERO_EXPOSURE_RESEARCH_CVAR_PASS_BUT_ALPHA_DSR_BLOCKERS_REMAIN"


def _official_exposure_fraction(snapshot: pd.DataFrame) -> float:
    if "position_usdt" not in snapshot.columns:
        return 0.0
    position = pd.to_numeric(snapshot["position_usdt"], errors="coerce").fillna(0.0).abs()
    if "capital_usdt" in snapshot.columns:
        capital = pd.to_numeric(snapshot["capital_usdt"], errors="coerce").replace(0.0, pd.NA).dropna()
        if not capital.empty:
            return float(position.sum() / float(capital.iloc[0]))
    return float(position.sum())


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    official_snapshot = pd.read_parquet(OFFICIAL_SNAPSHOT)

    selected = select_safe_top1_by_score(predictions)
    daily = build_daily_policy_returns(predictions, selected)
    cvar_summary = summarize_research_cvar(daily)
    status, decision, classification = classify_result(cvar_summary)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))
    official_exposure = _official_exposure_fraction(official_snapshot)

    summary_path = OUTPUT_DIR / "research_sandbox_nonzero_exposure_cvar_summary.json"
    daily_returns_path = OUTPUT_DIR / "research_sandbox_nonzero_exposure_daily_returns.parquet"
    combo_metrics_path = OUTPUT_DIR / "research_sandbox_nonzero_exposure_cvar_combo_metrics.parquet"
    summary_payload = {
        "hypothesis": (
            "A research/sandbox CVaR evaluator can measure empirical CVaR on nonzero ex-ante "
            "exposure without promoting official or treating zero-exposure official CVaR as economic robustness."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "position_fraction": POSITION_FRACTION,
        "cvar_alpha": CVAR_ALPHA,
        "cvar_limit": CVAR_LIMIT,
        "policy": "safe_top1_by_rank_score_no_realized_eligibility_fixed_fraction",
        "research_cvar": cvar_summary,
        "official_snapshot_exposure_fraction": official_exposure,
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "masks_dsr": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    daily.to_parquet(daily_returns_path, index=False)
    pd.DataFrame(cvar_summary["combo_metrics"]).to_parquet(combo_metrics_path, index=False)

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_active_combo_days",
            "metric_value": cvar_summary["active_combo_days"],
            "metric_threshold": f">= {MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR}",
            "metric_status": "PASS"
            if cvar_summary["median_active_days"] >= MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_median_active_days",
            "metric_value": cvar_summary["median_active_days"],
            "metric_threshold": f">= {MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR}",
            "metric_status": "PASS"
            if cvar_summary["median_active_days"] >= MIN_ACTIVE_DATES_FOR_RESEARCH_CVAR
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_max_cvar_95_loss_fraction",
            "metric_value": cvar_summary["max_cvar_95_loss_fraction"],
            "metric_threshold": f"<= {CVAR_LIMIT}",
            "metric_status": "PASS" if cvar_summary["max_cvar_95_loss_fraction"] <= CVAR_LIMIT else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_median_cvar_95_loss_fraction",
            "metric_value": cvar_summary["median_cvar_95_loss_fraction"],
            "metric_threshold": f"<= {CVAR_LIMIT}",
            "metric_status": "PASS" if cvar_summary["median_cvar_95_loss_fraction"] <= CVAR_LIMIT else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_median_combo_sharpe",
            "metric_value": cvar_summary["median_combo_sharpe"],
            "metric_threshold": "> 0.0 for positive-alpha candidate",
            "metric_status": "PASS" if cvar_summary["median_combo_sharpe"] > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "official_snapshot_exposure_fraction",
            "metric_value": official_exposure,
            "metric_threshold": "> 0 for official economic CVaR",
            "metric_status": "FAIL" if official_exposure == 0.0 else "PASS",
        },
    ]

    generated_artifacts = [
        artifact_record(summary_path),
        artifact_record(daily_returns_path),
        artifact_record(combo_metrics_path),
    ]
    source_artifacts = [
        artifact_record(STAGE_A_PREDICTIONS),
        artifact_record(OFFICIAL_SNAPSHOT),
        artifact_record(OFFICIAL_REPORT),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [str(OFFICIAL_SNAPSHOT), str(OFFICIAL_REPORT)],
        "research_artifacts_generated": [str(summary_path), str(daily_returns_path), str(combo_metrics_path)],
        "summary": [
            f"classification={classification}",
            f"research_active_combo_days={cvar_summary['active_combo_days']}",
            f"research_max_cvar_95_loss_fraction={cvar_summary['max_cvar_95_loss_fraction']}",
            f"research_median_cvar_95_loss_fraction={cvar_summary['median_cvar_95_loss_fraction']}",
            f"research_median_combo_sharpe={cvar_summary['median_combo_sharpe']}",
            "research sandbox CVaR module produced nonzero-exposure measurements",
            "official CVaR remains NOT_PROVEN_ZERO_EXPOSURE",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "safe_policy_alpha_merit_not_proven",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "The nonzero-exposure CVaR result is sandbox/research-only.",
            "The evaluated safe top1 policy still has negative median combo Sharpe.",
            "Official Phase4 snapshot exposure remains zero, so official economic CVaR remains unproven.",
            "DSR honest remains 0.0 and blocks any promotion.",
        ],
        "next_recommended_step": (
            "Continue autonomously with a materially different research-only gate focused on DSR-zero "
            "diagnostics or an alternate ex-ante ranking/sizing hypothesis. Do not promote official."
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
            "Research/sandbox CVaR evaluation gate.",
            "No official artifacts were promoted.",
            "Selection uses rank_score_stage_a only; realized stage_a_eligible is not used as a policy input.",
            "Official zero-exposure CVaR remains a blocker and is not treated as economic robustness.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Research/sandbox nonzero-exposure CVaR gate result: `{status}/{decision}`. "
            f"Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. Official promotion remains forbidden because "
            "`dsr_honest=0.0` and official CVaR is still zero exposure."
        ),
        "Mudanças implementadas": (
            "Added a research-only CVaR evaluator for a fixed-fraction sandbox policy. The policy "
            "selects top1 by `rank_score_stage_a` per combo/date and never uses realized "
            "`stage_a_eligible` as an ex-ante rule."
        ),
        "Artifacts gerados": (
            f"- `{summary_path.relative_to(REPO_ROOT)}`\n"
            f"- `{daily_returns_path.relative_to(REPO_ROOT)}`\n"
            f"- `{combo_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Active combo-days: `{cvar_summary['active_combo_days']}`. Median active days: "
            f"`{cvar_summary['median_active_days']}`. Max empirical CVaR95 loss fraction: "
            f"`{cvar_summary['max_cvar_95_loss_fraction']}` versus limit `{CVAR_LIMIT}`. "
            f"Median combo Sharpe remains `{cvar_summary['median_combo_sharpe']}`."
        ),
        "Avaliação contra gates": (
            "The research CVaR module measured nonzero sandbox exposure and all combos remained "
            "inside the 15% CVaR limit. The gate remains PARTIAL when alpha merit is negative, "
            "DSR honest is zero, and official exposure is zero."
        ),
        "Riscos residuais": (
            "This is not official economic robustness. Official Phase4 exposure remains zero, DSR "
            "remains 0.0, and cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Keep the CVaR evaluator as research/sandbox evidence and continue with "
            "a materially different blocker, preferably DSR-zero diagnostics or alternate ex-ante sizing."
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
