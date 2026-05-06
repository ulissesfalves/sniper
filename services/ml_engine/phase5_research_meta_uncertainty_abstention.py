#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
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

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack  # noqa: E402

import phase5_research_meta_disagreement_abstention as meta_base  # noqa: E402

GATE_SLUG = "phase5_research_meta_uncertainty_abstention_gate"
PHASE_FAMILY = "phase5_research_meta_uncertainty_abstention"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
PHASE4_OOS_PREDICTIONS = REPO_ROOT / "data" / "models" / "phase4" / "phase4_oos_predictions.parquet"
AGENDA_PATH = REPO_ROOT / "reports" / "state" / "sniper_research_agenda.yaml"

CAPITAL_USDT = 100_000.0
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
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_bma_meta_agree_p65_m50_s10_k3",
        "mode": "long_bma_meta_agree_low_sigma",
        "p_bma_min": 0.65,
        "p_meta_min": 0.50,
        "sigma_max": 1.00,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_bma_meta_agree_p60_m50_s10_k3",
        "mode": "long_bma_meta_agree_low_sigma",
        "p_bma_min": 0.60,
        "p_meta_min": 0.50,
        "sigma_max": 1.00,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_bma_meta_agree_p55_m50_s10_k3",
        "mode": "long_bma_meta_agree_low_sigma",
        "p_bma_min": 0.55,
        "p_meta_min": 0.50,
        "sigma_max": 1.00,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_bma_meta_agree_p45_m50_s10_k3",
        "mode": "long_bma_meta_agree_low_sigma",
        "p_bma_min": 0.45,
        "p_meta_min": 0.50,
        "sigma_max": 1.00,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_bma_meta_agree_p60_m55_s12_k3",
        "mode": "long_bma_meta_agree_low_sigma",
        "p_bma_min": 0.60,
        "p_meta_min": 0.55,
        "sigma_max": 1.20,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    },
    {
        "family": "meta_uncertainty_abstention_long_only",
        "policy": "long_meta_confident_m50_s10_k3",
        "mode": "long_meta_confidence_low_sigma",
        "p_meta_min": 0.50,
        "sigma_max": 1.00,
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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value) if not isinstance(value, (str, bytes, bool, type(None))) else False:
        return None
    return value


def validate_policy_grid(policies: tuple[dict[str, Any], ...] = PREDECLARED_POLICIES) -> list[str]:
    errors: list[str] = []
    for policy in policies:
        forbidden = FORBIDDEN_SELECTION_INPUTS.intersection(set(policy.get("selection_inputs", [])))
        if forbidden:
            errors.append(f"{policy.get('policy')} uses forbidden selection inputs: {sorted(forbidden)}")
        if str(policy.get("mode")) not in {"long_bma_meta_agree_low_sigma", "long_meta_confidence_low_sigma"}:
            errors.append(f"{policy.get('policy')} has unsupported mode={policy.get('mode')}")
        if float(policy.get("gross_exposure", 0.0)) <= 0.0:
            errors.append(f"{policy.get('policy')} has nonpositive gross_exposure")
    return errors


