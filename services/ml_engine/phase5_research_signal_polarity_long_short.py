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

GATE_SLUG = "phase5_research_signal_polarity_long_short_gate"
PHASE_FAMILY = "phase5_research_signal_polarity_long_short"
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
ALTERNATIVE_FAMILY_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_alternative_exante_family_gate" / "gate_report.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

CAPITAL_USDT = 100_000.0
CVAR_ALPHA = 0.05
CVAR_LIMIT = 0.15
SR_NEEDED_FOR_PROMOTION = 4.47
MIN_MEDIAN_ACTIVE_DAYS = 120

PREDECLARED_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "family": "signal_polarity_short_high",
        "policy": "short_high_p_bma_k1",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 1,
        "gross_exposure": 0.04,
    },
    {
        "family": "signal_polarity_short_high",
        "policy": "short_high_p_bma_k2",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 2,
        "gross_exposure": 0.04,
    },
    {
        "family": "signal_polarity_short_high",
        "policy": "short_high_p_bma_k3",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 3,
        "gross_exposure": 0.04,
    },
    {
        "family": "signal_polarity_short_high",
        "policy": "short_high_p_stage_a_k3",
        "score_col": "p_stage_a_raw",
        "mode": "short_high",
        "top_k": 3,
        "gross_exposure": 0.04,
    },
    {
        "family": "signal_polarity_market_neutral",
        "policy": "long_high_short_low_p_stage_a_k2",
        "score_col": "p_stage_a_raw",
        "mode": "long_high_short_low",
        "top_k": 2,
        "gross_exposure": 0.04,
    },
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


def _safe_numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def normalize_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["combo"] = work["combo"].astype(str)
    work["symbol"] = work["symbol"].astype(str)
    work["p_bma_pkf"] = _safe_numeric(work, "p_bma_pkf", 0.5)
    work["p_stage_a_raw"] = _safe_numeric(work, "p_stage_a_raw", 0.0)
    work["hmm_prob_bull"] = _safe_numeric(work, "hmm_prob_bull", 0.0)
    work["sigma_ewma"] = _safe_numeric(work, "sigma_ewma", 1.0).clip(lower=1e-6)
    work["pnl_real"] = _safe_numeric(work, "pnl_real", 0.0)
    work["slippage_frac"] = _safe_numeric(work, "slippage_frac", 0.0)
    work["pnl_net_proxy"] = work["pnl_real"] - work["slippage_frac"]
    return work


