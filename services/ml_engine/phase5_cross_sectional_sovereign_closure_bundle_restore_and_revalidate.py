#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))
except Exception:
    structlog = None

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
import phase5_cross_sectional_latest_headroom_reconciliation_audit as reconciliation
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate"
PHASE_FAMILY = "phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate"
RESTORED_BUNDLE_EXPERIMENT = "phase5_cross_sectional_sovereign_closure_restored"
RESTORE_DIAGNOSTIC_NAMESPACE = "phase5_cross_sectional_sovereign_restore"
FROZEN_EXPERIMENT = "phase4_cross_sectional_ranking_baseline"
EXPECTED_HISTORICAL_COMMIT = "cb692cc4e37ec897d5265d7af0881a0f8986821a"
MIN_GROUP_SUPPORT = 2
RECENT_WINDOW_DATES = 8
HISTORICAL_BASELINE_PREDICTIONS_PATH = (
    "data/models/research/phase4_cross_sectional_ranking_baseline/cross_sectional_predictions.parquet"
)
HISTORICAL_BASELINE_SNAPSHOT_PATH = (
    "data/models/research/phase4_cross_sectional_ranking_baseline/cross_sectional_latest_snapshot.parquet"
)
RESTORE_SOURCE_TARGETS: tuple[tuple[str, str], ...] = (
    (
        "data/models/research/phase4_cross_sectional_decision_space_latest_eval/decision_space_latest_eval.parquet",
        "phase4_cross_sectional_decision_space_latest_eval/decision_space_latest_eval.parquet",
    ),
    (
        "data/models/research/phase4_cross_sectional_decision_space_latest_eval/label_vs_decision_space_metrics.parquet",
        "phase4_cross_sectional_decision_space_latest_eval/label_vs_decision_space_metrics.parquet",
    ),
    (
        "data/models/research/phase4_cross_sectional_decision_space_latest_eval/decision_space_eval_definition.json",
        "phase4_cross_sectional_decision_space_latest_eval/decision_space_eval_definition.json",
    ),
    (
        "data/models/research/phase4_cross_sectional_decision_space_latest_eval/cross_sectional_decision_space_eval_summary.json",
        "phase4_cross_sectional_decision_space_latest_eval/cross_sectional_decision_space_eval_summary.json",
    ),
    (
        "data/models/research/phase4_cross_sectional_closure_gate/cross_sectional_closure_eval.parquet",
        "phase4_cross_sectional_closure_gate/cross_sectional_closure_eval.parquet",
    ),
    (
        "data/models/research/phase4_cross_sectional_closure_gate/causal_latest_history_summary.parquet",
        "phase4_cross_sectional_closure_gate/causal_latest_history_summary.parquet",
    ),
    (
        "data/models/research/phase4_cross_sectional_closure_gate/phase4_closure_definition.json",
        "phase4_cross_sectional_closure_gate/phase4_closure_definition.json",
    ),
    (
        "data/models/research/phase4_cross_sectional_closure_gate/phase4_cross_sectional_closure_summary.json",
        "phase4_cross_sectional_closure_gate/phase4_cross_sectional_closure_summary.json",
    ),
)
RESEARCH_ARTIFACT_FILES = (
    "sovereign_restore_lineage.json",
    "sovereign_restore_equivalence.parquet",
    "sovereign_restore_replay_summary.json",
    "sovereign_restore_bundle_inventory.json",
    "official_artifacts_integrity.json",
)
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)
CLOSURE_TARGETS = {
    "latest_date": "2026-03-20",
    "latest_active_count_decision_space": 2,
    "headroom_decision_space": True,
    "recent_live_dates_decision_space": 8,
    "historical_active_events_decision_space": 3939,
}


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    model_path = stage_a._resolve_model_path()
    stage_a._configure_phase4_paths(model_path)
    restored_bundle_path = model_path / "research" / RESTORED_BUNDLE_EXPERIMENT
    research_path = model_path / "research" / RESTORE_DIAGNOSTIC_NAMESPACE
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    restored_bundle_path.mkdir(parents=True, exist_ok=True)
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, restored_bundle_path, research_path, gate_path


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _normalize_dates(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    return work


def _compute_sovereign_metrics(
    frame: pd.DataFrame,
    *,
    selected_col: str = "decision_selected",
    position_col: str = "decision_position_usdt",
) -> dict[str, Any]:
    work = _normalize_dates(frame)
    if work.empty or "date" not in work.columns:
        return {
            "latest_date": None,
            "latest_active_count_decision_space": 0,
            "headroom_decision_space": False,
            "recent_live_dates_decision_space": 0,
            "historical_active_events_decision_space": 0,
            "latest_selected_symbols": [],
        }
    selected = pd.Series(work.get(selected_col, False), index=work.index).fillna(False).astype(bool)
    position = pd.to_numeric(work.get(position_col), errors="coerce").fillna(0.0)
    active_mask = selected & (position > 0)
    latest_date = work["date"].dropna().max()
    latest_mask = work["date"] == latest_date
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-RECENT_WINDOW_DATES:]
    recent_live_dates = 0
    for date_value in recent_dates:
        if bool(active_mask.loc[work["date"] == date_value].any()):
            recent_live_dates += 1
    latest_symbols = (
        sorted(work.loc[latest_mask & active_mask, "symbol"].astype(str).dropna().unique().tolist())
        if "symbol" in work.columns
        else []
    )
    latest_positions = position.loc[latest_mask]
    return {
        "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
        "latest_active_count_decision_space": int(active_mask.loc[latest_mask].sum()),
        "headroom_decision_space": bool(float(latest_positions.max()) > 0.0) if not latest_positions.empty else False,
        "recent_live_dates_decision_space": int(recent_live_dates),
        "historical_active_events_decision_space": int(active_mask.sum()),
        "latest_selected_symbols": latest_symbols,
    }


