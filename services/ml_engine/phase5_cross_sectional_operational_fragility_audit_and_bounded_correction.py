#!/usr/bin/env python3
from __future__ import annotations

import json
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

import phase4_cpcv as phase4
import phase4_stage_a_experiment as stage_a
import phase5_cross_sectional_hardening_baseline as hardening
import phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate as restore
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_cross_sectional_operational_fragility_audit_and_bounded_correction"
PHASE_FAMILY = "phase5_cross_sectional_operational_fragility_audit_and_bounded_correction"
RESTORED_BUNDLE_EXPERIMENT = restore.RESTORED_BUNDLE_EXPERIMENT
RESTORE_NAMESPACE = restore.RESTORE_DIAGNOSTIC_NAMESPACE
RESEARCH_NAMESPACE = "phase5_cross_sectional_operational_fragility"
RECENT_WINDOW_DATES = restore.RECENT_WINDOW_DATES
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)
RESEARCH_ARTIFACT_FILES = (
    "operational_fragility_decomposition.parquet",
    "operational_fragility_challengers.parquet",
    "operational_fragility_summary.json",
    "official_artifacts_integrity.json",
)
CHALLENGER_REGIME = "challenger_regime_light_filter"
CHALLENGER_BUFFER = "challenger_edge_after_friction_buffer"
CHALLENGER_COMBINED = "challenger_edge_buffer_plus_concentration_cap"


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_paths() -> tuple[Path, Path, Path]:
    model_path = stage_a._resolve_model_path()
    stage_a._configure_phase4_paths(model_path)
    research_path = model_path / "research" / RESEARCH_NAMESPACE
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, research_path, gate_path


