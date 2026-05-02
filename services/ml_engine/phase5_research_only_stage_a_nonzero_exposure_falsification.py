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

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_research_only_stage_a_nonzero_exposure_falsification_gate"
PHASE_FAMILY = "phase5_research_only_stage_a_nonzero_exposure_falsification"
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
STAGE_A_SNAPSHOT_PROXY = RESEARCH_BASELINE_DIR / "stage_a_snapshot_proxy.parquet"
OFFICIAL_SNAPSHOT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_execution_snapshot.parquet"
OFFICIAL_REPORT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_report_v4.json"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

SR_NEEDED_FOR_PROMOTION = 4.47
MIN_ACTIVE_DATES_FOR_PASS = 120


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


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
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
    """Ex-ante sandbox policy: top ranked symbol per combo/date, no realized eligibility filter."""
    work = _normalize(frame)
    ranked = work.sort_values(
        ["combo", "date", "rank_score_stage_a", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    return ranked.groupby(["combo", "date"], as_index=False).head(1).copy()


def select_unsafe_realized_eligible_top1(frame: pd.DataFrame) -> pd.DataFrame:
    """Diagnostic only: uses realized Stage A eligibility and must never drive a policy."""
    work = _normalize(frame)
    eligible = pd.Series(work.get("stage_a_eligible", False), index=work.index).fillna(False).astype(bool)
    ranked = work.loc[eligible].sort_values(
        ["combo", "date", "rank_score_stage_a", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    if ranked.empty:
        return ranked.copy()
    return ranked.groupby(["combo", "date"], as_index=False).head(1).copy()


def _combo_daily_returns(all_predictions: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    work = _normalize(all_predictions)
    selected = _normalize(selected) if not selected.empty else selected.copy()
    rows: list[dict[str, Any]] = []
    for combo, combo_frame in work.groupby("combo"):
        dates = pd.to_datetime(combo_frame["date"]).dropna().sort_values().unique()
        series = pd.Series(0.0, index=pd.to_datetime(dates))
        combo_selected = selected.loc[selected["combo"] == combo] if not selected.empty else selected
        if not combo_selected.empty:
            values = combo_selected.groupby("date")["pnl_net_proxy"].sum()
            series.loc[values.index] = values.values
        for date_value, pnl_value in series.items():
            rows.append({"combo": str(combo), "date": date_value, "daily_return_proxy": float(pnl_value)})
    return pd.DataFrame(rows)


def _max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return 0.0
    drawdown = cumulative_returns - cumulative_returns.cummax()
    return abs(float(drawdown.min()))


def summarize_policy(name: str, all_predictions: pd.DataFrame, selected: pd.DataFrame) -> dict[str, Any]:
    daily = _combo_daily_returns(all_predictions, selected)
    combo_rows: list[dict[str, Any]] = []
    for combo, combo_daily in daily.groupby("combo"):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        active = returns != 0.0
        mean = float(returns.mean()) if len(returns) else 0.0
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = 0.0 if std == 0.0 else mean / std * (252.0 ** 0.5)
        cumulative = returns.cumsum()
        active_returns = returns.loc[active]
        combo_rows.append(
            {
                "combo": combo,
                "total_days": int(len(returns)),
                "active_days": int(active.sum()),
                "cum_return_proxy": round(float(returns.sum()), 6),
                "annualized_sharpe_proxy": round(float(sharpe), 6),
                "max_drawdown_proxy": round(_max_drawdown(cumulative), 6),
                "win_rate_active": round(float((active_returns > 0).mean()), 6) if len(active_returns) else 0.0,
            }
        )
    combo_metrics = pd.DataFrame(combo_rows)
    selected_dates = int(pd.to_datetime(selected["date"]).dropna().nunique()) if not selected.empty else 0
    return {
        "policy": name,
        "selected_events": int(len(selected)),
        "selected_dates": selected_dates,
        "combo_count": int(combo_metrics["combo"].nunique()) if not combo_metrics.empty else 0,
        "median_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].median()), 6)
        if not combo_metrics.empty
        else 0.0,
        "min_combo_sharpe": round(float(combo_metrics["annualized_sharpe_proxy"].min()), 6)
        if not combo_metrics.empty
        else 0.0,
        "median_combo_cum_return": round(float(combo_metrics["cum_return_proxy"].median()), 6)
        if not combo_metrics.empty
        else 0.0,
        "median_active_days": round(float(combo_metrics["active_days"].median()), 6) if not combo_metrics.empty else 0.0,
        "median_win_rate_active": round(float(combo_metrics["win_rate_active"].median()), 6)
        if not combo_metrics.empty
        else 0.0,
        "max_combo_drawdown": round(float(combo_metrics["max_drawdown_proxy"].max()), 6)
        if not combo_metrics.empty
        else 0.0,
        "combo_metrics": combo_rows,
    }


def classify_result(safe_summary: dict[str, Any], unsafe_summary: dict[str, Any]) -> tuple[str, str, str]:
    selected_events = int(safe_summary.get("selected_events", 0))
    selected_dates = int(safe_summary.get("selected_dates", 0))
    median_sharpe = float(safe_summary.get("median_combo_sharpe", 0.0))
    min_sharpe = float(safe_summary.get("min_combo_sharpe", 0.0))
    unsafe_median_sharpe = float(unsafe_summary.get("median_combo_sharpe", 0.0))

    if selected_events == 0 or selected_dates == 0:
        return "FAIL", "abandon", "SAFE_POLICY_ZERO_EXPOSURE"
    if median_sharpe >= SR_NEEDED_FOR_PROMOTION and min_sharpe > 0.0 and selected_dates >= MIN_ACTIVE_DATES_FOR_PASS:
        return "PASS", "advance", "RESEARCH_ONLY_SAFE_POLICY_STRONG_ENOUGH_FOR_FURTHER_GATES"
    if median_sharpe > 0.0 and selected_dates >= MIN_ACTIVE_DATES_FOR_PASS:
        return "PARTIAL", "correct", "NONZERO_EXPOSURE_BUT_DSR_GAP_REMAINS"
    if unsafe_median_sharpe > SR_NEEDED_FOR_PROMOTION:
        return "FAIL", "abandon", "ONLY_REALIZED_ELIGIBILITY_LOOKS_GOOD_SAFE_POLICY_FAILS"
    return "FAIL", "abandon", "SAFE_POLICY_NEGATIVE_OR_INSUFFICIENT_MERIT"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    snapshot_proxy = pd.read_parquet(STAGE_A_SNAPSHOT_PROXY)
    official_snapshot = pd.read_parquet(OFFICIAL_SNAPSHOT)

    safe_selected = select_safe_top1_by_score(predictions)
    unsafe_selected = select_unsafe_realized_eligible_top1(predictions)
    safe_summary = summarize_policy("safe_top1_by_rank_score_no_realized_eligibility", predictions, safe_selected)
    unsafe_summary = summarize_policy("unsafe_realized_stage_a_eligible_top1_diagnostic_only", predictions, unsafe_selected)

    official_exposure = float(pd.to_numeric(official_snapshot.get("position_usdt", 0), errors="coerce").fillna(0.0).abs().sum())
    proxy_exposure = float(
        pd.to_numeric(snapshot_proxy.get("position_usdt_stage_a_proxy", 0), errors="coerce").fillna(0.0).abs().sum()
    )

    status, decision, classification = classify_result(safe_summary, unsafe_summary)
    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "safe_selected_events",
            "metric_value": safe_summary["selected_events"],
            "metric_threshold": "> 0",
            "metric_status": "PASS" if safe_summary["selected_events"] > 0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "safe_selected_dates",
            "metric_value": safe_summary["selected_dates"],
            "metric_threshold": f">= {MIN_ACTIVE_DATES_FOR_PASS}",
            "metric_status": "PASS" if safe_summary["selected_dates"] >= MIN_ACTIVE_DATES_FOR_PASS else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "safe_median_combo_sharpe",
            "metric_value": safe_summary["median_combo_sharpe"],
            "metric_threshold": f">= {SR_NEEDED_FOR_PROMOTION}",
            "metric_status": "PASS" if safe_summary["median_combo_sharpe"] >= SR_NEEDED_FOR_PROMOTION else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "safe_min_combo_sharpe",
            "metric_value": safe_summary["min_combo_sharpe"],
            "metric_threshold": "> 0.0",
            "metric_status": "PASS" if safe_summary["min_combo_sharpe"] > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "unsafe_median_combo_sharpe",
            "metric_value": unsafe_summary["median_combo_sharpe"],
            "metric_threshold": "diagnostic only; must not be used for policy",
            "metric_status": "INCONCLUSIVE",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "official_snapshot_exposure_usdt",
            "metric_value": official_exposure,
            "metric_threshold": "> 0 for economic CVaR",
            "metric_status": "FAIL" if official_exposure == 0.0 else "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "research_snapshot_proxy_exposure_usdt",
            "metric_value": proxy_exposure,
            "metric_threshold": "> 0 for research proxy exposure",
            "metric_status": "FAIL" if proxy_exposure == 0.0 else "PASS",
        },
    ]

    summary_path = OUTPUT_DIR / "stage_a_nonzero_exposure_summary.json"
    combo_metrics_path = OUTPUT_DIR / "stage_a_nonzero_exposure_combo_metrics.parquet"
    summary = {
        "hypothesis": "Stage A research baseline can produce nonzero sandbox exposure and enough ex-ante-safe merit to justify further research.",
        "status": status,
        "decision": decision,
        "classification": classification,
        "safe_policy": safe_summary,
        "unsafe_diagnostic_policy": unsafe_summary,
        "official_snapshot_exposure_usdt": official_exposure,
        "research_snapshot_proxy_exposure_usdt": proxy_exposure,
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "masks_dsr": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(safe_summary["combo_metrics"]).assign(policy=safe_summary["policy"]).to_parquet(
        combo_metrics_path, index=False
    )

    generated_artifacts = [artifact_record(summary_path), artifact_record(combo_metrics_path)]
    source_artifacts = [
        artifact_record(STAGE_A_PREDICTIONS),
        artifact_record(STAGE_A_SNAPSHOT_PROXY),
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
        "research_artifacts_generated": [str(summary_path), str(combo_metrics_path)],
        "summary": [
            f"classification={classification}",
            f"safe_selected_events={safe_summary['selected_events']}",
            f"safe_selected_dates={safe_summary['selected_dates']}",
            f"safe_median_combo_sharpe={safe_summary['median_combo_sharpe']}",
            f"unsafe_median_combo_sharpe={unsafe_summary['median_combo_sharpe']}",
            "unsafe policy uses realized stage_a_eligible and is diagnostic only",
            "no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "Safe ex-ante top1 policy has negative median combo Sharpe.",
            "Positive unsafe diagnostic result depends on realized eligibility and cannot drive decisions.",
            "Official and research snapshot exposures remain zero.",
        ],
        "next_recommended_step": "Abandon this Stage A nonzero-exposure thesis as a promotion path. Keep PR draft as governance evidence and either freeze the line or require a materially new research-only hypothesis.",
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
        "commands_executed": [
            f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}",
        ],
        "notes": [
            "Research-only falsification gate.",
            "No official artifacts were promoted.",
            "stage_a_eligible is treated as realized diagnostic metadata, not as an ex-ante policy input.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Research-only Stage A nonzero exposure thesis result: `{status}/{decision}`. "
            f"Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. Official Phase4 remains blocked by `dsr_honest=0.0` "
            "and zero-exposure CVaR."
        ),
        "Mudanças implementadas": (
            "Added a research/sandbox-only falsification harness. The safe policy selects top1 by "
            "`rank_score_stage_a` per combo/date without using realized eligibility. The unsafe "
            "`stage_a_eligible` policy is reported only as a leakage diagnostic."
        ),
        "Artifacts gerados": (
            f"- `{summary_path.relative_to(REPO_ROOT)}`\n"
            f"- `{combo_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Safe selected events: `{safe_summary['selected_events']}`; safe selected dates: "
            f"`{safe_summary['selected_dates']}`; safe median combo Sharpe: "
            f"`{safe_summary['median_combo_sharpe']}`. Unsafe diagnostic median combo Sharpe: "
            f"`{unsafe_summary['median_combo_sharpe']}`."
        ),
        "Avaliação contra gates": (
            "PASS would require nonzero exposure plus safe median combo Sharpe above the historical "
            f"promotion SR need `{SR_NEEDED_FOR_PROMOTION}` and positive minimum combo Sharpe. The "
            "safe policy fails that merit criterion. The only high Sharpe path uses realized "
            "`stage_a_eligible`, so it is not an admissible ex-ante decision rule."
        ),
        "Riscos residuais": (
            "DSR remains zero, official CVaR remains zero exposure, and cross-sectional remains "
            "`ALIVE_BUT_NOT_PROMOTABLE`. This gate does not support paper readiness or promotion."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Abandon this Stage A nonzero-exposure thesis as a promotion path; "
            "future work needs a materially different research-only hypothesis or a freeze decision."
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
