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

import phase5_research_signal_polarity_long_short as polarity

GATE_SLUG = "phase5_research_cluster_conditioned_polarity_gate"
PHASE_FAMILY = "phase5_research_cluster_conditioned_polarity"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
RESEARCH_BASELINE_DIR = REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline"
STAGE_A_PREDICTIONS = RESEARCH_BASELINE_DIR / "stage_a_predictions.parquet"
POST_FALSIFICATION_REAUDIT_GATE = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_post_candidate_falsification_global_reaudit_gate"
    / "gate_report.json"
)

SR_NEEDED_FOR_PROMOTION = 4.47
CVAR_LIMIT = 0.15
MIN_MEDIAN_ACTIVE_DAYS = 120
CLUSTERS = ("cluster_1", "cluster_2", "cluster_3")
POLICY_SPECS: tuple[tuple[str, int, float, float], ...] = (
    ("short_high", 1, 0.60, 0.70),
    ("short_high", 3, 0.60, 0.70),
    ("short_high", 1, 0.70, 0.85),
    ("short_high", 3, 0.70, 0.85),
    ("long_high_short_low", 1, 0.60, 0.70),
    ("long_high_short_low", 3, 0.60, 0.70),
    ("long_high_short_low", 1, 0.70, 0.85),
    ("long_high_short_low", 3, 0.70, 0.85),
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_policy_grid() -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    for cluster_name in CLUSTERS:
        for mode, top_k, p_bma_threshold, hmm_threshold in POLICY_SPECS:
            p_token = int(round(p_bma_threshold * 100))
            h_token = int(round(hmm_threshold * 100))
            policies.append(
                {
                    "family": "cluster_conditioned_polarity",
                    "policy": f"{cluster_name}_{mode}_p{p_token}_h{h_token}_k{top_k}",
                    "cluster_name": cluster_name,
                    "score_col": "p_bma_pkf",
                    "mode": mode,
                    "top_k": top_k,
                    "gross_exposure": 0.04,
                    "p_bma_threshold": p_bma_threshold,
                    "hmm_threshold": hmm_threshold,
                }
            )
    return policies


def select_cluster_policy(predictions: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    normalized = polarity.normalize_predictions(predictions)
    cluster_frame = normalized.loc[normalized["cluster_name"].astype(str) == str(config["cluster_name"])].copy()
    selected = polarity.select_policy(cluster_frame, config)
    if selected.empty:
        return selected
    selected["cluster_name"] = str(config["cluster_name"])
    return selected


def evaluate_cluster_family(
    predictions: pd.DataFrame,
    policies: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    policy_grid = build_policy_grid() if policies is None else policies
    position_frames = [select_cluster_policy(predictions, config) for config in policy_grid]
    nonempty = [frame for frame in position_frames if not frame.empty]
    if not nonempty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    positions = pd.concat(nonempty, ignore_index=True)
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
    daily, trades = polarity.build_daily_returns(predictions, positions)
    combo_metrics, policy_metrics = polarity.summarize_portfolios(daily, trades)
    return positions, daily, trades, pd.concat(
        [combo_metrics.assign(metric_level="combo"), policy_metrics.assign(metric_level="policy")],
        ignore_index=True,
        sort=False,
    )


def classify_cluster_family(metrics: pd.DataFrame) -> tuple[str, str, str, dict[str, Any]]:
    if metrics.empty:
        return "INCONCLUSIVE", "correct", "NO_CLUSTER_POLICIES_EVALUATED", {}
    policies = metrics.loc[metrics["metric_level"] == "policy"].copy()
    if policies.empty:
        return "INCONCLUSIVE", "correct", "NO_CLUSTER_POLICY_METRICS", {}
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
            ["median_combo_sharpe", "min_combo_sharpe"],
            ascending=[False, False],
            kind="mergesort",
        ).iloc[0].to_dict()
        if float(best["median_combo_sharpe"]) >= SR_NEEDED_FOR_PROMOTION:
            return "PASS", "advance", "STRONG_CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_NOT_PROMOTED", best
        return "PASS", "advance", "CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_NOT_PROMOTABLE", best
    best = policies.iloc[0].to_dict()
    if float(best.get("median_combo_sharpe", 0.0)) > 0.0:
        return "PARTIAL", "correct", "WEAK_CLUSTER_CONDITIONED_ALPHA_UNSTABLE", best
    return "FAIL", "abandon", "CLUSTER_CONDITIONED_POLARITY_NO_POSITIVE_SAFE_ALPHA", best


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
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    prior_reaudit = read_json(POST_FALSIFICATION_REAUDIT_GATE)
    policy_grid = build_policy_grid()
    positions, daily, trades, metrics_frame = evaluate_cluster_family(predictions, policy_grid)
    status, decision, classification, best = classify_cluster_family(metrics_frame)

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    positions_path = OUTPUT_DIR / "cluster_conditioned_polarity_positions.parquet"
    daily_path = OUTPUT_DIR / "cluster_conditioned_polarity_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "cluster_conditioned_polarity_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "cluster_conditioned_polarity_metrics.parquet"
    snapshot_path = OUTPUT_DIR / "cluster_conditioned_polarity_snapshot_proxy.parquet"
    report_path = OUTPUT_DIR / "portfolio_cvar_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    snapshot = positions.loc[positions["date"] == positions["date"].max()].copy() if not positions.empty else positions
    snapshot.to_parquet(snapshot_path, index=False)

    policy_metrics = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    payload = {
        "hypothesis": (
            "Cluster-conditioned signal polarity can use the ex-ante cluster_name partition plus "
            "p_bma/hmm filters to find a materially different research/sandbox candidate after "
            "short_high_p_bma_k3_p60_h70 was falsified."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "predeclared_policies": policy_grid,
        "best_policy": best,
        "policy_metrics": policy_metrics.to_dict(orient="records"),
        "prior_post_falsification_reaudit_summary": prior_reaudit.get("summary", []),
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
            "cluster_name_treated_as_ex_ante_partition": True,
            "masks_dsr": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    best_median = float(best.get("median_combo_sharpe", 0.0) or 0.0)
    best_min = float(best.get("min_combo_sharpe", 0.0) or 0.0)
    best_active = float(best.get("median_active_days", 0.0) or 0.0)
    best_cvar = float(best.get("max_cvar_95_loss_fraction", 0.0) or 0.0)
    gate_metrics = [
        _metric("policies_tested", len(policy_grid), ">= 12", len(policy_grid) >= 12),
        _metric("best_policy", best.get("policy", ""), "predeclared policy only", bool(best)),
        _metric("best_median_combo_sharpe", best_median, "> 0 research candidate", best_median > 0.0),
        _metric("best_min_combo_sharpe", best_min, "> 0 stability candidate", best_min > 0.0),
        _metric("best_median_active_days", best_active, f">= {MIN_MEDIAN_ACTIVE_DAYS}", best_active >= MIN_MEDIAN_ACTIVE_DAYS),
        _metric("best_max_cvar_95_loss_fraction", best_cvar, f"<= {CVAR_LIMIT}", best_cvar <= CVAR_LIMIT),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(POST_FALSIFICATION_REAUDIT_GATE)]
    generated_artifacts = [
        artifact_record(positions_path),
        artifact_record(daily_path),
        artifact_record(trades_path),
        artifact_record(metrics_path),
        artifact_record(snapshot_path),
        artifact_record(report_path),
    ]
    next_gate = (
        "phase5_research_cluster_conditioned_polarity_falsification_gate"
        if status == "PASS" and decision == "advance"
        else "phase5_post_candidate_falsification_governed_freeze_gate"
    )
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
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
            f"policies_tested={len(policy_grid)}",
            f"best_policy={best.get('policy', '')}",
            f"best_family={best.get('family', '')}",
            f"best_median_combo_sharpe={best_median}",
            f"best_min_combo_sharpe={best_min}",
            f"best_median_active_days={best_active}",
            f"best_max_cvar_95_loss_fraction={best_cvar}",
            "cluster_name used only as ex-ante partition",
            "pnl_real used only as realized outcome, never ex-ante selection",
            "no official promotion attempted",
            f"next_recommended_gate={next_gate}",
        ],
        "gates": gate_metrics,
        "blockers": [
            "candidate_below_required_honest_dsr_sharpe",
            "needs_stability_and_falsification",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "Cluster-conditioned candidate is research/sandbox only and not official support.",
            "Initial positive min Sharpe can still fail temporal, parameter or cost falsification.",
            "DSR remains 0.0 and official CVaR remains zero exposure.",
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
            "Research-only cluster-conditioned polarity family gate.",
            "Uses cluster_name, p_bma_pkf and hmm_prob_bull as ex-ante selection inputs.",
            "pnl_real is realized backtest outcome only.",
            "No official promotion, paper readiness, merge, A3/A4 reopen or threshold relaxation.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Cluster-conditioned polarity result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{git_context['branch']}`, commit `{git_context['head']}`. Research/sandbox only."
        ),
        "Mudanças implementadas": (
            "Added a materially different cluster-conditioned polarity family using ex-ante cluster partitioning."
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
            f"Best policy `{best.get('policy', '')}` had median Sharpe `{best_median}`, min Sharpe "
            f"`{best_min}`, median active days `{best_active}`, and max CVaR95 `{best_cvar}`."
        ),
        "Avaliação contra gates": (
            "The gate creates nonzero research exposure and a candidate for falsification, while preserving all promotion blockers."
        ),
        "Riscos residuais": (
            "The candidate is below sr_needed, not official, and still requires falsification before any survival claim."
        ),
        "Veredito final: advance / correct / abandon": (
            f"`{decision}`. Continue automatically to `{next_gate}` if safe."
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
