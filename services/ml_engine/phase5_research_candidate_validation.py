#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

THIS_FILE = Path(__file__).resolve()
THIS_DIR = THIS_FILE.parent
REPO_ROOT = THIS_FILE.parents[2]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import phase5_research_signal_polarity_long_short as polarity_gate

CANDIDATE_POLICY = "short_high_p_bma_k3_p60_h70"
CANDIDATE_FAMILY = "signal_polarity_stability_filtered"
SR_NEEDED_FOR_PROMOTION = 4.47
CVAR_LIMIT = 0.15
MIN_MEDIAN_ACTIVE_DAYS = 120
STAGE_A_PREDICTIONS = polarity_gate.STAGE_A_PREDICTIONS
PRIOR_CANDIDATE_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_signal_polarity_stability_correction_gate"
    / "portfolio_cvar_research_report.json"
)
FULL_PHASE_COMPARISON_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_full_phase_family_comparison_gate"
    / "gate_report.json"
)

FORBIDDEN_EXANTE_SELECTION_COLUMNS = {
    "stage_a_eligible",
    "pnl_real",
    "avg_sl_train",
    "rank_target_stage_a",
    "future_return",
    "forward_return",
    "label",
}


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
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if pd.isna(value) if not isinstance(value, (str, bytes, bool, type(None))) else False:
        return None
    return value


def candidate_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "family": CANDIDATE_FAMILY,
        "policy": CANDIDATE_POLICY,
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 3,
        "gross_exposure": 0.04,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.70,
    }
    config.update(overrides)
    top_k = int(config["top_k"])
    p_threshold = int(round(float(config["p_bma_threshold"]) * 100))
    h_threshold = int(round(float(config["hmm_threshold"]) * 100))
    if "policy" not in overrides:
        config["policy"] = f"short_high_p_bma_k{top_k}_p{p_threshold}_h{h_threshold}"
    return config


def load_predictions() -> pd.DataFrame:
    return pd.read_parquet(STAGE_A_PREDICTIONS)