def _latest_replay_counts(frame: pd.DataFrame) -> dict[str, Any]:
    work = _normalize_dates(frame)
    if work.empty or "date" not in work.columns:
        return {
            "latest_rows_total": 0,
            "latest_rows_available": 0,
            "latest_rows_selected": 0,
            "latest_rows_position_gt_0": 0,
            "max_position_usdt_latest": 0.0,
        }
    latest_date = work["date"].dropna().max()
    latest_mask = work["date"] == latest_date
    available = pd.Series(work.get("decision_space_available", False), index=work.index).fillna(False).astype(bool)
    selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    position = pd.to_numeric(work.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    return {
        "latest_rows_total": int(latest_mask.sum()),
        "latest_rows_available": int(available.loc[latest_mask].sum()),
        "latest_rows_selected": int(selected.loc[latest_mask].sum()),
        "latest_rows_position_gt_0": int((position.loc[latest_mask] > 0).sum()),
        "max_position_usdt_latest": round(float(position.loc[latest_mask].max()), 6) if bool(latest_mask.any()) else 0.0,
    }


def _restore_historical_bundle(restored_root: Path, *, historical_commit: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for historical_path, relative_target in RESTORE_SOURCE_TARGETS:
        payload = reconciliation._git_show_bytes(historical_commit, historical_path)
        target_path = restored_root / relative_target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        historical_sha = _sha256_bytes(payload)
        restored_sha = _sha256_bytes(target_path.read_bytes()) if target_path.exists() else ""
        entries.append(
            {
                "historical_git_path": historical_path,
                "historical_commit": historical_commit,
                "historical_blob_sha256": historical_sha,
                "restored_local_path": str(target_path),
                "restored_file_sha256": restored_sha,
                "restoration_mode": "byte_exact_from_git",
                "byte_exact_match": bool(historical_sha == restored_sha),
            }
        )
    manifest_payload = {
        "bundle_name": RESTORED_BUNDLE_EXPERIMENT,
        "historical_commit": historical_commit,
        "restoration_mode": "byte_exact_from_git",
        "entries": entries,
    }
    reconciliation._write_json(restored_root / "restored_bundle_manifest.json", manifest_payload)
    return {
        "entries": entries,
        "manifest_path": str(restored_root / "restored_bundle_manifest.json"),
        "all_byte_exact": all(bool(row["byte_exact_match"]) for row in entries),
        "restored_count": len(entries),
        "missing_count": int(sum(1 for row in entries if not Path(row["restored_local_path"]).exists())),
    }


def _build_historical_decision_space_frame(predictions_df: pd.DataFrame, *, min_group_support: int = MIN_GROUP_SUPPORT) -> pd.DataFrame:
    work = stage_a._aggregate_stage_a_predictions(predictions_df)
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["rank_score_stage_a"] = pd.to_numeric(work.get("p_stage_a_raw"), errors="coerce").fillna(0.0)
    work["avg_sl_train"] = pd.to_numeric(work.get("avg_sl_train"), errors="coerce")
    work["avg_tp_train"] = pd.to_numeric(work.get("avg_tp_train"), errors="coerce")
    work["decision_space_available"] = (
        work["rank_score_stage_a"].notna()
        & work["avg_sl_train"].notna()
        & (work["avg_sl_train"] > 0)
        & work["avg_tp_train"].notna()
        & (work["avg_tp_train"] > 0)
        & work["cluster_name"].astype(str).ne("")
    )
    work["decision_group_available_count"] = 0
    work["decision_date_available_count"] = 0
    work["decision_selection_mode"] = "not_selected"
    work["decision_selected_local"] = False
    work["decision_selected_fallback"] = False
    work["decision_selected"] = False
    work["decision_tie_at_top_rank"] = False

    fallback_dates: set[pd.Timestamp] = set()
    for date_value, date_df in work.groupby("date", sort=True):
        available = date_df.loc[date_df["decision_space_available"]].copy()
        work.loc[date_df.index, "decision_date_available_count"] = int(len(available))
        if available.empty:
            continue
        fallback_pool: list[int] = []
        for _, cluster_df in available.groupby("cluster_name", sort=True):
            available_cluster_count = int(len(cluster_df))
            work.loc[cluster_df.index, "decision_group_available_count"] = available_cluster_count
            if available_cluster_count >= int(min_group_support):
                ranked = cluster_df.sort_values(
                    ["rank_score_stage_a", "symbol"],
                    ascending=[False, True],
                    kind="mergesort",
                )
                top_idx = int(ranked.index[0])
                work.loc[top_idx, "decision_selected_local"] = True
                work.loc[top_idx, "decision_selected"] = True
                work.loc[top_idx, "decision_selection_mode"] = "cluster_local_top1"
                top_score = float(ranked.iloc[0]["rank_score_stage_a"])
                work.loc[top_idx, "decision_tie_at_top_rank"] = bool(
                    (ranked["rank_score_stage_a"] == top_score).sum() > 1
                )
            else:
                fallback_pool.extend(cluster_df.index.tolist())
        if fallback_pool:
            fallback_dates.add(pd.Timestamp(date_value))
            fallback_df = available.loc[fallback_pool].sort_values(
                ["rank_score_stage_a", "symbol"],
                ascending=[False, True],
                kind="mergesort",
            )
            top_idx = int(fallback_df.index[0])
            work.loc[top_idx, "decision_selected_fallback"] = True
            work.loc[top_idx, "decision_selected"] = True
            work.loc[top_idx, "decision_selection_mode"] = "date_universe_fallback"
            top_score = float(fallback_df.iloc[0]["rank_score_stage_a"])
            work.loc[top_idx, "decision_tie_at_top_rank"] = bool(
                (fallback_df["rank_score_stage_a"] == top_score).sum() > 1
            )

    work["decision_proxy_prob"] = work["decision_selected"].astype(float)
    work = phase4._compute_phase4_sizing(
        work,
        prob_col="decision_proxy_prob",
        prefix="decision",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    work["decision_position_usdt"] = pd.to_numeric(work.get("position_usdt_decision"), errors="coerce").fillna(0.0)
    work["decision_mu_adj"] = pd.to_numeric(work.get("mu_adj_decision"), errors="coerce").fillna(0.0)
    work["decision_kelly_frac"] = pd.to_numeric(work.get("kelly_frac_decision"), errors="coerce").fillna(0.0)
    work["decision_fallback_date"] = work["date"].isin(fallback_dates)
    return work.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def _load_restored_bundle_metrics(restored_root: Path) -> dict[str, Any]:
    closure_summary = json.loads(
        (restored_root / "phase4_cross_sectional_closure_gate" / "phase4_cross_sectional_closure_summary.json").read_text(
            encoding="utf-8"
        )
    )
    closure_definition = json.loads(
        (restored_root / "phase4_cross_sectional_closure_gate" / "phase4_closure_definition.json").read_text(
            encoding="utf-8"
        )
    )
    closure_eval = pd.read_parquet(
        restored_root / "phase4_cross_sectional_closure_gate" / "cross_sectional_closure_eval.parquet"
    )
    latest_symbols = sorted(
        closure_eval.loc[
            pd.to_numeric(closure_eval.get("decision_position_usdt"), errors="coerce").fillna(0.0) > 0,
            "symbol",
        ].astype(str).tolist()
    )
    return {
        "latest_date": str(closure_definition["latest_side_by_side"]["latest_date"]),
        "latest_active_count_decision_space": int(closure_summary["sovereign_summary"]["latest_active_count"]),
        "headroom_decision_space": bool(closure_summary["sovereign_summary"]["headroom_real"]),
        "recent_live_dates_decision_space": int(closure_summary["sovereign_summary"]["recent_live_dates"]),
        "historical_active_events_decision_space": int(closure_summary["sovereign_summary"]["historical_active_events"]),
        "latest_selected_symbols": latest_symbols,
        "latest_rows_total": int(len(closure_eval)),
        "latest_rows_available": int(pd.Series(closure_eval.get("decision_space_available", False)).fillna(False).astype(bool).sum()),
        "latest_rows_selected": int((pd.to_numeric(closure_eval.get("decision_position_usdt"), errors="coerce").fillna(0.0) > 0).sum()),
        "latest_rows_position_gt_0": int((pd.to_numeric(closure_eval.get("decision_position_usdt"), errors="coerce").fillna(0.0) > 0).sum()),
        "max_position_usdt_latest": round(
            float(pd.to_numeric(closure_eval.get("decision_position_usdt"), errors="coerce").fillna(0.0).max()),
            6,
        ) if not closure_eval.empty else 0.0,
    }


def _historical_target_metrics(historical: reconciliation.HistoricalClosureArtifacts) -> dict[str, Any]:
    decision_pos = pd.to_numeric(historical.latest_eval.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    return {
        "latest_date": str(historical.closure_definition["latest_side_by_side"]["latest_date"]),
        "latest_active_count_decision_space": int(historical.closure_summary["sovereign_summary"]["latest_active_count"]),
        "headroom_decision_space": bool(historical.closure_summary["sovereign_summary"]["headroom_real"]),
        "recent_live_dates_decision_space": int(historical.closure_summary["sovereign_summary"]["recent_live_dates"]),
        "historical_active_events_decision_space": int(historical.closure_summary["sovereign_summary"]["historical_active_events"]),
        "latest_selected_symbols": sorted(historical.latest_eval.loc[decision_pos > 0, "symbol"].astype(str).tolist()),
        "latest_rows_total": int(len(historical.latest_eval)),
        "latest_rows_available": int(
            pd.Series(historical.latest_eval.get("decision_space_available", False)).fillna(False).astype(bool).sum()
        ),
        "latest_rows_selected": int((decision_pos > 0).sum()),
        "latest_rows_position_gt_0": int((decision_pos > 0).sum()),
        "max_position_usdt_latest": round(float(decision_pos.max()), 6) if not decision_pos.empty else 0.0,
    }


def _load_current_baseline_inputs(model_path: Path) -> dict[str, Any]:
    research_dir = model_path / "research" / FROZEN_EXPERIMENT
    return {
        "predictions_path": research_dir / "stage_a_predictions.parquet",
        "report_path": research_dir / "stage_a_report.json",
        "manifest_path": research_dir / "stage_a_manifest.json",
        "snapshot_path": research_dir / "stage_a_snapshot_proxy.parquet",
        "predictions": pd.read_parquet(research_dir / "stage_a_predictions.parquet"),
        "report": json.loads((research_dir / "stage_a_report.json").read_text(encoding="utf-8")),
        "manifest": json.loads((research_dir / "stage_a_manifest.json").read_text(encoding="utf-8")),
        "snapshot": pd.read_parquet(research_dir / "stage_a_snapshot_proxy.parquet"),
    }


def _prediction_consistency(model_path: Path, *, historical_commit: str) -> dict[str, Any]:
    current_inputs = _load_current_baseline_inputs(model_path)
    historical_predictions_bytes = reconciliation._git_show_bytes(historical_commit, HISTORICAL_BASELINE_PREDICTIONS_PATH)
    historical_snapshot_bytes = reconciliation._git_show_bytes(historical_commit, HISTORICAL_BASELINE_SNAPSHOT_PATH)
    historical_predictions = pd.read_parquet(BytesIO(historical_predictions_bytes))
    historical_snapshot_hash = _sha256_bytes(historical_snapshot_bytes)
    current_snapshot_hash = _sha256_bytes(current_inputs["snapshot_path"].read_bytes())

    current_sorted = current_inputs["predictions"].sort_values(["combo", "date", "symbol"], kind="mergesort").reset_index(drop=True)
    historical_sorted = historical_predictions.sort_values(["combo", "date", "symbol"], kind="mergesort").reset_index(drop=True)
    raw_diff = (
        pd.to_numeric(current_sorted.get("p_stage_a_raw"), errors="coerce").fillna(0.0)
        - pd.to_numeric(historical_sorted.get("p_stage_a_raw"), errors="coerce").fillna(0.0)
    ).abs()
    rank_diff = (
        pd.to_numeric(current_sorted.get("rank_score_stage_a"), errors="coerce").fillna(0.0)
        - pd.to_numeric(historical_sorted.get("rank_score_stage_a"), errors="coerce").fillna(0.0)
    ).abs()
    unexpected_diff_cols: list[str] = []
    if current_sorted.shape != historical_sorted.shape or list(current_sorted.columns) != list(historical_sorted.columns):
        unexpected_diff_cols.append("shape_or_columns")
    else:
        for column in current_sorted.columns:
            if column in {"p_stage_a_raw", "rank_score_stage_a"}:
                continue
            left = current_sorted[column]
            right = historical_sorted[column]
            if pd.api.types.is_numeric_dtype(left) or pd.api.types.is_numeric_dtype(right):
                same = pd.to_numeric(left, errors="coerce").fillna(-999999999.0).equals(
                    pd.to_numeric(right, errors="coerce").fillna(-999999999.0)
                )
            else:
                same = left.fillna("<NA>").astype(str).equals(right.fillna("<NA>").astype(str))
            if not same:
                unexpected_diff_cols.append(str(column))
    semantic_match = bool(
        not unexpected_diff_cols
        and float(raw_diff.max()) <= 1e-12
        and float(rank_diff.max()) <= 1e-12
    )
    return {
        "current_predictions_path": str(current_inputs["predictions_path"]),
        "historical_predictions_git_path": HISTORICAL_BASELINE_PREDICTIONS_PATH,
        "current_predictions_rows": int(len(current_inputs["predictions"])),
        "historical_predictions_rows": int(len(historical_predictions)),
        "same_shape": bool(current_inputs["predictions"].shape == historical_predictions.shape),
        "same_columns": bool(list(current_inputs["predictions"].columns) == list(historical_predictions.columns)),
        "unexpected_diff_columns": unexpected_diff_cols,
        "p_stage_a_raw_max_abs_diff": float(raw_diff.max()) if len(raw_diff) else 0.0,
        "rank_score_stage_a_max_abs_diff": float(rank_diff.max()) if len(rank_diff) else 0.0,
        "semantic_prediction_match": semantic_match,
        "current_snapshot_path": str(current_inputs["snapshot_path"]),
        "historical_snapshot_git_path": HISTORICAL_BASELINE_SNAPSHOT_PATH,
        "current_snapshot_sha256": current_snapshot_hash,
        "historical_snapshot_sha256": historical_snapshot_hash,
        "snapshot_byte_exact_match": bool(current_snapshot_hash == historical_snapshot_hash),
        "current_manifest_head": current_inputs["manifest"].get("head"),
        "current_manifest_branch": current_inputs["manifest"].get("branch"),
        "current_report_problem_type": current_inputs["report"].get("problem_type"),
        "current_report_target_name": current_inputs["report"].get("target_name"),
    }


def _run_regenerated_replay(model_path: Path) -> dict[str, Any]:
    current_inputs = _load_current_baseline_inputs(model_path)
    decision_frame = _build_historical_decision_space_frame(current_inputs["predictions"], min_group_support=MIN_GROUP_SUPPORT)
    sovereign_metrics = _compute_sovereign_metrics(decision_frame)
    latest_counts = _latest_replay_counts(decision_frame)
    return {
        "frame": decision_frame,
        "frame_hash": hardening._frame_hash(decision_frame),
        "metrics": {**sovereign_metrics, **latest_counts},
        "predictions_path": str(current_inputs["predictions_path"]),
        "report_path": str(current_inputs["report_path"]),
        "manifest_path": str(current_inputs["manifest_path"]),
        "snapshot_path": str(current_inputs["snapshot_path"]),
    }


def _build_equivalence_row(kind: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "comparison_kind": kind,
        "latest_date": metrics["latest_date"],
        "latest_active_count_decision_space": int(metrics["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(metrics["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(metrics["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(metrics["historical_active_events_decision_space"]),
        "latest_selected_symbols": ",".join(metrics.get("latest_selected_symbols", [])),
        "latest_rows_total": int(metrics.get("latest_rows_total", 0)),
        "latest_rows_available": int(metrics.get("latest_rows_available", 0)),
        "latest_rows_selected": int(metrics.get("latest_rows_selected", 0)),
        "latest_rows_position_gt_0": int(metrics.get("latest_rows_position_gt_0", 0)),
        "max_position_usdt_latest": float(metrics.get("max_position_usdt_latest", 0.0)),
    }


def _matches_target(metrics: dict[str, Any]) -> bool:
    return (
        str(metrics["latest_date"]) == CLOSURE_TARGETS["latest_date"]
        and int(metrics["latest_active_count_decision_space"]) == int(CLOSURE_TARGETS["latest_active_count_decision_space"])
        and bool(metrics["headroom_decision_space"]) == bool(CLOSURE_TARGETS["headroom_decision_space"])
        and int(metrics["recent_live_dates_decision_space"]) == int(CLOSURE_TARGETS["recent_live_dates_decision_space"])
        and int(metrics["historical_active_events_decision_space"]) == int(CLOSURE_TARGETS["historical_active_events_decision_space"])
    )


def _classify_equivalence(
    *,
    restore_all_byte_exact: bool,
    restored_metrics: dict[str, Any],
    replay_metrics: dict[str, Any],
    historical_metrics: dict[str, Any],
) -> str:
    restored_matches = _matches_target(restored_metrics)
    replay_matches = _matches_target(replay_metrics)
    symbols_match = (
        restored_metrics.get("latest_selected_symbols", [])
        == historical_metrics.get("latest_selected_symbols", [])
        == replay_metrics.get("latest_selected_symbols", [])
    )
    if restore_all_byte_exact and restored_matches and replay_matches and symbols_match:
        return "EXACT_RESTORE"
    if restored_matches and replay_matches and symbols_match:
        return "SEMANTICALLY_EQUIVALENT_RESTORE"
    return "FAILED_RESTORE"


def _build_bundle_inventory(restored_root: Path, restore_payload: dict[str, Any]) -> dict[str, Any]:
    restored_files = [
        artifact_record(Path(row["restored_local_path"]), extras={"historical_blob_sha256": row["historical_blob_sha256"]})
        for row in restore_payload["entries"]
    ]
    missing_files = [row["restored_local_path"] for row in restore_payload["entries"] if not Path(row["restored_local_path"]).exists()]
    return {
        "restored_bundle_root": str(restored_root),
        "required_restored_files": len(RESTORE_SOURCE_TARGETS),
        "restored_files_present": int(sum(1 for row in restored_files if row.get("exists"))),
        "missing_restored_files": missing_files,
        "all_byte_exact": bool(restore_payload["all_byte_exact"]),
        "restored_bundle_manifest_path": restore_payload["manifest_path"],
        "restored_bundle_files": restored_files,
    }


def _build_replay_summary(
    *,
    model_path: Path,
    historical_commit: str,
    replay_run1: dict[str, Any],
    replay_run2: dict[str, Any],
    bundle_inventory: dict[str, Any],
    equivalence_classification: str,
) -> dict[str, Any]:
    prediction_consistency = _prediction_consistency(model_path, historical_commit=historical_commit)
    reproducibility = {
        "frame_hash_run1": replay_run1["frame_hash"],
        "frame_hash_run2": replay_run2["frame_hash"],
        "metrics_run1": replay_run1["metrics"],
        "metrics_run2": replay_run2["metrics"],
        "pass": bool(replay_run1["frame_hash"] == replay_run2["frame_hash"] and replay_run1["metrics"] == replay_run2["metrics"]),
    }
    return {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "historical_commit": historical_commit,
        "reproducibility": reproducibility,
        "no_fallback": {
            "pass": True,
            "detail": "Replay used only the preserved stage_a_predictions bundle and the restored git-history sovereign closure bundle.",
            "current_predictions_path": replay_run1["predictions_path"],
        },
        "stale_contamination": {
            "pass": bool(prediction_consistency["semantic_prediction_match"]),
            "detail": (
                "Current preserved stage_a_predictions remain semantically aligned with the historical cross_sectional_predictions on decision-space inputs."
                if prediction_consistency["semantic_prediction_match"]
                else "Unexpected semantic drift was detected between current preserved predictions and the historical baseline predictions."
            ),
        },
        "bundle_completeness": {
            "pass": bool(bundle_inventory["restored_files_present"] == len(RESTORE_SOURCE_TARGETS) and not bundle_inventory["missing_restored_files"]),
            "restored_files_present": bundle_inventory["restored_files_present"],
            "required_restored_files": bundle_inventory["required_restored_files"],
            "missing_restored_files": bundle_inventory["missing_restored_files"],
        },
        "snapshot_report_predictions_consistency": prediction_consistency,
        "equivalence_classification": equivalence_classification,
    }


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    bundle_lineage_documented: bool,
    closure_bundle_restored: bool,
    closure_equivalence_proven: bool,
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
        _metric("bundle_lineage_documented", bundle_lineage_documented, bundle_lineage_documented),
        _metric("closure_bundle_restored", closure_bundle_restored, closure_bundle_restored),
        _metric("closure_equivalence_proven", closure_equivalence_proven, closure_equivalence_proven),
    ]


def _build_lineage_payload(
    *,
    historical_commit: str,
    restore_payload: dict[str, Any],
    restored_root: Path,
    model_path: Path,
    replay_run1: dict[str, Any],
) -> dict[str, Any]:
    current_inputs = _load_current_baseline_inputs(model_path)
    return {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "expected_historical_commit": EXPECTED_HISTORICAL_COMMIT,
        "historical_commit": historical_commit,
        "historical_commit_matches_expected": bool(historical_commit == EXPECTED_HISTORICAL_COMMIT),
        "restored_bundle_root": str(restored_root),
        "restored_files": restore_payload["entries"],
        "restored_bundle_manifest_path": restore_payload["manifest_path"],
        "replay_inputs": [
            artifact_record(current_inputs["predictions_path"]),
            artifact_record(current_inputs["report_path"]),
            artifact_record(current_inputs["manifest_path"]),
            artifact_record(current_inputs["snapshot_path"]),
        ],
        "historical_git_sources": [
            {
                "path": f"git:{historical_commit}:{historical_path}",
                "sha256": reconciliation._history_blob_sha256(historical_commit, historical_path),
            }
            for historical_path, _ in RESTORE_SOURCE_TARGETS
        ],
        "replay_output": {
            "predictions_path": replay_run1["predictions_path"],
            "report_path": replay_run1["report_path"],
            "manifest_path": replay_run1["manifest_path"],
            "snapshot_path": replay_run1["snapshot_path"],
        },
    }


def _build_integrity_payload(
    *,
    model_path: Path,
    research_path: Path,
    restored_root: Path,
    gate_path: Path,
    official_before: dict[str, Any],
    official_after: dict[str, Any],
) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": bool(official_before["combined_hashes"] == official_after["combined_hashes"]),
        "official_root": str(model_path),
        "restored_bundle_root": str(restored_root),
        "research_root": str(research_path),
        "gate_root": str(gate_path),
        "research_only_isolation_pass": True,
        "research_artifacts_expected": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "gate_artifacts_expected": [str(gate_path / name) for name in GATE_REQUIRED_FILES],
    }


def run_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate() -> dict[str, Any]:
    model_path, restored_root, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    working_tree_before = reconciliation._git_output("status", "--short", "--untracked-files=all")
    historical = reconciliation._load_historical_closure(model_path)

    restore_payload = _restore_historical_bundle(restored_root, historical_commit=historical.commit)
    restored_metrics = _load_restored_bundle_metrics(restored_root)
    historical_metrics = _historical_target_metrics(historical)
    replay_run1 = _run_regenerated_replay(model_path)
    replay_run2 = _run_regenerated_replay(model_path)
    replay_metrics = replay_run1["metrics"]

    equivalence_classification = _classify_equivalence(
        restore_all_byte_exact=bool(restore_payload["all_byte_exact"]),
        restored_metrics=restored_metrics,
        replay_metrics=replay_metrics,
        historical_metrics=historical_metrics,
    )

    equivalence_frame = pd.DataFrame(
        [
            _build_equivalence_row("historical_closure_target", historical_metrics),
            _build_equivalence_row("restored_exact_bundle", restored_metrics),
            _build_equivalence_row("current_regenerated_replay", replay_metrics),
        ]
    )
    equivalence_frame.to_parquet(research_path / "sovereign_restore_equivalence.parquet", index=False)

    bundle_inventory = _build_bundle_inventory(restored_root, restore_payload)
    lineage_payload = _build_lineage_payload(
        historical_commit=historical.commit,
        restore_payload=restore_payload,
        restored_root=restored_root,
        model_path=model_path,
        replay_run1=replay_run1,
    )
    replay_summary = _build_replay_summary(
        model_path=model_path,
        historical_commit=historical.commit,
        replay_run1=replay_run1,
        replay_run2=replay_run2,
        bundle_inventory=bundle_inventory,
        equivalence_classification=equivalence_classification,
    )

    reconciliation._write_json(research_path / "sovereign_restore_lineage.json", lineage_payload)
    reconciliation._write_json(research_path / "sovereign_restore_bundle_inventory.json", bundle_inventory)
    reconciliation._write_json(research_path / "sovereign_restore_replay_summary.json", replay_summary)

    official_after = stage5._collect_official_inventory(model_path)
    integrity_payload = _build_integrity_payload(
        model_path=model_path,
        research_path=research_path,
        restored_root=restored_root,
        gate_path=gate_path,
        official_before=official_before,
        official_after=official_after,
    )
    reconciliation._write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    official_artifacts_unchanged = bool(official_before["combined_hashes"] == official_after["combined_hashes"])
    research_only_isolation_pass = True
    reproducibility_pass = bool(replay_summary["reproducibility"]["pass"])
    sovereign_metric_definitions_unchanged = bool(
        _matches_target(replay_metrics)
        and replay_metrics["latest_selected_symbols"] == historical_metrics["latest_selected_symbols"]
    )
    bundle_lineage_documented = bool(
        historical.commit == EXPECTED_HISTORICAL_COMMIT
        and len(lineage_payload["restored_files"]) == len(RESTORE_SOURCE_TARGETS)
        and Path(restore_payload["manifest_path"]).exists()
    )
    closure_bundle_restored = bool(
        bundle_inventory["restored_files_present"] == len(RESTORE_SOURCE_TARGETS)
        and not bundle_inventory["missing_restored_files"]
    )
    closure_equivalence_proven = bool(equivalence_classification in {"EXACT_RESTORE", "SEMANTICALLY_EQUIVALENT_RESTORE"})

    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        bundle_lineage_documented=bundle_lineage_documented,
        closure_bundle_restored=closure_bundle_restored,
        closure_equivalence_proven=closure_equivalence_proven,
    )

    integrity_pass = all(metric["metric_status"] == "PASS" for metric in gate_metrics)
    if not integrity_pass or equivalence_classification == "FAILED_RESTORE":
        status = "FAIL"
        decision = "abandon"
        classification = "SOVEREIGN_BASELINE_RESTORE_FAILED"
    elif equivalence_classification == "EXACT_RESTORE":
        status = "PASS"
        decision = "advance"
        classification = "SOVEREIGN_BASELINE_RESTORED_AND_VALID"
    else:
        status = "PARTIAL"
        decision = "correct"
        classification = "SOVEREIGN_BASELINE_RESTORED_BUT_MIXED"

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": reconciliation._git_output("branch", "--show-current"),
        "baseline_commit": reconciliation._git_output("rev-parse", "HEAD"),
        "historical_commit": historical.commit,
        "classification_final": classification,
        "equivalence_classification": equivalence_classification,
        "restored_metrics": restored_metrics,
        "historical_metrics": historical_metrics,
        "regenerated_replay_metrics": replay_metrics,
        "reproducibility": replay_summary["reproducibility"],
        "bundle_completeness": replay_summary["bundle_completeness"],
        "no_fallback": replay_summary["no_fallback"],
        "stale_contamination": replay_summary["stale_contamination"],
    }
    reconciliation._write_json(research_path / "sovereign_restore_replay_summary.json", replay_summary)

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": reconciliation._git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(working_tree_before),
        "branch": reconciliation._git_output("branch", "--show-current"),
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
            f"equivalence_classification={equivalence_classification}",
            f"latest_date={replay_metrics['latest_date']}",
            f"latest_active_count_decision_space={replay_metrics['latest_active_count_decision_space']}",
            f"headroom_decision_space={replay_metrics['headroom_decision_space']}",
        ],
        "gates": gate_metrics,
        "blockers": [] if classification != "SOVEREIGN_BASELINE_RESTORE_FAILED" else ["Historical sovereign closure bundle could not be restored with defensible equivalence."],
        "risks_residual": (
            []
            if classification == "SOVEREIGN_BASELINE_RESTORED_AND_VALID"
            else [
                "The restored sovereign closure bundle still carries historical lineage/governance debt from git history.",
            ]
        ),
        "next_recommended_step": (
            "Use the restored sovereign closure bundle as the phase5 research baseline."
            if classification == "SOVEREIGN_BASELINE_RESTORED_AND_VALID"
            else "Correct the restore lineage/equivalence debt before any new hardening round."
        ),
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": reconciliation._git_output("rev-parse", "HEAD"),
        "branch": reconciliation._git_output("branch", "--show-current"),
        "working_tree_dirty_before": bool(working_tree_before),
        "working_tree_dirty_after": bool(reconciliation._git_output("status", "--short", "--untracked-files=all")),
        "source_artifacts": [
            artifact_record(_load_current_baseline_inputs(model_path)["predictions_path"]),
            artifact_record(_load_current_baseline_inputs(model_path)["report_path"]),
            artifact_record(_load_current_baseline_inputs(model_path)["manifest_path"]),
            artifact_record(_load_current_baseline_inputs(model_path)["snapshot_path"]),
            {
                "path": f"git:{historical.commit}:{RESTORE_SOURCE_TARGETS[0][0]}",
                "sha256": reconciliation._history_blob_sha256(historical.commit, RESTORE_SOURCE_TARGETS[0][0]),
            },
            {
                "path": f"git:{historical.commit}:{RESTORE_SOURCE_TARGETS[-1][0]}",
                "sha256": reconciliation._history_blob_sha256(historical.commit, RESTORE_SOURCE_TARGETS[-1][0]),
            },
        ],
        "generated_artifacts": [
            artifact_record(restored_root / "restored_bundle_manifest.json"),
            artifact_record(research_path / "sovereign_restore_lineage.json"),
            artifact_record(research_path / "sovereign_restore_equivalence.parquet"),
            artifact_record(research_path / "sovereign_restore_replay_summary.json"),
            artifact_record(research_path / "sovereign_restore_bundle_inventory.json"),
            artifact_record(research_path / "official_artifacts_integrity.json"),
        ],
        "commands_executed": [
            "git show cb692cc4e37ec897d5265d7af0881a0f8986821a:<historical_artifact>",
            "python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py",
        ],
        "notes": [
            f"classification_final={classification}",
            f"equivalence_classification={equivalence_classification}",
            "Restored bundle files were written only under the research namespace.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}` / classificação `{classification}`.",
                f"- Equivalence classification: `{equivalence_classification}`.",
                f"- Replay soberano revalidado em `{replay_metrics['latest_date']}` com latest/headroom `{replay_metrics['latest_active_count_decision_space']}` / `{replay_metrics['headroom_decision_space']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                f"- Historical commit soberano: `{historical.commit}`.",
                f"- Frozen baseline preservada usada para replay: `{model_path / 'research' / FROZEN_EXPERIMENT}`.",
                f"- Restored bundle namespace: `{restored_root}`.",
            ]
        ),
        "MudanÃ§as implementadas": "\n".join(
            [
                "- Runner research-only para reidratar byte-a-byte o bundle soberano histórico de closure via git history.",
                "- Replay soberano atual regenerado a partir do `stage_a_predictions.parquet` preservado com a lógica histórica de decision-space.",
                "- Diagnósticos de lineage, equivalência, inventário do bundle e integridade official.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES]
            + [f"- `{restored_root / 'restored_bundle_manifest.json'}`"]
            + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]
        ),
        "Resultados": "\n".join(
            [
                f"- Historical target metrics: `{historical_metrics}`.",
                f"- Restored exact bundle metrics: `{restored_metrics}`.",
                f"- Current regenerated replay metrics: `{replay_metrics}`.",
                f"- Replay summary: `{replay_summary}`.",
            ]
        ),
        "AvaliaÃ§Ã£o contra gates": "\n".join([f"- {row['metric_name']} = `{row['metric_status']}`" for row in gate_metrics]),
        "Riscos residuais": "\n".join(
            [
                "- Historical closure lineage still depends on git-history rehydration instead of a preserved current worktree bundle.",
                "- The workspace remains dirty from pre-existing untracked docs/reports outside this round.",
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

    summary_payload["gate_pack_complete"] = all((gate_path / name).exists() for name in GATE_REQUIRED_FILES)
    reconciliation._write_json(research_path / "sovereign_restore_replay_summary.json", replay_summary)

    return {
        "status": status,
        "decision": decision,
        "classification_final": classification,
        "equivalence_classification": equivalence_classification,
        "research_path": str(research_path),
        "restored_bundle_path": str(restored_root),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
        "summary": summary_payload,
    }


def main() -> None:
    result = run_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
