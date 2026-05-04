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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from services.common.gate_reports import (  # noqa: E402
    GATE_REPORT_MARKDOWN_SECTIONS,
    artifact_record,
    utc_now_iso,
    write_gate_pack,
)

import phase5_research_meta_disagreement_abstention as meta_gate  # noqa: E402

GATE_SLUG = "phase5_research_meta_disagreement_stability_falsification_gate"
PHASE_FAMILY = "phase5_research_meta_disagreement_stability_falsification"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
INITIAL_GATE_REPORT = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_meta_disagreement_abstention_gate" / "gate_report.json"
)
INITIAL_RESEARCH_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_meta_disagreement_abstention_gate"
    / "meta_disagreement_research_report.json"
)

CANDIDATE_POLICY = "short_bma_high_meta_low_p60_m40_k3"
CANDIDATE_FAMILY = "meta_calibration_disagreement_abstention"
CVAR_LIMIT = 0.15
MIN_MEDIAN_ACTIVE_DAYS = 120


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_git_context() -> dict[str, Any]:
    return {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }


def candidate_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "family": CANDIDATE_FAMILY,
        "policy": CANDIDATE_POLICY,
        "mode": "short_bma_high_meta_low",
        "p_bma_min": 0.60,
        "p_meta_max": 0.40,
        "top_k": 3,
        "gross_exposure": 0.04,
        "selection_inputs": ["p_bma_pkf", "p_meta_calibrated", "sigma_ewma"],
    }
    config.update(overrides)
    if "policy" not in overrides:
        p_val = int(round(float(config["p_bma_min"]) * 100))
        m_val = int(round(float(config["p_meta_max"]) * 100))
        config["policy"] = f"short_bma_high_meta_low_p{p_val}_m{m_val}_k{int(config['top_k'])}"
    return config


def load_predictions() -> pd.DataFrame:
    return pd.read_parquet(meta_gate.PHASE4_OOS_PREDICTIONS)