def select_policy(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = meta_base.normalize_predictions(frame)
    mode = str(config["mode"])
    sigma_mask = work["sigma_ewma"] <= float(config["sigma_max"])
    if mode == "long_bma_meta_agree_low_sigma":
        mask = work["p_bma_pkf"] >= float(config["p_bma_min"])
        mask &= work["p_meta_calibrated"] >= float(config["p_meta_min"])
        mask &= sigma_mask
        candidates = work.loc[mask].copy()
        candidates["score"] = (candidates["p_bma_pkf"] + candidates["p_meta_calibrated"] - 1.0) / candidates[
            "sigma_ewma"
        ]
    elif mode == "long_meta_confidence_low_sigma":
        mask = work["p_meta_calibrated"] >= float(config["p_meta_min"])
        mask &= sigma_mask
        candidates = work.loc[mask].copy()
        candidates["score"] = (candidates["p_meta_calibrated"] - 0.5) / candidates["sigma_ewma"]
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
    selected["target_weight"] = float(config["gross_exposure"]) / counts
    selected["family"] = str(config["family"])
    selected["policy"] = str(config["policy"])
    selected["position_usdt"] = selected["target_weight"] * CAPITAL_USDT
    return selected


def _stable_symbol_bucket(symbol: Any, modulo: int = 2) -> int:
    digest = hashlib.sha256(str(symbol).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def apply_universe_filter(predictions: pd.DataFrame, universe_filter: str) -> pd.DataFrame:
    normalized = meta_base.normalize_predictions(predictions)
    if universe_filter == "all":
        return predictions.copy()
    if universe_filter == "drop_high_sigma_q80":
        limit = float(normalized["sigma_ewma"].quantile(0.80))
        return predictions.loc[normalized["sigma_ewma"] <= limit].copy()
    if universe_filter == "symbol_hash_even":
        return predictions.loc[normalized["symbol"].map(lambda value: _stable_symbol_bucket(value) == 0)].copy()
    if universe_filter == "symbol_hash_odd":
        return predictions.loc[normalized["symbol"].map(lambda value: _stable_symbol_bucket(value) == 1)].copy()
    raise ValueError(f"unknown universe_filter={universe_filter}")


def _positions_for_metrics(positions: pd.DataFrame) -> pd.DataFrame:
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
        "pnl_net_proxy",
    ]
    existing = [column for column in keep if column in positions.columns]
    return positions[existing].copy()


def build_daily_returns_with_cost(
    predictions: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    extra_cost_per_exposure: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily, trades = meta_base.build_daily_returns(predictions, positions)
    if extra_cost_per_exposure and not daily.empty:
        daily = daily.copy()
        daily["daily_return_proxy"] = daily["daily_return_proxy"] - (
            daily["exposure_fraction"] * float(extra_cost_per_exposure)
        )
    return daily, trades


def policy_summary(metrics_frame: pd.DataFrame) -> dict[str, Any]:
    policies = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    if policies.empty:
        return {}
    return _json_safe(policies.iloc[0].to_dict())


def evaluate_config(
    predictions: pd.DataFrame,
    config: dict[str, Any],
    *,
    extra_cost_per_exposure: float = 0.0,
    universe_filter: str = "all",
) -> dict[str, Any]:
    filtered = apply_universe_filter(predictions, universe_filter)
    positions = select_policy(filtered, config)
    if positions.empty:
        return {
            "config": config,
            "positions": positions,
            "daily": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "metrics": pd.DataFrame(),
            "summary": {},
        }
    positions = _positions_for_metrics(positions)
    daily, trades = build_daily_returns_with_cost(
        filtered,
        positions,
        extra_cost_per_exposure=extra_cost_per_exposure,
    )
    combo_metrics, policy_metrics = meta_base.summarize_portfolios(daily, trades)
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


def evaluate_policy_grid(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    errors = validate_policy_grid()
    if errors:
        raise ValueError("; ".join(errors))
    position_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    metric_frames: list[pd.DataFrame] = []
    for config in PREDECLARED_POLICIES:
        result = evaluate_config(predictions, config)
        if not result["positions"].empty:
            position_frames.append(result["positions"])
            daily_frames.append(result["daily"])
            trade_frames.append(result["trades"])
            metric_frames.append(result["metrics"])
    positions = pd.concat(position_frames, ignore_index=True) if position_frames else pd.DataFrame()
    daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    metrics = pd.concat(metric_frames, ignore_index=True, sort=False) if metric_frames else pd.DataFrame()
    return positions, daily, trades, metrics


def best_policy(metrics: pd.DataFrame) -> dict[str, Any]:
    if metrics.empty:
        return {}
    policies = metrics.loc[metrics["metric_level"] == "policy"].copy()
    if policies.empty:
        return {}
    policies = policies.sort_values(
        ["median_combo_sharpe", "min_combo_sharpe", "median_active_days"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    return _json_safe(policies.iloc[0].to_dict())


def scenario_status(summary: dict[str, Any]) -> dict[str, bool]:
    median_sharpe = float(summary.get("median_combo_sharpe") or 0.0)
    min_sharpe = float(summary.get("min_combo_sharpe") or 0.0)
    median_active_days = float(summary.get("median_active_days") or 0.0)
    cvar = float(summary.get("max_cvar_95_loss_fraction") or 0.0)
    max_exposure = float(summary.get("max_exposure_fraction") or 0.0)
    return {
        "nonzero_exposure": max_exposure > 0.0,
        "positive_median_sharpe": median_sharpe > 0.0,
        "positive_min_sharpe": min_sharpe > 0.0,
        "active_days_sufficient": median_active_days >= MIN_MEDIAN_ACTIVE_DAYS,
        "cvar_within_research_limit": cvar <= CVAR_LIMIT,
    }


def build_scenarios(best: dict[str, Any]) -> list[dict[str, Any]]:
    if not best:
        return []
    policy_name = str(best["policy"])
    base_config = next(config for config in PREDECLARED_POLICIES if config["policy"] == policy_name)
    scenarios: list[dict[str, Any]] = [
        {"scenario": "base", "config": dict(base_config), "extra_cost_per_exposure": 0.0, "universe_filter": "all"}
    ]
    for cost_bps in (5, 10, 20):
        scenarios.append(
            {
                "scenario": f"cost_{cost_bps}bps",
                "config": dict(base_config),
                "extra_cost_per_exposure": cost_bps / 10_000.0,
                "universe_filter": "all",
            }
        )
    for p_bma_min in (0.50, 0.55, 0.60, 0.65):
        if base_config["mode"] == "long_bma_meta_agree_low_sigma":
            config = dict(base_config)
            config["p_bma_min"] = p_bma_min
            config["policy"] = f"sensitivity_p_bma_{int(p_bma_min * 100)}"
            scenarios.append(
                {
                    "scenario": config["policy"],
                    "config": config,
                    "extra_cost_per_exposure": 0.0,
                    "universe_filter": "all",
                }
            )
    for p_meta_min in (0.45, 0.50, 0.55):
        config = dict(base_config)
        config["p_meta_min"] = p_meta_min
        config["policy"] = f"sensitivity_p_meta_{int(p_meta_min * 100)}"
        scenarios.append(
            {"scenario": config["policy"], "config": config, "extra_cost_per_exposure": 0.0, "universe_filter": "all"}
        )
    for sigma_max in (0.80, 1.00, 1.20):
        config = dict(base_config)
        config["sigma_max"] = sigma_max
        config["policy"] = f"sensitivity_sigma_{int(sigma_max * 100)}"
        scenarios.append(
            {"scenario": config["policy"], "config": config, "extra_cost_per_exposure": 0.0, "universe_filter": "all"}
        )
    for top_k in (1, 3, 5):
        config = dict(base_config)
        config["top_k"] = top_k
        config["policy"] = f"sensitivity_top_k_{top_k}"
        scenarios.append(
            {"scenario": config["policy"], "config": config, "extra_cost_per_exposure": 0.0, "universe_filter": "all"}
        )
    for universe_filter in ("drop_high_sigma_q80", "symbol_hash_even", "symbol_hash_odd"):
        scenarios.append(
            {
                "scenario": f"universe_{universe_filter}",
                "config": dict(base_config),
                "extra_cost_per_exposure": 0.0,
                "universe_filter": universe_filter,
            }
        )
    return scenarios


def evaluate_scenarios(predictions: pd.DataFrame, scenarios: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    hard_falsifiers: list[str] = []
    for scenario in scenarios:
        result = evaluate_config(
            predictions,
            scenario["config"],
            extra_cost_per_exposure=float(scenario["extra_cost_per_exposure"]),
            universe_filter=str(scenario["universe_filter"]),
        )
        summary = result["summary"]
        status = scenario_status(summary)
        scenario_name = str(scenario["scenario"])
        row = {
            "scenario": scenario_name,
            "policy": str(scenario["config"]["policy"]),
            "mode": str(scenario["config"]["mode"]),
            "extra_cost_per_exposure": float(scenario["extra_cost_per_exposure"]),
            "universe_filter": str(scenario["universe_filter"]),
            "median_combo_sharpe": float(summary.get("median_combo_sharpe") or 0.0),
            "min_combo_sharpe": float(summary.get("min_combo_sharpe") or 0.0),
            "median_active_days": float(summary.get("median_active_days") or 0.0),
            "max_cvar_95_loss_fraction": float(summary.get("max_cvar_95_loss_fraction") or 0.0),
            "max_drawdown_proxy": float(summary.get("max_drawdown_proxy") or 0.0),
            "median_turnover_fraction": float(summary.get("median_turnover_fraction") or 0.0),
            "max_exposure_fraction": float(summary.get("max_exposure_fraction") or 0.0),
            "passed": all(status.values()),
            **status,
        }
        rows.append(row)
        if scenario_name != "base" and not row["passed"]:
            hard_falsifiers.append(scenario_name)
    return pd.DataFrame(rows), hard_falsifiers


def classify_meta_uncertainty(
    best: dict[str, Any],
    hard_falsifiers: list[str],
    *,
    policy_grid_errors: list[str],
) -> tuple[str, str, str]:
    if policy_grid_errors:
        return "FAIL", "abandon", "META_UNCERTAINTY_FORBIDDEN_SELECTION_INPUT"
    if not best:
        return "INCONCLUSIVE", "correct", "NO_META_UNCERTAINTY_POLICY_EVALUATED"
    base_status = scenario_status(best)
    if hard_falsifiers:
        return "FAIL", "abandon", "META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS"
    if all(base_status.values()):
        return "PASS", "advance", "META_UNCERTAINTY_RESEARCH_CANDIDATE_NOT_PROMOTABLE"
    if base_status["positive_median_sharpe"]:
        return "PARTIAL", "correct", "META_UNCERTAINTY_POSITIVE_ALPHA_UNSTABLE"
    return "FAIL", "abandon", "META_UNCERTAINTY_NO_POSITIVE_SAFE_ALPHA"


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
    positions, daily, trades, metrics_frame = evaluate_policy_grid(predictions)
    best = best_policy(metrics_frame)
    scenarios = build_scenarios(best)
    scenario_frame, hard_falsifiers = evaluate_scenarios(predictions, scenarios)
    policy_grid_errors = validate_policy_grid()
    status, decision, classification = classify_meta_uncertainty(
        best,
        hard_falsifiers,
        policy_grid_errors=policy_grid_errors,
    )

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    positions_path = OUTPUT_DIR / "meta_uncertainty_positions.parquet"
    daily_path = OUTPUT_DIR / "meta_uncertainty_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "meta_uncertainty_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "meta_uncertainty_metrics.parquet"
    scenario_path = OUTPUT_DIR / "meta_uncertainty_scenarios.parquet"
    report_path = OUTPUT_DIR / "meta_uncertainty_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    scenario_frame.to_parquet(scenario_path, index=False)

    best_median = float(best.get("median_combo_sharpe", 0.0) or 0.0)
    best_min = float(best.get("min_combo_sharpe", 0.0) or 0.0)
    best_active = float(best.get("median_active_days", 0.0) or 0.0)
    best_cvar = float(best.get("max_cvar_95_loss_fraction", 0.0) or 0.0)
    best_drawdown = float(best.get("max_drawdown_proxy", 0.0) or 0.0)
    best_turnover = float(best.get("median_turnover_fraction", 0.0) or 0.0)

    next_gate = (
        "phase5_research_meta_uncertainty_stability_falsification_gate"
        if status == "PASS"
        else "phase5_research_cvar_constrained_meta_sizing_gate"
        if status == "FAIL"
        else "phase5_research_meta_uncertainty_correction_gate"
    )
    payload = {
        "hypothesis": (
            "AGENDA-H02: long-only meta uncertainty/agreement abstention can generate nonzero "
            "research exposure using Phase4 BMA confidence, calibrated meta confidence and low sigma."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "selected_agenda_id": "AGENDA-H02",
        "predeclared_policies": list(PREDECLARED_POLICIES),
        "best_policy": best,
        "scenario_count": int(len(scenario_frame)),
        "hard_falsifier_count": int(len(hard_falsifiers)),
        "hard_falsifiers": hard_falsifiers,
        "scenario_metrics": scenario_frame.to_dict(orient="records"),
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "long_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_stage_a_eligible_as_policy_input": False,
            "uses_avg_sl_train_as_policy_input": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
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
        "next_recommended_gate": next_gate,
    }
    report_path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate_metrics = [
        _metric("selected_agenda_id", "AGENDA-H02", "AGENDA-H02", True),
        _metric("policies_tested", len(PREDECLARED_POLICIES), ">= 5", len(PREDECLARED_POLICIES) >= 5),
        _metric("forbidden_selection_input_count", len(policy_grid_errors), "0", not policy_grid_errors),
        _metric("best_policy", best.get("policy", ""), "predeclared policy only", bool(best)),
        _metric("best_median_combo_sharpe", best_median, "> 0 research alpha", best_median > 0.0),
        _metric("best_min_combo_sharpe", best_min, "> 0 stable alpha", best_min > 0.0),
        _metric("best_median_active_days", best_active, f">= {MIN_MEDIAN_ACTIVE_DAYS}", best_active >= MIN_MEDIAN_ACTIVE_DAYS),
        _metric("best_max_cvar_95_loss_fraction", best_cvar, f"<= {CVAR_LIMIT}", best_cvar <= CVAR_LIMIT),
        _metric("scenario_count", int(len(scenario_frame)), ">= 10", len(scenario_frame) >= 10),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0", len(hard_falsifiers) == 0),
        _metric("candidate_below_sr_needed", best_median < SR_NEEDED_FOR_PROMOTION, "true", best_median < SR_NEEDED_FOR_PROMOTION),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]

    generated_core = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(scenario_path),
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
            "selected_agenda_id=AGENDA-H02",
            f"best_policy={best.get('policy', '')}",
            f"best_median_combo_sharpe={best_median:.6f}",
            f"best_min_combo_sharpe={best_min:.6f}",
            f"best_median_active_days={best_active:.1f}",
            f"best_max_cvar_95_loss_fraction={best_cvar:.8f}",
            f"best_max_drawdown_proxy={best_drawdown:.8f}",
            f"best_median_turnover_fraction={best_turnover:.8f}",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            "long-only research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            "no realized variable used as selection input",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "meta_uncertainty_long_only_instability_under_falsification"
            if hard_falsifiers
            else "candidate_requires_deeper_stability_before_any_deepening",
        ],
        "risks_residual": [
            "Long-only research exposure remains sandbox evidence only and cannot support official promotion.",
            "The gate does not change DSR=0.0 or the official zero-exposure CVaR blocker.",
            "Any survivor would still require stability/falsification and a decision gate before preservation.",
        ],
        "next_recommended_step": next_gate,
    }
    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": [artifact_record(PHASE4_OOS_PREDICTIONS), artifact_record(AGENDA_PATH)],
        "generated_artifacts": generated_core,
        "commands_executed": [
            "python services/ml_engine/phase5_research_meta_uncertainty_abstention.py",
            "python -m pytest tests/unit/test_phase5_research_meta_uncertainty_abstention.py -q",
        ],
        "notes": [
            "Research/sandbox-only AGENDA-H02 gate.",
            "Uses p_bma_pkf, p_meta_calibrated and sigma_ewma as ex-ante selection inputs.",
            "pnl_real is realized outcome only.",
            "No official promotion, no paper readiness, no A3/A4 reopening.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"AGENDA-H02 evaluated long-only meta uncertainty/agreement abstention. "
            f"Status={status}, decision={decision}, classification={classification}. "
            f"Best policy `{best.get('policy', '')}` has median Sharpe {best_median:.6f}, "
            f"min Sharpe {best_min:.6f}, active days {best_active:.1f}, max CVaR95 {best_cvar:.8f} "
            f"and {len(hard_falsifiers)} hard falsifiers."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}` at `{git_context['head']}`. "
            "Input is Phase4 OOS predictions as base artifact; no official artifact is promoted."
        ),
        "Mudanças implementadas": (
            "Added a research/sandbox runner for long-only meta uncertainty abstention. "
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
        "Veredito final: advance / correct / abandon": f"{status}/{decision}. Next: {next_gate}.",
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
