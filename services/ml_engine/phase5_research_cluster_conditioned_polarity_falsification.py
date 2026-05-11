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

import phase5_research_candidate_validation as candidate_validation
import phase5_research_cluster_conditioned_polarity as cluster_gate
import phase5_research_signal_polarity_long_short as polarity

GATE_SLUG = "phase5_research_cluster_conditioned_polarity_falsification_gate"
PHASE_FAMILY = "phase5_research_cluster_conditioned_polarity_falsification"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
CLUSTER_GATE_REPORT = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_cluster_conditioned_polarity_gate" / "gate_report.json"
)
CLUSTER_GATE_PORTFOLIO_REPORT = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_cluster_conditioned_polarity_gate"
    / "portfolio_cvar_research_report.json"
)

CANDIDATE_CONFIG = {
    "family": "cluster_conditioned_polarity",
    "policy": "cluster_2_long_high_short_low_p60_h70_k3",
    "cluster_name": "cluster_2",
    "score_col": "p_bma_pkf",
    "mode": "long_high_short_low",
    "top_k": 3,
    "gross_exposure": 0.04,
    "p_bma_threshold": 0.60,
    "hmm_threshold": 0.70,
}
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_symbol_bucket(symbol: Any, modulo: int = 2) -> int:
    return candidate_validation._stable_symbol_bucket(symbol, modulo=modulo)


def apply_universe_filter(predictions: pd.DataFrame, universe_filter: str) -> pd.DataFrame:
    normalized = polarity.normalize_predictions(predictions)
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


def evaluate_config(
    predictions: pd.DataFrame,
    config: dict[str, Any],
    *,
    extra_cost_per_exposure: float = 0.0,
    universe_filter: str = "all",
) -> dict[str, Any]:
    filtered = apply_universe_filter(predictions, universe_filter)
    positions = cluster_gate.select_cluster_policy(filtered, config)
    if positions.empty:
        return {"positions": positions, "daily": pd.DataFrame(), "trades": pd.DataFrame(), "summary": {}}
    keep = [
        "family",
        "policy",
        "cluster_name",
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
    daily, trades = polarity.build_daily_returns(filtered, positions)
    if extra_cost_per_exposure:
        daily = daily.copy()
        daily["daily_return_proxy"] = daily["daily_return_proxy"] - (
            daily["exposure_fraction"] * float(extra_cost_per_exposure)
        )
    combo_metrics, policy_metrics = polarity.summarize_portfolios(daily, trades)
    metrics = pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )
    summary = policy_metrics.iloc[0].to_dict() if not policy_metrics.empty else {}
    return {"positions": positions, "daily": daily, "trades": trades, "metrics": metrics, "summary": summary}


def _scenario_passed(summary: dict[str, Any]) -> bool:
    return (
        float(summary.get("median_combo_sharpe") or 0.0) > 0.0
        and float(summary.get("min_combo_sharpe") or 0.0) > 0.0
        and float(summary.get("median_active_days") or 0.0) >= MIN_MEDIAN_ACTIVE_DAYS
        and float(summary.get("max_cvar_95_loss_fraction") or 0.0) <= CVAR_LIMIT
    )


def _scenario_record(scenario: str, scenario_type: str, summary: dict[str, Any], threshold: str) -> dict[str, Any]:
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
        "passed": _scenario_passed(summary),
    }


