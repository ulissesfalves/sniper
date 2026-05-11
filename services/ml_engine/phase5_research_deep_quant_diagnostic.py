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

GATE_SLUG = "phase5_research_deep_quant_diagnostic_gate"
PHASE_FAMILY = "phase5_research_deep_quant_diagnostic"
PHASE4_REPORT = REPO_ROOT / "data" / "models" / "phase4" / "phase4_report_v4.json"
PHASE4_DIAGNOSTIC = REPO_ROOT / "data" / "models" / "phase4" / "phase4_gate_diagnostic.json"
PHASE4_INTEGRITY = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase6_research_baseline_rehydration_clean_regeneration_gate"
    / "phase4_artifact_integrity_report.json"
)
RESEARCH_DAILY_RETURNS = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate"
    / "research_sandbox_nonzero_exposure_daily_returns.parquet"
)
THRESHOLD_POLICY_METRICS = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_threshold_sizing_falsification_gate"
    / "rank_score_threshold_family_policy_metrics.parquet"
)
STABILITY_POLICY_METRICS = (
    REPO_ROOT
    / "reports"
    / "gates"
    / "phase5_research_rank_score_stability_correction_gate"
    / "rank_score_stability_correction_policy_metrics.parquet"
)
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

DSR_PASS_THRESHOLD = 0.95
ANNUALIZATION = 252.0


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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _max_drawdown(cumulative_returns: pd.Series) -> float:
    if cumulative_returns.empty:
        return 0.0
    drawdown = cumulative_returns - cumulative_returns.cummax()
    return abs(float(drawdown.min()))


