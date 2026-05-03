#!/usr/bin/env python3
from __future__ import annotations

import json
import math
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

GATE_SLUG = "phase5_research_meta_disagreement_abstention_gate"
PHASE_FAMILY = "phase5_research_meta_disagreement_abstention"
PHASE4_OOS_PREDICTIONS = REPO_ROOT / "data" / "models" / "phase4" / "phase4_oos_predictions.parquet"
AGENDA_PATH = REPO_ROOT / "reports" / "state" / "sniper_research_agenda.yaml"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

CAPITAL_USDT = 100_000.0
CVAR_ALPHA = 0.05
CVAR_LIMIT = 0.15
SR_NEEDED_FOR_PROMOTION = 4.47
MIN_MEDIAN_ACTIVE_DAYS = 120

FORBIDDEN_SELECTION_INPUTS = {
    "pnl_real",
    "pnl_exec_meta",
    "stage_a_eligible",
    "stage_a_score_realized",
    "avg_sl_train",
    "avg_tp_train",
    "label",
    "y_meta",
}

PREDECLARED_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_bma_high_meta_low_p55_m45_k3",
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.55,
        "p_meta_max": 0.45,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_bma_high_meta_low_p60_m40_k1",
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.60,
        "p_meta_max": 0.40,
        "top_k": 1,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_bma_high_meta_low_p60_m40_k3",
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.60,
        "p_meta_max": 0.40,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_bma_high_meta_low_p60_m40_k5",
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.60,
        "p_meta_max": 0.40,
        "top_k": 5,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_bma_high_meta_low_p65_m35_k3",
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.65,
        "p_meta_max": 0.35,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_calibration_disagreement_abstention",
        "policy": "short_meta_low_m40_k3",
        "mode": "short_meta_low",
        "p_meta_max": 0.40,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_meta_calibrated", "sigma_ewma"],
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


def _safe_numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value) and not isinstance(value, (str, bytes, bool)):
        return None
    return value


def validate_policy_grid(policies: tuple[dict[str, Any], ...] = PREDECLARED_POLICIES) -> list[str]:
    errors: list[str] = []
    for policy in policies:
        forbidden = FORBIDDEN_SELECTION_INPUTS.intersection(set(policy.get("selection_inputs", [])))
        if forbidden:
            errors.append(f"{policy.get('policy')} uses forbidden selection inputs: {sorted(forbidden)}")
        if str(policy.get("mode")) not in {"short_bma_high_meta_low", "short_meta_low"}:
            errors.append(f"{policy.get('policy')} has unsupported mode={policy.get('mode')}")
    return errors


def normalize_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"combo", "date", "symbol", "p_bma_pkf", "p_meta_calibrated", "sigma_ewma", "pnl_real"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["combo"] = work["combo"].astype(str)
    work["symbol"] = work["symbol"].astype(str)
    work["p_bma_pkf"] = _safe_numeric(work, "p_bma_pkf", 0.5).clip(lower=0.0, upper=1.0)
    work["p_meta_calibrated"] = _safe_numeric(work, "p_meta_calibrated", 0.5).clip(lower=0.0, upper=1.0)
    work["p_meta_raw"] = _safe_numeric(work, "p_meta_raw", 0.5)
    work["sigma_ewma"] = _safe_numeric(work, "sigma_ewma", 1.0).clip(lower=1e-6)
    work["hmm_prob_bull"] = _safe_numeric(work, "hmm_prob_bull", 0.0).clip(lower=0.0, upper=1.0)
    work["pnl_real"] = _safe_numeric(work, "pnl_real", 0.0)
    work["slippage_exec_meta"] = _safe_numeric(work, "slippage_exec_meta", 0.0)
    work["pnl_net_proxy"] = work["pnl_real"] - work["slippage_exec_meta"]
    work["meta_disagreement"] = work["p_bma_pkf"] - work["p_meta_calibrated"]
    return work