def temporal_scenarios(daily: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period_index in range(3):
        parts: list[pd.DataFrame] = []
        for _, combo_daily in daily.groupby("combo"):
            ordered = combo_daily.sort_values("date")
            start = period_index * len(ordered) // 3
            end = (period_index + 1) * len(ordered) // 3
            parts.append(ordered.iloc[start:end].copy())
        period_daily = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        empty_trades = pd.DataFrame(columns=["family", "policy", "combo", "date", "turnover_fraction"])
        combo_metrics, policy_metrics = polarity.summarize_portfolios(period_daily, empty_trades)
        summary = policy_metrics.iloc[0].to_dict() if not policy_metrics.empty else {}
        rows.append(
            _scenario_record(
                f"temporal_third_{period_index + 1}",
                "temporal_subperiod",
                summary,
                "median Sharpe > 0, min Sharpe > 0, median active days >= 120",
            )
        )
    return rows


def run_falsification_scenarios(predictions: pd.DataFrame) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    base_eval = evaluate_config(predictions, CANDIDATE_CONFIG)
    scenarios: list[dict[str, Any]] = [
        _scenario_record("base_candidate", "base", base_eval["summary"], "baseline candidate must remain positive")
    ]
    scenarios.extend(temporal_scenarios(base_eval["daily"]))
    for bps, cost in ((5, 0.0005), (10, 0.0010), (20, 0.0020)):
        evaluation = evaluate_config(predictions, CANDIDATE_CONFIG, extra_cost_per_exposure=cost)
        scenarios.append(
            _scenario_record(
                f"extra_cost_{bps}bps",
                "cost_stress",
                evaluation["summary"],
                "cost-stressed min Sharpe must remain > 0",
            )
        )
    for top_k in (2, 3):
        for p_threshold in (0.55, 0.60, 0.65):
            config = dict(CANDIDATE_CONFIG)
            config["top_k"] = top_k
            config["p_bma_threshold"] = p_threshold
            config["policy"] = f"cluster_2_lhsl_p{int(p_threshold * 100)}_h70_k{top_k}"
            evaluation = evaluate_config(predictions, config)
            scenarios.append(
                _scenario_record(
                    config["policy"],
                    "parameter_sensitivity",
                    evaluation["summary"],
                    "parameter variant must remain positive and active",
                )
            )
    for universe_filter in ("drop_high_sigma_q80", "symbol_hash_even", "symbol_hash_odd"):
        evaluation = evaluate_config(predictions, CANDIDATE_CONFIG, universe_filter=universe_filter)
        scenarios.append(
            _scenario_record(
                universe_filter,
                "universe_stress",
                evaluation["summary"],
                "universe stress must remain positive and active",
            )
        )
    hard_falsifiers = [
        scenario["scenario"]
        for scenario in scenarios
        if scenario["scenario"] != "base_candidate" and not scenario["passed"]
    ]
    return scenarios, hard_falsifiers, base_eval


def classify_falsification(hard_falsifiers: list[str]) -> tuple[str, str, str]:
    if hard_falsifiers:
        return "FAIL", "abandon", "CLUSTER_CONDITIONED_CANDIDATE_FALSIFIED_BY_TEMPORAL_COST_OR_UNIVERSE_STRESS"
    return "PASS", "advance", "CLUSTER_CONDITIONED_CANDIDATE_SURVIVED_FALSIFICATION_RESEARCH_ONLY"


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
    predictions = pd.read_parquet(cluster_gate.STAGE_A_PREDICTIONS)
    prior_gate = read_json(CLUSTER_GATE_REPORT)
    prior_report = read_json(CLUSTER_GATE_PORTFOLIO_REPORT)
    scenarios, hard_falsifiers, base_eval = run_falsification_scenarios(predictions)
    status, decision, classification = classify_falsification(hard_falsifiers)

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }
    scenarios_path = OUTPUT_DIR / "cluster_conditioned_polarity_falsification_scenarios.parquet"
    report_path = OUTPUT_DIR / "cluster_conditioned_polarity_falsification_report.json"
    base_positions_path = OUTPUT_DIR / "cluster_conditioned_polarity_candidate_positions.parquet"
    base_daily_path = OUTPUT_DIR / "cluster_conditioned_polarity_candidate_daily_returns.parquet"
    pd.DataFrame(scenarios).to_parquet(scenarios_path, index=False)
    base_eval["positions"].to_parquet(base_positions_path, index=False)
    base_eval["daily"].to_parquet(base_daily_path, index=False)

    payload = {
        "hypothesis": (
            "The cluster_2_long_high_short_low_p60_h70_k3 research candidate can survive temporal, "
            "cost, parameter and universe falsification without promotion."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "candidate": CANDIDATE_CONFIG,
        "base_summary": base_eval["summary"],
        "scenarios": scenarios,
        "hard_falsifiers": hard_falsifiers,
        "prior_cluster_gate_summary": prior_gate.get("summary", []),
        "prior_cluster_report_classification": prior_report.get("classification"),
        "governance": {
            "research_only": True,
            "sandbox_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_pnl_real_only_as_realized_backtest_outcome": True,
        },
    }
    report_path.write_text(json.dumps(candidate_validation.json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base_summary = base_eval["summary"]
    temporal_min = min(float(row.get("min_combo_sharpe") or 0.0) for row in scenarios if row["scenario_type"] == "temporal_subperiod")
    cost_20 = next(row for row in scenarios if row["scenario"] == "extra_cost_20bps")
    metrics = [
        _metric("base_median_combo_sharpe", base_summary.get("median_combo_sharpe"), "> 0", float(base_summary.get("median_combo_sharpe") or 0.0) > 0.0),
        _metric("base_min_combo_sharpe", base_summary.get("min_combo_sharpe"), "> 0", float(base_summary.get("min_combo_sharpe") or 0.0) > 0.0),
        _metric("temporal_subperiod_min_sharpe", round(temporal_min, 6), "> 0", temporal_min > 0.0),
        _metric("extra_cost_20bps_min_sharpe", cost_20.get("min_combo_sharpe"), "> 0", float(cost_20.get("min_combo_sharpe") or 0.0) > 0.0),
        _metric("hard_falsifier_count", len(hard_falsifiers), "0 to survive", len(hard_falsifiers) == 0),
        _metric("official_promotion_allowed", False, "false", True),
    ]
    source_artifacts = [
        artifact_record(CLUSTER_GATE_REPORT),
        artifact_record(CLUSTER_GATE_PORTFOLIO_REPORT),
        artifact_record(cluster_gate.STAGE_A_PREDICTIONS),
    ]
    generated_artifacts = [
        artifact_record(scenarios_path),
        artifact_record(report_path),
        artifact_record(base_positions_path),
        artifact_record(base_daily_path),
    ]
    next_gate = "phase5_research_cluster_conditioned_polarity_decision_gate"
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(path) for path in (scenarios_path, report_path, base_positions_path, base_daily_path)],
        "summary": [
            f"classification={classification}",
            "candidate_policy=cluster_2_long_high_short_low_p60_h70_k3",
            f"hard_falsifier_count={len(hard_falsifiers)}",
            f"hard_falsifiers={','.join(hard_falsifiers)}",
            f"base_median_combo_sharpe={base_summary.get('median_combo_sharpe')}",
            f"base_min_combo_sharpe={base_summary.get('min_combo_sharpe')}",
            f"temporal_subperiod_min_sharpe={round(temporal_min, 6)}",
            f"extra_cost_20bps_min_sharpe={cost_20.get('min_combo_sharpe')}",
            "candidate remains research/sandbox only",
            "no official promotion attempted",
            f"next_recommended_gate={next_gate}",
        ],
        "gates": metrics,
        "blockers": [
            "cluster_conditioned_candidate_falsified_by_temporal_or_cost_stress",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "Initial cluster-conditioned alpha is not stable enough for a robust research survivor.",
            "The candidate remains below sr_needed and cannot support promotion.",
            "Official CVaR remains zero exposure.",
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
            "Research-only cluster-conditioned candidate falsification gate.",
            "No official promotion, readiness, A3/A4 reopen, merge or real trading.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Cluster-conditioned falsification result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research/sandbox only."
        ),
        "Mudanças implementadas": (
            "Added temporal, cost, parameter and universe stress tests for the cluster-conditioned candidate."
        ),
        "Artifacts gerados": (
            f"- `{scenarios_path.relative_to(REPO_ROOT)}`\n"
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            f"- `{base_positions_path.relative_to(REPO_ROOT)}`\n"
            f"- `{base_daily_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Hard falsifiers: `{', '.join(hard_falsifiers)}`. Temporal min Sharpe `{round(temporal_min, 6)}`; "
            f"20 bps cost min Sharpe `{cost_20.get('min_combo_sharpe')}`."
        ),
        "Avaliação contra gates": (
            "The candidate failed robustness but no governance boundary was crossed."
        ),
        "Riscos residuais": (
            "DSR=0.0, official CVaR zero exposure and non-promotability remain."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue to `{next_gate}` to record the candidate decision."
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
