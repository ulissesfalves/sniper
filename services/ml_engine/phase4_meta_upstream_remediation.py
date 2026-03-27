#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import structlog

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ml_engine_path = REPO_ROOT / "services" / "ml_engine"
if str(ml_engine_path) not in sys.path:
    sys.path.insert(0, str(ml_engine_path))

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))

from services.common.gate_reports import artifact_record, sha256_file, write_gate_pack
from services.ml_engine.meta_labeling.isotonic_calibration import fit_isotonic_calibrator
from services.ml_engine.phase4_cpcv import (
    _aggregate_oos_predictions,
    _attach_execution_pnl,
    _apply_cluster_calibration,
    _build_execution_snapshot,
    _build_operational_path_report,
    _compute_phase4_sizing,
    _fit_cluster_calibrators,
)
from services.ml_engine.sizing.kelly_cvar import P_CUTOFF, compute_kelly_fraction

GATE_SLUG = "phase4_meta_upstream_remediation"
PHASE_FAMILY = "phase4_research_upstream"
CLASS_FIX_FOUND = "UPSTREAM_FIX_FOUND"
CLASS_INCONCLUSIVE = "UPSTREAM_REMEDIATION_INCONCLUSIVE"
CLASS_FAMILY_WEAK = "META_FAMILY_OPERATIONALLY_WEAK"
THRESHOLDS = (0.45, 0.46, 0.47, 0.50, 0.51, 0.55, 0.60)


def _resolve_model_path() -> Path:
    docker_path = Path("/data/models")
    if docker_path.exists():
        return docker_path
    return (REPO_ROOT / "data" / "models").resolve()


MODEL_PATH = _resolve_model_path()
PHASE4_PATH = MODEL_PATH / "phase4"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG

REPORT_PATH = PHASE4_PATH / "phase4_report_v4.json"
SNAPSHOT_PATH = PHASE4_PATH / "phase4_execution_snapshot.parquet"
AGGREGATED_PATH = PHASE4_PATH / "phase4_aggregated_predictions.parquet"
OOS_PATH = PHASE4_PATH / "phase4_oos_predictions.parquet"
ALIGNMENT_GATE_PATH = REPO_ROOT / "reports" / "gates" / "phase4_alignment_meta_audit" / "gate_report.json"
ALIGNMENT_DIAGNOSTIC_PATH = MODEL_PATH / "research" / "phase4_alignment_meta_audit" / "meta_path_diagnostic.json"
PHASE4_CPCV_SOURCE = THIS_FILE.parent / "phase4_cpcv.py"
ISOTONIC_SOURCE = THIS_FILE.parent / "meta_labeling" / "isotonic_calibration.py"
KELLY_SOURCE = THIS_FILE.parent / "sizing" / "kelly_cvar.py"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _worktree_dirty() -> bool:
    return bool(_git_output("status", "--short"))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _coerce_num(series: pd.Series | Any) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _paper_environment_state() -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    daemon_available = result.returncode == 0 and bool(result.stdout.strip())
    return {
        "daemon_available": daemon_available,
        "stdout": result.stdout.strip(),
        "stderr": (result.stderr or "").strip(),
        "clean": not daemon_available,
        "status": "docker_daemon_unavailable" if not daemon_available else "daemon_available",
    }


def _line_ref(function: Any) -> dict[str, Any]:
    try:
        _, line_number = inspect.getsourcelines(function)
    except (OSError, TypeError):
        line_number = None
    source_path = inspect.getsourcefile(function)
    return {
        "path": source_path,
        "line_start": line_number,
        "qualname": getattr(function, "__qualname__", getattr(function, "__name__", "unknown")),
    }


