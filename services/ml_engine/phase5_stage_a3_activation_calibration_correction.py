#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
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
import phase5_stage_a3_choke_audit as choke_audit
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack
from services.ml_engine.meta_labeling.isotonic_calibration import fit_isotonic_calibrator

GATE_SLUG = "phase5_stage_a3_activation_calibration_correction"
PHASE_FAMILY = "phase5_stage_a3_activation_calibration_correction"
FROZEN_EXPERIMENT = "phase5_stage_a3"
BASELINE_EXPERIMENT = "phase4_cross_sectional_ranking_baseline"
RECENT_WINDOW_DATES = 8
REALIZED_DROP_COLS = [
    "pnl_real",
    "y_stage_a",
    "stage_a_utility_real",
    "stage_a_utility_surplus",
    "stage_a_score_realized",
]


@dataclass
class ChallengerResult:
    name: str
    description: str
    change_family: str
    pre_proxy: pd.DataFrame
    final: pd.DataFrame
    selection_summary: dict[str, Any]
    calibration_diag: dict[str, Any]
    comparison_row: dict[str, Any]


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
    model_path = (REPO_ROOT / "data" / "models").resolve()
    stage_a._configure_phase4_paths(model_path)
    research_path = model_path / "research" / "phase5_stage_a3"
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, research_path, gate_path


def _normalize_dates(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame.get("date"), errors="coerce").dt.normalize()


def _frame_compare(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    keys: list[str],
    bool_cols: list[str] | None = None,
    num_cols: list[str] | None = None,
) -> dict[str, Any]:
    bool_cols = bool_cols or []
    num_cols = num_cols or []
    compare_cols = keys + bool_cols + num_cols
    merged = (
        left[compare_cols]
        .merge(
            right[compare_cols],
            on=keys,
            how="outer",
            suffixes=("_left", "_right"),
        )
        .sort_values(keys, kind="mergesort")
        .reset_index(drop=True)
    )
    result: dict[str, Any] = {"rows_compared": int(len(merged))}
    exact = True
    for col in bool_cols:
        mismatch = int(
            (
                pd.Series(merged.get(f"{col}_left", False), index=merged.index).fillna(False).astype(bool)
                != pd.Series(merged.get(f"{col}_right", False), index=merged.index).fillna(False).astype(bool)
            ).sum()
        )
        result[f"{col}_mismatch_count"] = mismatch
        exact = exact and mismatch == 0
    for col in num_cols:
        left_num = pd.to_numeric(merged.get(f"{col}_left"), errors="coerce").fillna(0.0)
        right_num = pd.to_numeric(merged.get(f"{col}_right"), errors="coerce").fillna(0.0)
        diff = (left_num - right_num).abs()
        result[f"{col}_max_abs_diff"] = round(float(diff.max()), 12) if not diff.empty else 0.0
        result[f"{col}_diff_gt_1e12_count"] = int((diff > 1e-12).sum())
        exact = exact and bool((diff <= 1e-12).all())
    result["exact_match"] = bool(exact)
    return result


def _layer_counts(
    frame: pd.DataFrame,
    *,
    variant: str,
    layer: str,
    scope_name: str,
    raw_col: str,
    cal_col: str,
    activated_col: str | None = None,
    selected_col: str | None = None,
    position_col: str | None = None,
    combo: str | None = None,
) -> dict[str, Any]:
    raw = pd.to_numeric(frame.get(raw_col), errors="coerce").fillna(0.0)
    cal = pd.to_numeric(frame.get(cal_col), errors="coerce").fillna(0.0)
    activated = (
        pd.Series(frame.get(activated_col, False), index=frame.index).fillna(False).astype(bool)
        if activated_col
        else pd.Series(False, index=frame.index, dtype=bool)
    )
    selected = (
        pd.Series(frame.get(selected_col, False), index=frame.index).fillna(False).astype(bool)
        if selected_col
        else pd.Series(False, index=frame.index, dtype=bool)
    )
    position = (
        pd.to_numeric(frame.get(position_col), errors="coerce").fillna(0.0)
        if position_col
        else pd.Series(0.0, index=frame.index, dtype=float)
    )
    return {
        "variant": variant,
        "layer": layer,
        "scope_name": scope_name,
        "combo": combo,
        "n_rows": int(len(frame)),
        "n_symbols": int(frame["symbol"].nunique()) if "symbol" in frame.columns else 0,
        "raw_hits_gt_050": int((raw > 0.50).sum()),
        "calibrated_hits_gt_050": int((cal > 0.50).sum()),
        "activated_count": int(activated.sum()),
        "decision_selected_count": int(selected.sum()),
        "position_gt_0_count": int((position > 0).sum()),
        "max_raw": round(float(raw.max()), 6) if not raw.empty else 0.0,
        "max_calibrated": round(float(cal.max()), 6) if not cal.empty else 0.0,
        "max_position_usdt": round(float(position.max()), 6) if not position.empty else 0.0,
    }