def _stable_symbol_bucket(symbol: Any, modulo: int = 2) -> int:
    digest = hashlib.sha256(str(symbol).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def apply_universe_filter(predictions: pd.DataFrame, universe_filter: str) -> pd.DataFrame:
    work = predictions.copy()
    if universe_filter == "all":
        return work
    normalized = polarity_gate.normalize_predictions(work)
    if universe_filter == "drop_high_sigma_q80":
        limit = float(normalized["sigma_ewma"].quantile(0.80))
        return work.loc[normalized["sigma_ewma"] <= limit].copy()
    if universe_filter == "symbol_hash_even":
        return work.loc[normalized["symbol"].map(lambda value: _stable_symbol_bucket(value) == 0)].copy()
    if universe_filter == "symbol_hash_odd":
        return work.loc[normalized["symbol"].map(lambda value: _stable_symbol_bucket(value) == 1)].copy()
    raise ValueError(f"unknown universe_filter={universe_filter}")


def select_positions(
    predictions: pd.DataFrame,
    config: dict[str, Any],
    *,
    universe_filter: str = "all",
) -> pd.DataFrame:
    filtered = apply_universe_filter(predictions, universe_filter)
    selected = polarity_gate.select_policy(filtered, config)
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
    return selected[keep].copy()


def select_null_positions(predictions: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = polarity_gate.normalize_predictions(predictions)
    mask = pd.Series(True, index=work.index)
    if "p_bma_threshold" in config:
        mask &= work["p_bma_pkf"] >= float(config["p_bma_threshold"])
    if "hmm_threshold" in config:
        mask &= work["hmm_prob_bull"] >= float(config["hmm_threshold"])
    work = work.loc[mask].copy()
    work["symbol_hash"] = work["symbol"].map(lambda value: int(hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8], 16))
    work["rank_null"] = work.groupby(["combo", "date"])["symbol_hash"].rank(method="first", ascending=True)
    top_k = int(config["top_k"])
    gross = float(config["gross_exposure"])
    work["target_weight"] = 0.0
    work.loc[work["rank_null"] <= top_k, "target_weight"] = -gross / top_k
    selected = work.loc[work["target_weight"] != 0.0].copy()
    selected["family"] = "null_symbol_hash_control"
    selected["policy"] = "null_short_symbol_hash_k3_p60_h70"
    selected["score"] = selected["symbol_hash"]
    selected["position_usdt"] = selected["target_weight"] * polarity_gate.CAPITAL_USDT
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
    return selected[keep].copy()


def build_daily_returns_with_cost(
    predictions: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    extra_cost_per_position: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = polarity_gate.normalize_predictions(predictions)
    date_grid = work[["combo", "date"]].drop_duplicates()
    daily_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    if positions.empty:
        return pd.DataFrame(), pd.DataFrame()
    for (family, policy), policy_positions in positions.groupby(["family", "policy"]):
        policy_positions = policy_positions.copy()
        policy_positions["contribution"] = (
            policy_positions["target_weight"] * policy_positions["pnl_net_proxy"]
            - policy_positions["target_weight"].abs() * float(extra_cost_per_position)
        )
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
    return pd.concat(daily_frames, ignore_index=True), pd.concat(trade_frames, ignore_index=True)


def summarize_daily(daily: pd.DataFrame, trades: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades is None:
        trades = pd.DataFrame(columns=["family", "policy", "combo", "date", "turnover_fraction"])
    return polarity_gate.summarize_portfolios(daily, trades)


def policy_summary(metrics_frame: pd.DataFrame) -> dict[str, Any]:
    policies = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    if policies.empty:
        return {}
    return json_safe(policies.iloc[0].to_dict())


def evaluate_candidate(
    predictions: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
    *,
    extra_cost_per_position: float = 0.0,
    universe_filter: str = "all",
) -> dict[str, Any]:
    predictions = load_predictions() if predictions is None else predictions
    config = candidate_config() if config is None else config
    positions = select_positions(predictions, config, universe_filter=universe_filter)
    daily, trades = build_daily_returns_with_cost(
        predictions,
        positions,
        extra_cost_per_position=extra_cost_per_position,
    )
    combo_metrics, policy_metrics = summarize_daily(daily, trades)
    metrics = pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )
    return {
        "config": config,
        "positions": positions,
        "daily": daily,
        "trades": trades,
        "metrics": metrics,
        "summary": policy_summary(metrics),
    }


def evaluate_null_control(predictions: pd.DataFrame | None = None) -> dict[str, Any]:
    predictions = load_predictions() if predictions is None else predictions
    config = candidate_config(policy=CANDIDATE_POLICY)
    positions = select_null_positions(predictions, config)
    daily, trades = build_daily_returns_with_cost(predictions, positions)
    combo_metrics, policy_metrics = summarize_daily(daily, trades)
    metrics = pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )
    return {
        "config": {"control": "null_symbol_hash", **config},
        "positions": positions,
        "daily": daily,
        "trades": trades,
        "metrics": metrics,
        "summary": policy_summary(metrics),
    }


def temporal_subperiod_summaries(
    daily: pd.DataFrame,
    *,
    periods: int = 3,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period_index in range(periods):
        parts: list[pd.DataFrame] = []
        for _, combo_daily in daily.groupby("combo"):
            ordered = combo_daily.sort_values("date")
            start = period_index * len(ordered) // periods
            end = (period_index + 1) * len(ordered) // periods
            parts.append(ordered.iloc[start:end].copy())
        period_daily = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        combo_metrics, policy_metrics = summarize_daily(period_daily)
        summary = policy_summary(pd.concat([combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")]))
        rows.append(
            {
                "scenario": f"temporal_third_{period_index + 1}",
                "period_index": period_index + 1,
                **summary,
            }
        )
    return pd.DataFrame(json_safe(rows))


def scenario_record(
    scenario: str,
    scenario_type: str,
    summary: dict[str, Any],
    *,
    threshold: str,
    passed: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "scenario_type": scenario_type,
        "median_combo_sharpe": summary.get("median_combo_sharpe"),
        "min_combo_sharpe": summary.get("min_combo_sharpe"),
        "median_active_days": summary.get("median_active_days"),
        "min_active_days": summary.get("min_active_days"),
        "max_cvar_95_loss_fraction": summary.get("max_cvar_95_loss_fraction"),
        "max_drawdown_proxy": summary.get("max_drawdown_proxy"),
        "median_turnover_fraction": summary.get("median_turnover_fraction"),
        "threshold": threshold,
        "passed": bool(passed),
        **(details or {}),
    }


def candidate_governance_checks(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = candidate_config() if config is None else config
    selection_inputs = {
        str(config.get("score_col")),
        "p_bma_pkf" if "p_bma_threshold" in config else "",
        "hmm_prob_bull" if "hmm_threshold" in config else "",
    }
    selection_inputs.discard("")
    forbidden_used = sorted(selection_inputs & FORBIDDEN_EXANTE_SELECTION_COLUMNS)
    return {
        "candidate": config.get("policy"),
        "research_only": True,
        "sandbox_only": True,
        "uses_short_exposure": config.get("mode") == "short_high",
        "promotes_official": False,
        "reopens_a3_a4": False,
        "relaxes_thresholds": False,
        "declares_paper_readiness": False,
        "masks_dsr": False,
        "treats_zero_exposure_cvar_as_economic_robustness": False,
        "selection_inputs": sorted(selection_inputs),
        "forbidden_exante_selection_columns": sorted(FORBIDDEN_EXANTE_SELECTION_COLUMNS),
        "forbidden_selection_columns_used": forbidden_used,
        "uses_realized_variable_as_ex_ante_rule": bool(forbidden_used),
        "pnl_real_usage": "realized_backtest_outcome_only",
    }


def status_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    median_sharpe = float(summary.get("median_combo_sharpe") or 0.0)
    min_sharpe = float(summary.get("min_combo_sharpe") or 0.0)
    median_active_days = float(summary.get("median_active_days") or 0.0)
    cvar = float(summary.get("max_cvar_95_loss_fraction") or 0.0)
    return {
        "positive_median_sharpe": median_sharpe > 0.0,
        "positive_min_sharpe": min_sharpe > 0.0,
        "active_days_sufficient": median_active_days >= MIN_MEDIAN_ACTIVE_DAYS,
        "cvar_within_research_limit": cvar <= CVAR_LIMIT,
        "below_sr_needed": median_sharpe < SR_NEEDED_FOR_PROMOTION,
        "promotion_allowed": False,
    }


def summary_value(report: dict[str, Any], key: str) -> str:
    prefix = f"{key}="
    for item in report.get("summary", []):
        text = str(item)
        if text.startswith(prefix):
            return text[len(prefix) :]
    return ""


def current_git_context() -> dict[str, Any]:
    return {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }
