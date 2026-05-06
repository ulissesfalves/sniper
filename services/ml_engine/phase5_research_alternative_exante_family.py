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

GATE_SLUG = "phase5_research_alternative_exante_family_gate"
PHASE_FAMILY = "phase5_research_alternative_exante_family"
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
DEEP_DIAGNOSTIC_GATE = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_deep_quant_diagnostic_gate" / "gate_report.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

CAPITAL_USDT = 100_000.0
CVAR_ALPHA = 0.05
CVAR_LIMIT = 0.15
STRESS_RHO1_MULTIPLIER = 2.0627128075074257
SR_NEEDED_FOR_PROMOTION = 4.47
MIN_MEDIAN_ACTIVE_DAYS = 120

PREDECLARED_POLICIES: tuple[dict[str, Any], ...] = (
    {
        "family": "volatility_targeted_topk",
        "policy": "vt_top3_p55_h55",
        "top_k": 3,
        "p_bma_threshold": 0.55,
        "hmm_threshold": 0.55,
        "gross_exposure": 0.03,
        "score_mode": "edge_inverse_vol",
    },
    {
        "family": "volatility_targeted_topk",
        "policy": "vt_top3_p65_h55",
        "top_k": 3,
        "p_bma_threshold": 0.65,
        "hmm_threshold": 0.55,
        "gross_exposure": 0.03,
        "score_mode": "edge_inverse_vol",
    },
    {
        "family": "risk_budgeted_topk",
        "policy": "rb_top5_p52_h50",
        "top_k": 5,
        "p_bma_threshold": 0.52,
        "hmm_threshold": 0.50,
        "gross_exposure": 0.05,
        "score_mode": "edge_inverse_vol_uniqueness",
    },
    {
        "family": "regime_filtered_defensive_ensemble",
        "policy": "ensemble_top3",
        "top_k": 3,
        "p_bma_threshold": 0.58,
        "hmm_threshold": 0.90,
        "gross_exposure": 0.025,
        "score_mode": "defensive_ensemble",
        "p_stage_quantile_min": 0.80,
        "sigma_quantile_max": 0.75,
    },
    {
        "family": "uncertainty_abstention",
        "policy": "abstention_conf_top2",
        "top_k": 2,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.70,
        "gross_exposure": 0.02,
        "score_mode": "confidence_inverse_vol",
        "confidence_threshold": 0.10,
        "uniqueness_threshold": 0.20,
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


def _zscore(series: pd.Series) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").fillna(0.0)
    std = float(clean.std(ddof=0))
    if std == 0.0:
        return pd.Series(0.0, index=clean.index)
    return (clean - float(clean.mean())) / std


def normalize_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["combo"] = work["combo"].astype(str)
    work["symbol"] = work["symbol"].astype(str)
    work["p_bma_pkf"] = _safe_numeric(work, "p_bma_pkf", 0.5)
    work["p_stage_a_raw"] = _safe_numeric(work, "p_stage_a_raw", 0.0)
    work["sigma_ewma"] = _safe_numeric(work, "sigma_ewma", 1.0).clip(lower=1e-6)
    work["uniqueness"] = _safe_numeric(work, "uniqueness", 0.0).clip(lower=0.0)
    work["hmm_prob_bull"] = _safe_numeric(work, "hmm_prob_bull", 0.0).clip(lower=0.0, upper=1.0)
    work["pnl_real"] = _safe_numeric(work, "pnl_real", 0.0)
    work["slippage_frac"] = _safe_numeric(work, "slippage_frac", 0.0)
    work["pnl_net_proxy"] = work["pnl_real"] - work["slippage_frac"]
    work["p_bma_edge"] = (work["p_bma_pkf"] - 0.5).clip(lower=0.0)
    work["confidence"] = (work["p_bma_pkf"] - 0.5).abs()
    work["p_bma_z"] = _zscore(work["p_bma_pkf"])
    work["p_stage_z"] = _zscore(work["p_stage_a_raw"])
    work["hmm_z"] = _zscore(work["hmm_prob_bull"])
    work["sigma_z"] = _zscore(work["sigma_ewma"])
    return work


def score_policy(work: pd.DataFrame, config: dict[str, Any]) -> pd.Series:
    mode = str(config["score_mode"])
    if mode == "edge_inverse_vol":
        return work["p_bma_edge"] / work["sigma_ewma"] * work["hmm_prob_bull"]
    if mode == "edge_inverse_vol_uniqueness":
        return work["p_bma_edge"] / work["sigma_ewma"] * (1.0 + work["uniqueness"]) * work["hmm_prob_bull"]
    if mode == "defensive_ensemble":
        return 0.55 * work["p_bma_z"] + 0.25 * work["p_stage_z"] + 0.20 * work["hmm_z"] - 0.20 * work["sigma_z"]
    if mode == "confidence_inverse_vol":
        return work["confidence"] / work["sigma_ewma"] * (1.0 + work["uniqueness"]) * work["hmm_prob_bull"]
    raise ValueError(f"unknown score_mode={mode}")


def select_policy(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    work = normalize_predictions(frame)
    mask = work["p_bma_pkf"] >= float(config["p_bma_threshold"])
    mask &= work["hmm_prob_bull"] >= float(config["hmm_threshold"])
    if "p_stage_quantile_min" in config:
        cutoff = float(work["p_stage_a_raw"].quantile(float(config["p_stage_quantile_min"])))
        mask &= work["p_stage_a_raw"] >= cutoff
    if "sigma_quantile_max" in config:
        cutoff = float(work["sigma_ewma"].quantile(float(config["sigma_quantile_max"])))
        mask &= work["sigma_ewma"] <= cutoff
    if "confidence_threshold" in config:
        mask &= work["confidence"] >= float(config["confidence_threshold"])
    if "uniqueness_threshold" in config:
        mask &= work["uniqueness"] >= float(config["uniqueness_threshold"])

    candidates = work.loc[mask].copy()
    if candidates.empty:
        candidates["score"] = pd.Series(dtype="float64")
        candidates["target_weight"] = pd.Series(dtype="float64")
        return candidates
    candidates["score"] = score_policy(candidates, config)
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
    selected["raw_weight"] = selected["score"] / selected["sigma_ewma"].clip(lower=1e-6)
    gross = float(config["gross_exposure"])
    denom = selected.groupby(["combo", "date"])["raw_weight"].transform("sum").replace(0.0, pd.NA)
    selected["target_weight"] = (selected["raw_weight"] / denom).fillna(0.0) * gross
    selected["family"] = str(config["family"])
    selected["policy"] = str(config["policy"])
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
    daily_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    if positions.empty:
        position_keys: set[tuple[str, str, str]] = set()
    else:
        position_keys = set(zip(positions["family"], positions["policy"], positions["combo"]))

    for family, policy in positions[["family", "policy"]].drop_duplicates().itertuples(index=False, name=None):
        policy_positions = positions.loc[(positions["family"] == family) & (positions["policy"] == policy)].copy()
        for combo, combo_frame in work.groupby("combo"):
            dates = pd.to_datetime(combo_frame["date"]).dropna().sort_values().unique()
            returns = pd.Series(0.0, index=pd.to_datetime(dates))
            exposure = pd.Series(0.0, index=pd.to_datetime(dates))
            stress = pd.Series(0.0, index=pd.to_datetime(dates))
            combo_positions = policy_positions.loc[policy_positions["combo"] == combo]
            if not combo_positions.empty:
                date_returns = (
                    combo_positions.assign(contribution=combo_positions["target_weight"] * combo_positions["pnl_net_proxy"])
                    .groupby("date")["contribution"]
                    .sum()
                )
                date_exposure = combo_positions.groupby("date")["target_weight"].apply(lambda value: value.abs().sum())
                date_stress = (
                    combo_positions.assign(stress_loss=combo_positions["target_weight"].abs() * combo_positions["sigma_ewma"])
                    .groupby("date")["stress_loss"]
                    .sum()
                    * STRESS_RHO1_MULTIPLIER
                )
                returns.loc[date_returns.index] = date_returns.values
                exposure.loc[date_exposure.index] = date_exposure.values
                stress.loc[date_stress.index] = date_stress.values
            for date_value in returns.index:
                daily_rows.append(
                    {
                        "family": str(family),
                        "policy": str(policy),
                        "combo": str(combo),
                        "date": date_value,
                        "daily_return_proxy": float(returns.loc[date_value]),
                        "exposure_fraction": float(exposure.loc[date_value]),
                        "stress_rho1_loss_fraction": float(stress.loc[date_value]),
                    }
                )

            if not combo_positions.empty:
                weights = combo_positions.pivot_table(
                    index="date",
                    columns="symbol",
                    values="target_weight",
                    aggfunc="sum",
                    fill_value=0.0,
                ).sort_index()
                deltas = weights.diff().fillna(weights).abs().sum(axis=1).rename("turnover_fraction").reset_index()
                deltas["family"] = str(family)
                deltas["policy"] = str(policy)
                deltas["combo"] = str(combo)
                trade_frames.append(deltas[["family", "policy", "combo", "date", "turnover_fraction"]])

    if not position_keys:
        return pd.DataFrame(), pd.DataFrame()
    daily = pd.DataFrame(daily_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    return daily, trades


def summarize_portfolios(daily: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    combo_rows: list[dict[str, Any]] = []
    for (family, policy, combo), combo_daily in daily.groupby(["family", "policy", "combo"]):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        exposure = pd.to_numeric(combo_daily["exposure_fraction"], errors="coerce").fillna(0.0)
        stress = pd.to_numeric(combo_daily["stress_rho1_loss_fraction"], errors="coerce").fillna(0.0)
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
                "mean_exposure_fraction": round(float(exposure.mean()), 8) if len(exposure) else 0.0,
                "mean_turnover_fraction": round(float(policy_trades["turnover_fraction"].mean()), 8)
                if not policy_trades.empty
                else 0.0,
                "max_stress_rho1_loss_fraction": round(float(stress.max()), 8) if len(stress) else 0.0,
                "median_stress_rho1_loss_fraction": round(float(stress.loc[active].median()), 8)
                if active.any()
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
                "max_stress_rho1_loss_fraction": round(float(policy_frame["max_stress_rho1_loss_fraction"].max()), 8),
            }
        )
    return combo_metrics, pd.DataFrame(policy_rows)


def evaluate_families(predictions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    position_frames = [select_policy(predictions, config) for config in PREDECLARED_POLICIES]
    positions = pd.concat([frame for frame in position_frames if not frame.empty], ignore_index=True)
    if positions.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    keep_columns = [
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
        "uniqueness",
        "pnl_net_proxy",
    ]
    positions = positions[keep_columns].copy()
    daily, trades = build_daily_returns(predictions, positions)
    combo_metrics, policy_metrics = summarize_portfolios(daily, trades)
    return positions, daily, trades, pd.concat([combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")], ignore_index=True, sort=False)


def classify_family(policy_metrics: pd.DataFrame) -> tuple[str, str, str, dict[str, Any]]:
    if policy_metrics.empty:
        return "INCONCLUSIVE", "correct", "NO_POLICIES_EVALUATED", {}
    policies = policy_metrics.loc[policy_metrics["metric_level"] == "policy"].copy()
    policies = policies.sort_values(
        ["median_combo_sharpe", "median_active_days", "min_combo_sharpe"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    best = policies.iloc[0].to_dict()
    active_positive = policies.loc[
        (policies["median_active_days"] >= MIN_MEDIAN_ACTIVE_DAYS)
        & (policies["median_combo_sharpe"] > 0.0)
        & (policies["max_cvar_95_loss_fraction"] <= CVAR_LIMIT)
    ]
    if not active_positive.empty:
        best_active = active_positive.sort_values(
            ["median_combo_sharpe", "min_combo_sharpe"], ascending=[False, False], kind="mergesort"
        ).iloc[0].to_dict()
        if (
            float(best_active["median_combo_sharpe"]) >= SR_NEEDED_FOR_PROMOTION
            and float(best_active["min_combo_sharpe"]) > 0.0
        ):
            return "PASS", "advance", "STRONG_RESEARCH_ONLY_EXANTE_FAMILY_CANDIDATE_NOT_PROMOTED", best_active
        return "PARTIAL", "correct", "WEAK_POSITIVE_EXANTE_FAMILY_UNSTABLE_NOT_PROMOTABLE", best_active
    if float(best.get("median_combo_sharpe", 0.0)) > 0.0:
        return "PARTIAL", "correct", "POSITIVE_ALPHA_BUT_INSUFFICIENT_ACTIVE_HISTORY_OR_STABILITY", best
    return "FAIL", "abandon", "ALTERNATIVE_EXANTE_FAMILIES_NO_POSITIVE_SAFE_MEDIAN_ALPHA", best


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    deep_gate = _read_json(DEEP_DIAGNOSTIC_GATE)
    positions, daily, trades, metrics_frame = evaluate_families(predictions)
    status, decision, classification, best = classify_family(metrics_frame)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    positions_path = OUTPUT_DIR / "alternative_exante_family_positions.parquet"
    daily_path = OUTPUT_DIR / "alternative_exante_family_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "alternative_exante_family_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "alternative_exante_family_metrics.parquet"
    snapshot_path = OUTPUT_DIR / "alternative_exante_family_snapshot_proxy.parquet"
    cvar_report_path = OUTPUT_DIR / "portfolio_cvar_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    if positions.empty:
        snapshot = positions.copy()
    else:
        latest_date = positions["date"].max()
        snapshot = positions.loc[positions["date"] == latest_date].copy()
    snapshot.to_parquet(snapshot_path, index=False)

    policy_metrics = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    families_tested = sorted(policy_metrics["family"].dropna().astype(str).unique().tolist()) if not policy_metrics.empty else []
    positive_policy_count = int((policy_metrics["median_combo_sharpe"] > 0.0).sum()) if not policy_metrics.empty else 0
    cvar_payload = {
        "hypothesis": (
            "Materially different ex-ante families using p_bma/sigma/hmm/uncertainty can generate "
            "nonzero research exposure and improve alpha without Stage A realized eligibility or rank_score repetition."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "predeclared_policies": list(PREDECLARED_POLICIES),
        "families_tested": families_tested,
        "best_policy": best,
        "positive_policy_count": positive_policy_count,
        "policy_metrics": policy_metrics.to_dict(orient="records"),
        "prior_deep_diagnostic_summary": deep_gate.get("summary", []),
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_stage_a_eligible_as_policy_input": False,
            "uses_rank_score_stage_a_as_primary_family": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
            "masks_dsr": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
    }
    cvar_report_path.write_text(json.dumps(cvar_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "families_tested",
            "metric_value": families_tested,
            "metric_threshold": ">= 3 material alternatives in this gate",
            "metric_status": "PASS" if len(families_tested) >= 3 else "FAIL",
        },
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
            "metric_threshold": f">= {SR_NEEDED_FOR_PROMOTION} for promotion; > 0 for candidate",
            "metric_status": "PASS" if float(best.get("median_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_min_combo_sharpe",
            "metric_value": best.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0 for stable candidate",
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
            "metric_name": "positive_policy_count",
            "metric_value": positive_policy_count,
            "metric_threshold": "> 0",
            "metric_status": "PASS" if positive_policy_count > 0 else "FAIL",
        },
    ]
    generated_artifacts = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(snapshot_path),
        artifact_record(cvar_report_path),
    ]
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(DEEP_DIAGNOSTIC_GATE)]
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
            str(cvar_report_path),
        ],
        "summary": [
            f"classification={classification}",
            f"families_tested={','.join(families_tested)}",
            f"best_policy={best.get('policy', '')}",
            f"best_family={best.get('family', '')}",
            f"best_median_combo_sharpe={best.get('median_combo_sharpe', 0.0)}",
            f"best_min_combo_sharpe={best.get('min_combo_sharpe', 0.0)}",
            f"best_median_active_days={best.get('median_active_days', 0.0)}",
            f"best_max_cvar_95_loss_fraction={best.get('max_cvar_95_loss_fraction', 0.0)}",
            "pnl_real used only as realized backtest outcome, never ex-ante selection",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": [
            "alternative_family_not_promotable",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_combo_or_active_history_instability",
        ],
        "risks_residual": [
            "The best alternative may be positive but insufficiently active or unstable.",
            "Research CVaR with exposure does not become official economic CVaR.",
            "No official policy was changed; further correction must remain research-only.",
        ],
        "next_recommended_step": (
            "If PARTIAL, attempt one active-history/stability correction inside the alternative family; "
            "otherwise compare/freeze the tested families."
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
            "Research-only alternative ex-ante family gate.",
            "Does not use stage_a_eligible, avg_sl_train, pnl_real, or realized labels as selection rules.",
            "pnl_real is used only to score realized backtest outcomes.",
            "No official artifacts were promoted.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Alternative ex-ante family gate result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. The gate tests research/sandbox portfolios only."
        ),
        "MudanÃ§as implementadas": (
            "Added predeclared volatility-targeted, risk-budgeted, defensive ensemble and uncertainty "
            "abstention research families based on ex-ante p_bma/sigma/hmm/uncertainty features."
        ),
        "Artifacts gerados": (
            f"- `{positions_path.relative_to(REPO_ROOT)}`\n"
            f"- `{daily_path.relative_to(REPO_ROOT)}`\n"
            f"- `{trades_path.relative_to(REPO_ROOT)}`\n"
            f"- `{metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{snapshot_path.relative_to(REPO_ROOT)}`\n"
            f"- `{cvar_report_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Best policy `{best.get('policy', '')}` from `{best.get('family', '')}` had median combo Sharpe "
            f"`{best.get('median_combo_sharpe', 0.0)}`, min combo Sharpe `{best.get('min_combo_sharpe', 0.0)}`, "
            f"median active days `{best.get('median_active_days', 0.0)}`, and max CVaR95 loss "
            f"`{best.get('max_cvar_95_loss_fraction', 0.0)}`."
        ),
        "AvaliaÃ§Ã£o contra gates": (
            "The gate generates nonzero research exposure and CVaR metrics, but promotion requires stable "
            "positive cross-combo performance and DSR clearance. Those blockers remain."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, official CVaR remains zero exposure, and cross-sectional remains not promotable."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue only with a bounded research correction or final family comparison."
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