def select_policy(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = normalize_predictions(frame)
    mode = str(config["mode"])
    if mode == "short_bma_high_meta_low":
        mask = work["p_bma_pkf"] >= float(config["p_bma_min"])
        mask &= work["p_meta_calibrated"] <= float(config["p_meta_max"])
        candidates = work.loc[mask].copy()
        candidates["score"] = candidates["meta_disagreement"] / candidates["sigma_ewma"]
    elif mode == "short_meta_low":
        mask = work["p_meta_calibrated"] <= float(config["p_meta_max"])
        candidates = work.loc[mask].copy()
        candidates["score"] = (0.5 - candidates["p_meta_calibrated"]) / candidates["sigma_ewma"]
    else:
        raise ValueError(f"unknown mode={mode}")

    if candidates.empty:
        candidates["target_weight"] = pd.Series(dtype="float64")
        return candidates

    candidates = candidates.loc[candidates["score"] > 0.0].copy()
    if candidates.empty:
        candidates["target_weight"] = pd.Series(dtype="float64")
        return candidates

    candidates = candidates.sort_values(
        ["combo", "date", "score", "symbol"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    selected = candidates.groupby(["combo", "date"], as_index=False).head(int(config["top_k"])).copy()
    counts = selected.groupby(["combo", "date"])["symbol"].transform("count").clip(lower=1)
    selected["target_weight"] = -float(config["gross_exposure"]) / counts
    selected["family"] = str(config["family"])
    selected["policy"] = str(config["policy"])
    selected["position_usdt"] = selected["target_weight"] * CAPITAL_USDT
    return selected


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


def summarize_portfolios(daily: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    combo_rows: list[dict[str, Any]] = []
    for (family, policy, combo), combo_daily in daily.groupby(["family", "policy", "combo"]):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        exposure = pd.to_numeric(combo_daily["exposure_fraction"], errors="coerce").fillna(0.0)
        active = exposure > 0.0
        std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
        sharpe = 0.0 if std == 0.0 else float(returns.mean()) / std * (252.0**0.5)
        var_95, cvar_95 = _empirical_var_cvar(returns)
        policy_trades = (
            trades.loc[(trades["family"] == family) & (trades["policy"] == policy) & (trades["combo"] == combo)]
            if not trades.empty
            else pd.DataFrame()
        )
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
                "max_cvar_95_loss_fraction": round(float(policy_frame["cvar_95_loss_fraction"].max()), 8),
                "median_cvar_95_loss_fraction": round(float(policy_frame["cvar_95_loss_fraction"].median()), 8),
                "max_drawdown_proxy": round(float(policy_frame["max_drawdown_proxy"].max()), 8),
                "median_turnover_fraction": round(float(policy_frame["mean_turnover_fraction"].median()), 8),
                "max_exposure_fraction": round(float(policy_frame["max_exposure_fraction"].max()), 8),
            }
        )
    return combo_metrics, pd.DataFrame(policy_rows)


def evaluate_family(
    predictions: pd.DataFrame,
    policies: tuple[dict[str, Any], ...] = PREDECLARED_POLICIES,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    errors = validate_policy_grid(policies)
    if errors:
        raise ValueError("; ".join(errors))
    position_frames = [select_policy(predictions, config) for config in policies]
    nonempty = [frame for frame in position_frames if not frame.empty]
    if not nonempty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    positions = pd.concat(nonempty, ignore_index=True)
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
        "p_meta_calibrated",
        "p_meta_raw",
        "sigma_ewma",
        "hmm_prob_bull",
        "meta_disagreement",
        "pnl_net_proxy",
    ]
    positions = positions[keep].copy()
    daily, trades = build_daily_returns(predictions, positions)
    combo_metrics, policy_metrics = summarize_portfolios(daily, trades)
    metrics = pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )
    return positions, daily, trades, metrics


def classify_family(metrics: pd.DataFrame) -> tuple[str, str, str, dict[str, Any]]:
    if metrics.empty:
        return "INCONCLUSIVE", "correct", "NO_META_DISAGREEMENT_POLICIES_EVALUATED", {}
    policies = metrics.loc[metrics["metric_level"] == "policy"].copy()
    if policies.empty:
        return "INCONCLUSIVE", "correct", "NO_META_DISAGREEMENT_POLICY_METRICS", {}
    policies = policies.sort_values(
        ["median_combo_sharpe", "min_combo_sharpe", "median_active_days"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    eligible = policies.loc[
        (policies["median_active_days"] >= MIN_MEDIAN_ACTIVE_DAYS)
        & (policies["median_combo_sharpe"] > 0.0)
        & (policies["min_combo_sharpe"] > 0.0)
        & (policies["max_cvar_95_loss_fraction"] <= CVAR_LIMIT)
    ]
    if not eligible.empty:
        best = eligible.sort_values(
            ["median_combo_sharpe", "min_combo_sharpe", "median_active_days"],
            ascending=[False, False, False],
            kind="mergesort",
        ).iloc[0].to_dict()
        if float(best["median_combo_sharpe"]) >= SR_NEEDED_FOR_PROMOTION:
            return "PASS", "advance", "STRONG_META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTED", best
        return "PASS", "advance", "META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTABLE", best
    best = policies.iloc[0].to_dict()
    if float(best.get("median_combo_sharpe", 0.0)) > 0.0:
        return "PARTIAL", "correct", "META_DISAGREEMENT_POSITIVE_ALPHA_UNSTABLE", best
    return "FAIL", "abandon", "META_DISAGREEMENT_NO_POSITIVE_SAFE_ALPHA", best


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
    predictions = pd.read_parquet(PHASE4_OOS_PREDICTIONS)
    positions, daily, trades, metrics_frame = evaluate_family(predictions)
    status, decision, classification, best = classify_family(metrics_frame)

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    positions_path = OUTPUT_DIR / "meta_disagreement_positions.parquet"
    daily_path = OUTPUT_DIR / "meta_disagreement_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "meta_disagreement_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "meta_disagreement_metrics.parquet"
    snapshot_path = OUTPUT_DIR / "meta_disagreement_snapshot_proxy.parquet"
    report_path = OUTPUT_DIR / "meta_disagreement_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    snapshot = positions.loc[positions["date"] == positions["date"].max()].copy() if not positions.empty else positions
    snapshot.to_parquet(snapshot_path, index=False)

    policy_metrics = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    best_median = float(best.get("median_combo_sharpe", 0.0) or 0.0)
    best_min = float(best.get("min_combo_sharpe", 0.0) or 0.0)
    best_active = float(best.get("median_active_days", 0.0) or 0.0)
    best_cvar = float(best.get("max_cvar_95_loss_fraction", 0.0) or 0.0)

    payload = {
        "hypothesis": (
            "Phase4 meta calibration disagreement can identify a materially different "
            "research/sandbox abstention policy: high BMA confidence paired with low "
            "calibrated meta probability is treated as overconfidence and shorted in sandbox only."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "selected_agenda_id": "AGENDA-H01",
        "predeclared_policies": list(PREDECLARED_POLICIES),
        "best_policy": best,
        "policy_metrics": policy_metrics.to_dict(orient="records"),
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_stage_a_eligible_as_policy_input": False,
            "uses_avg_sl_train_as_policy_input": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
            "short_exposure_research_sandbox_only": True,
            "masks_dsr": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
        "promotion": {
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
            "dsr_honest": 0.0,
            "sr_needed": SR_NEEDED_FOR_PROMOTION,
            "candidate_below_sr_needed": best_median < SR_NEEDED_FOR_PROMOTION,
        },
        "next_recommended_gate": "phase5_research_meta_disagreement_stability_falsification_gate"
        if status == "PASS"
        else "continue_agenda_if_high_medium_hypothesis_remains",
    }
    report_path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate_metrics = [
        _metric("agenda_generated", AGENDA_PATH.exists(), "true", AGENDA_PATH.exists()),
        _metric("policies_tested", len(PREDECLARED_POLICIES), ">= 5", len(PREDECLARED_POLICIES) >= 5),
        _metric("forbidden_selection_input_count", 0, "0", not validate_policy_grid()),
        _metric("best_policy", best.get("policy", ""), "predeclared policy only", bool(best)),
        _metric("best_median_combo_sharpe", best_median, "> 0 research candidate", best_median > 0.0),
        _metric("best_min_combo_sharpe", best_min, "> 0 stability candidate", best_min > 0.0),
        _metric("best_median_active_days", best_active, f">= {MIN_MEDIAN_ACTIVE_DAYS}", best_active >= MIN_MEDIAN_ACTIVE_DAYS),
        _metric("best_max_cvar_95_loss_fraction", best_cvar, f"<= {CVAR_LIMIT}", best_cvar <= CVAR_LIMIT),
        _metric("candidate_below_sr_needed", best_median < SR_NEEDED_FOR_PROMOTION, "true", best_median < SR_NEEDED_FOR_PROMOTION),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]

    source_artifacts = [artifact_record(PHASE4_OOS_PREDICTIONS), artifact_record(AGENDA_PATH)]
    generated_core = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(snapshot_path),
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
        "official_artifacts_used": [str(PHASE4_OOS_PREDICTIONS)],
        "research_artifacts_generated": [str(item["path"]) for item in generated_core],
        "summary": [
            f"classification={classification}",
            f"selected_agenda_id=AGENDA-H01",
            f"best_policy={best.get('policy', '')}",
            f"best_median_combo_sharpe={best_median:.6f}",
            f"best_min_combo_sharpe={best_min:.6f}",
            f"best_median_active_days={best_active:.1f}",
            f"best_max_cvar_95_loss_fraction={best_cvar:.8f}",
            "research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            "no realized variable used as selection input",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "candidate_requires_stability_and_falsification_before_any_deepening",
        ],
        "risks_residual": [
            "Short exposure is research/sandbox only and cannot support official promotion.",
            "The candidate remains below sr_needed and does not change DSR=0.0.",
            "A dedicated stability/falsification gate is still required before preserving the candidate as robust.",
        ],
        "next_recommended_step": payload["next_recommended_gate"],
    }
    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_core,
        "commands_executed": [
            "python services/ml_engine/phase5_research_meta_disagreement_abstention.py",
            "python -m pytest tests/unit/test_phase5_research_meta_disagreement_abstention.py -q",
        ],
        "notes": [
            "Research/sandbox-only agenda expansion gate.",
            "Uses p_bma_pkf, p_meta_calibrated and sigma_ewma as ex-ante selection inputs.",
            "pnl_real is realized outcome only.",
            "No official promotion, no paper readiness, no A3/A4 reopening.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Agenda expansion selected AGENDA-H01 and evaluated meta calibration disagreement. "
            f"Status={status}, decision={decision}, classification={classification}. "
            f"Best policy `{best.get('policy', '')}` has median Sharpe {best_median:.6f}, "
            f"min Sharpe {best_min:.6f}, median active days {best_active:.1f} and max CVaR95 {best_cvar:.8f}."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}` at `{git_context['head']}`. "
            "Input is Phase4 OOS predictions as a base artifact; no official artifact is promoted."
        ),
        "Mudanças implementadas": (
            "Added a research/sandbox runner for meta calibration disagreement abstention. "
            "Selection is based on ex-ante `p_bma_pkf`, `p_meta_calibrated` and `sigma_ewma`; "
            "`pnl_real` is used only as realized backtest outcome."
        ),
        "Artifacts gerados": "\n".join(f"- `{item['path']}`" for item in generated_core),
        "Resultados": "\n".join(gate_report["summary"]),
        "Avaliação contra gates": "\n".join(
            f"- {item['metric_name']}: {item['metric_value']} / {item['metric_threshold']} => {item['metric_status']}"
            for item in gate_metrics
        ),
        "Riscos residuais": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
        "Veredito final: advance / correct / abandon": f"{status}/{decision}. Next: {payload['next_recommended_gate']}.",
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    return gate_report


def main() -> int:
    report = run_gate()
    print(json.dumps({"gate_slug": GATE_SLUG, "status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