def _stable_symbol_bucket(symbol: Any, modulo: int = 2) -> int:
    digest = hashlib.sha256(str(symbol).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def apply_universe_filter(predictions: pd.DataFrame, universe_filter: str) -> pd.DataFrame:
    normalized = meta_gate.normalize_predictions(predictions)
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


def build_daily_returns_with_cost(
    predictions: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    extra_cost_per_exposure: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily, trades = meta_gate.build_daily_returns(predictions, positions)
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
    return json_safe(policies.iloc[0].to_dict())


def evaluate_config(
    predictions: pd.DataFrame | None = None,
    config: dict[str, Any] | None = None,
    *,
    extra_cost_per_exposure: float = 0.0,
    universe_filter: str = "all",
) -> dict[str, Any]:
    predictions = load_predictions() if predictions is None else predictions
    config = candidate_config() if config is None else config
    filtered = apply_universe_filter(predictions, universe_filter)
    positions = meta_gate.select_policy(filtered, config)
    if positions.empty:
        return {
            "config": config,
            "positions": positions,
            "daily": pd.DataFrame(),
            "trades": pd.DataFrame(),
            "metrics": pd.DataFrame(),
            "summary": {},
        }
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
    daily, trades = build_daily_returns_with_cost(
        filtered,
        positions,
        extra_cost_per_exposure=extra_cost_per_exposure,
    )
    combo_metrics, policy_metrics = meta_gate.summarize_portfolios(daily, trades)
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


def status_from_summary(summary: dict[str, Any]) -> dict[str, bool]:
    median_sharpe = float(summary.get("median_combo_sharpe") or 0.0)
    min_sharpe = float(summary.get("min_combo_sharpe") or 0.0)
    median_active_days = float(summary.get("median_active_days") or 0.0)
    cvar = float(summary.get("max_cvar_95_loss_fraction") or 0.0)
    return {
        "positive_median_sharpe": median_sharpe > 0.0,
        "positive_min_sharpe": min_sharpe > 0.0,
        "active_days_sufficient": median_active_days >= MIN_MEDIAN_ACTIVE_DAYS,
        "cvar_within_research_limit": cvar <= CVAR_LIMIT,
    }


def scenario_passed(summary: dict[str, Any], *, min_active_days: int = MIN_MEDIAN_ACTIVE_DAYS) -> bool:
    flags = status_from_summary(summary)
    return (
        flags["positive_median_sharpe"]
        and flags["positive_min_sharpe"]
        and float(summary.get("median_active_days") or 0.0) >= min_active_days
        and flags["cvar_within_research_limit"]
    )


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


def temporal_subperiod_scenarios(daily: pd.DataFrame, *, periods: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period_index in range(periods):
        parts: list[pd.DataFrame] = []
        for _, combo_daily in daily.groupby("combo"):
            ordered = combo_daily.sort_values("date")
            start = period_index * len(ordered) // periods
            end = (period_index + 1) * len(ordered) // periods
            parts.append(ordered.iloc[start:end].copy())
        period_daily = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        empty_trades = pd.DataFrame(columns=["family", "policy", "combo", "date", "turnover_fraction"])
        combo_metrics, policy_metrics = meta_gate.summarize_portfolios(period_daily, empty_trades)
        metrics = pd.concat(
            [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
            ignore_index=True,
            sort=False,
        )
        summary = policy_summary(metrics)
        rows.append(
            scenario_record(
                f"temporal_third_{period_index + 1}",
                "temporal_subperiod",
                summary,
                threshold="median Sharpe > 0, min Sharpe > 0, median active days >= 80",
                passed=scenario_passed(summary, min_active_days=80),
                details={"period_index": period_index + 1},
            )
        )
    return rows


def leakage_control_record(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = candidate_config() if config is None else config
    policy_errors = meta_gate.validate_policy_grid((config,))
    forbidden = sorted(set(config.get("selection_inputs", [])) & meta_gate.FORBIDDEN_SELECTION_INPUTS)
    passed = not policy_errors and not forbidden
    return {
        "scenario": "leakage_control",
        "scenario_type": "leakage_control",
        "median_combo_sharpe": None,
        "min_combo_sharpe": None,
        "median_active_days": None,
        "min_active_days": None,
        "max_cvar_95_loss_fraction": None,
        "max_drawdown_proxy": None,
        "median_turnover_fraction": None,
        "threshold": "selection inputs exclude realized variables",
        "passed": passed,
        "selection_inputs": ",".join(config.get("selection_inputs", [])),
        "forbidden_selection_inputs": ",".join(forbidden),
        "policy_validation_errors": ";".join(policy_errors),
    }


def run_stability_falsification_scenarios(
    predictions: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    predictions = load_predictions() if predictions is None else predictions
    scenarios: list[dict[str, Any]] = []
    base = evaluate_config(predictions)
    base_summary = base["summary"]
    scenarios.append(
        scenario_record(
            "base_candidate",
            "base",
            base_summary,
            threshold="median/min Sharpe > 0, active days >= 120, CVaR <= 0.15",
            passed=scenario_passed(base_summary),
        )
    )
    scenarios.extend(temporal_subperiod_scenarios(base["daily"]))

    for bps, cost in ((5, 0.0005), (10, 0.0010), (20, 0.0020)):
        evaluation = evaluate_config(predictions, extra_cost_per_exposure=cost)
        summary = evaluation["summary"]
        scenarios.append(
            scenario_record(
                f"cost_{bps}bps",
                "cost_stress",
                summary,
                threshold="median/min Sharpe > 0 under added cost",
                passed=scenario_passed(summary),
                details={"extra_cost_per_exposure": cost},
            )
        )

    for p_threshold in (0.55, 0.60, 0.65):
        for meta_threshold in (0.35, 0.40, 0.45):
            for top_k in (1, 3, 5):
                config = candidate_config(
                    p_bma_min=p_threshold,
                    p_meta_max=meta_threshold,
                    top_k=top_k,
                )
                evaluation = evaluate_config(predictions, config)
                summary = evaluation["summary"]
                scenarios.append(
                    scenario_record(
                        config["policy"],
                        "parameter_sensitivity",
                        summary,
                        threshold="parameter variant keeps median/min Sharpe > 0 and active days >= 120",
                        passed=scenario_passed(summary),
                        details={
                            "p_bma_min": p_threshold,
                            "p_meta_max": meta_threshold,
                            "top_k": top_k,
                        },
                    )
                )

    for universe_filter in ("drop_high_sigma_q80", "symbol_hash_even", "symbol_hash_odd"):
        evaluation = evaluate_config(predictions, universe_filter=universe_filter)
        summary = evaluation["summary"]
        scenarios.append(
            scenario_record(
                universe_filter,
                "universe_stress",
                summary,
                threshold="universe perturbation keeps median/min Sharpe > 0 and active days >= 120",
                passed=scenario_passed(summary),
                details={"universe_filter": universe_filter},
            )
        )

    scenarios.append(leakage_control_record())
    scenario_frame = pd.DataFrame(json_safe(scenarios))
    hard_falsifiers = [
        str(row["scenario"])
        for _, row in scenario_frame.iterrows()
        if row["scenario"] not in {"base_candidate", "leakage_control"} and not bool(row["passed"])
    ]
    diagnostics = {
        "scenario_count": int(len(scenario_frame)),
        "failed_scenarios": scenario_frame.loc[~scenario_frame["passed"].astype(bool), "scenario"].astype(str).tolist(),
        "hard_falsifiers": hard_falsifiers,
        "parameter_scenario_count": int((scenario_frame["scenario_type"] == "parameter_sensitivity").sum()),
        "parameter_pass_rate": round(
            float(scenario_frame.loc[scenario_frame["scenario_type"] == "parameter_sensitivity", "passed"].mean()),
            6,
        ),
        "base_summary": base_summary,
        "base_positions": base["positions"],
        "base_daily": base["daily"],
    }
    return scenario_frame, hard_falsifiers, diagnostics


def classify_stability_falsification(
    hard_falsifiers: list[str],
    base_summary: dict[str, Any] | None = None,
    leakage_passed: bool = True,
) -> tuple[str, str, str]:
    base_summary = base_summary or {}
    if not leakage_passed:
        return "FAIL", "abandon", "META_DISAGREEMENT_CANDIDATE_FALSIFIED_BY_LEAKAGE_CONTROL"
    if not scenario_passed(base_summary):
        return "FAIL", "abandon", "META_DISAGREEMENT_CANDIDATE_BASE_METRICS_FAILED"
    if hard_falsifiers:
        return "FAIL", "abandon", "META_DISAGREEMENT_CANDIDATE_FALSIFIED_BY_STABILITY_STRESS"
    return "PASS", "advance", "META_DISAGREEMENT_CANDIDATE_SURVIVED_FALSIFICATION_RESEARCH_ONLY"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def _markdown_sections(values: dict[str, str]) -> dict[str, str]:
    sections = {section: "" for section in GATE_REPORT_MARKDOWN_SECTIONS}
    keys = list(GATE_REPORT_MARKDOWN_SECTIONS)
    defaults = {
        keys[0]: values.get("summary", ""),
        keys[1]: values.get("baseline", ""),
        keys[2]: values.get("changes", ""),
        keys[3]: values.get("artifacts", ""),
        keys[4]: values.get("results", ""),
        keys[5]: values.get("evaluation", ""),
        keys[6]: values.get("risks", ""),
        keys[7]: values.get("verdict", ""),
    }
    sections.update(defaults)
    return sections


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = load_predictions()
    initial_gate = read_json(INITIAL_GATE_REPORT)
    initial_report = read_json(INITIAL_RESEARCH_REPORT)
    scenario_frame, hard_falsifiers, diagnostics = run_stability_falsification_scenarios(predictions)
    leakage_passed = bool(
        scenario_frame.loc[scenario_frame["scenario"] == "leakage_control", "passed"].iloc[0]
        if "leakage_control" in set(scenario_frame["scenario"])
        else False
    )
    status, decision, classification = classify_stability_falsification(
        hard_falsifiers,
        diagnostics["base_summary"],
        leakage_passed,
    )
    git_context = current_git_context()

    scenarios_path = OUTPUT_DIR / "meta_disagreement_stability_falsification_scenarios.parquet"
    report_path = OUTPUT_DIR / "meta_disagreement_stability_falsification_report.json"
    base_positions_path = OUTPUT_DIR / "meta_disagreement_candidate_positions.parquet"
    base_daily_path = OUTPUT_DIR / "meta_disagreement_candidate_daily_returns.parquet"
    scenario_frame.to_parquet(scenarios_path, index=False)
    diagnostics["base_positions"].to_parquet(base_positions_path, index=False)
    diagnostics["base_daily"].to_parquet(base_daily_path, index=False)

    payload = {
        "hypothesis": (
            "The meta-disagreement research candidate short_bma_high_meta_low_p60_m40_k3 "
            "must survive temporal, cost, parameter, universe and leakage falsification before preservation."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "candidate_policy": CANDIDATE_POLICY,
        "candidate_family": CANDIDATE_FAMILY,
        "initial_gate": {
            "status": initial_gate.get("status"),
            "decision": initial_gate.get("decision"),
            "classification": initial_report.get("classification"),
        },
        "base_summary": diagnostics["base_summary"],
        "scenario_metrics": scenario_frame.to_dict(orient="records"),
        "hard_falsifiers": hard_falsifiers,
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
            "short_exposure_research_sandbox_only": True,
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
        },
        "next_recommended_gate": "phase5_research_meta_disagreement_candidate_decision_gate",
    }
    write_json(report_path, payload)

    base_summary = diagnostics["base_summary"]
    temporal_rows = scenario_frame.loc[scenario_frame["scenario_type"] == "temporal_subperiod"]
    cost_20 = scenario_frame.loc[scenario_frame["scenario"] == "cost_20bps"].iloc[0].to_dict()
    parameter_rows = scenario_frame.loc[scenario_frame["scenario_type"] == "parameter_sensitivity"]
    universe_rows = scenario_frame.loc[scenario_frame["scenario_type"] == "universe_stress"]
    failed_count = int((~scenario_frame["passed"].astype(bool)).sum())
    gate_metrics = [
        _metric("scenario_count", len(scenario_frame), ">= 35", len(scenario_frame) >= 35),
        _metric("failed_scenario_count", failed_count, "0 to survive", failed_count == 0),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 to survive", len(hard_falsifiers) == 0),
        _metric(
            "base_median_combo_sharpe",
            base_summary.get("median_combo_sharpe"),
            "> 0",
            float(base_summary.get("median_combo_sharpe") or 0.0) > 0.0,
        ),
        _metric(
            "base_min_combo_sharpe",
            base_summary.get("min_combo_sharpe"),
            "> 0",
            float(base_summary.get("min_combo_sharpe") or 0.0) > 0.0,
        ),
        _metric(
            "base_max_cvar_95_loss_fraction",
            base_summary.get("max_cvar_95_loss_fraction"),
            f"<= {CVAR_LIMIT}",
            float(base_summary.get("max_cvar_95_loss_fraction") or 0.0) <= CVAR_LIMIT,
        ),
        _metric(
            "base_max_drawdown_proxy",
            base_summary.get("max_drawdown_proxy"),
            "reported",
            base_summary.get("max_drawdown_proxy") is not None,
        ),
        _metric(
            "base_median_turnover_fraction",
            base_summary.get("median_turnover_fraction"),
            "reported",
            base_summary.get("median_turnover_fraction") is not None,
        ),
        _metric(
            "worst_temporal_min_sharpe",
            temporal_rows["min_combo_sharpe"].min(),
            "> 0",
            bool((temporal_rows["min_combo_sharpe"] > 0.0).all()),
        ),
        _metric(
            "cost_20bps_min_combo_sharpe",
            cost_20.get("min_combo_sharpe"),
            "> 0",
            float(cost_20.get("min_combo_sharpe") or 0.0) > 0.0,
        ),
        _metric(
            "parameter_pass_rate",
            diagnostics["parameter_pass_rate"],
            "1.0 for full stability",
            diagnostics["parameter_pass_rate"] >= 1.0,
        ),
        _metric(
            "universe_stress_passed",
            bool(universe_rows["passed"].all()),
            "true",
            bool(universe_rows["passed"].all()),
        ),
        _metric("leakage_control_passed", leakage_passed, "true", leakage_passed),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]

    source_artifacts = [
        artifact_record(meta_gate.PHASE4_OOS_PREDICTIONS),
        artifact_record(INITIAL_GATE_REPORT),
        artifact_record(INITIAL_RESEARCH_REPORT),
    ]
    generated_artifacts = [
        artifact_record(scenarios_path),
        artifact_record(report_path),
        artifact_record(base_positions_path),
        artifact_record(base_daily_path),
    ]
    next_gate = "phase5_research_meta_disagreement_candidate_decision_gate"
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [str(meta_gate.PHASE4_OOS_PREDICTIONS)],
        "research_artifacts_generated": [str(item["path"]) for item in generated_artifacts],
        "summary": [
            f"classification={classification}",
            f"candidate_policy={CANDIDATE_POLICY}",
            f"scenario_count={len(scenario_frame)}",
            f"failed_scenario_count={failed_count}",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            f"base_median_combo_sharpe={base_summary.get('median_combo_sharpe')}",
            f"base_min_combo_sharpe={base_summary.get('min_combo_sharpe')}",
            f"base_max_cvar_95_loss_fraction={base_summary.get('max_cvar_95_loss_fraction')}",
            f"base_max_drawdown_proxy={base_summary.get('max_drawdown_proxy')}",
            f"base_median_turnover_fraction={base_summary.get('median_turnover_fraction')}",
            f"cost_20bps_min_combo_sharpe={cost_20.get('min_combo_sharpe')}",
            "candidate remains research/sandbox only",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
            f"next_recommended_gate={next_gate}",
        ],
        "gates": gate_metrics,
        "blockers": [
            "meta_disagreement_candidate_requires_decision_gate",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "short_exposure_research_sandbox_only",
        ],
        "risks_residual": [
            "Research/sandbox short exposure is not official support.",
            "DSR=0.0 and sr_needed=4.47 remain promotion blockers.",
            "Any hard falsifier must be recorded by the candidate decision gate before selecting another hypothesis.",
        ],
        "next_recommended_step": f"Execute the next safe in-repo gate automatically: {next_gate}.",
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
            "Research-only meta-disagreement stability/falsification gate.",
            "Tests temporal stability, costs, p/meta thresholds, top-k, universe stress, leakage controls, CVaR, turnover and drawdown.",
            "No official promotion, no paper readiness, no merge, no A3/A4 reopen.",
        ],
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=_markdown_sections(
            {
                "summary": f"Meta-disagreement stability/falsification result: `{status}/{decision}`. Classification: `{classification}`.",
                "baseline": f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research/sandbox only.",
                "changes": "Added autonomous stability/falsification sweeps for temporal subperiods, costs, p/meta thresholds, top-k, universe stress and leakage controls.",
                "artifacts": "\n".join(f"- `{item['path']}`" for item in generated_artifacts),
                "results": f"Hard falsifiers: `{', '.join(hard_falsifiers) or 'none'}`. Scenario count `{len(scenario_frame)}`.",
                "evaluation": "The gate preserves research/official separation and forwards to the candidate decision gate.",
                "risks": "DSR=0.0, official CVaR zero exposure and non-promotability remain active blockers.",
                "verdict": f"`{status}/{decision}`. Continue to `{next_gate}`.",
            }
        ),
    )
    return gate_report


def main() -> int:
    report = run_gate()
    print(json.dumps({"gate_slug": GATE_SLUG, "status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