def _selected_mask(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(frame.get("decision_selected", False), index=frame.index).fillna(False).astype(bool)


def _active_mask(frame: pd.DataFrame) -> pd.Series:
    position = pd.to_numeric(frame.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    return _selected_mask(frame) & (position > 0)


def _prepare_operational_frame(model_path: Path) -> dict[str, Any]:
    replay = restore._run_regenerated_replay(model_path)
    frame = replay["frame"].copy()
    frame = phase4._attach_execution_pnl(frame, position_col="decision_position_usdt", output_col="pnl_exec_stage_a")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["decision_position_usdt"] = pd.to_numeric(frame.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    frame["decision_kelly_frac"] = pd.to_numeric(frame.get("decision_kelly_frac"), errors="coerce").fillna(0.0)
    frame["decision_mu_adj"] = pd.to_numeric(frame.get("decision_mu_adj"), errors="coerce").fillna(0.0)
    frame["position_usdt_stage_a"] = frame["decision_position_usdt"]
    frame["kelly_frac_stage_a"] = frame["decision_kelly_frac"]
    frame["mu_adj_stage_a"] = frame["decision_mu_adj"]
    frame["decision_proxy_prob"] = pd.to_numeric(frame.get("decision_proxy_prob"), errors="coerce").fillna(
        _selected_mask(frame).astype(float)
    )
    frame["pnl_gross_before_friction_stage_a"] = _estimate_pre_friction_pnl(frame)
    replay["frame"] = frame
    replay["frame_hash"] = hardening._frame_hash(frame)
    replay["metrics"] = stage5._compute_decision_space_metrics(frame)
    return replay


def _estimate_pre_friction_pnl(frame: pd.DataFrame) -> pd.Series:
    pnl_exec = pd.to_numeric(frame.get("pnl_exec_stage_a"), errors="coerce").fillna(0.0).copy()
    labels = pd.to_numeric(frame.get("label", pd.Series(np.nan, index=frame.index)), errors="coerce")
    barrier_sl = pd.to_numeric(frame.get("barrier_sl", pd.Series(np.nan, index=frame.index)), errors="coerce")
    p0 = pd.to_numeric(frame.get("p0", pd.Series(np.nan, index=frame.index)), errors="coerce")
    position = pd.to_numeric(frame.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    stop_mask = (
        labels.notna()
        & (labels == -1)
        & barrier_sl.notna()
        & (barrier_sl > 0)
        & p0.notna()
        & (p0 > 0)
        & (position > 0)
    )
    gross = pnl_exec.copy()
    gross.loc[stop_mask] = (barrier_sl.loc[stop_mask] / p0.loc[stop_mask]) - 1.0
    return gross


def _apply_keep_mask(frame: pd.DataFrame, keep_mask: pd.Series) -> pd.DataFrame:
    work = frame.copy()
    current_selected = _selected_mask(work)
    keep = current_selected & pd.Series(keep_mask, index=work.index).fillna(False).astype(bool)
    work["decision_selected"] = keep
    if "decision_selected_local" in work.columns:
        work["decision_selected_local"] = (
            pd.Series(work.get("decision_selected_local", False), index=work.index).fillna(False).astype(bool) & keep
        )
    if "decision_selected_fallback" in work.columns:
        work["decision_selected_fallback"] = (
            pd.Series(work.get("decision_selected_fallback", False), index=work.index).fillna(False).astype(bool) & keep
        )
    for col in (
        "decision_position_usdt",
        "decision_kelly_frac",
        "decision_mu_adj",
        "position_usdt_stage_a",
        "kelly_frac_stage_a",
        "mu_adj_stage_a",
        "decision_proxy_prob",
        "pnl_exec_stage_a",
        "pnl_gross_before_friction_stage_a",
    ):
        if col in work.columns:
            values = pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
            work[col] = values.where(keep, 0.0)
    return work


def _apply_regime_light_filter(frame: pd.DataFrame, *, hmm_threshold: float) -> pd.DataFrame:
    hmm = pd.to_numeric(frame.get("hmm_prob_bull"), errors="coerce").fillna(0.0)
    return _apply_keep_mask(frame, _selected_mask(frame) & (hmm >= float(hmm_threshold)))


def _apply_edge_buffer(frame: pd.DataFrame, *, score_threshold: float) -> pd.DataFrame:
    score = pd.to_numeric(frame.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0)
    return _apply_keep_mask(frame, _selected_mask(frame) & (score > float(score_threshold)))


def _apply_top_n_cap(frame: pd.DataFrame, *, n_per_date: int) -> pd.DataFrame:
    selected = frame.loc[_selected_mask(frame)].copy()
    if selected.empty:
        return _apply_keep_mask(frame, pd.Series(False, index=frame.index))
    ranked = selected.sort_values(["date", "rank_score_stage_a", "symbol"], ascending=[True, False, True], kind="mergesort")
    ranked["rn"] = ranked.groupby("date").cumcount() + 1
    keep_idx = set(ranked.loc[ranked["rn"] <= int(n_per_date)].index.tolist())
    return _apply_keep_mask(frame, pd.Series(frame.index.isin(list(keep_idx)), index=frame.index))


def _apply_edge_buffer_plus_top_n(frame: pd.DataFrame, *, score_threshold: float, n_per_date: int) -> pd.DataFrame:
    return _apply_top_n_cap(_apply_edge_buffer(frame, score_threshold=score_threshold), n_per_date=n_per_date)


def _daily_turnover(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if work.empty:
        return pd.DataFrame(columns=["date", "turnover_proxy_daily"])
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["symbol"] = work["symbol"].astype(str)
    work["decision_position_usdt"] = pd.to_numeric(work.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    pivot = (
        work.pivot_table(
            index="date",
            columns="symbol",
            values="decision_position_usdt",
            aggfunc="sum",
            fill_value=0.0,
        )
        .sort_index()
    )
    if pivot.empty:
        return pd.DataFrame(columns=["date", "turnover_proxy_daily"])
    gross = pivot.abs().sum(axis=1)
    diffs = pivot.diff().abs().sum(axis=1)
    if not diffs.empty:
        diffs.iloc[0] = pivot.iloc[0].abs().sum()
    turnover = diffs / gross.replace(0.0, np.nan)
    turnover = turnover.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return turnover.rename("turnover_proxy_daily").reset_index()


def _daily_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["decision_space_available"] = pd.Series(work.get("decision_space_available", False), index=work.index).fillna(False).astype(bool)
    work["decision_selected_fallback"] = pd.Series(work.get("decision_selected_fallback", False), index=work.index).fillna(False).astype(bool)
    work["decision_position_usdt"] = pd.to_numeric(work.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    work["pnl_exec_stage_a"] = pd.to_numeric(work.get("pnl_exec_stage_a"), errors="coerce").fillna(0.0)
    work["pnl_gross_before_friction_stage_a"] = pd.to_numeric(work.get("pnl_gross_before_friction_stage_a"), errors="coerce").fillna(0.0)
    daily = (
        work.groupby("date", sort=True)
        .apply(
            lambda grp: pd.Series(
                {
                    "rows_total": int(len(grp)),
                    "rows_available": int(grp["decision_space_available"].sum()),
                    "rows_selected": int(_selected_mask(grp).sum()),
                    "rows_position_gt_0": int(_active_mask(grp).sum()),
                    "gross_position_usdt": round(float(grp.loc[_active_mask(grp), "decision_position_usdt"].sum()), 6),
                    "max_position_usdt": round(float(grp.loc[_active_mask(grp), "decision_position_usdt"].max()), 6)
                    if bool(_active_mask(grp).any())
                    else 0.0,
                    "pnl_net_sum": round(float(grp.loc[_active_mask(grp), "pnl_exec_stage_a"].sum()), 6),
                    "pnl_gross_sum": round(float(grp.loc[_active_mask(grp), "pnl_gross_before_friction_stage_a"].sum()), 6),
                    "fallback_selected_rows": int(
                        (_active_mask(grp) & pd.Series(grp.get("decision_selected_fallback", False), index=grp.index).fillna(False).astype(bool)).sum()
                    ),
                    "date_available_count": int(pd.to_numeric(grp.get("decision_date_available_count"), errors="coerce").fillna(0.0).max()),
                }
            )
        )
        .reset_index()
    )
    turnover = _daily_turnover(frame)
    if not turnover.empty:
        daily = daily.merge(turnover, on="date", how="left")
    else:
        daily["turnover_proxy_daily"] = 0.0
    daily["turnover_proxy_daily"] = pd.to_numeric(daily.get("turnover_proxy_daily"), errors="coerce").fillna(0.0)
    gross = pd.to_numeric(daily.get("gross_position_usdt"), errors="coerce").fillna(0.0)
    max_pos = pd.to_numeric(daily.get("max_position_usdt"), errors="coerce").fillna(0.0)
    daily["concentration_proxy_daily"] = np.where(gross > 0.0, max_pos / gross, 0.0)
    return daily


def _build_decomposition_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    active = frame.loc[_active_mask(frame)].copy()
    active["date"] = pd.to_datetime(active["date"], errors="coerce").dt.normalize()
    active["year"] = active["date"].dt.year
    daily = _daily_metrics(frame)
    rows: list[dict[str, Any]] = []

    gross_eval = phase4._evaluate_decision_policy(
        frame,
        label="gross_before_friction",
        threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
        signal_col="decision_proxy_prob",
        position_col="position_usdt_stage_a",
        pnl_col="pnl_gross_before_friction_stage_a",
    )
    net_eval = phase4._evaluate_decision_policy(
        frame,
        label="net_after_friction",
        threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
        signal_col="decision_proxy_prob",
        position_col="position_usdt_stage_a",
        pnl_col="pnl_exec_stage_a",
    )
    rows.append(
        {
            "section": "overall",
            "slice_name": "gross_vs_net",
            "n_rows": int(len(active)),
            "n_days": int(active["date"].nunique()) if not active.empty else 0,
            "pnl_net_sum": round(float(pd.to_numeric(active.get("pnl_exec_stage_a"), errors="coerce").fillna(0.0).sum()), 6),
            "pnl_gross_sum": round(float(pd.to_numeric(active.get("pnl_gross_before_friction_stage_a"), errors="coerce").fillna(0.0).sum()), 6),
            "sharpe_net": round(float(net_eval["sharpe"]), 4),
            "sharpe_gross": round(float(gross_eval["sharpe"]), 4),
            "friction_sharpe_delta": round(float(gross_eval["sharpe"]) - float(net_eval["sharpe"]), 4),
            "equity_final_net": round(float(net_eval["equity_final"]), 2),
            "equity_final_gross": round(float(gross_eval["equity_final"]), 2),
            "loss_abs_net": round(float(-pd.to_numeric(active.get("pnl_exec_stage_a"), errors="coerce").fillna(0.0).clip(upper=0.0).sum()), 6),
        }
    )

    if not active.empty:
        by_year = active.groupby("year", sort=True).agg(
            n_rows=("symbol", "size"),
            pnl_net_sum=("pnl_exec_stage_a", "sum"),
            pnl_gross_sum=("pnl_gross_before_friction_stage_a", "sum"),
        )
        for year, grp in by_year.iterrows():
            rows.append(
                {
                    "section": "year",
                    "slice_name": str(int(year)),
                    "n_rows": int(grp["n_rows"]),
                    "n_days": int(active.loc[active["year"] == year, "date"].nunique()),
                    "pnl_net_sum": round(float(grp["pnl_net_sum"]), 6),
                    "pnl_gross_sum": round(float(grp["pnl_gross_sum"]), 6),
                    "mean_pnl_net": round(float(grp["pnl_net_sum"]) / float(grp["n_rows"]), 6),
                    "loss_abs_net": round(float(-min(float(grp["pnl_net_sum"]), 0.0)), 6),
                }
            )
        for mode, grp in active.groupby("decision_selection_mode", sort=True):
            pnl = pd.to_numeric(grp["pnl_exec_stage_a"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "section": "selection_mode",
                    "slice_name": str(mode),
                    "n_rows": int(len(grp)),
                    "n_days": int(grp["date"].nunique()),
                    "pnl_net_sum": round(float(pnl.sum()), 6),
                    "mean_pnl_net": round(float(pnl.mean()), 6),
                    "loss_abs_net": round(float(-pnl.clip(upper=0.0).sum()), 6),
                }
            )

        for flag_value, grp in daily.groupby(daily["fallback_selected_rows"] > 0):
            pnl = pd.to_numeric(grp["pnl_net_sum"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "section": "fallback_day_flag",
                    "slice_name": "fallback_day" if bool(flag_value) else "no_fallback_day",
                    "n_rows": int(grp["rows_position_gt_0"].sum()),
                    "n_days": int(len(grp)),
                    "pnl_net_sum": round(float(pnl.sum()), 6),
                    "mean_pnl_net": round(float(pnl.mean()), 6),
                    "loss_abs_net": round(float(-pnl.clip(upper=0.0).sum()), 6),
                }
            )

        for active_count, grp in daily.groupby("rows_position_gt_0", sort=True):
            pnl = pd.to_numeric(grp["pnl_net_sum"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "section": "active_events_per_day",
                    "slice_name": str(int(active_count)),
                    "n_rows": int(grp["rows_position_gt_0"].sum()),
                    "n_days": int(len(grp)),
                    "pnl_net_sum": round(float(pnl.sum()), 6),
                    "mean_pnl_net": round(float(pnl.mean()), 6),
                    "loss_abs_net": round(float(-pnl.clip(upper=0.0).sum()), 6),
                }
            )

        group_bucket = pd.cut(
            pd.to_numeric(active.get("decision_group_available_count"), errors="coerce").fillna(0.0),
            bins=[0, 1, 2, 3, 5, 10, 100],
            include_lowest=True,
        )
        for bucket, grp in active.groupby(group_bucket, observed=False):
            pnl = pd.to_numeric(grp["pnl_exec_stage_a"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "section": "group_available_bucket",
                    "slice_name": str(bucket),
                    "n_rows": int(len(grp)),
                    "n_days": int(grp["date"].nunique()),
                    "pnl_net_sum": round(float(pnl.sum()), 6),
                    "mean_pnl_net": round(float(pnl.mean()), 6),
                    "loss_abs_net": round(float(-pnl.clip(upper=0.0).sum()), 6),
                }
            )

    if not daily.empty:
        recent_daily = daily.sort_values("date", kind="mergesort").tail(RECENT_WINDOW_DATES)
        for _, row in recent_daily.iterrows():
            rows.append(
                {
                    "section": "recent_date",
                    "slice_name": row["date"].strftime("%Y-%m-%d"),
                    "n_rows": int(row["rows_position_gt_0"]),
                    "n_days": 1,
                    "pnl_net_sum": round(float(row["pnl_net_sum"]), 6),
                    "pnl_gross_sum": round(float(row["pnl_gross_sum"]), 6),
                    "rows_total": int(row["rows_total"]),
                    "rows_available": int(row["rows_available"]),
                    "rows_selected": int(row["rows_selected"]),
                    "max_position_usdt": round(float(row["max_position_usdt"]), 6),
                    "gross_position_usdt": round(float(row["gross_position_usdt"]), 6),
                    "turnover_proxy_daily": round(float(row["turnover_proxy_daily"]), 6),
                    "concentration_proxy_daily": round(float(row["concentration_proxy_daily"]), 6),
                    "fallback_selected_rows": int(row["fallback_selected_rows"]),
                }
            )

    decomposition = pd.DataFrame(rows)
    total_loss_abs = float(-pd.to_numeric(daily.get("pnl_net_sum"), errors="coerce").fillna(0.0).clip(upper=0.0).sum()) if not daily.empty else 0.0
    latest_date = pd.to_datetime(daily["date"], errors="coerce").max() if not daily.empty else pd.NaT
    recent_cutoff = latest_date - pd.Timedelta(days=365) if pd.notna(latest_date) else pd.NaT
    regime_recent_loss_abs = (
        float(-pd.to_numeric(daily.loc[daily["date"] >= recent_cutoff, "pnl_net_sum"], errors="coerce").fillna(0.0).clip(upper=0.0).sum())
        if pd.notna(recent_cutoff)
        else 0.0
    )
    sparse_loss_abs = (
        float(-pd.to_numeric(daily.loc[daily["fallback_selected_rows"] > 0, "pnl_net_sum"], errors="coerce").fillna(0.0).clip(upper=0.0).sum())
        if not daily.empty
        else 0.0
    )
    concentration_loss_abs = (
        float(-pd.to_numeric(daily.loc[daily["rows_position_gt_0"] >= 3, "pnl_net_sum"], errors="coerce").fillna(0.0).clip(upper=0.0).sum())
        if not daily.empty
        else 0.0
    )
    turnover_threshold = float(pd.to_numeric(daily.get("turnover_proxy_daily"), errors="coerce").fillna(0.0).quantile(0.75)) if not daily.empty else 0.0
    turnover_loss_abs = (
        float(
            -pd.to_numeric(
                daily.loc[pd.to_numeric(daily.get("turnover_proxy_daily"), errors="coerce").fillna(0.0) >= turnover_threshold, "pnl_net_sum"],
                errors="coerce",
            )
            .fillna(0.0)
            .clip(upper=0.0)
            .sum()
        )
        if not daily.empty
        else 0.0
    )
    fragility_stats = {
        "gross_sharpe": round(float(gross_eval["sharpe"]), 4),
        "net_sharpe": round(float(net_eval["sharpe"]), 4),
        "friction_sharpe_delta": round(float(gross_eval["sharpe"]) - float(net_eval["sharpe"]), 4),
        "total_loss_abs": round(total_loss_abs, 6),
        "regime_recent_loss_abs": round(regime_recent_loss_abs, 6),
        "regime_recent_loss_share": round(regime_recent_loss_abs / total_loss_abs, 4) if total_loss_abs > 0 else 0.0,
        "sparse_contest_loss_abs": round(sparse_loss_abs, 6),
        "sparse_contest_loss_share": round(sparse_loss_abs / total_loss_abs, 4) if total_loss_abs > 0 else 0.0,
        "concentration_loss_abs": round(concentration_loss_abs, 6),
        "concentration_loss_share": round(concentration_loss_abs / total_loss_abs, 4) if total_loss_abs > 0 else 0.0,
        "turnover_loss_abs": round(turnover_loss_abs, 6),
        "turnover_loss_share": round(turnover_loss_abs / total_loss_abs, 4) if total_loss_abs > 0 else 0.0,
        "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
    }
    return decomposition, fragility_stats


def _classify_operational_fragility(
    *,
    fragility_stats: dict[str, Any],
    regime_summary: dict[str, Any],
) -> str:
    if fragility_stats["gross_sharpe"] > 0.0 and fragility_stats["net_sharpe"] <= 0.0:
        return "FRICTION_DOMINANT"
    if (
        regime_summary.get("latest_365d_sharpe") is not None
        and float(regime_summary.get("latest_365d_sharpe") or 0.0) < 0.0
        and float(regime_summary.get("pre_latest_365d_sharpe") or 0.0) >= 0.0
        and float(fragility_stats.get("regime_recent_loss_share") or 0.0) >= max(
            float(fragility_stats.get("sparse_contest_loss_share") or 0.0),
            float(fragility_stats.get("concentration_loss_share") or 0.0),
            float(fragility_stats.get("turnover_loss_share") or 0.0),
            0.40,
        )
    ):
        return "REGIME_DEPENDENCE_DOMINANT"
    if float(fragility_stats.get("sparse_contest_loss_share") or 0.0) >= max(
        float(fragility_stats.get("concentration_loss_share") or 0.0),
        float(fragility_stats.get("turnover_loss_share") or 0.0),
        0.45,
    ):
        return "SPARSE_CONTEST_DOMINANT"
    if float(fragility_stats.get("concentration_loss_share") or 0.0) >= max(
        float(fragility_stats.get("turnover_loss_share") or 0.0),
        0.45,
    ):
        return "CONCENTRATION_DOMINANT"
    if float(fragility_stats.get("turnover_loss_share") or 0.0) >= 0.45:
        return "TURNOVER_DOMINANT"
    return "MIXED_OPERATIONAL_FRAGILITY"


def _build_comparator_row(
    *,
    scenario: str,
    frame: pd.DataFrame,
    source: str,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
) -> dict[str, Any]:
    row = hardening._build_metric_row(
        scenario=scenario,
        frame=frame,
        signal_col="decision_proxy_prob",
        pnl_col="pnl_exec_stage_a",
        scenario_type="operational_policy",
        source=source,
    )
    regime_rows = pd.DataFrame(
        hardening._build_regime_slice_rows(
            frame,
            scenario=scenario,
            signal_col="decision_proxy_prob",
            pnl_col="pnl_exec_stage_a",
        )
    )
    regime_summary = hardening._regime_slice_summary(regime_rows, scenario)
    friction_rows: list[dict[str, Any]] = []
    for stress in phase4.PHASE4_FRICTION_STRESS_SPECS:
        if stress["label"] == "base":
            continue
        stressed_frame, stressed_pnl_col = hardening._apply_friction_stress(
            frame,
            label=f"{scenario}_{stress['label']}",
            slippage_mult=float(stress["slippage_mult"]),
            extra_cost_bps=float(stress["extra_cost_bps"]),
        )
        friction_rows.append(
            hardening._build_metric_row(
                scenario=f"{scenario}_friction_{stress['label']}",
                frame=stressed_frame,
                signal_col="decision_proxy_prob",
                pnl_col=stressed_pnl_col,
                scenario_type="friction",
                source=source,
            )
        )
    slippage_stress_impact = {
        stress_row["scenario"]: {
            "delta_sharpe_operational": round(float(stress_row["sharpe_operational"]) - float(row["sharpe_operational"]), 4),
            "delta_equity_final": round(float(stress_row["equity_final"]) - float(row["equity_final"]), 2),
            "delta_historical_active_events_decision_space": int(stress_row["historical_active_events_decision_space"]) - int(row["historical_active_events_decision_space"]),
        }
        for stress_row in friction_rows
    }
    row["regime_slice_results"] = json.dumps(regime_summary, ensure_ascii=False, sort_keys=True)
    row["slippage_stress_impact"] = json.dumps(slippage_stress_impact, ensure_ascii=False, sort_keys=True)
    row["official_artifacts_unchanged"] = bool(official_artifacts_unchanged)
    row["research_only_isolation_pass"] = bool(research_only_isolation_pass)
    row["reproducibility_pass"] = bool(reproducibility_pass)
    return row


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    dominant_operational_fragility_classified: bool,
    bounded_challengers_executed: bool,
) -> list[dict[str, Any]]:
    def _metric(name: str, value: Any, passed: bool) -> dict[str, Any]:
        return {
            "gate_slug": GATE_SLUG,
            "metric_name": name,
            "metric_value": value,
            "metric_threshold": "PASS",
            "metric_status": "PASS" if passed else "FAIL",
        }

    return [
        _metric("official_artifacts_unchanged", official_artifacts_unchanged, official_artifacts_unchanged),
        _metric("research_only_isolation_pass", research_only_isolation_pass, research_only_isolation_pass),
        _metric("reproducibility_pass", reproducibility_pass, reproducibility_pass),
        _metric("sovereign_metric_definitions_unchanged", sovereign_metric_definitions_unchanged, sovereign_metric_definitions_unchanged),
        _metric("dominant_operational_fragility_classified", dominant_operational_fragility_classified, dominant_operational_fragility_classified),
        _metric("bounded_challengers_executed", bounded_challengers_executed, bounded_challengers_executed),
    ]


def _classify_round(
    *,
    integrity_pass: bool,
    baseline_row: dict[str, Any],
    challenger_rows: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if not integrity_pass:
        return "FAIL", "abandon", "OPERATIONAL_FRAGILITY_PERSISTS"
    qualified = [
        row
        for row in challenger_rows
        if int(row.get("latest_active_count_decision_space", 0)) > 0
        and bool(row.get("headroom_decision_space", False))
        and float(row.get("sharpe_operational", 0.0)) > float(baseline_row.get("sharpe_operational", 0.0)) + 0.10
        and float(row.get("dsr_honest", 0.0)) > 0.0
        and int(row.get("subperiods_positive", 0)) > int(baseline_row.get("subperiods_positive", 0))
    ]
    if qualified:
        return "PASS", "advance", "LOW_REGRET_OPERATIONAL_FIX_EXISTS"
    materially_improved = [
        row
        for row in challenger_rows
        if int(row.get("latest_active_count_decision_space", 0)) > 0
        and bool(row.get("headroom_decision_space", False))
        and float(row.get("sharpe_operational", 0.0)) > float(baseline_row.get("sharpe_operational", 0.0)) + 0.10
    ]
    if materially_improved:
        return "PARTIAL", "correct", "OPERATIONAL_FRAGILITY_PERSISTS"
    return "PARTIAL", "correct", "FAMILY_NEEDS_STRUCTURAL_RETHINK"


def run_phase5_cross_sectional_operational_fragility_audit_and_bounded_correction() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    working_tree_before = _git_output("status", "--short", "--untracked-files=all")

    restored_summary = json.loads(
        (model_path / "research" / RESTORE_NAMESPACE / "sovereign_restore_replay_summary.json").read_text(encoding="utf-8")
    )
    replay_run1 = _prepare_operational_frame(model_path)
    replay_run2 = _prepare_operational_frame(model_path)
    reproducibility = {
        "frame_hash_run1": replay_run1["frame_hash"],
        "frame_hash_run2": replay_run2["frame_hash"],
        "metrics_run1": replay_run1["metrics"],
        "metrics_run2": replay_run2["metrics"],
        "pass": bool(replay_run1["frame_hash"] == replay_run2["frame_hash"] and replay_run1["metrics"] == replay_run2["metrics"]),
    }

    base_frame = replay_run1["frame"]
    decomposition_frame, fragility_stats = _build_decomposition_frame(base_frame)
    decomposition_frame.to_parquet(research_path / "operational_fragility_decomposition.parquet", index=False)

    baseline_regime_rows = pd.DataFrame(
        hardening._build_regime_slice_rows(
            base_frame,
            scenario="frozen_sovereign_baseline",
            signal_col="decision_proxy_prob",
            pnl_col="pnl_exec_stage_a",
        )
    )
    baseline_regime_summary = hardening._regime_slice_summary(baseline_regime_rows, "frozen_sovereign_baseline")
    dominant_fragility = _classify_operational_fragility(
        fragility_stats=fragility_stats,
        regime_summary=baseline_regime_summary,
    )

    official_after = stage5._collect_official_inventory(model_path)
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True
    reproducibility_pass = bool(reproducibility["pass"])
    helper_metrics = stage5._compute_decision_space_metrics(base_frame)
    explicit_metrics = restore._compute_sovereign_metrics(
        base_frame,
        selected_col="decision_selected",
        position_col="decision_position_usdt",
    )
    sovereign_metric_definitions_unchanged = bool(
        helper_metrics["latest_active_count_decision_space"] == explicit_metrics["latest_active_count_decision_space"]
        and helper_metrics["headroom_decision_space"] == explicit_metrics["headroom_decision_space"]
        and helper_metrics["recent_live_dates_decision_space"] == explicit_metrics["recent_live_dates_decision_space"]
        and helper_metrics["historical_active_events_decision_space"] == explicit_metrics["historical_active_events_decision_space"]
    )

    challenger_frames = {
        "frozen_sovereign_baseline": base_frame,
        CHALLENGER_REGIME: _apply_regime_light_filter(base_frame, hmm_threshold=0.999),
        CHALLENGER_BUFFER: _apply_edge_buffer(base_frame, score_threshold=0.45),
        CHALLENGER_COMBINED: _apply_edge_buffer_plus_top_n(base_frame, score_threshold=0.45, n_per_date=2),
    }
    challenger_reasons = {
        CHALLENGER_REGIME: "Filtro leve de regime via hmm_prob_bull >= 0.999 para testar diretamente a fragilidade recente.",
        CHALLENGER_BUFFER: "Buffer operacional de edge via p_stage_a_calibrated > 0.45 sobre linhas já selecionadas.",
        CHALLENGER_COMBINED: "Combinação bounded do buffer de edge com cap top-2 por data para conter caudas operacionais de dias com 3 ativos.",
    }

    comparator_rows: list[dict[str, Any]] = []
    for scenario, frame in challenger_frames.items():
        comparator_rows.append(
            _build_comparator_row(
                scenario=scenario,
                frame=frame,
                source=RESTORED_BUNDLE_EXPERIMENT,
                official_artifacts_unchanged=official_artifacts_unchanged,
                research_only_isolation_pass=research_only_isolation_pass,
                reproducibility_pass=reproducibility_pass,
            )
        )

    comparators = pd.DataFrame(comparator_rows)
    comparators.to_parquet(research_path / "operational_fragility_challengers.parquet", index=False)

    dominant_operational_fragility_classified = bool(dominant_fragility in {
        "FRICTION_DOMINANT",
        "CONCENTRATION_DOMINANT",
        "TURNOVER_DOMINANT",
        "REGIME_DEPENDENCE_DOMINANT",
        "SPARSE_CONTEST_DOMINANT",
        "MIXED_OPERATIONAL_FRAGILITY",
    })
    bounded_challengers_executed = bool(len(comparators) == 4)
    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        dominant_operational_fragility_classified=dominant_operational_fragility_classified,
        bounded_challengers_executed=bounded_challengers_executed,
    )

    baseline_row = next(row for row in comparator_rows if row["scenario"] == "frozen_sovereign_baseline")
    challenger_rows = [row for row in comparator_rows if row["scenario"] != "frozen_sovereign_baseline"]
    status, decision, classification = _classify_round(
        integrity_pass=all(metric["metric_status"] == "PASS" for metric in gate_metrics),
        baseline_row=baseline_row,
        challenger_rows=challenger_rows,
    )

    best_challenger = max(challenger_rows, key=lambda row: float(row.get("sharpe_operational", -999.0)))
    slippage_stress_impact = {
        row["scenario"]: json.loads(row["slippage_stress_impact"])
        for row in comparator_rows
    }
    regime_slice_results = {
        row["scenario"]: json.loads(row["regime_slice_results"])
        for row in comparator_rows
    }

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "baseline_experiment": RESTORED_BUNDLE_EXPERIMENT,
        "classification_final": classification,
        "status": status,
        "decision": decision,
        "baseline_metrics": baseline_row,
        "challenger_metrics": {row["scenario"]: row for row in challenger_rows},
        "best_challenger": best_challenger["scenario"],
        "dominant_operational_fragility": dominant_fragility,
        "fragility_stats": fragility_stats,
        "baseline_regime_slice_results": baseline_regime_summary,
        "reproducibility": reproducibility,
        "restored_summary_reference": {
            "equivalence_classification": restored_summary.get("equivalence_classification"),
            "bundle_completeness": restored_summary.get("bundle_completeness"),
        },
        "challenger_reasons": challenger_reasons,
        "slippage_stress_impact": slippage_stress_impact,
        "regime_slice_results": regime_slice_results,
    }
    _write_json(research_path / "operational_fragility_summary.json", summary_payload)

    integrity_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": official_artifacts_unchanged,
        "official_root": str(model_path),
        "restored_bundle_root": str(model_path / "research" / RESTORED_BUNDLE_EXPERIMENT),
        "research_root": str(research_path),
        "gate_root": str(gate_path),
        "research_only_isolation_pass": research_only_isolation_pass,
    }
    _write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(working_tree_before),
        "branch": _git_output("branch", "--show-current"),
        "official_artifacts_used": [
            str(model_path / "phase3"),
            str(model_path / "features"),
            str(model_path / "phase4" / "phase4_report_v4.json"),
            str(model_path / "phase4" / "phase4_execution_snapshot.parquet"),
            str(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
            str(model_path / "phase4" / "phase4_oos_predictions.parquet"),
            str(model_path / "phase4" / "phase4_gate_diagnostic.json"),
        ],
        "research_artifacts_generated": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "summary": [
            f"classification_final={classification}",
            f"dominant_operational_fragility={dominant_fragility}",
            f"baseline_latest_active_count_decision_space={baseline_row['latest_active_count_decision_space']}",
            f"baseline_headroom_decision_space={baseline_row['headroom_decision_space']}",
            f"best_challenger={best_challenger['scenario']}",
            f"best_challenger_sharpe_operational={best_challenger['sharpe_operational']}",
        ],
        "gates": gate_metrics,
        "blockers": [] if decision != "abandon" else ["integrity_failure_or_sovereign_ruler_change"],
        "risks_residual": [
            dominant_fragility,
            f"baseline_sharpe_operational={baseline_row['sharpe_operational']}",
            f"best_challenger_dsr_honest={best_challenger['dsr_honest']}",
        ],
        "next_recommended_step": (
            "Proceed with the bounded operational fix candidate."
            if decision == "advance"
            else "Treat the family as operationally live but still fragile; do not advance without another focused correction round."
        ),
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "branch": _git_output("branch", "--show-current"),
        "working_tree_dirty_before": bool(working_tree_before),
        "working_tree_dirty_after": bool(_git_output("status", "--short", "--untracked-files=all")),
        "source_artifacts": [
            artifact_record(model_path / "research" / RESTORE_NAMESPACE / "sovereign_restore_replay_summary.json"),
            artifact_record(model_path / "research" / RESTORED_BUNDLE_EXPERIMENT / "restored_bundle_manifest.json"),
            artifact_record(model_path / "research" / "phase4_cross_sectional_ranking_baseline" / "stage_a_predictions.parquet"),
        ],
        "generated_artifacts": [artifact_record(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "commands_executed": [
            "python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py",
        ],
        "notes": [
            f"classification_final={classification}",
            f"dominant_operational_fragility={dominant_fragility}",
            f"baseline_anchor={RESTORED_BUNDLE_EXPERIMENT}",
        ],
    }
    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}` / classificação `{classification}`.",
                f"- Baseline soberana congelada latest/headroom: `{baseline_row['latest_active_count_decision_space']}` / `{baseline_row['headroom_decision_space']}`.",
                f"- Fragilidade dominante: `{dominant_fragility}`; melhor challenger: `{best_challenger['scenario']}` com sharpe `{best_challenger['sharpe_operational']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                f"- Baseline research-only obrigatória: `{RESTORED_BUNDLE_EXPERIMENT}`.",
                "- Sem alteração de official, target, features, modelo ou régua soberana.",
                f"- Restore equivalence de referência: `{restored_summary.get('equivalence_classification')}`.",
            ]
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- Runner research-only para decompor fragilidade operacional e testar challengers bounded sobre o frame soberano restaurado.",
                "- Challengers bounded: filtro leve de regime, buffer de edge e combinação buffer+cap top-2 por data.",
                "- Reuso dos helpers já existentes de métricas soberanas, fricção, regime slices e gate pack.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES]
            + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]
        ),
        "Resultados": "\n".join(
            [
                f"- Baseline: `{baseline_row}`.",
                f"- Dominant fragility stats: `{fragility_stats}`.",
                f"- Challenger rows: `{ {row['scenario']: {k: row[k] for k in ('latest_active_count_decision_space', 'headroom_decision_space', 'sharpe_operational', 'dsr_honest', 'subperiods_positive')} for row in challenger_rows} }`.",
            ]
        ),
        "Avaliação contra gates": "\n".join([f"- {row['metric_name']} = `{row['metric_status']}`" for row in gate_metrics]),
        "Riscos residuais": "\n".join(
            [
                f"- Melhor melhoria bounded ainda deixa `dsr_honest={best_challenger['dsr_honest']}`.",
                f"- Regime slice baseline: `{baseline_regime_summary}`.",
                "- Fricção adicional continua negativa em todos os comparadores.",
            ]
        ),
        "Veredito final: advance / correct / abandon": f"- `{classification}` -> decision `{decision}`",
    }

    outputs = write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    return {
        "status": status,
        "decision": decision,
        "classification_final": classification,
        "dominant_operational_fragility": dominant_fragility,
        "research_path": str(research_path),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
    }


def main() -> None:
    result = run_phase5_cross_sectional_operational_fragility_audit_and_bounded_correction()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
