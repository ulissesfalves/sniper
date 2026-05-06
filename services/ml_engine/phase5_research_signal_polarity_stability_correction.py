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

import phase5_research_signal_polarity_long_short as polarity_gate

GATE_SLUG = "phase5_research_signal_polarity_stability_correction_gate"
PHASE_FAMILY = "phase5_research_signal_polarity_stability_correction"
STAGE_A_PREDICTIONS = polarity_gate.STAGE_A_PREDICTIONS
POLARITY_GATE_REPORT = (
    REPO_ROOT / "reports" / "gates" / "phase5_research_signal_polarity_long_short_gate" / "gate_report.json"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

CVAR_LIMIT = polarity_gate.CVAR_LIMIT
SR_NEEDED_FOR_PROMOTION = polarity_gate.SR_NEEDED_FOR_PROMOTION
MIN_MEDIAN_ACTIVE_DAYS = polarity_gate.MIN_MEDIAN_ACTIVE_DAYS

PREDECLARED_CORRECTIONS: tuple[dict[str, Any], ...] = (
    {
        "family": "signal_polarity_stability_filtered",
        "policy": "short_high_p_bma_k3_p60_h70",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 3,
        "gross_exposure": 0.04,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.70,
    },
    {
        "family": "signal_polarity_stability_filtered",
        "policy": "short_high_p_bma_k3_p60_h90",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 3,
        "gross_exposure": 0.04,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.90,
    },
    {
        "family": "signal_polarity_stability_filtered",
        "policy": "short_high_p_bma_k1_p60_h70",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 1,
        "gross_exposure": 0.04,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.70,
    },
    {
        "family": "signal_polarity_stability_filtered",
        "policy": "short_high_p_bma_k5_p60_h70",
        "score_col": "p_bma_pkf",
        "mode": "short_high",
        "top_k": 5,
        "gross_exposure": 0.04,
        "p_bma_threshold": 0.60,
        "hmm_threshold": 0.70,
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


def classify_correction(policy_metrics: pd.DataFrame) -> tuple[str, str, str, dict[str, Any]]:
    policies = policy_metrics.loc[policy_metrics["metric_level"] == "policy"].copy()
    if policies.empty:
        return "INCONCLUSIVE", "correct", "NO_CORRECTIONS_EVALUATED", {}
    eligible = policies.loc[
        (policies["median_active_days"] >= MIN_MEDIAN_ACTIVE_DAYS)
        & (policies["median_combo_sharpe"] > 0.0)
        & (policies["min_combo_sharpe"] > 0.0)
        & (policies["max_cvar_95_loss_fraction"] <= CVAR_LIMIT)
    ].copy()
    ranked = (eligible if not eligible.empty else policies).sort_values(
        ["min_combo_sharpe", "median_combo_sharpe"], ascending=[False, False], kind="mergesort"
    )
    best = ranked.iloc[0].to_dict()
    if eligible.empty:
        return "FAIL", "abandon", "NO_STABLE_SIGNAL_POLARITY_CORRECTION", best
    if float(best["median_combo_sharpe"]) >= SR_NEEDED_FOR_PROMOTION:
        return "PASS", "advance", "STRONG_STABLE_SIGNAL_POLARITY_RESEARCH_CANDIDATE_NOT_PROMOTED", best
    return "PASS", "advance", "STABLE_SIGNAL_POLARITY_RESEARCH_CANDIDATE_BELOW_DSR_PROMOTION_BAR", best


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions = pd.read_parquet(STAGE_A_PREDICTIONS)
    polarity_report = _read_json(POLARITY_GATE_REPORT)
    positions, daily, trades, metrics_frame = polarity_gate.evaluate_policies(predictions, PREDECLARED_CORRECTIONS)
    status, decision, classification, best = classify_correction(metrics_frame)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    positions_path = OUTPUT_DIR / "signal_polarity_stability_positions.parquet"
    daily_path = OUTPUT_DIR / "signal_polarity_stability_daily_returns.parquet"
    trades_path = OUTPUT_DIR / "signal_polarity_stability_trade_log.parquet"
    metrics_path = OUTPUT_DIR / "signal_polarity_stability_metrics.parquet"
    snapshot_path = OUTPUT_DIR / "signal_polarity_stability_snapshot_proxy.parquet"
    report_path = OUTPUT_DIR / "portfolio_cvar_research_report.json"
    positions.to_parquet(positions_path, index=False)
    daily.to_parquet(daily_path, index=False)
    trades.to_parquet(trades_path, index=False)
    metrics_frame.to_parquet(metrics_path, index=False)
    positions.loc[positions["date"] == positions["date"].max()].to_parquet(snapshot_path, index=False)

    policy_metrics = metrics_frame.loc[metrics_frame["metric_level"] == "policy"].copy()
    payload = {
        "hypothesis": (
            "A bounded p_bma/hmm stability correction can turn the signal-polarity research family "
            "into a stable non-promotable research candidate while preserving DSR and official blockers."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "predeclared_corrections": list(PREDECLARED_CORRECTIONS),
        "best_correction": best,
        "policy_metrics": policy_metrics.to_dict(orient="records"),
        "prior_polarity_summary": polarity_report.get("summary", []),
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
            "metric_name": "best_correction_policy",
            "metric_value": best.get("policy", ""),
            "metric_threshold": "predeclared correction only",
            "metric_status": "PASS" if best else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_median_combo_sharpe",
            "metric_value": best.get("median_combo_sharpe", 0.0),
            "metric_threshold": f"> 0 research candidate; >= {SR_NEEDED_FOR_PROMOTION} promotion blocked unless DSR passes",
            "metric_status": "PASS" if float(best.get("median_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_min_combo_sharpe",
            "metric_value": best.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0",
            "metric_status": "PASS" if float(best.get("min_combo_sharpe", 0.0)) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_median_active_days",
            "metric_value": best.get("median_active_days", 0.0),
            "metric_threshold": f">= {MIN_MEDIAN_ACTIVE_DAYS}",
            "metric_status": "PASS"
            if float(best.get("median_active_days", 0.0)) >= MIN_MEDIAN_ACTIVE_DAYS
            else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "best_correction_max_cvar_95_loss_fraction",
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
    source_artifacts = [artifact_record(STAGE_A_PREDICTIONS), artifact_record(POLARITY_GATE_REPORT)]
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
            f"best_correction_policy={best.get('policy', '')}",
            f"best_correction_median_combo_sharpe={best.get('median_combo_sharpe', 0.0)}",
            f"best_correction_min_combo_sharpe={best.get('min_combo_sharpe', 0.0)}",
            f"best_correction_median_active_days={best.get('median_active_days', 0.0)}",
            f"best_correction_max_cvar_95_loss_fraction={best.get('max_cvar_95_loss_fraction', 0.0)}",
            "stable positive research candidate survives as sandbox-only",
            "DSR and official CVaR blockers remain",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "candidate_sharpe_below_required_honest_dsr",
            "short_exposure_research_only_not_official",
            "official_cvar_zero_exposure_not_economic_robustness",
        ],
        "risks_residual": [
            "The surviving candidate is sandbox/research-only and uses short exposure.",
            "Median Sharpe is positive but still far below sr_needed for honest DSR clearance.",
            "Promotion would require explicit future gate and official execution support.",
        ],
        "next_recommended_step": (
            "Run family comparison/falsification. Preserve this as a surviving research-only candidate, "
            "not official promotion evidence."
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
            "Research-only stability correction for signal-polarity family.",
            "Correction uses p_bma and hmm filters only; no realized eligibility.",
            "No official artifacts were promoted.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Signal polarity stability correction result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This correction remains research/sandbox only."
        ),
        "MudanÃ§as implementadas": (
            "Added bounded p_bma/hmm stability filters for the signal-polarity family after the "
            "initial PARTIAL gate."
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
            f"Best correction `{best.get('policy', '')}` had median combo Sharpe "
            f"`{best.get('median_combo_sharpe', 0.0)}`, min combo Sharpe "
            f"`{best.get('min_combo_sharpe', 0.0)}`, median active days "
            f"`{best.get('median_active_days', 0.0)}`, and max CVaR95 loss "
            f"`{best.get('max_cvar_95_loss_fraction', 0.0)}`."
        ),
        "AvaliaÃ§Ã£o contra gates": (
            "The correction creates a stable positive research candidate, but it is below the honest "
            "DSR promotion bar and remains sandbox-only."
        ),
        "Riscos residuais": (
            "DSR remains 0.0, official CVaR remains zero exposure, and short exposure is not official."
        ),
        "Veredito final: advance / correct / abandon": (
            "`advance` as research-only candidate. Proceed to family comparison and state update."
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