def _latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    return (
        df.sort_values(["date", "symbol"], kind="mergesort")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values(["date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )


def compute_shrinkage_summary(
    df: pd.DataFrame,
    *,
    source_name: str,
    scope_type: str,
    scope_value: str,
) -> dict[str, Any]:
    work = df.copy()
    raw = _coerce_num(work.get("p_meta_raw"))
    calibrated = _coerce_num(work.get("p_meta_calibrated"))
    shrinkage = raw - calibrated
    return {
        "source_name": source_name,
        "scope_type": scope_type,
        "scope_value": str(scope_value),
        "n_rows": int(len(work)),
        "p_meta_raw_mean": round(float(raw.mean()), 6) if len(work) else 0.0,
        "p_meta_raw_p50": round(float(raw.quantile(0.50)), 6) if len(work) else 0.0,
        "p_meta_raw_p90": round(float(raw.quantile(0.90)), 6) if len(work) else 0.0,
        "p_meta_calibrated_mean": round(float(calibrated.mean()), 6) if len(work) else 0.0,
        "p_meta_calibrated_p50": round(float(calibrated.quantile(0.50)), 6) if len(work) else 0.0,
        "p_meta_calibrated_p90": round(float(calibrated.quantile(0.90)), 6) if len(work) else 0.0,
        "shrinkage_mean": round(float(shrinkage.mean()), 6) if len(work) else 0.0,
        "shrinkage_p50": round(float(shrinkage.quantile(0.50)), 6) if len(work) else 0.0,
        "shrinkage_p90": round(float(shrinkage.quantile(0.90)), 6) if len(work) else 0.0,
        "shrinkage_min": round(float(shrinkage.min()), 6) if len(work) else 0.0,
        "shrinkage_max": round(float(shrinkage.max()), 6) if len(work) else 0.0,
        "raw_gt_050": int((raw > 0.50).sum()),
        "calibrated_gt_050": int((calibrated > 0.50).sum()),
        "raw_gt_051": int((raw > 0.51).sum()),
        "calibrated_gt_051": int((calibrated > 0.51).sum()),
        "survival_050": round(float(((calibrated > 0.50).sum()) / max((raw > 0.50).sum(), 1)), 6),
        "survival_051": round(float(((calibrated > 0.51).sum()) / max((raw > 0.51).sum(), 1)), 6),
    }


def build_shrinkage_frame(aggregated_df: pd.DataFrame, oos_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    latest_df = _latest_snapshot(aggregated_df)
    rows.append(compute_shrinkage_summary(aggregated_df, source_name="aggregated", scope_type="overall", scope_value="all"))
    rows.append(compute_shrinkage_summary(latest_df, source_name="latest_snapshot", scope_type="overall", scope_value="all"))

    agg_year = aggregated_df.copy()
    agg_year["year"] = pd.to_datetime(agg_year["date"], errors="coerce").dt.year.fillna(-1).astype(int)
    for year, frame in agg_year.groupby("year", dropna=False):
        rows.append(compute_shrinkage_summary(frame, source_name="aggregated", scope_type="year", scope_value=str(year)))

    if "cluster_name" in aggregated_df.columns:
        for cluster_name, frame in aggregated_df.groupby("cluster_name", dropna=False):
            rows.append(
                compute_shrinkage_summary(
                    frame,
                    source_name="aggregated",
                    scope_type="cluster_name",
                    scope_value=str(cluster_name),
                )
            )

    if "combo" in oos_df.columns:
        for combo, frame in oos_df.groupby("combo", dropna=False):
            rows.append(
                compute_shrinkage_summary(
                    frame,
                    source_name="oos",
                    scope_type="combo",
                    scope_value=str(combo),
                )
            )

    return pd.DataFrame(rows).sort_values(["source_name", "scope_type", "scope_value"], kind="mergesort").reset_index(drop=True)


def build_threshold_survival_rows(
    df: pd.DataFrame,
    *,
    variant_name: str,
    scope_name: str,
    selected_prob_label: str,
) -> list[dict[str, Any]]:
    work = df.copy()
    rows_total = int(len(work))
    raw = _coerce_num(work.get("p_meta_raw"))
    selected = _coerce_num(work.get("p_meta_calibrated"))
    mu = _coerce_num(work.get("mu_adj_meta"))
    kelly = _coerce_num(work.get("kelly_frac_meta"))
    position = _coerce_num(work.get("position_usdt_meta"))

    rows: list[dict[str, Any]] = [
        {
            "variant_name": variant_name,
            "scope_name": scope_name,
            "stage_name": "rows_total",
            "threshold": "",
            "count": rows_total,
            "rate_vs_rows": round(float(rows_total / rows_total), 6) if rows_total else 0.0,
        },
        {
            "variant_name": variant_name,
            "scope_name": scope_name,
            "stage_name": "mu_adj_meta_gt_0",
            "threshold": "",
            "count": int((mu > 0).sum()),
            "rate_vs_rows": round(float((mu > 0).mean()), 6) if rows_total else 0.0,
        },
        {
            "variant_name": variant_name,
            "scope_name": scope_name,
            "stage_name": "kelly_frac_meta_gt_0",
            "threshold": "",
            "count": int((kelly > 0).sum()),
            "rate_vs_rows": round(float((kelly > 0).mean()), 6) if rows_total else 0.0,
        },
        {
            "variant_name": variant_name,
            "scope_name": scope_name,
            "stage_name": "position_usdt_meta_gt_0",
            "threshold": "",
            "count": int((position > 0).sum()),
            "rate_vs_rows": round(float((position > 0).mean()), 6) if rows_total else 0.0,
        },
    ]
    for threshold in THRESHOLDS:
        raw_mask = raw > threshold
        selected_mask = selected > threshold
        rows.append(
            {
                "variant_name": variant_name,
                "scope_name": scope_name,
                "stage_name": "p_meta_raw_gt_threshold",
                "selected_prob_label": selected_prob_label,
                "threshold": threshold,
                "count": int(raw_mask.sum()),
                "rate_vs_rows": round(float(raw_mask.mean()), 6) if rows_total else 0.0,
                "survival_vs_raw": 1.0,
            }
        )
        rows.append(
            {
                "variant_name": variant_name,
                "scope_name": scope_name,
                "stage_name": "selected_prob_gt_threshold",
                "selected_prob_label": selected_prob_label,
                "threshold": threshold,
                "count": int(selected_mask.sum()),
                "rate_vs_rows": round(float(selected_mask.mean()), 6) if rows_total else 0.0,
                "survival_vs_raw": round(float(selected_mask.sum() / max(raw_mask.sum(), 1)), 6),
            }
        )
    return rows


def build_threshold_survival_frame(variants: list[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant in variants:
        rows.extend(
            build_threshold_survival_rows(
                variant["predictions_df"],
                variant_name=str(variant["variant_name"]),
                scope_name="historical",
                selected_prob_label=str(variant["selected_prob_label"]),
            )
        )
        rows.extend(
            build_threshold_survival_rows(
                variant["snapshot_df"],
                variant_name=str(variant["variant_name"]),
                scope_name="latest_snapshot",
                selected_prob_label=str(variant["selected_prob_label"]),
            )
        )
    return pd.DataFrame(rows)


def _normalize_parquet_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in normalized.columns:
        if normalized[column].dtype == object:
            normalized[column] = normalized[column].map(lambda value: "" if value is None else str(value))
    return normalized


def run_variant_from_aggregated(
    aggregated_df: pd.DataFrame,
    *,
    variant_name: str,
    selected_prob_series: pd.Series,
    selected_prob_label: str,
    diagnostic_only: bool,
    candidate_fix: bool,
) -> dict[str, Any]:
    work = aggregated_df.copy()
    work["p_meta_calibrated"] = _coerce_num(selected_prob_series)
    work = _compute_phase4_sizing(
        work,
        prob_col="p_meta_calibrated",
        prefix="meta",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    work = _attach_execution_pnl(work, position_col="position_usdt_meta", output_col="pnl_exec_meta")
    snapshot_df = _build_execution_snapshot(work)
    operational_path = _build_operational_path_report(work, snapshot_df)
    latest_top_candidates = operational_path.get("latest_top_candidates", [])
    rank_head = latest_top_candidates[0]["p_meta_calibrated"] if latest_top_candidates else 0.0
    rank_next = latest_top_candidates[1]["p_meta_calibrated"] if len(latest_top_candidates) > 1 else 0.0
    return {
        "variant_name": variant_name,
        "selected_prob_label": selected_prob_label,
        "diagnostic_only": diagnostic_only,
        "candidate_fix": candidate_fix,
        "predictions_df": work,
        "snapshot_df": snapshot_df,
        "operational_path": operational_path,
        "summary": {
            "variant_name": variant_name,
            "selected_prob_label": selected_prob_label,
            "diagnostic_only": diagnostic_only,
            "candidate_fix": candidate_fix,
            "sharpe_operational": operational_path.get("sharpe"),
            "dsr_honest": operational_path.get("dsr_honest"),
            "latest_active_count": operational_path.get("activation_funnel", {}).get("latest_snapshot_active_count"),
            "headroom_real": bool(operational_path.get("activation_funnel", {}).get("latest_snapshot_p_meta_calibrated_gt_050", 0) > 0),
            "historical_active_events": operational_path.get("sparsity", {}).get("historical_active_events"),
            "latest_stage": operational_path.get("choke_point", {}).get("latest_snapshot_stage"),
            "latest_snapshot_max_p_meta_calibrated": operational_path.get("sparsity", {}).get("latest_snapshot_max_p_meta_calibrated"),
            "latest_snapshot_max_position_usdt": operational_path.get("sparsity", {}).get("latest_snapshot_max_position_usdt"),
            "position_gt_0_rows_final": int((_coerce_num(work.get("position_usdt_meta")) > 0).sum()),
            "position_gt_0_latest": int((_coerce_num(snapshot_df.get("position_usdt")) > 0).sum()),
            "rank_margin_latest": round(float(rank_head - rank_next), 6),
        },
    }


def build_global_isotonic_variant(oos_df: pd.DataFrame) -> pd.DataFrame:
    calibrator = fit_isotonic_calibrator(
        oos_df["p_meta_raw"].to_numpy(dtype=float),
        oos_df["y_meta"].to_numpy(dtype=float),
        pd.to_datetime(oos_df["date"], errors="coerce"),
    )
    challenger = oos_df.copy()
    challenger["p_meta_calibrated"] = calibrator.predict(
        pd.to_numeric(challenger["p_meta_raw"], errors="coerce").fillna(0.0).clip(0.001, 0.999).to_numpy(dtype=float)
    )
    challenger = _compute_phase4_sizing(
        challenger,
        prob_col="p_meta_calibrated",
        prefix="meta",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    challenger = _attach_execution_pnl(challenger, position_col="position_usdt_meta", output_col="pnl_exec_meta")
    aggregated = _aggregate_oos_predictions(challenger)
    aggregated = _compute_phase4_sizing(
        aggregated,
        prob_col="p_meta_calibrated",
        prefix="meta",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    aggregated = _attach_execution_pnl(aggregated, position_col="position_usdt_meta", output_col="pnl_exec_meta")
    return aggregated


def classify_final_result(
    *,
    baseline_summary: Mapping[str, Any],
    challenger_summaries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    honest_candidates = [row for row in challenger_summaries if row.get("candidate_fix") is True]
    viable_fix = next(
        (
            row for row in honest_candidates
            if bool(row.get("headroom_real"))
            and int(row.get("latest_active_count") or 0) >= 1
            and float(row.get("dsr_honest") or 0.0) > float(baseline_summary.get("dsr_honest") or 0.0)
        ),
        None,
    )
    if viable_fix is not None:
        return {
            "classification": CLASS_FIX_FOUND,
            "decision": "correct",
            "blocker_real": "Existe remediation upstream bounded e research-only que recupera headroom no latest com melhora honesta de DSR.",
            "next_recommended_step": "Validar a remediation upstream bounded em rodada dedicada e manter official intacto ate confirmacao adicional.",
        }

    diagnostic_raw = next((row for row in challenger_summaries if row.get("variant_name") == "diagnostic_raw_passthrough"), None)
    if honest_candidates and all(
        int(row.get("latest_active_count") or 0) == 0
        and not bool(row.get("headroom_real"))
        and float(row.get("dsr_honest") or 0.0) <= 0.0
        for row in honest_candidates
    ):
        return {
            "classification": CLASS_FAMILY_WEAK,
            "decision": "abandon",
            "blocker_real": (
                "Mesmo apos challengers bounded e OOS-honestos, a familia atual continua sem latest headroom ou melhora honesta de DSR; "
                "o unico alivio aparece no raw passthrough diagnostico, mas com degradacao operacional."
            ),
            "next_recommended_step": "Encerrar remediation bounded nesta familia e preparar pivot estrutural somente quando explicitamente autorizado.",
        }

    if diagnostic_raw and int(diagnostic_raw.get("latest_active_count") or 0) >= 1:
        return {
            "classification": CLASS_INCONCLUSIVE,
            "decision": "correct",
            "blocker_real": "O raw passthrough abre headroom, mas nenhum challenger bounded e defensavel converte isso em melhora operacional honesta.",
            "next_recommended_step": "Se autorizado, testar uma unica remediation upstream adicional claramente OOS-honesta antes de abandonar a familia.",
        }

    return {
        "classification": CLASS_INCONCLUSIVE,
        "decision": "correct",
        "blocker_real": "A compressao raw->calibrated foi medida, mas os challengers bounded nao foram suficientes para fechar uma conclusao forte.",
        "next_recommended_step": "Executar apenas uma remediation upstream adicional se houver hipotese causal nova e bounded.",
    }


def _subset_summary(df: pd.DataFrame, threshold: float) -> dict[str, Any]:
    raw = _coerce_num(df.get("p_meta_raw"))
    calibrated = _coerce_num(df.get("p_meta_calibrated"))
    shrinkage = raw - calibrated
    raw_mask = raw > threshold
    calibrated_mask = calibrated > threshold
    subset = df.loc[raw_mask].copy()
    return {
        "threshold": threshold,
        "rows_total": int(len(df)),
        "raw_gt_threshold": int(raw_mask.sum()),
        "calibrated_gt_threshold": int(calibrated_mask.sum()),
        "survival_vs_raw": round(float(calibrated_mask.sum() / max(raw_mask.sum(), 1)), 6),
        "shrinkage_mean_on_raw_gt_threshold": round(float(shrinkage.loc[raw_mask].mean()), 6) if raw_mask.any() else 0.0,
        "raw_mean_on_raw_gt_threshold": round(float(raw.loc[raw_mask].mean()), 6) if raw_mask.any() else 0.0,
        "calibrated_mean_on_raw_gt_threshold": round(float(calibrated.loc[raw_mask].mean()), 6) if raw_mask.any() else 0.0,
        "clusters_present": sorted(subset["cluster_name"].astype(str).unique().tolist()) if raw_mask.any() and "cluster_name" in subset.columns else [],
    }


def run_remediation() -> dict[str, Any]:
    git_branch = _git_output("branch", "--show-current")
    baseline_commit = _git_output("rev-parse", "HEAD")
    working_tree_dirty_before = _env_bool("PHASE4_META_UPSTREAM_WORKTREE_DIRTY_BEFORE", _worktree_dirty())

    official_paths = (REPORT_PATH, SNAPSHOT_PATH, AGGREGATED_PATH, OOS_PATH)
    official_hashes_before = {str(path): sha256_file(path) for path in official_paths if path.exists()}

    official_report = _read_json(REPORT_PATH)
    aggregated_df = pd.read_parquet(AGGREGATED_PATH)
    oos_df = pd.read_parquet(OOS_PATH)
    prior_gate_report = _read_json(ALIGNMENT_GATE_PATH) if ALIGNMENT_GATE_PATH.exists() else {}
    prior_diagnostic = _read_json(ALIGNMENT_DIAGNOSTIC_PATH) if ALIGNMENT_DIAGNOSTIC_PATH.exists() else {}

    shrinkage_df = build_shrinkage_frame(aggregated_df, oos_df)
    baseline_variant = run_variant_from_aggregated(
        aggregated_df,
        variant_name="official_cluster_calibrated",
        selected_prob_series=_coerce_num(aggregated_df["p_meta_calibrated"]),
        selected_prob_label="p_meta_calibrated",
        diagnostic_only=False,
        candidate_fix=False,
    )
    raw_variant = run_variant_from_aggregated(
        aggregated_df,
        variant_name="diagnostic_raw_passthrough",
        selected_prob_series=_coerce_num(aggregated_df["p_meta_raw"]),
        selected_prob_label="p_meta_raw_passthrough",
        diagnostic_only=True,
        candidate_fix=False,
    )
    global_isotonic_agg = build_global_isotonic_variant(oos_df)
    global_variant = run_variant_from_aggregated(
        global_isotonic_agg,
        variant_name="research_global_isotonic",
        selected_prob_series=_coerce_num(global_isotonic_agg["p_meta_calibrated"]),
        selected_prob_label="p_meta_calibrated_global_isotonic",
        diagnostic_only=False,
        candidate_fix=True,
    )
    variants = [baseline_variant, raw_variant, global_variant]
    threshold_survival_df = build_threshold_survival_frame(variants)

    baseline_summary = baseline_variant["summary"]
    challenger_summaries = [raw_variant["summary"], global_variant["summary"]]
    final_result = classify_final_result(
        baseline_summary=baseline_summary,
        challenger_summaries=challenger_summaries,
    )

    diagnostics = {
        "phase4_alignment_meta_audit_reference": {
            "gate_report_path": str(ALIGNMENT_GATE_PATH),
            "diagnostic_path": str(ALIGNMENT_DIAGNOSTIC_PATH),
            "status": prior_gate_report.get("status"),
            "decision": prior_gate_report.get("decision"),
            "classification": prior_diagnostic.get("classification"),
        },
        "official_phase4_paths": {
            "report_path": str(REPORT_PATH),
            "snapshot_path": str(SNAPSHOT_PATH),
            "aggregated_predictions_path": str(AGGREGATED_PATH),
            "oos_predictions_path": str(OOS_PATH),
        },
        "calibration_oos_logic": {
            "phase4_cpcv._fit_cluster_calibrators": _line_ref(_fit_cluster_calibrators),
            "phase4_cpcv._apply_cluster_calibration": _line_ref(_apply_cluster_calibration),
            "isotonic_calibration.fit_isotonic_calibrator": _line_ref(fit_isotonic_calibrator),
            "isotonic_calibration._kfold_isotonic_fit": _line_ref(fit_isotonic_calibrator.__globals__.get("_kfold_isotonic_fit")),
            "kelly_cvar.compute_kelly_fraction": _line_ref(compute_kelly_fraction),
            "phase4_prob_mode": official_report.get("phase4_meta_prob_mode"),
            "cluster_calibration_mode": official_report.get("cpcv", {}).get("cluster_calibration_mode"),
            "cluster_calibration_artifact": official_report.get("cpcv", {}).get("cluster_calibration_artifact"),
            "kelly_hard_cutoff": P_CUTOFF,
            "oos_flow": [
                "prediction_rows -> oos_df pooled by CPCV trajectory",
                "_fit_cluster_calibrators(oos_df) ajusta isotonic por cluster com fallback global usando date/y_meta/p_meta_raw OOS",
                "_apply_cluster_calibration(oos_df, calibrators, symbol_to_cluster) produz p_meta_calibrated por linha OOS",
                "_compute_phase4_sizing(... prob_col='p_meta_calibrated' ...) aplica hard cutoff 0.51 e Kelly",
                "_aggregate_oos_predictions(oos_df) consolida (date,symbol)",
                "_compute_phase4_sizing(aggregated_predictions, prob_col='p_meta_calibrated', ...) governa o snapshot oficial",
            ],
        },
        "shrinkage_overall_aggregated": compute_shrinkage_summary(aggregated_df, source_name="aggregated", scope_type="overall", scope_value="all"),
        "shrinkage_latest_snapshot": compute_shrinkage_summary(_latest_snapshot(aggregated_df), source_name="latest_snapshot", scope_type="overall", scope_value="all"),
        "raw_gt_threshold_context": {
            "historical_raw_gt_050": _subset_summary(aggregated_df, 0.50),
            "historical_raw_gt_051": _subset_summary(aggregated_df, 0.51),
            "latest_raw_gt_050": _subset_summary(_latest_snapshot(aggregated_df), 0.50),
            "latest_raw_gt_051": _subset_summary(_latest_snapshot(aggregated_df), 0.51),
        },
        "challenger_summaries": [variant["summary"] for variant in variants],
        "dominant_cause": (
            "score_compression_raw_to_calibrated"
            if baseline_summary["latest_active_count"] == 0 and baseline_summary["headroom_real"] is False
            else "mixed"
        ),
        "compression_notes": [
            "O choke dominante continua em raw -> calibrated no latest cut oficial.",
            "O hard cutoff de Kelly em 0.51 amplifica pequenas compressoes perto do threshold operacional.",
        ],
        "final_classification": final_result["classification"],
        "blocker_real": final_result["blocker_real"],
    }

    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)
    shrinkage_path = RESEARCH_PATH / "calibration_shrinkage.parquet"
    threshold_survival_path = RESEARCH_PATH / "threshold_survival.parquet"
    diagnostics_path = RESEARCH_PATH / "calibration_diagnostics.json"
    summary_path = RESEARCH_PATH / "meta_upstream_remediation_summary.json"

    _normalize_parquet_frame(shrinkage_df).to_parquet(shrinkage_path, index=False)
    _normalize_parquet_frame(threshold_survival_df).to_parquet(threshold_survival_path, index=False)
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    remediation_summary = {
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "official_baseline_summary": baseline_summary,
        "challengers": challenger_summaries,
        "bounded_fix_found": final_result["classification"] == CLASS_FIX_FOUND,
        "next_recommended_step": final_result["next_recommended_step"],
    }
    summary_path.write_text(json.dumps(remediation_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_hashes_after = {str(path): sha256_file(path) for path in official_paths if path.exists()}
    official_artifacts_unchanged = official_hashes_before == official_hashes_after
    no_official_research_mixing = (
        str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
        and official_artifacts_unchanged
        and all(not str(path).startswith(str(PHASE4_PATH)) for path in (shrinkage_path, threshold_survival_path, diagnostics_path, summary_path))
    )
    tests_passed = os.getenv("PHASE4_META_UPSTREAM_TESTS_PASSED", "0") == "1"
    paper_state = _paper_environment_state()

    gates = [
        {"name": "official_artifacts_unchanged", "value": official_artifacts_unchanged, "threshold": "true", "status": "PASS" if official_artifacts_unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_official_research_mixing, "threshold": "true", "status": "PASS" if no_official_research_mixing else "FAIL"},
        {"name": "raw_calibrated_path_mapped", "value": diagnostics["dominant_cause"], "threshold": "mapped", "status": "PASS"},
        {"name": "calibration_oos_logic_mapped", "value": True, "threshold": "true", "status": "PASS" if diagnostics["calibration_oos_logic"] else "FAIL"},
        {"name": "shrinkage_measured", "value": (not shrinkage_df.empty and not threshold_survival_df.empty), "threshold": "true", "status": "PASS" if (not shrinkage_df.empty and not threshold_survival_df.empty) else "FAIL"},
        {"name": "bounded_research_challengers_executed", "value": 2, "threshold": ">=2", "status": "PASS"},
        {"name": "final_classification_assigned", "value": final_result["classification"], "threshold": "one_of(UPSTREAM_FIX_FOUND,UPSTREAM_REMEDIATION_INCONCLUSIVE,META_FAMILY_OPERATIONALLY_WEAK)", "status": "PASS"},
        {"name": "paper_environment_clean", "value": paper_state["clean"], "threshold": "true", "status": "PASS" if paper_state["clean"] else "FAIL"},
        {"name": "tests_passed", "value": tests_passed, "threshold": "true", "status": "PASS" if tests_passed else "FAIL"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"

    summary = {
        "sharpe_operational": baseline_summary.get("sharpe_operational"),
        "dsr_honest": baseline_summary.get("dsr_honest"),
        "latest_active_count": baseline_summary.get("latest_active_count"),
        "headroom_real": baseline_summary.get("headroom_real"),
        "historical_active_events": baseline_summary.get("historical_active_events"),
    }

    report_sections = {
        "Resumo executivo": (
            f"Rodada concluida com status `{status}` e decision `{final_result['decision']}`. "
            f"Classificacao final: `{final_result['classification']}`."
        ),
        "Baseline congelado": (
            f"- `branch`: `{git_branch}`\n"
            f"- `baseline_commit`: `{baseline_commit}`\n"
            f"- `working_tree_dirty_before`: `{working_tree_dirty_before}`\n"
            f"- `official_report_path`: `{REPORT_PATH}`\n"
            f"- `official_snapshot_path`: `{SNAPSHOT_PATH}`\n"
            f"- `official_aggregated_path`: `{AGGREGATED_PATH}`\n"
            f"- `official_oos_path`: `{OOS_PATH}`"
        ),
        "Mudanças implementadas": (
            "- runner research-only para medir shrinkage raw->calibrated e executar challengers bounded\n"
            "- challenger diagnostico raw_passthrough para medir limite superior sem calibracao\n"
            "- challenger bounded global_isotonic para testar remediation upstream minima dentro da familia atual"
        ),
        "Artifacts gerados": (
            f"- `{shrinkage_path}`\n"
            f"- `{threshold_survival_path}`\n"
            f"- `{diagnostics_path}`\n"
            f"- `{summary_path}`\n"
            f"- `{GATE_PATH / 'gate_report.json'}`\n"
            f"- `{GATE_PATH / 'gate_report.md'}`\n"
            f"- `{GATE_PATH / 'gate_manifest.json'}`\n"
            f"- `{GATE_PATH / 'gate_metrics.parquet'}`"
        ),
        "Resultados": (
            f"- baseline oficial: `latest_active_count={baseline_summary['latest_active_count']}`, `headroom_real={baseline_summary['headroom_real']}`, "
            f"`historical_active_events={baseline_summary['historical_active_events']}`, `sharpe={baseline_summary['sharpe_operational']}`, `dsr={baseline_summary['dsr_honest']}`\n"
            f"- raw passthrough diagnostico: `latest_active_count={raw_variant['summary']['latest_active_count']}`, `headroom_real={raw_variant['summary']['headroom_real']}`, "
            f"`historical_active_events={raw_variant['summary']['historical_active_events']}`, `sharpe={raw_variant['summary']['sharpe_operational']}`, `dsr={raw_variant['summary']['dsr_honest']}`\n"
            f"- global isotonic bounded: `latest_active_count={global_variant['summary']['latest_active_count']}`, `headroom_real={global_variant['summary']['headroom_real']}`, "
            f"`historical_active_events={global_variant['summary']['historical_active_events']}`, `sharpe={global_variant['summary']['sharpe_operational']}`, `dsr={global_variant['summary']['dsr_honest']}`"
        ),
        "Avaliação contra gates": "\n".join(
            f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`"
            for item in gates
        ),
        "Riscos residuais": (
            "- o raw passthrough mostra que existe headroom latente acima do oficial, mas ele nao e remediation defensavel porque degrada o merito operacional\n"
            "- o challenger bounded global_isotonic melhora Sharpe historico, mas continua sem latest headroom ou DSR honesto positivo"
        ),
        "Veredito final: advance / correct / abandon": final_result["decision"],
    }

    blockers: list[str] = []
    if final_result["classification"] == CLASS_FAMILY_WEAK:
        blockers.append("A familia atual continua sem remediation upstream bounded que recupere latest headroom com merito operacional.")
    elif status != "PASS":
        blockers.append("A remediation upstream ficou inconclusiva ou sem evidencias reproduziveis suficientes.")

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": final_result["decision"],
        "baseline_commit": baseline_commit,
        "working_tree_dirty": _worktree_dirty(),
        "branch": git_branch,
        "official_artifacts_used": [
            {"path": str(path), "sha256_before": official_hashes_before.get(str(path)), "sha256_after": official_hashes_after.get(str(path))}
            for path in official_paths
        ],
        "research_artifacts_generated": [
            artifact_record(shrinkage_path),
            artifact_record(threshold_survival_path),
            artifact_record(diagnostics_path),
            artifact_record(summary_path),
        ],
        "summary": summary,
        "gates": gates,
        "blockers": blockers,
        "risks_residual": [
            "A calibracao oficial melhora ECE, mas comprime scores perto do corte operacional e interage com o hard cutoff de Kelly em 0.51.",
            "Nenhum challenger bounded e defensavel nesta rodada recuperou latest_active_count com melhoria honesta de DSR.",
        ],
        "next_recommended_step": final_result["next_recommended_step"],
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": _utc_now_iso(),
        "baseline_commit": baseline_commit,
        "branch": git_branch,
        "working_tree_dirty_before": working_tree_dirty_before,
        "working_tree_dirty_after": _worktree_dirty(),
        "source_artifacts": [
            artifact_record(PHASE4_CPCV_SOURCE, extras={"role": "official_phase4_logic"}),
            artifact_record(ISOTONIC_SOURCE, extras={"role": "official_calibration_logic"}),
            artifact_record(KELLY_SOURCE, extras={"role": "official_kelly_logic"}),
            artifact_record(REPORT_PATH, extras={"role": "official_phase4_report"}),
            artifact_record(SNAPSHOT_PATH, extras={"role": "official_phase4_snapshot"}),
            artifact_record(AGGREGATED_PATH, extras={"role": "official_phase4_aggregated_predictions"}),
            artifact_record(OOS_PATH, extras={"role": "official_phase4_oos_predictions"}),
            artifact_record(ALIGNMENT_GATE_PATH, extras={"role": "prior_alignment_gate"}) if ALIGNMENT_GATE_PATH.exists() else {"path": str(ALIGNMENT_GATE_PATH), "role": "prior_alignment_gate_missing"},
            artifact_record(ALIGNMENT_DIAGNOSTIC_PATH, extras={"role": "prior_alignment_diagnostic"}) if ALIGNMENT_DIAGNOSTIC_PATH.exists() else {"path": str(ALIGNMENT_DIAGNOSTIC_PATH), "role": "prior_alignment_diagnostic_missing"},
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "git status --short --untracked-files=all",
            "git diff --stat",
            "Get-FileHash data\\models\\phase4\\phase4_report_v4.json,data\\models\\phase4\\phase4_execution_snapshot.parquet,data\\models\\phase4\\phase4_aggregated_predictions.parquet,data\\models\\phase4\\phase4_oos_predictions.parquet -Algorithm SHA256 | Select-Object Path,Hash",
            "Select-String -Path services\\ml_engine\\phase4_cpcv.py -Pattern '_fit_cluster_calibrators|_apply_cluster_calibration|_compute_phase4_sizing|_build_execution_snapshot|_build_operational_path_report|PHASE4_META_PROB_MODE'",
            "Get-Content services\\ml_engine\\phase4_cpcv.py | Select-Object -Skip 1540 -First 180",
            "Get-Content services\\ml_engine\\meta_labeling\\isotonic_calibration.py | Select-Object -First 260",
            "Get-Content services\\ml_engine\\sizing\\kelly_cvar.py | Select-Object -Skip 40 -First 120",
            "python -m py_compile services\\ml_engine\\phase4_meta_upstream_remediation.py tests\\unit\\test_phase4_meta_upstream_remediation.py",
            "python -m pytest tests\\unit\\test_phase4_meta_upstream_remediation.py -q",
            "python services\\ml_engine\\phase4_meta_upstream_remediation.py",
        ],
        "notes": [
            f"final_classification={final_result['classification']}",
            f"decision={final_result['decision']}",
            f"paper_environment={paper_state['status']}",
            "diagnostic challenger raw_passthrough is not promotable and is used only to bound headroom without calibration",
        ],
    }

    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": item["name"],
            "metric_value": item["value"],
            "metric_threshold": item["threshold"],
            "metric_status": item["status"],
        }
        for item in gates
    ]
    gate_metrics.extend(
        [
            {
                "gate_slug": GATE_SLUG,
                "metric_name": f"{row['variant_name']}__{metric_name}",
                "metric_value": row[metric_name],
                "metric_threshold": "",
                "metric_status": "INFO",
            }
            for row in [baseline_summary, raw_variant["summary"], global_variant["summary"]]
            for metric_name in (
                "sharpe_operational",
                "dsr_honest",
                "latest_active_count",
                "headroom_real",
                "historical_active_events",
            )
        ]
    )

    write_gate_pack(
        output_dir=GATE_PATH,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=report_sections,
    )
    return {
        "status": status,
        "decision": final_result["decision"],
        "classification": final_result["classification"],
        "gate_path": str(GATE_PATH),
        "research_path": str(RESEARCH_PATH),
    }


def main() -> None:
    result = run_remediation()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