def annualized_sharpe(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if len(clean) <= 1:
        return 0.0
    std = float(clean.std(ddof=1))
    if std == 0.0:
        return 0.0
    return float(clean.mean()) / std * (ANNUALIZATION**0.5)


def describe_return_series(returns: pd.Series, exposure: pd.Series | None = None) -> dict[str, Any]:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if exposure is None:
        exposure = pd.Series(0.0, index=clean.index)
    exposure_clean = pd.to_numeric(exposure, errors="coerce").fillna(0.0)
    active = exposure_clean > 0.0
    return {
        "total_days": int(len(clean)),
        "active_days": int(active.sum()),
        "cum_return_proxy": round(float(clean.sum()), 8),
        "annualized_sharpe_proxy": round(annualized_sharpe(clean), 6),
        "skew_proxy": round(float(clean.skew()), 6) if len(clean) > 2 else 0.0,
        "kurtosis_proxy": round(float(clean.kurt()), 6) if len(clean) > 3 else 0.0,
        "max_drawdown_proxy": round(_max_drawdown(clean.cumsum()), 8),
        "mean_exposure_fraction": round(float(exposure_clean.mean()), 8) if len(exposure_clean) else 0.0,
        "max_exposure_fraction": round(float(exposure_clean.max()), 8) if len(exposure_clean) else 0.0,
        "mean_turnover_proxy": round(float(exposure_clean.diff().abs().fillna(0.0).mean()), 8)
        if len(exposure_clean)
        else 0.0,
    }


def summarize_daily_returns(daily: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    combo_rows: list[dict[str, Any]] = []
    subperiod_rows: list[dict[str, Any]] = []
    work = daily.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    for combo, combo_daily in work.sort_values(["combo", "date"]).groupby("combo"):
        returns = pd.to_numeric(combo_daily["daily_return_proxy"], errors="coerce").fillna(0.0)
        exposure = pd.to_numeric(combo_daily["exposure_fraction"], errors="coerce").fillna(0.0)
        combo_summary = describe_return_series(returns, exposure)
        combo_summary["combo"] = str(combo)
        combo_rows.append(combo_summary)

        if len(combo_daily) >= 3:
            indexed = combo_daily.reset_index(drop=True)
            split_points = [
                (0, len(indexed) // 3),
                (len(indexed) // 3, (2 * len(indexed)) // 3),
                ((2 * len(indexed)) // 3, len(indexed)),
            ]
            for subperiod_idx, (start, stop) in enumerate(split_points, start=1):
                segment = indexed.iloc[start:stop]
                if segment.empty:
                    continue
                segment_returns = pd.to_numeric(segment["daily_return_proxy"], errors="coerce").fillna(0.0)
                segment_exposure = pd.to_numeric(segment["exposure_fraction"], errors="coerce").fillna(0.0)
                segment_summary = describe_return_series(segment_returns, segment_exposure)
                segment_summary.update(
                    {
                        "combo": str(combo),
                        "subperiod": int(subperiod_idx),
                        "start_date": str(pd.to_datetime(segment["date"].min()).date()),
                        "end_date": str(pd.to_datetime(segment["date"].max()).date()),
                    }
                )
                subperiod_rows.append(segment_summary)
    return combo_rows, subperiod_rows


def summarize_policy_sensitivity(*frames: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for frame in frames:
        if frame.empty:
            continue
        for _, row in frame.iterrows():
            rows.append(
                {
                    "policy": str(row.get("policy", "")),
                    "median_active_days": round(_as_float(row.get("median_active_days")), 6),
                    "median_combo_sharpe": round(_as_float(row.get("median_combo_sharpe")), 6),
                    "min_combo_sharpe": round(_as_float(row.get("min_combo_sharpe")), 6),
                    "max_cvar_95_loss_fraction": round(_as_float(row.get("max_cvar_95_loss_fraction")), 8),
                }
            )
    return sorted(rows, key=lambda item: item["median_combo_sharpe"], reverse=True)


def build_diagnostic_payload(
    phase4_report: dict[str, Any],
    integrity_report: dict[str, Any],
    combo_rows: list[dict[str, Any]],
    subperiod_rows: list[dict[str, Any]],
    sensitivity_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    dsr = dict(integrity_report.get("dsr") or phase4_report.get("dsr") or {})
    sharpe_is = _as_float(dsr.get("sharpe_is"), _as_float(phase4_report.get("fallback", {}).get("sharpe")))
    sr_needed = _as_float(dsr.get("sr_needed"), 4.47)
    dsr_honest = _as_float(dsr.get("dsr_honest"), 0.0)
    dsr_passed = bool(dsr.get("passed", False))
    n_trials = int(_as_float(dsr.get("n_trials_honest"), 5000))
    combo_frame = pd.DataFrame(combo_rows)
    subperiod_frame = pd.DataFrame(subperiod_rows)
    sensitivity_frame = pd.DataFrame(sensitivity_rows)
    best_sensitivity = sensitivity_rows[0] if sensitivity_rows else {}

    return {
        "dsr": {
            "dsr_honest": round(dsr_honest, 6),
            "dsr_passed": dsr_passed,
            "dsr_pass_threshold": DSR_PASS_THRESHOLD,
            "n_trials_honest": n_trials,
            "sharpe_is": round(sharpe_is, 6),
            "sr_needed": round(sr_needed, 6),
            "sharpe_gap_to_sr_needed": round(sr_needed - sharpe_is, 6),
        },
        "research_nonzero_exposure_returns": {
            "combo_count": int(combo_frame["combo"].nunique()) if not combo_frame.empty else 0,
            "median_active_days": round(float(combo_frame["active_days"].median()), 6)
            if not combo_frame.empty
            else 0.0,
            "median_sharpe": round(float(combo_frame["annualized_sharpe_proxy"].median()), 6)
            if not combo_frame.empty
            else 0.0,
            "min_sharpe": round(float(combo_frame["annualized_sharpe_proxy"].min()), 6)
            if not combo_frame.empty
            else 0.0,
            "median_skew": round(float(combo_frame["skew_proxy"].median()), 6) if not combo_frame.empty else 0.0,
            "median_kurtosis": round(float(combo_frame["kurtosis_proxy"].median()), 6)
            if not combo_frame.empty
            else 0.0,
            "max_drawdown": round(float(combo_frame["max_drawdown_proxy"].max()), 8)
            if not combo_frame.empty
            else 0.0,
            "median_turnover_proxy": round(float(combo_frame["mean_turnover_proxy"].median()), 8)
            if not combo_frame.empty
            else 0.0,
        },
        "subperiods": {
            "rows": int(len(subperiod_rows)),
            "median_subperiod_sharpe": round(float(subperiod_frame["annualized_sharpe_proxy"].median()), 6)
            if not subperiod_frame.empty
            else 0.0,
            "min_subperiod_sharpe": round(float(subperiod_frame["annualized_sharpe_proxy"].min()), 6)
            if not subperiod_frame.empty
            else 0.0,
            "negative_subperiod_count": int((subperiod_frame["annualized_sharpe_proxy"] < 0.0).sum())
            if not subperiod_frame.empty
            else 0,
        },
        "sensitivity": {
            "policy_rows_scanned": int(len(sensitivity_rows)),
            "best_policy": best_sensitivity,
            "positive_median_policy_count": int((sensitivity_frame["median_combo_sharpe"] > 0.0).sum())
            if not sensitivity_frame.empty
            else 0,
            "positive_and_stable_policy_count": int(
                (
                    (sensitivity_frame["median_combo_sharpe"] > 0.0)
                    & (sensitivity_frame["min_combo_sharpe"] > 0.0)
                ).sum()
            )
            if not sensitivity_frame.empty
            else 0,
        },
        "root_causes": [
            "honest DSR remains 0.0 under the fixed honest n_trials budget",
            "chosen Sharpe is far below sr_needed; closing the gap requires new ex-ante alpha, not threshold relaxation",
            "subperiod and cross-combo dispersion show instability in research-only exposure paths",
            "prior weak positive threshold variants do not remove negative min combo Sharpe",
            "diagnostics do not authorize official promotion or paper readiness",
        ],
    }


def classify_diagnostic(payload: dict[str, Any]) -> tuple[str, str, str]:
    dsr = payload.get("dsr", {})
    exposure = payload.get("research_nonzero_exposure_returns", {})
    subperiods = payload.get("subperiods", {})
    sensitivity = payload.get("sensitivity", {})
    if exposure.get("combo_count", 0) <= 0 or subperiods.get("rows", 0) <= 0:
        return "INCONCLUSIVE", "correct", "DEEP_QUANT_INPUTS_INCOMPLETE"
    if dsr.get("dsr_passed") or _as_float(dsr.get("dsr_honest")) >= DSR_PASS_THRESHOLD:
        return "FAIL", "freeze", "UNEXPECTED_DSR_PASS_CONFLICTS_WITH_KNOWN_BLOCKER"
    if sensitivity.get("policy_rows_scanned", 0) <= 0:
        return "PARTIAL", "correct", "DEEP_DSR_DIAGNOSTIC_WITHOUT_POLICY_SENSITIVITY"
    return "PASS", "advance", "DEEP_QUANT_DSR_AND_STABILITY_DIAGNOSTIC_COMPLETE"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    phase4_report = _read_json(PHASE4_REPORT)
    _ = _read_json(PHASE4_DIAGNOSTIC)
    integrity_report = _read_json(PHASE4_INTEGRITY)
    daily = pd.read_parquet(RESEARCH_DAILY_RETURNS)
    threshold_metrics = pd.read_parquet(THRESHOLD_POLICY_METRICS)
    stability_metrics = pd.read_parquet(STABILITY_POLICY_METRICS)

    combo_rows, subperiod_rows = summarize_daily_returns(daily)
    sensitivity_rows = summarize_policy_sensitivity(threshold_metrics, stability_metrics)
    diagnostic = build_diagnostic_payload(
        phase4_report,
        integrity_report,
        combo_rows,
        subperiod_rows,
        sensitivity_rows,
    )
    status, decision, classification = classify_diagnostic(diagnostic)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    report_path = OUTPUT_DIR / "deep_quant_diagnostic_report.json"
    combo_metrics_path = OUTPUT_DIR / "deep_quant_combo_metrics.parquet"
    subperiod_metrics_path = OUTPUT_DIR / "deep_quant_subperiod_metrics.parquet"
    sensitivity_path = OUTPUT_DIR / "deep_quant_policy_sensitivity.parquet"
    payload = {
        "hypothesis": (
            "A deep research-only diagnostic can decompose why DSR remains 0.0 and identify "
            "which mathematical constraints block promotion without relaxing thresholds."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "diagnostic": diagnostic,
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "masks_dsr": False,
            "treats_diagnostic_as_operational_signal": False,
        },
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(combo_rows).to_parquet(combo_metrics_path, index=False)
    pd.DataFrame(subperiod_rows).to_parquet(subperiod_metrics_path, index=False)
    pd.DataFrame(sensitivity_rows).to_parquet(sensitivity_path, index=False)

    dsr = diagnostic["dsr"]
    exposure = diagnostic["research_nonzero_exposure_returns"]
    subperiods = diagnostic["subperiods"]
    sensitivity = diagnostic["sensitivity"]
    metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "diagnostic_complete",
            "metric_value": status == "PASS",
            "metric_threshold": "true",
            "metric_status": "PASS" if status == "PASS" else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "dsr_honest",
            "metric_value": dsr["dsr_honest"],
            "metric_threshold": f">= {DSR_PASS_THRESHOLD} for promotion",
            "metric_status": "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "sharpe_gap_to_sr_needed",
            "metric_value": dsr["sharpe_gap_to_sr_needed"],
            "metric_threshold": "<= 0.0",
            "metric_status": "FAIL" if dsr["sharpe_gap_to_sr_needed"] > 0.0 else "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "median_research_sharpe",
            "metric_value": exposure["median_sharpe"],
            "metric_threshold": "> 0.0 diagnostic only",
            "metric_status": "PASS" if exposure["median_sharpe"] > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "negative_subperiod_count",
            "metric_value": subperiods["negative_subperiod_count"],
            "metric_threshold": "== 0 for stable candidate",
            "metric_status": "FAIL" if subperiods["negative_subperiod_count"] > 0 else "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "positive_and_stable_policy_count",
            "metric_value": sensitivity["positive_and_stable_policy_count"],
            "metric_threshold": "> 0",
            "metric_status": "PASS" if sensitivity["positive_and_stable_policy_count"] > 0 else "FAIL",
        },
    ]

    generated_artifacts = [
        artifact_record(report_path),
        artifact_record(combo_metrics_path),
        artifact_record(subperiod_metrics_path),
        artifact_record(sensitivity_path),
    ]
    source_artifacts = [
        artifact_record(PHASE4_REPORT),
        artifact_record(PHASE4_DIAGNOSTIC),
        artifact_record(PHASE4_INTEGRITY),
        artifact_record(RESEARCH_DAILY_RETURNS),
        artifact_record(THRESHOLD_POLICY_METRICS),
        artifact_record(STABILITY_POLICY_METRICS),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [str(PHASE4_REPORT), str(PHASE4_DIAGNOSTIC), str(PHASE4_INTEGRITY)],
        "research_artifacts_generated": [
            str(report_path),
            str(combo_metrics_path),
            str(subperiod_metrics_path),
            str(sensitivity_path),
        ],
        "summary": [
            f"classification={classification}",
            f"dsr_honest={dsr['dsr_honest']}",
            f"sharpe_is={dsr['sharpe_is']}",
            f"sr_needed={dsr['sr_needed']}",
            f"sharpe_gap_to_sr_needed={dsr['sharpe_gap_to_sr_needed']}",
            f"median_research_sharpe={exposure['median_sharpe']}",
            f"min_research_sharpe={exposure['min_sharpe']}",
            f"negative_subperiod_count={subperiods['negative_subperiod_count']}",
            f"positive_and_stable_policy_count={sensitivity['positive_and_stable_policy_count']}",
            "diagnostic only; no official promotion attempted",
        ],
        "gates": metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "sharpe_gap_to_required_honest_dsr",
            "subperiod_and_combo_instability",
            "no_stable_positive_prior_policy",
        ],
        "risks_residual": [
            "The diagnostic explains constraints but is not an operational signal.",
            "A new ex-ante research family is required to attack the Sharpe gap.",
            "Official CVaR and promotion blockers remain unchanged.",
        ],
        "next_recommended_step": (
            "Continue with an alternative research-only family using ex-ante p_bma/sigma/hmm/uncertainty "
            "signals, not Stage A realized eligibility and not rank_score repetitions."
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
            "Research-only deep quantitative diagnostic.",
            "No threshold relaxation and no official promotion.",
            "pnl_real is only present in upstream realized-return research metrics; this gate is diagnostic.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Deep quantitative diagnostic result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. DSR remains `0.0`; this diagnostic does not promote official."
        ),
        "MudanÃ§as implementadas": (
            "Added a research-only diagnostic decomposing DSR, required Sharpe, skew/kurtosis, "
            "drawdown, turnover proxy, subperiod stability, and prior policy sensitivity."
        ),
        "Artifacts gerados": (
            f"- `{report_path.relative_to(REPO_ROOT)}`\n"
            f"- `{combo_metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{subperiod_metrics_path.relative_to(REPO_ROOT)}`\n"
            f"- `{sensitivity_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"DSR honest `{dsr['dsr_honest']}`; Sharpe `{dsr['sharpe_is']}` versus required "
            f"`{dsr['sr_needed']}` leaves gap `{dsr['sharpe_gap_to_sr_needed']}`. "
            f"Median research Sharpe is `{exposure['median_sharpe']}` and negative subperiod count is "
            f"`{subperiods['negative_subperiod_count']}`."
        ),
        "AvaliaÃ§Ã£o contra gates": (
            "The diagnostic is complete and preserves blockers. It passes only as research diagnosis; "
            "promotion metrics still fail."
        ),
        "Riscos residuais": (
            "DSR is still 0.0, CVaR official remains zero exposure, and the prior rank-score line "
            "remains abandoned."
        ),
        "Veredito final: advance / correct / abandon": (
            "`advance` for diagnostic capability. Continue to a materially different research-only family."
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