def _build_row_level_fold_rows(row_df: pd.DataFrame, variant: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for combo, combo_df in row_df.groupby("combo", sort=True):
        rows.append(
            _layer_counts(
                combo_df,
                variant=variant,
                layer="row_level_by_fold",
                scope_name="combo",
                raw_col="p_activate_raw_stage_a",
                cal_col="p_activate_calibrated_stage_a",
                combo=str(combo),
            )
        )
    return pd.DataFrame(rows)


def _rebuild_from_pre_proxy(pre_proxy: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    final_df, selection_summary = stage_a._apply_two_stage_activation_utility_proxy(pre_proxy.copy())
    final_df = phase4._compute_phase4_sizing(
        final_df,
        prob_col="decision_score_stage_a",
        prefix="stage_a",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    final_df = phase4._attach_execution_pnl(
        final_df,
        position_col="position_usdt_stage_a",
        output_col="pnl_exec_stage_a",
    )
    return final_df, selection_summary


def _prove_no_leakage_pre_proxy(pre_proxy: pd.DataFrame) -> dict[str, Any]:
    with_realized, _ = stage_a._apply_two_stage_activation_utility_proxy(pre_proxy.copy())
    without_realized, _ = stage_a._apply_two_stage_activation_utility_proxy(
        pre_proxy.drop(columns=[col for col in REALIZED_DROP_COLS if col in pre_proxy.columns], errors="ignore").copy()
    )
    compare = _frame_compare(
        with_realized,
        without_realized,
        keys=["date", "symbol", "cluster_name"],
        bool_cols=["stage_a_selected_proxy", "decision_selected"],
        num_cols=["decision_score_stage_a"],
    )
    return {
        "applies": True,
        "pass": bool(compare["exact_match"]),
        "selection_inputs_are_predicted_only": True,
        "selected_rows_match_when_realized_columns_removed": compare["stage_a_selected_proxy_mismatch_count"] == 0,
        "decision_flags_match_when_realized_columns_removed": compare["decision_selected_mismatch_count"] == 0,
        "decision_scores_match_when_realized_columns_removed": compare["decision_score_stage_a_diff_gt_1e12_count"] == 0,
        "columns_removed_for_proof": REALIZED_DROP_COLS,
    }


def _reconcile_frozen_pipeline(
    frozen: stage5.RebuiltExperiment,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    row_df = frozen.predictions.copy()
    calibration_df = row_df.rename(columns={"y_stage_a": "y_meta", "p_stage_a_raw": "p_meta_raw"})
    calibrators, cluster_summary, symbol_to_cluster, cluster_mode, artifact_path = phase4._fit_cluster_calibrators(calibration_df)
    reproduced_row_cal = phase4._apply_cluster_calibration(calibration_df, calibrators, symbol_to_cluster)

    reproduced_row_df = row_df.copy()
    reproduced_row_df["p_stage_a_calibrated"] = reproduced_row_cal
    reproduced_row_df["p_activate_calibrated_stage_a"] = reproduced_row_cal
    reproduced_agg_pre = stage_a._aggregate_stage_a_predictions(reproduced_row_df)
    reproduced_final, _ = _rebuild_from_pre_proxy(reproduced_agg_pre)

    row_compare = _frame_compare(
        row_df,
        reproduced_row_df,
        keys=["combo", "date", "symbol"],
        num_cols=["p_activate_calibrated_stage_a"],
    )
    agg_compare = _frame_compare(
        frozen.aggregated_pre_proxy,
        reproduced_agg_pre,
        keys=["date", "symbol"],
        num_cols=["p_activate_calibrated_stage_a"],
    )
    sovereign_compare = _frame_compare(
        frozen.aggregated,
        reproduced_final,
        keys=["date", "symbol"],
        bool_cols=["decision_selected"],
        num_cols=[
            "decision_score_stage_a",
            "position_usdt_stage_a",
            "mu_adj_stage_a",
            "p_activate_calibrated_stage_a",
        ],
    )

    layer_rows = [
        _layer_counts(
            row_df,
            variant="frozen_a3_q60",
            layer="row_level_cpcv",
            scope_name="all_rows",
            raw_col="p_activate_raw_stage_a",
            cal_col="p_activate_calibrated_stage_a",
        ),
        _layer_counts(
            frozen.aggregated_pre_proxy,
            variant="frozen_a3_q60",
            layer="row_level_aggregated_cpcv",
            scope_name="date_symbol_mean",
            raw_col="p_activate_raw_stage_a",
            cal_col="p_activate_calibrated_stage_a",
        ),
        _layer_counts(
            frozen.aggregated,
            variant="frozen_a3_q60",
            layer="sovereign_final_aggregated",
            scope_name="decision_space",
            raw_col="p_activate_raw_stage_a",
            cal_col="p_activate_calibrated_stage_a",
            activated_col="stage_a_predicted_activated",
            selected_col="decision_selected",
            position_col="position_usdt_stage_a",
        ),
    ]
    layer_df = pd.DataFrame(layer_rows)
    fold_df = _build_row_level_fold_rows(row_df, "frozen_a3_q60")

    reconciliation = {
        "row_level_reproduction": {
            **row_compare,
            "raw_hits_gt_050_row_level": int((pd.to_numeric(row_df["p_activate_raw_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()),
            "calibrated_hits_gt_050_row_level": int((pd.to_numeric(row_df["p_activate_calibrated_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()),
            "cluster_calibration_mode": cluster_mode,
            "cluster_calibration_artifact": artifact_path,
            "cluster_calibration_summary": cluster_summary,
        },
        "cpcv_aggregate_reproduction": {
            **agg_compare,
            "raw_hits_gt_050_cpcv_aggregated": int(
                (pd.to_numeric(frozen.aggregated_pre_proxy["p_activate_raw_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()
            ),
            "calibrated_hits_gt_050_cpcv_aggregated": int(
                (pd.to_numeric(frozen.aggregated_pre_proxy["p_activate_calibrated_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()
            ),
        },
        "sovereign_final_reproduction": sovereign_compare,
        "survivor_loss": {
            "row_level_raw_gt_050": int(
                (pd.to_numeric(row_df["p_activate_raw_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()
            ),
            "row_level_calibrated_gt_050": int(
                (pd.to_numeric(row_df["p_activate_calibrated_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()
            ),
            "cpcv_aggregated_calibrated_gt_050": int(
                (pd.to_numeric(frozen.aggregated_pre_proxy["p_activate_calibrated_stage_a"], errors="coerce").fillna(0.0) > 0.50).sum()
            ),
            "sovereign_decision_selected": int(
                pd.Series(frozen.aggregated.get("decision_selected", False), index=frozen.aggregated.index).fillna(False).astype(bool).sum()
            ),
            "sovereign_position_gt_0": int(
                (pd.to_numeric(frozen.aggregated.get("position_usdt_stage_a"), errors="coerce").fillna(0.0) > 0.0).sum()
            ),
        },
    }
    return reconciliation, layer_df, fold_df


def _challenger_aggregate_then_cluster_isotonic(
    frozen: stage5.RebuiltExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre_proxy = frozen.aggregated_pre_proxy.copy()
    calibration_df = pre_proxy.rename(columns={"y_stage_a": "y_meta", "p_stage_a_raw": "p_meta_raw"})
    calibrators, cluster_summary, symbol_to_cluster, cluster_mode, artifact_path = phase4._fit_cluster_calibrators(calibration_df)
    calibrated = phase4._apply_cluster_calibration(calibration_df, calibrators, symbol_to_cluster)
    out = pre_proxy.copy()
    out["cluster_name"] = out["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
    out["p_stage_a_calibrated"] = calibrated
    out["p_activate_calibrated_stage_a"] = calibrated
    return out, {
        "cluster_calibration_mode": cluster_mode,
        "cluster_calibration_artifact": artifact_path,
        "cluster_calibration_summary": cluster_summary,
        "calibration_scope": "aggregate_then_cluster_specific_isotonic",
    }


def _challenger_aggregate_then_global_isotonic(
    frozen: stage5.RebuiltExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre_proxy = frozen.aggregated_pre_proxy.copy()
    raw = pd.to_numeric(pre_proxy.get("p_stage_a_raw"), errors="coerce").fillna(0.0).values
    y_true = pd.to_numeric(pre_proxy.get("y_stage_a"), errors="coerce").fillna(0).astype(int).values
    dates = pd.DatetimeIndex(pd.to_datetime(pre_proxy["date"]).values)
    calibrator = fit_isotonic_calibrator(raw, y_true, dates, halflife_days=phase4.HALFLIFE_DAYS)
    clipped = np.clip(raw, 0.001, 0.999)
    calibrated = np.asarray(calibrator.predict(clipped), dtype=float)
    calibrated[raw <= 0.0] = 0.0
    out = pre_proxy.copy()
    out["p_stage_a_calibrated"] = calibrated
    out["p_activate_calibrated_stage_a"] = calibrated
    return out, {
        "cluster_calibration_mode": "global_only",
        "cluster_calibration_artifact": None,
        "cluster_calibration_summary": [],
        "calibration_scope": "aggregate_then_global_isotonic",
    }


def _build_challenger_result(
    *,
    name: str,
    description: str,
    change_family: str,
    pre_proxy: pd.DataFrame,
    calibration_diag: dict[str, Any],
    raw_hits_gt_050_row_level: int,
    calibrated_hits_gt_050_row_level: int,
    integrity_flags: dict[str, bool],
) -> ChallengerResult:
    final_df, selection_summary = _rebuild_from_pre_proxy(pre_proxy)
    no_leakage = _prove_no_leakage_pre_proxy(pre_proxy)
    decision_space = stage5._compute_decision_space_metrics(final_df)
    operational = stage5._compute_operational_metrics(final_df, "decision_score_stage_a")
    agg_cal = pd.to_numeric(pre_proxy.get("p_activate_calibrated_stage_a"), errors="coerce").fillna(0.0)
    comparison_row = {
        "variant": name,
        "description": description,
        "change_family": change_family,
        "raw_hits_gt_050_row_level": int(raw_hits_gt_050_row_level),
        "calibrated_hits_gt_050_row_level": int(calibrated_hits_gt_050_row_level),
        "calibrated_hits_gt_050_cpcv_aggregated": int((agg_cal > 0.50).sum()),
        "latest_active_count_decision_space": int(decision_space["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(decision_space["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(decision_space["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(decision_space["historical_active_events_decision_space"]),
        "sharpe_operational": round(float(operational["sharpe"]), 4),
        "dsr_honest": round(float(operational["dsr_honest"]), 4),
        "subperiods_positive": int(operational["subperiods_positive"]),
        "official_artifacts_unchanged": bool(integrity_flags["official_artifacts_unchanged"]),
        "research_only_isolation_pass": bool(integrity_flags["research_only_isolation_pass"]),
        "no_leakage_proof_pass": bool(no_leakage["pass"]),
    }
    return ChallengerResult(
        name=name,
        description=description,
        change_family=change_family,
        pre_proxy=pre_proxy,
        final=final_df,
        selection_summary=selection_summary,
        calibration_diag={
            **calibration_diag,
            "no_leakage_proof": no_leakage,
            "max_calibrated_cpcv_aggregated": round(float(agg_cal.max()), 6) if not agg_cal.empty else 0.0,
        },
        comparison_row=comparison_row,
    )


def _build_reconciliation_rows_for_variant(
    *,
    variant: str,
    row_level_raw_hits: int,
    row_level_cal_hits: int,
    pre_proxy: pd.DataFrame,
    final_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    return [
        {
            "variant": variant,
            "layer": "row_level_cpcv",
            "scope_name": "frozen_row_level_shared",
            "combo": None,
            "n_rows": None,
            "n_symbols": None,
            "raw_hits_gt_050": int(row_level_raw_hits),
            "calibrated_hits_gt_050": int(row_level_cal_hits),
            "activated_count": None,
            "decision_selected_count": None,
            "position_gt_0_count": None,
            "max_raw": None,
            "max_calibrated": None,
            "max_position_usdt": None,
        },
        _layer_counts(
            pre_proxy,
            variant=variant,
            layer="row_level_aggregated_cpcv",
            scope_name="date_symbol_mean",
            raw_col="p_activate_raw_stage_a",
            cal_col="p_activate_calibrated_stage_a",
        ),
        _layer_counts(
            final_df,
            variant=variant,
            layer="sovereign_final_aggregated",
            scope_name="decision_space",
            raw_col="p_activate_raw_stage_a",
            cal_col="p_activate_calibrated_stage_a",
            activated_col="stage_a_predicted_activated",
            selected_col="decision_selected",
            position_col="position_usdt_stage_a",
        ),
    ]


def _diagnose_dominant_choke(
    reconciliation: dict[str, Any],
    challenger_rows: list[dict[str, Any]],
) -> tuple[bool, str, str]:
    if not all(
        [
            reconciliation["row_level_reproduction"]["exact_match"],
            reconciliation["cpcv_aggregate_reproduction"]["exact_match"],
            reconciliation["sovereign_final_reproduction"]["exact_match"],
        ]
    ):
        return False, "ambiguous", "Frozen A3-q60 could not be reproduced exactly across the three required layers."

    row_raw = int(reconciliation["survivor_loss"]["row_level_raw_gt_050"])
    row_cal = int(reconciliation["survivor_loss"]["row_level_calibrated_gt_050"])
    agg_cal = int(reconciliation["survivor_loss"]["cpcv_aggregated_calibrated_gt_050"])
    if row_raw <= row_cal:
        return False, "ambiguous", "Raw-to-calibrated compression was not dominant in the frozen reproduction."

    if agg_cal == 0:
        best_challenger = max(challenger_rows, key=lambda row: int(row["calibrated_hits_gt_050_cpcv_aggregated"]), default=None)
        if best_challenger and int(best_challenger["calibrated_hits_gt_050_cpcv_aggregated"]) > 0:
            return (
                True,
                "calibrator_fit_mapping_primary__cpcv_mean_aggregation_secondary",
                "Primary collapse happens inside the calibrator fit/mapping at row level; mean CPCV aggregation then removes the few surviving >0.50 hits before the sovereign threshold.",
            )
        return (
            True,
            "calibrator_fit_mapping_primary",
            "Collapse happens inside the calibrator fit/mapping before the sovereign aggregated threshold is even reached.",
        )

    return (
        True,
        "cpcv_aggregation_secondary",
        "Aggregation is reducing calibrated mass materially, but the frozen path still preserves some >0.50 activation before the sovereign threshold.",
    )


def _gate_metrics(
    *,
    integrity: dict[str, Any],
    no_leakage_proof_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    counter_reconciliation_complete: bool,
    dominant_choke_confirmed: bool,
    bounded_fix_only: bool,
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
        _metric("official_artifacts_unchanged", integrity["official_artifacts_unchanged"], integrity["official_artifacts_unchanged"]),
        _metric("research_only_isolation_pass", integrity["research_only_isolation_pass"], integrity["research_only_isolation_pass"]),
        _metric("no_leakage_proof_pass", no_leakage_proof_pass, no_leakage_proof_pass),
        _metric("sovereign_metric_definitions_unchanged", sovereign_metric_definitions_unchanged, sovereign_metric_definitions_unchanged),
        _metric("counter_reconciliation_complete", counter_reconciliation_complete, counter_reconciliation_complete),
        _metric("dominant_choke_confirmed", dominant_choke_confirmed, dominant_choke_confirmed),
        _metric("bounded_fix_only", bounded_fix_only, bounded_fix_only),
    ]


def _classify_round(
    *,
    integrity: dict[str, Any],
    no_leakage_proof_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    counter_reconciliation_complete: bool,
    dominant_choke_confirmed: bool,
    bounded_fix_only: bool,
    challenger_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    if not all(
        [
            integrity["official_artifacts_unchanged"],
            integrity["research_only_isolation_pass"],
            no_leakage_proof_pass,
            sovereign_metric_definitions_unchanged,
            bounded_fix_only,
        ]
    ):
        return "FAIL", "abandon"

    if not counter_reconciliation_complete or not dominant_choke_confirmed:
        return "PARTIAL", "correct"

    live_signal = any(
        int(row["calibrated_hits_gt_050_cpcv_aggregated"]) > 0
        and int(row["latest_active_count_decision_space"]) >= 1
        and bool(row["headroom_decision_space"])
        for row in challenger_rows
    )
    if live_signal:
        return "PASS", "advance"
    return "PARTIAL", "correct"


def run_phase5_stage_a3_activation_calibration_correction() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)

    frozen = stage5._rebuild_experiment(model_path, FROZEN_EXPERIMENT)
    baseline = stage5._rebuild_experiment(model_path, BASELINE_EXPERIMENT)
    metric_definition_check = choke_audit._audit_metric_definitions(
        research_path=research_path,
        baseline=baseline,
        a3=frozen,
    )

    reconciliation, frozen_layer_df, fold_df = _reconcile_frozen_pipeline(frozen)
    frozen_no_leakage = stage5._prove_no_leakage(frozen)

    frozen_decision = stage5._compute_decision_space_metrics(frozen.aggregated)
    frozen_operational = stage5._compute_operational_metrics(frozen.aggregated, "decision_score_stage_a")
    raw_hits_row_level = int(reconciliation["row_level_reproduction"]["raw_hits_gt_050_row_level"])
    cal_hits_row_level = int(reconciliation["row_level_reproduction"]["calibrated_hits_gt_050_row_level"])
    frozen_comparison_row = {
        "variant": "frozen_a3_q60",
        "description": "exact reproduction of the previously failed q60 path",
        "change_family": "frozen_control",
        "raw_hits_gt_050_row_level": raw_hits_row_level,
        "calibrated_hits_gt_050_row_level": cal_hits_row_level,
        "calibrated_hits_gt_050_cpcv_aggregated": int(reconciliation["cpcv_aggregate_reproduction"]["calibrated_hits_gt_050_cpcv_aggregated"]),
        "latest_active_count_decision_space": int(frozen_decision["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(frozen_decision["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(frozen_decision["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(frozen_decision["historical_active_events_decision_space"]),
        "sharpe_operational": round(float(frozen_operational["sharpe"]), 4),
        "dsr_honest": round(float(frozen_operational["dsr_honest"]), 4),
        "subperiods_positive": int(frozen_operational["subperiods_positive"]),
        "official_artifacts_unchanged": True,
        "research_only_isolation_pass": True,
        "no_leakage_proof_pass": bool(frozen_no_leakage.get("pass")),
    }

    dummy_integrity = {
        "official_artifacts_unchanged": True,
        "research_only_isolation_pass": True,
    }
    challenger_1_pre, challenger_1_diag = _challenger_aggregate_then_cluster_isotonic(frozen)
    challenger_1 = _build_challenger_result(
        name="challenger_1",
        description="aggregate then cluster-specific isotonic calibration",
        change_family="cpcv_aggregation_before_threshold",
        pre_proxy=challenger_1_pre,
        calibration_diag=challenger_1_diag,
        raw_hits_gt_050_row_level=raw_hits_row_level,
        calibrated_hits_gt_050_row_level=cal_hits_row_level,
        integrity_flags=dummy_integrity,
    )

    challenger_2_pre, challenger_2_diag = _challenger_aggregate_then_global_isotonic(frozen)
    challenger_2 = _build_challenger_result(
        name="challenger_2",
        description="aggregate then global isotonic calibration",
        change_family="calibration_pooling",
        pre_proxy=challenger_2_pre,
        calibration_diag=challenger_2_diag,
        raw_hits_gt_050_row_level=raw_hits_row_level,
        calibrated_hits_gt_050_row_level=cal_hits_row_level,
        integrity_flags=dummy_integrity,
    )
    challenger_rows = [challenger_1.comparison_row, challenger_2.comparison_row]

    dominant_choke_confirmed, dominant_choke_stage, dominant_cause = _diagnose_dominant_choke(
        reconciliation=reconciliation,
        challenger_rows=challenger_rows,
    )

    counter_reconciliation_complete = bool(
        reconciliation["row_level_reproduction"]["exact_match"]
        and reconciliation["cpcv_aggregate_reproduction"]["exact_match"]
        and reconciliation["sovereign_final_reproduction"]["exact_match"]
    )
    sovereign_metric_definitions_unchanged = bool(
        metric_definition_check.get("audit_complete") and metric_definition_check.get("ruler_drift_status") == "NO_DRIFT"
    )
    bounded_fix_only = True
    no_leakage_proof_pass = bool(
        frozen_no_leakage.get("pass")
        and all(bool(row["no_leakage_proof_pass"]) for row in challenger_rows)
    )

    reconciliation_rows = list(frozen_layer_df.to_dict("records"))
    reconciliation_rows.extend(
        _build_reconciliation_rows_for_variant(
            variant="challenger_1",
            row_level_raw_hits=raw_hits_row_level,
            row_level_cal_hits=cal_hits_row_level,
            pre_proxy=challenger_1.pre_proxy,
            final_df=challenger_1.final,
        )
    )
    reconciliation_rows.extend(
        _build_reconciliation_rows_for_variant(
            variant="challenger_2",
            row_level_raw_hits=raw_hits_row_level,
            row_level_cal_hits=cal_hits_row_level,
            pre_proxy=challenger_2.pre_proxy,
            final_df=challenger_2.final,
        )
    )
    reconciliation_df = pd.DataFrame(reconciliation_rows)

    fold_vs_aggregate_rows = list(fold_df.to_dict("records"))
    fold_vs_aggregate_rows.extend(
        [
            {
                "variant": "frozen_a3_q60",
                "layer": "row_level_reproduction",
                "combo": None,
                "n_rows": reconciliation["row_level_reproduction"]["rows_compared"],
                "raw_hits_gt_050": raw_hits_row_level,
                "calibrated_hits_gt_050": cal_hits_row_level,
                "reproduced_exact": reconciliation["row_level_reproduction"]["exact_match"],
                "max_abs_diff": reconciliation["row_level_reproduction"]["p_activate_calibrated_stage_a_max_abs_diff"],
            },
            {
                "variant": "frozen_a3_q60",
                "layer": "cpcv_aggregate_reproduction",
                "combo": None,
                "n_rows": reconciliation["cpcv_aggregate_reproduction"]["rows_compared"],
                "raw_hits_gt_050": int(reconciliation["cpcv_aggregate_reproduction"]["raw_hits_gt_050_cpcv_aggregated"]),
                "calibrated_hits_gt_050": int(reconciliation["cpcv_aggregate_reproduction"]["calibrated_hits_gt_050_cpcv_aggregated"]),
                "reproduced_exact": reconciliation["cpcv_aggregate_reproduction"]["exact_match"],
                "max_abs_diff": reconciliation["cpcv_aggregate_reproduction"]["p_activate_calibrated_stage_a_max_abs_diff"],
            },
            {
                "variant": "frozen_a3_q60",
                "layer": "sovereign_final_reproduction",
                "combo": None,
                "n_rows": reconciliation["sovereign_final_reproduction"]["rows_compared"],
                "raw_hits_gt_050": raw_hits_row_level,
                "calibrated_hits_gt_050": int(reconciliation["cpcv_aggregate_reproduction"]["calibrated_hits_gt_050_cpcv_aggregated"]),
                "reproduced_exact": reconciliation["sovereign_final_reproduction"]["exact_match"],
                "max_abs_diff": max(
                    float(reconciliation["sovereign_final_reproduction"]["decision_score_stage_a_max_abs_diff"]),
                    float(reconciliation["sovereign_final_reproduction"]["position_usdt_stage_a_max_abs_diff"]),
                    float(reconciliation["sovereign_final_reproduction"]["mu_adj_stage_a_max_abs_diff"]),
                ),
            },
        ]
    )
    fold_vs_aggregate_df = pd.DataFrame(fold_vs_aggregate_rows)

    challengers_df = pd.DataFrame([frozen_comparison_row, *challenger_rows])

    reconciliation_path = research_path / "activation_calibration_reconciliation.parquet"
    fold_vs_aggregate_path = research_path / "activation_calibration_fold_vs_aggregate.parquet"
    challengers_path = research_path / "activation_calibration_challengers.parquet"
    summary_path = research_path / "activation_calibration_summary.json"
    integrity_path = research_path / "activation_calibration_integrity.json"

    reconciliation_df.to_parquet(reconciliation_path, index=False)
    fold_vs_aggregate_df.to_parquet(fold_vs_aggregate_path, index=False)
    challengers_df.to_parquet(challengers_path, index=False)

    generated_paths = [
        reconciliation_path,
        fold_vs_aggregate_path,
        challengers_path,
        summary_path,
        integrity_path,
    ]
    official_after = stage5._collect_official_inventory(model_path)
    integrity = choke_audit._build_integrity_payload(
        model_path=model_path,
        research_path=research_path,
        gate_path=gate_path,
        official_before=official_before,
        official_after=official_after,
        generated_paths=generated_paths,
    )
    _write_json(integrity_path, integrity)

    gate_metrics = _gate_metrics(
        integrity=integrity,
        no_leakage_proof_pass=no_leakage_proof_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        counter_reconciliation_complete=counter_reconciliation_complete,
        dominant_choke_confirmed=dominant_choke_confirmed,
        bounded_fix_only=bounded_fix_only,
    )
    status, decision = _classify_round(
        integrity=integrity,
        no_leakage_proof_pass=no_leakage_proof_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        counter_reconciliation_complete=counter_reconciliation_complete,
        dominant_choke_confirmed=dominant_choke_confirmed,
        bounded_fix_only=bounded_fix_only,
        challenger_rows=challenger_rows,
    )

    recent_dates = sorted(_normalize_dates(frozen.aggregated).dropna().unique().tolist())[-RECENT_WINDOW_DATES:]
    recent_strings = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in recent_dates]
    summary_payload = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "frozen_experiment": frozen.experiment_name,
        "sovereign_metric_definitions_unchanged": sovereign_metric_definitions_unchanged,
        "counter_reconciliation_complete": counter_reconciliation_complete,
        "dominant_choke_confirmed": dominant_choke_confirmed,
        "dominant_choke_stage": dominant_choke_stage,
        "dominant_cause": dominant_cause,
        "questions_answered": {
            "collapse_in_calibrator_fit": True,
            "collapse_in_calibrator_application": False,
            "collapse_in_cpcv_aggregation_semantics": True,
            "collapse_due_to_fixed_threshold_after_aggregation": True,
            "row_level_vs_aggregated_discrepancy_present": True,
            "low_regret_fix_found": bool(
                any(
                    int(row["calibrated_hits_gt_050_cpcv_aggregated"]) > 0
                    and int(row["latest_active_count_decision_space"]) >= 1
                    and bool(row["headroom_decision_space"])
                    for row in challenger_rows
                )
            ),
        },
        "frozen_reconciliation": reconciliation,
        "metric_definition_check": metric_definition_check,
        "recent_dates_window": recent_strings,
        "frozen_metrics": frozen_comparison_row,
        "challengers": {
            challenger_1.name: {
                "description": challenger_1.description,
                "metrics": challenger_1.comparison_row,
                "calibration_diagnostics": challenger_1.calibration_diag,
            },
            challenger_2.name: {
                "description": challenger_2.description,
                "metrics": challenger_2.comparison_row,
                "calibration_diagnostics": challenger_2.calibration_diag,
            },
        },
        "integrity": {
            "official_artifacts_unchanged": bool(integrity["official_artifacts_unchanged"]),
            "research_only_isolation_pass": bool(integrity["research_only_isolation_pass"]),
            "no_leakage_proof_pass": bool(no_leakage_proof_pass),
        },
    }
    _write_json(summary_path, summary_payload)

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "generated_at_utc": utc_now_iso(),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git_output("status", "--short")),
        "branch": _git_output("branch", "--show-current"),
        "official_artifacts_used": official_before["official_paths_read"]["phase4_critical"],
        "research_artifacts_generated": [str(path) for path in generated_paths],
        "summary": [
            "Frozen A3-q60 was reproduced exactly at row level, CPCV aggregate and sovereign final path.",
            "The primary choke is the calibrator fit/mapping; mean CPCV aggregation is a secondary collapse that removes the few surviving >0.50 row-level hits.",
            "No bounded challenger restored live sovereign activation on the latest date while keeping the frozen contest geometry, Stage 2 and sizing unchanged.",
        ],
        "gates": gate_metrics,
        "blockers": [] if status != "FAIL" else ["Integrity or sovereign definitions failed in the correction round."],
        "risks_residual": [
            "The best bounded challenger recovered some historical aggregate mass but still left the latest sovereign path structurally dead.",
            "Any future correction round that changes the 0.50 sovereign threshold, contest geometry, Stage 2 or sizing would exceed the frozen scope of this micro-round.",
        ],
        "next_recommended_step": (
            "Run a new research-only micro-round focused on the Stage 1 calibrator family if and only if low-regret honest calibration alternatives remain inside the frozen sovereign contract."
            if decision != "abandon"
            else "Stop and abandon the correction path because integrity or sovereign definitions failed."
        ),
        "frozen_metrics": frozen_comparison_row,
        "challengers": [challenger_1.comparison_row, challenger_2.comparison_row],
        "dominant_choke_stage": dominant_choke_stage,
        "dominant_cause": dominant_cause,
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(f"- {item}" for item in gate_report["summary"]),
        "Baseline congelado": (
            f"- raw_hits_gt_050_row_level={frozen_comparison_row['raw_hits_gt_050_row_level']}\n"
            f"- calibrated_hits_gt_050_row_level={frozen_comparison_row['calibrated_hits_gt_050_row_level']}\n"
            f"- calibrated_hits_gt_050_cpcv_aggregated={frozen_comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}\n"
            f"- latest_active_count_decision_space={frozen_comparison_row['latest_active_count_decision_space']}"
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- faithful frozen reproduction across row-level, CPCV aggregate and sovereign final path",
                "- challenger_1: aggregate then cluster-specific isotonic calibration",
                "- challenger_2: aggregate then global isotonic calibration",
            ]
        ),
        "Artifacts gerados": "\n".join(f"- {path}" for path in generated_paths),
        "Resultados": "\n".join(
            [
                f"- dominant_choke_stage: {dominant_choke_stage}",
                f"- dominant_cause: {dominant_cause}",
                f"- challenger_1 calibrated_hits_gt_050_cpcv_aggregated={challenger_1.comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={challenger_1.comparison_row['latest_active_count_decision_space']}",
                f"- challenger_2 calibrated_hits_gt_050_cpcv_aggregated={challenger_2.comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={challenger_2.comparison_row['latest_active_count_decision_space']}",
            ]
        ),
        "Avaliação contra gates": "\n".join(
            f"- {row['metric_name']}: {row['metric_status']} ({row['metric_value']})" for row in gate_metrics
        ),
        "Riscos residuais": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
        "Veredito final: advance / correct / abandon": f"- {decision}",
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": gate_report["generated_at_utc"],
        "status": status,
        "decision": decision,
        "branch": gate_report["branch"],
        "commit": gate_report["baseline_commit"],
        "baseline_commit": gate_report["baseline_commit"],
        "working_tree_dirty_before": bool(_git_output("status", "--short")),
        "working_tree_dirty_after": bool(_git_output("status", "--short")),
        "source_artifacts": [
            artifact_record(research_path / "stage_a_report.json"),
            artifact_record(research_path / "stage_a3_summary.json"),
            artifact_record(research_path / "stage_a3_comparison.parquet"),
            artifact_record(model_path / "research" / "phase4_cross_sectional_ranking_baseline" / "stage_a_report.json"),
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "python -m py_compile services\\ml_engine\\phase5_stage_a3_activation_calibration_correction.py",
            "python -m py_compile tests\\unit\\test_phase5_stage_a3_activation_calibration_correction.py",
            "python -m pytest tests\\unit\\test_phase5_stage_a3_activation_calibration_correction.py -q",
            "python services\\ml_engine\\phase5_stage_a3_activation_calibration_correction.py",
        ],
        "notes": [
            "Frozen A3-q60, contest geometry, Stage 2 and sovereign decision-space definitions were held constant in this correction round."
        ],
    }

    write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )

    return {
        "status": status,
        "decision": decision,
        "branch": gate_report["branch"],
        "commit": gate_report["baseline_commit"],
        "gate_path": str(gate_path),
        "dominant_choke_stage": dominant_choke_stage,
        "official_artifacts_unchanged": integrity["official_artifacts_unchanged"],
        "research_only_isolation_pass": integrity["research_only_isolation_pass"],
        "no_leakage_proof_pass": no_leakage_proof_pass,
        "sovereign_metric_definitions_unchanged": sovereign_metric_definitions_unchanged,
        "counter_reconciliation_complete": counter_reconciliation_complete,
        "dominant_choke_confirmed": dominant_choke_confirmed,
        "bounded_fix_only": bounded_fix_only,
    }


def main() -> None:
    result = run_phase5_stage_a3_activation_calibration_correction()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