def select_policy(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = normalize_predictions(frame)
    score_col = str(config["score_col"])
    mask = pd.Series(True, index=work.index)
    if "p_bma_threshold" in config:
        mask &= work["p_bma_pkf"] >= float(config["p_bma_threshold"])
    if "hmm_threshold" in config:
        mask &= work["hmm_prob_bull"] >= float(config["hmm_threshold"])
    if "sigma_quantile_max" in config:
        mask &= work["sigma_ewma"] <= float(work["sigma_ewma"].quantile(float(config["sigma_quantile_max"])))
    work = work.loc[mask].copy()
    if work.empty:
        work["target_weight"] = pd.Series(dtype="float64")
        return work

    keys = ["combo", "date"]
    work["rank_high"] = work.groupby(keys)[score_col].rank(method="first", ascending=False)
    work["rank_low"] = work.groupby(keys)[score_col].rank(method="first", ascending=True)
    work["target_weight"] = 0.0
    top_k = int(config["top_k"])
    gross = float(config["gross_exposure"])
    mode = str(config["mode"])
    if mode == "short_high":
        work.loc[work["rank_high"] <= top_k, "target_weight"] = -gross / top_k
    elif mode == "long_high_short_low":
        work.loc[work["rank_high"] <= top_k, "target_weight"] = gross / 2.0 / top_k
        work.loc[work["rank_low"] <= top_k, "target_weight"] = -gross / 2.0 / top_k
    else:
        raise ValueError(f"unknown polarity mode={mode}")
    selected = work.loc[work["target_weight"] != 0.0].copy()
    selected["family"] = str(config["family"])
    selected["policy"] = str(config["policy"])
    selected["score"] = selected[score_col]
    selected["position_usdt"] = selected["target_weight"] * CAPITAL_USDT
    return selected


def _empirical_var_cvar(returns: pd.Series) -> tuple[float, float]:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if clean.empty:
        return 0.0, 0.0
    losses = -clean
    var_95 = float(losses.quantile(1.0 - CVAR_ALPHA))
    tail = losses.loc[losses >= var_95]
    return var_95, float(tail.mean()) if not tail.empty else var_95


def _max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return 0.0
    drawdown = cumulative_returns - cumulative_returns.cummax()
    return abs(float(drawdown.min()))


def build_daily_returns(all_predictions: pd.DataFrame, positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = normalize_predictions(all_predictions)
    date_grid = work[["combo", "date"]].drop_duplicates()
    daily_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    for (family, policy), policy_positions in positions.groupby(["family", "policy"]):
        policy_positions = policy_positions.copy()
        policy_positions["contribution"] = policy_positions["target_weight"] * policy_positions["pnl_net_proxy"]
        daily = (
            policy_positions.groupby(["combo", "date"])
            .agg(
                daily_return_proxy=("contribution", "sum"),
                exposure_fraction=("target_weight", lambda value: float(value.abs().sum())),
            )
            .reset_index()
        )
        daily = date_grid.merge(daily, on=["combo", "date"], how="left").fillna(
            {"daily_return_proxy": 0.0, "exposure_fraction": 0.0}
        )
        daily["family"] = str(family)
        daily["policy"] = str(policy)
        daily_frames.append(daily[["family", "policy", "combo", "date", "daily_return_proxy", "exposure_fraction"]])

        for combo, combo_positions in policy_positions.groupby("combo"):
            weights = combo_positions.pivot_table(
                index="date",
                columns="symbol",
                values="target_weight",
                aggfunc="sum",
                fill_value=0.0,
            ).sort_index()
            turnover = weights.diff().fillna(weights).abs().sum(axis=1).rename("turnover_fraction").reset_index()
            turnover["family"] = str(family)
            turnover["policy"] = str(policy)
            turnover["combo"] = str(combo)
            trade_frames.append(turnover[["family", "policy", "combo", "date", "turnover_fraction"]])
    daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return daily, trades


def summarize_portfolios(daily: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    combo_rows: list[dict[str, Any]] = []
    for (family, policy, combo), combo_daily in daily.groupby(["family", "policy", "combo"]):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        exposure = pd.to_numeric(combo_daily["exposure_fraction"], errors="coerce").fillna(0.0)
        active = exposure > 0.0
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = 0.0 if std == 0.0 else float(returns.mean()) / std * (252.0**0.5)
        var_95, cvar_95 = _empirical_var_cvar(returns)
        policy_trades = trades.loc[
            (trades["family"] == family) & (trades["policy"] == policy) & (trades["combo"] == combo)
        ] if not trades.empty else pd.DataFrame()
        combo_rows.append(
            {
                "family": str(family),
                "policy": str(policy),
                "combo": str(combo),
                "total_days": int(len(returns)),
                "active_days": int(active.sum()),
                "active_ratio": round(float(active.mean()), 6) if len(active) else 0.0,
                "cum_return_proxy": round(float(returns.sum()), 8),
                "annualized_sharpe_proxy": round(float(sharpe), 6),
                "var_95_loss_fraction": round(var_95, 8),
                "cvar_95_loss_fraction": round(cvar_95, 8),
                "max_drawdown_proxy": round(_max_drawdown(returns.cumsum()), 8),
                "max_exposure_fraction": round(float(exposure.max()), 8) if len(exposure) else 0.0,
                "mean_turnover_fraction": round(float(policy_trades["turnover_fraction"].mean()), 8)
                if not policy_trades.empty
                else 0.0,
            }
        )
    combo_metrics = pd.DataFrame(combo_rows)
    policy_rows: list[dict[str, Any]] = []
    for (family, policy), policy_frame in combo_metrics.groupby(["family", "policy"]):
        policy_rows.append(
            {
                "family": str(family),
                "policy": str(policy),
                "combo_count": int(policy_frame["combo"].nunique()),
                "median_active_days": round(float(policy_frame["active_days"].median()), 6),
                "min_active_days": int(policy_frame["active_days"].min()),
                "median_combo_sharpe": round(float(policy_frame["annualized_sharpe_proxy"].median()), 6),
                "min_combo_sharpe": round(float(policy_frame["annualized_sharpe_proxy"].min()), 6),
                "median_cum_return_proxy": round(float(policy_frame["cum_return_proxy"].median()), 8),
                "max_cvar_95_loss_fraction": round(float(policy_frame["cvar_95_loss_fraction"].max()), 8),
                "max_drawdown_proxy": round(float(policy_frame["max_drawdown_proxy"].max()), 8),
                "median_turnover_fraction": round(float(policy_frame["mean_turnover_fraction"].median()), 8),
            }
        )
    return combo_metrics, pd.DataFrame(policy_rows)


def evaluate_policies(
    predictions: pd.DataFrame,
    policies: tuple[dict[str, Any], ...] = PREDECLARED_POLICIES,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    positions = pd.concat([select_policy(predictions, config) for config in policies], ignore_index=True)
    keep = [
        "family",
        "policy",
        "combo",
        "date",
        "symbol",
        "target_weight",
        "position_usdt",
        "score",
        "p_bma_pkf",
        "p_stage_a_raw",
        "sigma_ewma",
        "hmm_prob_bull",
        "pnl_net_proxy",
    ]
    positions = positions[keep].copy()
    daily, trades = build_daily_returns(predictions, positions)
    combo_metrics, policy_metrics = summarize_portfolios(daily, trades)
    return positions, daily, trades, pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )


def classify_polarity(policy_metrics: pd.DataFrame) -> tuple[str, str, str, dict[str, Any]]:
    policies = policy_metrics.loc[policy_metrics["metric_level"] == "policy"].copy()
    if policies.empty:
        return "INCONCLUSIVE", "correct", "NO_POLICIES_EVALUATED", {}
    eligible = policies.loc[
        (policies["median_active_days"] >= MIN_MEDIAN_ACTIVE_DAYS)
        & (policies["median_combo_sharpe"] > 0.0)
        & (policies["max_cvar_95_loss_fraction"] <= CVAR_LIMIT)
    ]
    ranked = (eligible if not eligible.empty else policies).sort_values(
        ["median_combo_sharpe", "min_combo_sharpe"], ascending=[False, False], kind="mergesort"
    )
    best = ranked.iloc[0].to_dict()
    if eligible.empty:
        return "FAIL", "abandon", "NO_POSITIVE_ACTIVE_POLARITY_POLICY", best
    if float(best["median_combo_sharpe"]) >= SR_NEEDED_FOR_PROMOTION and float(best["min_combo_sharpe"]) > 0.0:
        return "PASS", "advance", "STRONG_SIGNAL_POLARITY_CANDIDATE_NOT_PROMOTED", best
    if float(best["min_combo_sharpe"]) > 0.0:
        return "PASS", "advance", "STABLE_POSITIVE_SIGNAL_POLARITY_RESEARCH_CANDIDATE_NOT_PROMOTED", best
    return "PARTIAL", "correct", "POSITIVE_SIGNAL_POLARITY_CANDIDATE_NEEDS_STABILITY_CORRECTION", best


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    alternative_gate = _read_json(ALTERNATIVE_FAMILY_GATE)
    positions, daily, trades, metrics_frame = evaluate_policies(predictions)
    status, decision, classification, best = classify_polarity(metrics_frame)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    positions_path = OUTPUT_DIR / "signal_polarity_positions.parquet"
    daily_path = OUTPUT_DIR / "signal_polarity_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "signal_polarity_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "signal_polarity_metrics.parquet"
    snapshot_path = OUTPUT_DIR / "signal_polarity_snapshot_proxy.parquet"
    report_path = OUTPUT_DIR / "portfolio_cvar_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    positions.loc[positions["date"] == positions["date"].max()].to_parquet(snapshot_path, index=False)

    policy_metrics = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    payload = {
        "hypothesis": (
            "A research-only signal polarity family can test whether p_bma/p_stage scores are anti-signals "
            "by assigning short sandbox exposure to high-score names, without promoting official."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "predeclared_policies": list(PREDECLARED_POLICIES),
        "best_policy": best,
        "policy_metrics": policy_metrics.to_dict(orient="records"),
        "prior_alternative_family_summary": alternative_gate.get("summary", []),
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_stage_a_eligible_as_policy_input": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
            "short_exposure_is_research_sandbox_only": True,
            "masks_dsr": False,
        },
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_policy",
            "metric_value": best.get("policy", ""),
            "metric_threshold": "predeclared policy only",
            "metric_status": "PASS" if best else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_median_combo_sharpe",
            "metric_value": best.get("median_combo_sharpe", 0.0),
            "metric_threshold": f">= {SR_NEEDED_FOR_PROMOTION} for promotion; > 0 for research candidate",
            "metric_status": "PASS" if float(best.get("median_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_min_combo_sharpe",
            "metric_value": best.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0 for stable research candidate",
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
    ]
    generated_artifacts = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(snapshot_path),
        artifact_record(report_path),
    ]
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(ALTERNATIVE_FAMILY_GATE)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [],
        "research_artifacts_generated": [
            str(positions_path),
            str(daily_path),
            str(trades_path),
            str(metrics_path),
            str(snapshot_path),
            str(report_path),
        ],
        "summary": [
            f"classification={classification}",
            f"best_policy={best.get('policy', '')}",
            f"best_family={best.get('family', '')}",
            f"best_median_combo_sharpe={best.get('median_combo_sharpe', 0.0)}",
            f"best_min_combo_sharpe={best.get('min_combo_sharpe', 0.0)}",
            f"best_median_active_days={best.get('median_active_days', 0.0)}",
            f"best_max_cvar_95_loss_fraction={best.get('max_cvar_95_loss_fraction', 0.0)}",
            "short exposure is research/sandbox only",
            "pnl_real used only as realized outcome, never ex-ante selection",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": [
            "signal_polarity_candidate_not_promotable",
            "negative_min_combo_sharpe_needs_correction",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
        "risks_residual": [
            "Short exposure is research-only and may not match official execution constraints.",
            "Best candidate needs stability correction before it can survive research selection.",
            "Even stable research Sharpe below sr_needed cannot clear DSR promotion.",
        ],
        "next_recommended_step": (
            "Use one PARTIAL correction to test p_bma/hmm stability filters for the signal polarity family."
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
            "Research-only signal polarity gate with sandbox short exposure.",
            "No real orders, no credentials, no official promotion.",
            "pnl_real is used only as realized outcome for backtest scoring.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Signal polarity long-short gate result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This is a research-only polarity test."
        ),
        "MudanÃ§as implementadas": (
            "Added a sandbox signal-polarity module testing short-high and market-neutral policies "
            "from ex-ante p_bma/p_stage scores."
        ),
        "Artifacts gerados": (
            f"- `{positions_path.relative_to(REPO_ROOT)}`\n"
            f"- `{daily_path.relative_to(REPO_ROOT)}`\n"
            f"- `{trades_path.relative_to(REPO_ROOT)}`\n"
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{snapshot_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Best policy `{best.get('policy', '')}` had median combo Sharpe "
            f"`{best.get('median_combo_sharpe', 0.0)}`, min combo Sharpe "
            f"`{best.get('min_combo_sharpe', 0.0)}`, median active days "
            f"`{best.get('median_active_days', 0.0)}`, and max CVaR95 loss "
            f"`{best.get('max_cvar_95_loss_fraction', 0.0)}`."
        ),
        "AvaliaÃ§Ã£o contra gates": (
            "The polarity family found positive median research alpha, but negative min combo Sharpe "
            "requires bounded correction. It is not official and not readiness evidence."
        ),
        "Riscos residuais": (
            "Short exposure is sandbox-only, DSR remains 0.0, and official CVaR remains zero exposure."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue with the bounded stability correction for this family."
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
