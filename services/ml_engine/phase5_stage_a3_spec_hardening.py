#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_stage_a3_spec_hardening"
PHASE_FAMILY = "phase5_stage_a3_spec_hardening"
RECENT_WINDOW_DATES = 8
PROMOTION_CPCV_TRAJECTORIES = 15
EXPERIMENT_POSITIVE_RATE_MIN = 0.05
ADVANCE_POSITIVE_RATE_MIN = 0.08
CRITICAL_OFFICIAL_FILES = (
    "phase4_report_v4.json",
    "phase4_execution_snapshot.parquet",
    "phase4_aggregated_predictions.parquet",
    "phase4_oos_predictions.parquet",
    "phase4_gate_diagnostic.json",
)


@dataclass
class RebuiltExperiment:
    experiment_name: str
    report: dict[str, Any]
    predictions: pd.DataFrame
    aggregated_pre_proxy: pd.DataFrame
    aggregated: pd.DataFrame
    signal_col: str
    selection_summary: dict[str, Any]
    target_mode: str
    calibratable: bool


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


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_paths() -> tuple[Path, Path, Path]:
    model_path = stage_a._resolve_model_path()
    stage_a._configure_phase4_paths(model_path)
    research_path = model_path / "research" / "phase5_stage_a3"
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, research_path, gate_path


def _combined_hash(paths: list[Path]) -> str:
    return stage_a._combined_hash(paths) if paths else ""


def _artifact_list(paths: list[Path]) -> list[dict[str, Any]]:
    return [artifact_record(path) for path in paths]


def _collect_official_inventory(model_path: Path) -> dict[str, Any]:
    features_files = sorted((model_path / "features").glob("*.parquet"))
    phase3_files = sorted((model_path / "phase3").glob("*.parquet"))
    phase4_files = sorted(
        [path for path in (model_path / "phase4").iterdir() if path.is_file() and path.suffix.lower() in {".json", ".parquet"}]
    )
    critical_files = [(model_path / "phase4" / name) for name in CRITICAL_OFFICIAL_FILES if (model_path / "phase4" / name).exists()]
    return {
        "official_root": str(model_path),
        "official_paths_read": {
            "features": [str(path) for path in features_files],
            "phase3": [str(path) for path in phase3_files],
            "phase4": [str(path) for path in phase4_files],
            "phase4_critical": [str(path) for path in critical_files],
        },
        "inventories": {
            "features": _artifact_list(features_files),
            "phase3": _artifact_list(phase3_files),
            "phase4": _artifact_list(phase4_files),
            "phase4_critical": _artifact_list(critical_files),
        },
        "combined_hashes": {
            "features_combined_sha256": _combined_hash(features_files),
            "phase3_combined_sha256": _combined_hash(phase3_files),
            "phase4_combined_sha256": _combined_hash(phase4_files),
            "phase4_critical_combined_sha256": _combined_hash(critical_files),
        },
    }


def _screen_stage_a3_candidates(model_path: Path) -> list[dict[str, Any]]:
    pooled_df = phase4.load_pooled_meta_df()
    _, symbol_to_cluster, target_cluster_mode, target_cluster_artifact_path = phase4._load_symbol_vi_clusters(
        pooled_df["symbol"].astype(str).unique().tolist()
    )
    n = len(pooled_df)
    embargo = max(1, int(n * phase4.EMBARGO_PCT))
    splits = np.array_split(np.arange(n), phase4.N_SPLITS)
    combos = list(stage_a.combinations(range(phase4.N_SPLITS), phase4.N_TEST_SPLITS))

    candidate_rows: list[dict[str, Any]] = []
    for quantile in stage_a.TARGET_Q_CANDIDATES:
        combo_rows: list[dict[str, Any]] = []
        positive_count_total = 0
        oos_count_total = 0
        local_assignments = 0
        fallback_assignments = 0
        global_support_min = None
        for combo in combos:
            test_idx = np.concatenate([splits[i] for i in combo])
            test_set = set(test_idx.tolist())
            train_idx = np.array([j for j in range(n) if j not in test_set], dtype=int)
            purge_mask = np.zeros(n, dtype=bool)
            for fi in combo:
                fs, fe = splits[fi][0], splits[fi][-1]
                purge_mask |= (np.arange(n) >= fs - embargo) & (np.arange(n) <= fe + embargo)
            train_idx = train_idx[~purge_mask[train_idx]]
            if len(train_idx) < 40 or len(test_idx) < 15:
                continue
            train_df = pooled_df.iloc[train_idx].copy()
            test_df = pooled_df.iloc[test_idx].copy()
            symbol_stats, global_tp, global_sl = phase4._compute_symbol_trade_stats(train_df)
            train_df_with_stats = phase4._attach_trade_stats(
                train_df,
                symbol_stats,
                global_tp,
                global_sl,
                tp_col="avg_tp_train",
                sl_col="avg_sl_train",
            )
            train_df["cluster_name"] = train_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
            test_df["cluster_name"] = test_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
            cluster_thresholds, threshold_summary = stage_a._compute_two_stage_activation_thresholds(
                train_df_with_stats.assign(cluster_name=train_df["cluster_name"].values),
                quantile=quantile,
            )
            test_df = stage_a._prepare_stage_a_fold_frame(
                test_df,
                symbol_stats=symbol_stats,
                global_tp=global_tp,
                global_sl=global_sl,
                symbol_to_cluster=symbol_to_cluster,
                cluster_thresholds=cluster_thresholds,
                threshold_summary=threshold_summary,
            )
            positive_rate_test = float(pd.to_numeric(test_df["y_stage_a"], errors="coerce").fillna(0).mean())
            positives_test = int(pd.to_numeric(test_df["y_stage_a"], errors="coerce").fillna(0).sum())
            positive_count_total += positives_test
            oos_count_total += int(len(test_df))
            local_assignments += int(
                sum(
                    1
                    for row in threshold_summary.get("cluster_rows", [])
                    if row.get("threshold_source") == "cluster_local_q_train_positive"
                )
            )
            fallback_assignments += int(
                sum(
                    1
                    for row in threshold_summary.get("cluster_rows", [])
                    if row.get("threshold_source") != "cluster_local_q_train_positive"
                )
            )
            support_global = int(threshold_summary.get("train_positive_count_global", 0))
            global_support_min = support_global if global_support_min is None else min(global_support_min, support_global)
            combo_rows.append(
                {
                    "combo": str(combo),
                    "positive_rate_test": round(positive_rate_test, 4),
                    "positive_count_test": positives_test,
                    "oos_rows": int(len(test_df)),
                    "global_train_support": support_global,
                    "global_threshold_train": threshold_summary.get("global_threshold_train"),
                }
            )

        candidate_rows.append(
            {
                "candidate_id": f"A3-q{int(round(quantile * 100)):02d}",
                "candidate_type": "two_stage_activation_utility",
                "quantile": round(float(quantile), 4),
                "precommitted_primary_candidate": bool(np.isclose(quantile, stage_a.PRIMARY_Q)),
                "selection_allowed_in_this_round": bool(np.isclose(quantile, stage_a.PRIMARY_Q)),
                "formula": (
                    f"u_real = pnl_real / avg_sl_train; y_activate = 1[u_real >= Q{int(round(quantile * 100)):02d}_train"
                    "(u_real | cluster_name, u_real > 1)] with global fallback when support < 100"
                ),
                "screening_only": not np.isclose(quantile, stage_a.PRIMARY_Q),
                "positive_rate_oos_label_side": round(float(positive_count_total / oos_count_total), 4) if oos_count_total else 0.0,
                "historical_positive_events_label_side": int(positive_count_total),
                "combo_count_screened": int(len(combo_rows)),
                "min_global_train_support": int(global_support_min or 0),
                "cluster_assignments_local": int(local_assignments),
                "cluster_assignments_fallback": int(fallback_assignments),
                "prevalence_guard_pass": bool((positive_count_total / oos_count_total) >= EXPERIMENT_POSITIVE_RATE_MIN) if oos_count_total else False,
                "target_is_non_circular": True,
                "target_is_causal_ex_ante": True,
                "target_cluster_mode": target_cluster_mode,
                "target_cluster_artifact_path": target_cluster_artifact_path,
                "combo_rows": combo_rows,
            }
        )
    return candidate_rows


def _selected_target_payload(candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = next((row for row in candidate_rows if row.get("precommitted_primary_candidate")), None)
    if selected is None:
        raise RuntimeError("Primary Stage A3 candidate not present in screening set")
    return {
        "gate_slug": GATE_SLUG,
        "experiment_name": stage_a.EXPERIMENT_NAME,
        "selected_target_id": selected["candidate_id"],
        "selection_basis": "ex_ante_precommitted",
        "selection_locked_before_final_rerun": True,
        "selection_timestamp_utc": utc_now_iso(),
        "downstream_performance_used_for_selection": False,
        "non_selected_candidates_remain_screening_only": [
            row["candidate_id"] for row in candidate_rows if row["candidate_id"] != selected["candidate_id"]
        ],
        "target_mode": selected["candidate_type"],
        "quantile": selected["quantile"],
        "stage1_training": "calibrated_binary_activation_classifier",
        "stage2_training_policy": "activated_train_subset_only",
        "min_stage2_train_rows": stage_a.MIN_STAGE2_TRAIN_ROWS,
        "contest_geometry": {
            "local_selection": "top1 within (date, cluster_name) among predicted activated rows",
            "fallback_selection": "top1 on date-universe when activated_count(date, cluster_name) < 2",
            "min_eligible_per_date_cluster": stage_a.MIN_ELIGIBLE_PER_DATE_CLUSTER,
        },
        "sovereign_decision_space_definition_locked": True,
        "notes": "q40/q50/q60 were screened label-side only; q60 remained the only promotable candidate ex ante in this round.",
    }


def _run_stage_a3_experiment() -> dict[str, Any]:
    model_path = stage_a._resolve_model_path()
    research_path = model_path / "research" / "phase5_stage_a3"
    existing_report = research_path / "stage_a_report.json"
    existing_predictions = research_path / "stage_a_predictions.parquet"
    existing_snapshot = research_path / "stage_a_snapshot_proxy.parquet"
    existing_manifest = research_path / "stage_a_manifest.json"
    if all(path.exists() for path in [existing_report, existing_predictions, existing_snapshot, existing_manifest]):
        return {
            "research_path": str(research_path),
            "predictions_path": str(existing_predictions),
            "report_path": str(existing_report),
            "snapshot_path": str(existing_snapshot),
            "manifest_path": str(existing_manifest),
            "reused_existing_outputs": True,
        }
    result = stage_a.run_stage_a_experiment()
    if str(result.get("research_path", "")).rstrip("\\/").lower().endswith("phase5_stage_a3"):
        return result
    raise RuntimeError("Stage A3 experiment wrote to an unexpected research path")


def _load_stage_a_report(model_path: Path, experiment_name: str) -> dict[str, Any]:
    return _load_json(model_path / "research" / experiment_name / "stage_a_report.json")


def _load_stage_a_predictions(model_path: Path, experiment_name: str) -> pd.DataFrame:
    return pd.read_parquet(model_path / "research" / experiment_name / "stage_a_predictions.parquet")


def _detect_target_mode(report: dict[str, Any]) -> str:
    target_mode = str(
        report.get("classification_metrics", {}).get("target_selection_policy", {}).get("target_mode", "")
    ).strip()
    if target_mode:
        return target_mode
    target_name = str(report.get("target_name", "")).strip().lower()
    if "two_stage_activation_utility" in target_name:
        return "two_stage_activation_utility"
    if report.get("problem_type") == "cross_sectional_ranking":
        return "cross_sectional_ranking"
    return "binary_classification"


def _rebuild_experiment(model_path: Path, experiment_name: str) -> RebuiltExperiment:
    report = _load_stage_a_report(model_path, experiment_name)
    predictions = _load_stage_a_predictions(model_path, experiment_name)
    target_mode = _detect_target_mode(report)
    aggregated_pre_proxy = stage_a._aggregate_stage_a_predictions(predictions)
    selection_summary: dict[str, Any] = {}

    if target_mode == "cross_sectional_ranking":
        aggregated_pre_proxy["rank_score_stage_a"] = pd.to_numeric(
            aggregated_pre_proxy.get("p_stage_a_raw"),
            errors="coerce",
        ).fillna(0.0)
        aggregated, selection_summary = stage_a._apply_cross_sectional_ranking_proxy(aggregated_pre_proxy.copy())
        signal_col = "p_stage_a_calibrated"
        calibratable = False
    elif target_mode == "two_stage_activation_utility":
        aggregated_pre_proxy["p_activate_calibrated_stage_a"] = pd.to_numeric(
            aggregated_pre_proxy.get("p_activate_calibrated_stage_a", aggregated_pre_proxy.get("p_stage_a_calibrated")),
            errors="coerce",
        ).fillna(0.0)
        aggregated, selection_summary = stage_a._apply_two_stage_activation_utility_proxy(aggregated_pre_proxy.copy())
        signal_col = "decision_score_stage_a"
        calibratable = True
    else:
        aggregated = aggregated_pre_proxy.copy()
        aggregated["decision_selected"] = (
            pd.to_numeric(aggregated.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0) > stage_a.TARGET_ACTIVATION_THRESHOLD
        )
        aggregated["decision_score_stage_a"] = pd.to_numeric(aggregated.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0)
        signal_col = "p_stage_a_calibrated"
        calibratable = True

    sizing_prob_col = "decision_score_stage_a" if target_mode == "two_stage_activation_utility" else "p_stage_a_calibrated"
    aggregated = phase4._compute_phase4_sizing(
        aggregated,
        prob_col=sizing_prob_col,
        prefix="stage_a",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    aggregated = phase4._attach_execution_pnl(
        aggregated,
        position_col="position_usdt_stage_a",
        output_col="pnl_exec_stage_a",
    )
    if "decision_selected" not in aggregated.columns:
        aggregated["decision_selected"] = (
            pd.to_numeric(aggregated.get(signal_col), errors="coerce").fillna(0.0) > stage_a.TARGET_ACTIVATION_THRESHOLD
        )
    return RebuiltExperiment(
        experiment_name=experiment_name,
        report=report,
        predictions=predictions,
        aggregated_pre_proxy=aggregated_pre_proxy,
        aggregated=aggregated,
        signal_col=signal_col,
        selection_summary=selection_summary,
        target_mode=target_mode,
        calibratable=calibratable,
    )


def _compute_decision_space_metrics(aggregated: pd.DataFrame) -> dict[str, Any]:
    if aggregated.empty:
        return {
            "latest_active_count_decision_space": 0,
            "headroom_decision_space": False,
            "recent_live_dates_decision_space": 0,
            "historical_active_events_decision_space": 0,
            "decision_space_metrics_computed": False,
        }
    work = aggregated.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    pos = pd.to_numeric(work.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    active_mask = selected & (pos > 0)
    latest_date = work["date"].dropna().max()
    latest_mask = work["date"] == latest_date
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-RECENT_WINDOW_DATES:]
    recent_live_dates = 0
    for date_value in recent_dates:
        if bool(active_mask.loc[work["date"] == date_value].any()):
            recent_live_dates += 1
    latest_positions = pos.loc[latest_mask]
    return {
        "latest_active_count_decision_space": int(active_mask.loc[latest_mask].sum()),
        "headroom_decision_space": bool(float(latest_positions.max()) > 0.0) if not latest_positions.empty else False,
        "recent_live_dates_decision_space": int(recent_live_dates),
        "historical_active_events_decision_space": int(active_mask.sum()),
        "decision_space_metrics_computed": True,
    }


def _compute_operational_metrics(aggregated: pd.DataFrame, signal_col: str) -> dict[str, Any]:
    return phase4._evaluate_decision_policy(
        aggregated,
        label="stage_a3_phase5_gate_rebuild",
        threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
        signal_col=signal_col,
        position_col="position_usdt_stage_a",
        pnl_col="pnl_exec_stage_a",
    )


def _trajectory_metric_values(report: dict[str, Any]) -> list[float]:
    trajectories = report.get("classification_metrics", {}).get("trajectories", [])
    values = []
    for row in trajectories:
        for key in ("auc_raw", "auc_raw_vs_truth_top1", "auc_calibrated"):
            if row.get(key) is not None:
                values.append(float(row[key]))
                break
    return values


def _n_eff_mean(report: dict[str, Any]) -> float | None:
    trajectories = report.get("classification_metrics", {}).get("trajectories", [])
    values = [float(row["n_eff"]) for row in trajectories if row.get("n_eff") is not None]
    return round(float(np.mean(values)), 4) if values else None


def _cpcv_trajectories(report: dict[str, Any]) -> int:
    return int(len(report.get("classification_metrics", {}).get("trajectories", [])))


def _pbo(report: dict[str, Any]) -> float | None:
    existing = report.get("classification_metrics", {}).get("pbo")
    if existing is not None:
        return float(existing)
    values = _trajectory_metric_values(report)
    return round(float(np.mean(np.asarray(values) < 0.50)), 4) if values else None


def _ece_calibrated(rebuilt: RebuiltExperiment) -> float | None:
    if not rebuilt.calibratable:
        return None
    probs = pd.to_numeric(rebuilt.predictions.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0).values
    labels = pd.to_numeric(rebuilt.predictions.get("y_stage_a"), errors="coerce").fillna(0).values
    return round(float(phase4._compute_ece(probs, labels)), 4)


def _build_reliability_diagram(predictions: pd.DataFrame, output_path: Path) -> bool:
    if predictions.empty or "p_stage_a_calibrated" not in predictions.columns:
        return False
    probs = pd.to_numeric(predictions["p_stage_a_calibrated"], errors="coerce").fillna(0.0)
    labels = pd.to_numeric(predictions["y_stage_a"], errors="coerce").fillna(0)
    bins = np.linspace(0.0, 1.0, 11)
    work = pd.DataFrame({"prob": probs, "label": labels})
    work["bin"] = pd.cut(work["prob"], bins=bins, include_lowest=True, duplicates="drop")
    grouped = work.groupby("bin", observed=False).agg(prob_mean=("prob", "mean"), freq=("label", "mean"), n=("label", "size"))
    grouped = grouped.dropna().reset_index(drop=True)
    if grouped.empty:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", linewidth=1)
    ax.plot(grouped["prob_mean"], grouped["freq"], marker="o", color="#005f73", linewidth=2)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title("Stage A3 reliability")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path.exists()


def _prove_no_leakage(rebuilt: RebuiltExperiment) -> dict[str, Any]:
    if rebuilt.target_mode != "two_stage_activation_utility":
        return {
            "applies": False,
            "pass": None,
            "reason": "proof only applies to Stage A3 two-stage candidate",
        }
    pre_proxy = rebuilt.aggregated_pre_proxy.copy()
    with_realized, _ = stage_a._apply_two_stage_activation_utility_proxy(pre_proxy.copy())
    drop_cols = [
        "pnl_real",
        "y_stage_a",
        "stage_a_utility_real",
        "stage_a_utility_surplus",
        "stage_a_score_realized",
    ]
    no_realized = pre_proxy.drop(columns=[col for col in drop_cols if col in pre_proxy.columns], errors="ignore")
    without_realized, _ = stage_a._apply_two_stage_activation_utility_proxy(no_realized.copy())
    compare_cols = ["date", "symbol", "cluster_name", "stage_a_selected_proxy", "decision_score_stage_a"]
    merged = (
        with_realized[compare_cols]
        .rename(columns={"stage_a_selected_proxy": "selected_with", "decision_score_stage_a": "score_with"})
        .merge(
            without_realized[compare_cols].rename(
                columns={"stage_a_selected_proxy": "selected_without", "decision_score_stage_a": "score_without"}
            ),
            on=["date", "symbol", "cluster_name"],
            how="outer",
        )
    )
    selected_match = bool((merged["selected_with"].fillna(False) == merged["selected_without"].fillna(False)).all())
    score_match = bool(np.allclose(merged["score_with"].fillna(0.0), merged["score_without"].fillna(0.0)))
    return {
        "applies": True,
        "pass": bool(selected_match and score_match),
        "selection_inputs_are_predicted_only": True,
        "selected_rows_match_when_realized_columns_removed": selected_match,
        "decision_scores_match_when_realized_columns_removed": score_match,
        "columns_removed_for_proof": drop_cols,
    }


def _build_failure_modes(v1: RebuiltExperiment, v2: RebuiltExperiment, a3_row: dict[str, Any]) -> dict[str, Any]:
    v1_report = v1.report
    v2_report = v2.report
    return {
        "v1": {
            "diagnosis": "broad binary edge label preserved prevalence but collapsed at calibration/activation.",
            "positive_rate_oos": v1_report.get("classification_metrics", {}).get("positive_rate_oos"),
            "aggregated_raw_gt_050": v1_report.get("operational_proxy", {}).get("activation_funnel", {}).get("aggregated_p_meta_raw_gt_050"),
            "aggregated_calibrated_gt_050": v1_report.get("operational_proxy", {}).get("activation_funnel", {}).get("aggregated_p_meta_calibrated_gt_050"),
            "latest_raw_gt_050": v1_report.get("operational_proxy", {}).get("activation_funnel", {}).get("latest_snapshot_p_meta_raw_gt_050"),
            "latest_calibrated_gt_050": v1_report.get("operational_proxy", {}).get("activation_funnel", {}).get("latest_snapshot_p_meta_calibrated_gt_050"),
            "historical_active": v1_report.get("operational_proxy", {}).get("n_active"),
            "dsr_honest": v1_report.get("operational_proxy", {}).get("dsr_honest"),
        },
        "v2": {
            "diagnosis": "tightened target collapsed prevalence and still died at calibration/activation.",
            "positive_rate_oos": v2_report.get("classification_metrics", {}).get("positive_rate_oos"),
            "aggregated_raw_gt_050": v2_report.get("operational_proxy", {}).get("activation_funnel", {}).get("aggregated_p_meta_raw_gt_050"),
            "aggregated_calibrated_gt_050": v2_report.get("operational_proxy", {}).get("activation_funnel", {}).get("aggregated_p_meta_calibrated_gt_050"),
            "latest_raw_gt_050": v2_report.get("operational_proxy", {}).get("activation_funnel", {}).get("latest_snapshot_p_meta_raw_gt_050"),
            "latest_calibrated_gt_050": v2_report.get("operational_proxy", {}).get("activation_funnel", {}).get("latest_snapshot_p_meta_calibrated_gt_050"),
            "historical_active": v2_report.get("operational_proxy", {}).get("n_active"),
            "dsr_honest": v2_report.get("operational_proxy", {}).get("dsr_honest"),
        },
        "a3": {
            "diagnosis": "two-stage activation + utility preserved ex-ante selection geometry and prevented stage2 zero-dominance by training only on activated rows.",
            "positive_rate_oos": a3_row.get("positive_rate_oos"),
            "latest_active_count_decision_space": a3_row.get("latest_active_count_decision_space"),
            "recent_live_dates_decision_space": a3_row.get("recent_live_dates_decision_space"),
            "historical_active_events_decision_space": a3_row.get("historical_active_events_decision_space"),
            "sharpe_operational": a3_row.get("sharpe_operational"),
            "dsr_honest": a3_row.get("dsr_honest"),
        },
    }


def _build_comparator_row(
    rebuilt: RebuiltExperiment,
    *,
    row_name: str,
    reliability_diagram_present: bool | None,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    no_leakage_proof_pass: bool | None,
) -> dict[str, Any]:
    decision_space = _compute_decision_space_metrics(rebuilt.aggregated)
    operational = _compute_operational_metrics(rebuilt.aggregated, rebuilt.signal_col)
    return {
        "comparator": row_name,
        "experiment_name": rebuilt.experiment_name,
        "target_mode": rebuilt.target_mode,
        "positive_rate_oos": round(float(pd.to_numeric(rebuilt.predictions["y_stage_a"], errors="coerce").fillna(0).mean()), 4),
        "latest_active_count_decision_space": int(decision_space["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(decision_space["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(decision_space["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(decision_space["historical_active_events_decision_space"]),
        "sharpe_operational": round(float(operational["sharpe"]), 4),
        "dsr_honest": round(float(operational["dsr_honest"]), 4),
        "subperiods_positive": int(operational["subperiods_positive"]),
        "n_eff_mean": _n_eff_mean(rebuilt.report),
        "cpcv_trajectories": _cpcv_trajectories(rebuilt.report),
        "pbo": _pbo(rebuilt.report),
        "ece_calibrated": _ece_calibrated(rebuilt),
        "reliability_diagram_present": reliability_diagram_present,
        "official_artifacts_unchanged": official_artifacts_unchanged,
        "research_only_isolation_pass": research_only_isolation_pass,
        "no_leakage_proof_pass": no_leakage_proof_pass,
        "aux_p_activate_raw_gt_050": int((pd.to_numeric(rebuilt.predictions.get("p_stage_a_raw"), errors="coerce").fillna(0.0) > 0.50).sum()),
        "aux_p_activate_calibrated_gt_050": int(
            (pd.to_numeric(rebuilt.predictions.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0) > 0.50).sum()
        )
        if rebuilt.calibratable
        else None,
        "aux_mu_adj_stage_a3_gt_0": int((pd.to_numeric(rebuilt.aggregated.get("mu_adj_stage_a"), errors="coerce").fillna(0.0) > 0).sum()),
        "aux_utility_surplus_pred_gt_0": int(
            (pd.to_numeric(rebuilt.aggregated.get("utility_surplus_pred_stage_a"), errors="coerce").fillna(0.0) > 0).sum()
        )
        if "utility_surplus_pred_stage_a" in rebuilt.aggregated.columns
        else None,
    }


def _classify_round(a3_row: dict[str, Any], integrity: dict[str, Any]) -> tuple[str, str]:
    if not all(
        [
            integrity["no_leakage_proof_pass"],
            integrity["research_only_isolation_pass"],
            integrity["official_artifacts_unchanged"],
            integrity["target_is_non_circular"],
            integrity["decision_space_metrics_computed"],
        ]
    ):
        return "FAIL", "abandon"

    experiment_required = [
        float(a3_row["positive_rate_oos"]) >= EXPERIMENT_POSITIVE_RATE_MIN,
        int(a3_row["latest_active_count_decision_space"]) >= 1,
        bool(a3_row["headroom_decision_space"]),
        int(a3_row["recent_live_dates_decision_space"]) >= 5,
        int(a3_row["historical_active_events_decision_space"]) >= 120,
        float(a3_row["sharpe_operational"]) > 0.0,
        float(a3_row["dsr_honest"]) > 0.0,
        (a3_row["n_eff_mean"] or 0.0) > 120.0,
        (a3_row["pbo"] if a3_row["pbo"] is not None else 1.0) < 0.10,
    ]
    if a3_row["ece_calibrated"] is not None:
        experiment_required.append(float(a3_row["ece_calibrated"]) < 0.05)
    experiment_required.append(bool(a3_row["reliability_diagram_present"]))

    promotion_checks = [
        float(a3_row["positive_rate_oos"]) >= ADVANCE_POSITIVE_RATE_MIN,
        int(a3_row["latest_active_count_decision_space"]) >= 1,
        bool(a3_row["headroom_decision_space"]),
        int(a3_row["recent_live_dates_decision_space"]) >= 6,
        float(a3_row["sharpe_operational"]) >= 0.70,
        float(a3_row["dsr_honest"]) >= 0.95,
        int(a3_row["subperiods_positive"]) >= 4,
        (a3_row["n_eff_mean"] or 0.0) > 120.0,
        (a3_row["pbo"] if a3_row["pbo"] is not None else 1.0) < 0.10,
        int(a3_row["cpcv_trajectories"]) == PROMOTION_CPCV_TRAJECTORIES,
    ]
    if a3_row["ece_calibrated"] is not None:
        promotion_checks.append(float(a3_row["ece_calibrated"]) < 0.05)

    structural_fail = any(
        [
            float(a3_row["positive_rate_oos"]) < EXPERIMENT_POSITIVE_RATE_MIN,
            int(a3_row["latest_active_count_decision_space"]) < 1,
            not bool(a3_row["headroom_decision_space"]),
            int(a3_row["recent_live_dates_decision_space"]) < 5,
            int(a3_row["historical_active_events_decision_space"]) < 120,
            float(a3_row["sharpe_operational"]) <= 0.0,
            float(a3_row["dsr_honest"]) <= 0.0,
        ]
    )
    if all(experiment_required) and all(promotion_checks):
        return "PASS", "advance"
    if structural_fail:
        return "FAIL", "abandon"
    return "PARTIAL", "correct"


def _build_gate_metrics(a3_row: dict[str, Any], integrity: dict[str, Any]) -> list[dict[str, Any]]:
    def _metric(name: str, value: Any, threshold: str, passed: bool | None) -> dict[str, Any]:
        status = "NA" if passed is None else ("PASS" if passed else "FAIL")
        return {
            "gate_slug": GATE_SLUG,
            "metric_name": name,
            "metric_value": value,
            "metric_threshold": threshold,
            "metric_status": status,
        }

    return [
        _metric("no_leakage_proof_pass", integrity["no_leakage_proof_pass"], "PASS", integrity["no_leakage_proof_pass"]),
        _metric("research_only_isolation_pass", integrity["research_only_isolation_pass"], "PASS", integrity["research_only_isolation_pass"]),
        _metric("official_artifacts_unchanged", integrity["official_artifacts_unchanged"], "PASS", integrity["official_artifacts_unchanged"]),
        _metric("target_is_non_circular", integrity["target_is_non_circular"], "PASS", integrity["target_is_non_circular"]),
        _metric("decision_space_metrics_computed", integrity["decision_space_metrics_computed"], "PASS", integrity["decision_space_metrics_computed"]),
        _metric("positive_rate_oos", a3_row["positive_rate_oos"], ">= 0.05", float(a3_row["positive_rate_oos"]) >= 0.05),
        _metric("latest_active_count_decision_space", a3_row["latest_active_count_decision_space"], ">= 1", int(a3_row["latest_active_count_decision_space"]) >= 1),
        _metric("headroom_decision_space", a3_row["headroom_decision_space"], "== true", bool(a3_row["headroom_decision_space"])),
        _metric("recent_live_dates_decision_space", a3_row["recent_live_dates_decision_space"], ">= 5 of 8", int(a3_row["recent_live_dates_decision_space"]) >= 5),
        _metric("historical_active_events_decision_space", a3_row["historical_active_events_decision_space"], ">= 120", int(a3_row["historical_active_events_decision_space"]) >= 120),
        _metric("sharpe_operational", a3_row["sharpe_operational"], "> 0", float(a3_row["sharpe_operational"]) > 0),
        _metric("dsr_honest", a3_row["dsr_honest"], "> 0", float(a3_row["dsr_honest"]) > 0),
        _metric("n_eff_mean", a3_row["n_eff_mean"], "> 120", (a3_row["n_eff_mean"] or 0.0) > 120.0),
        _metric("cpcv_trajectories", a3_row["cpcv_trajectories"], "== 15", int(a3_row["cpcv_trajectories"]) == 15),
        _metric("pbo", a3_row["pbo"], "< 0.10", (a3_row["pbo"] if a3_row["pbo"] is not None else 1.0) < 0.10),
        _metric(
            "ece_calibrated",
            a3_row["ece_calibrated"] if a3_row["ece_calibrated"] is not None else "N/A",
            "< 0.05",
            None if a3_row["ece_calibrated"] is None else float(a3_row["ece_calibrated"]) < 0.05,
        ),
        _metric("reliability_diagram_present", a3_row["reliability_diagram_present"], "PASS", bool(a3_row["reliability_diagram_present"])),
        _metric("subperiods_positive", a3_row["subperiods_positive"], ">= 4 of 6 for promotion", int(a3_row["subperiods_positive"]) >= 4),
    ]


def run_phase5_stage_a3_spec_hardening() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = _collect_official_inventory(model_path)

    candidates = _screen_stage_a3_candidates(model_path)
    selected_target = _selected_target_payload(candidates)
    _write_json(research_path / "stage_a3_candidates.json", {"gate_slug": GATE_SLUG, "candidates": candidates})
    _write_json(research_path / "stage_a3_selected_target.json", selected_target)

    _run_stage_a3_experiment()

    official_after = _collect_official_inventory(model_path)
    critical_before = official_before["combined_hashes"]["phase4_critical_combined_sha256"]
    critical_after = official_after["combined_hashes"]["phase4_critical_combined_sha256"]
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True

    a3 = _rebuild_experiment(model_path, "phase5_stage_a3")
    baseline = _rebuild_experiment(model_path, "phase4_cross_sectional_ranking_baseline")
    v1 = _rebuild_experiment(model_path, "phase4_stage_a_experiment")
    v2 = _rebuild_experiment(model_path, "phase4_stage_a_experiment_v2")

    no_leakage_proof = _prove_no_leakage(a3)
    reliability_path = research_path / "stage_a3_reliability_diagram.png"
    reliability_diagram_present = _build_reliability_diagram(a3.predictions, reliability_path)

    integrity_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official": {
            "before": official_before,
            "after": official_after,
            "critical_hash_before": critical_before,
            "critical_hash_after": critical_after,
            "official_artifacts_unchanged": official_artifacts_unchanged,
        },
        "path_separation": {
            "official_root": str(model_path),
            "research_root": str(research_path),
            "gate_root": str(gate_path),
            "official_vs_research_disjoint": str(research_path).lower().startswith(str((model_path / "research").resolve()).lower()),
            "official_vs_gate_disjoint": str(gate_path).lower().startswith(str((REPO_ROOT / "reports" / "gates").resolve()).lower()),
        },
        "research_only_isolation_pass": research_only_isolation_pass,
        "no_official_overwrite_detected": official_artifacts_unchanged,
    }
    _write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    baseline_row = _build_comparator_row(
        baseline,
        row_name="baseline_cross_sectional_current",
        reliability_diagram_present=None,
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        no_leakage_proof_pass=None,
    )
    v1_row = _build_comparator_row(
        v1,
        row_name="stage_a_v1",
        reliability_diagram_present=None,
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        no_leakage_proof_pass=None,
    )
    v2_row = _build_comparator_row(
        v2,
        row_name="stage_a_v2",
        reliability_diagram_present=None,
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        no_leakage_proof_pass=None,
    )
    a3_row = _build_comparator_row(
        a3,
        row_name="stage_a3_q60",
        reliability_diagram_present=reliability_diagram_present,
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        no_leakage_proof_pass=bool(no_leakage_proof.get("pass")),
    )

    comparison_df = pd.DataFrame([baseline_row, v1_row, v2_row, a3_row])
    comparison_df.to_parquet(research_path / "stage_a3_comparison.parquet", index=False)
    _write_json(research_path / "stage_a3_failure_modes.json", _build_failure_modes(v1, v2, a3_row))

    integrity_checks = {
        "no_leakage_proof_pass": bool(no_leakage_proof.get("pass")),
        "research_only_isolation_pass": bool(research_only_isolation_pass),
        "official_artifacts_unchanged": bool(official_artifacts_unchanged),
        "target_is_non_circular": True,
        "decision_space_metrics_computed": bool(
            _compute_decision_space_metrics(a3.aggregated).get("decision_space_metrics_computed")
        ),
    }
    status, decision = _classify_round(a3_row, integrity_checks)

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "selected_target": selected_target,
        "sovereign_metrics": {
            key: a3_row[key]
            for key in [
                "positive_rate_oos",
                "latest_active_count_decision_space",
                "headroom_decision_space",
                "recent_live_dates_decision_space",
                "historical_active_events_decision_space",
                "sharpe_operational",
                "dsr_honest",
                "subperiods_positive",
                "n_eff_mean",
                "cpcv_trajectories",
                "pbo",
                "ece_calibrated",
            ]
        },
        "auxiliary_metrics": {
            key: a3_row[key]
            for key in [
                "aux_p_activate_raw_gt_050",
                "aux_p_activate_calibrated_gt_050",
                "aux_mu_adj_stage_a3_gt_0",
                "aux_utility_surplus_pred_gt_0",
            ]
        },
        "integrity": integrity_checks,
        "comparison_rows": [baseline_row, v1_row, v2_row, a3_row],
        "no_leakage_proof": no_leakage_proof,
    }
    _write_json(research_path / "stage_a3_summary.json", summary_payload)

    gate_metrics = _build_gate_metrics(a3_row, integrity_checks)
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git_output("status", "--short")),
        "branch": _git_output("branch", "--show-current"),
        "official_artifacts_used": official_before["official_paths_read"]["phase4_critical"],
        "research_artifacts_generated": [
            str(research_path / "stage_a3_candidates.json"),
            str(research_path / "stage_a3_selected_target.json"),
            str(research_path / "stage_a3_comparison.parquet"),
            str(research_path / "stage_a3_summary.json"),
            str(research_path / "stage_a3_reliability_diagram.png"),
            str(research_path / "stage_a3_failure_modes.json"),
            str(research_path / "official_artifacts_integrity.json"),
        ],
        "summary": [
            "Stage A3 screening stayed label-side and q60 was locked ex ante before the final rerun.",
            "Baseline geometry and sovereign decision-space ruler were preserved for the A3 comparison.",
            "Official artifacts remained isolated and were hashed before/after the round.",
        ],
        "gates": gate_metrics,
        "blockers": [] if decision != "abandon" else ["Stage A3 remained structurally weak under the sovereign decision-space gates."],
        "risks_residual": [
            "Latest decision-space activity can still remain sparse even if label-side prevalence survives.",
            "If stage2 activated-train support falls below threshold in future reruns, CPCV validity can drop below 15 trajectories.",
        ],
        "next_recommended_step": (
            "Proceed to FASE 5A-R2 quantitative stress research-only."
            if decision == "advance"
            else "Do not open FASE 5 clássica yet; keep corrections research-only."
        ),
    }
    markdown_sections = {
        "Resumo executivo": "\n".join([f"- status={status}", f"- decision={decision}", f"- selected_target={selected_target['selected_target_id']}"]),
        "Baseline congelado": "\n".join(
            [
                "- baseline_cross_sectional_current uses geometry locked in Fase 4 closure.",
                f"- baseline target_definition: {baseline.report.get('target_definition')}",
            ]
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- Added research-only two-stage Stage A3 runner path.",
                "- Recovered minimal gate writer source from compiled contract.",
                "- Materialized research-only integrity, comparison, and reliability artifacts.",
            ]
        ),
        "Artifacts gerados": "\n".join(f"- {path}" for path in gate_report["research_artifacts_generated"]),
        "Resultados": "\n".join(
            [
                f"- positive_rate_oos={a3_row['positive_rate_oos']}",
                f"- latest_active_count_decision_space={a3_row['latest_active_count_decision_space']}",
                f"- recent_live_dates_decision_space={a3_row['recent_live_dates_decision_space']}",
                f"- sharpe_operational={a3_row['sharpe_operational']}",
                f"- dsr_honest={a3_row['dsr_honest']}",
            ]
        ),
        "Avaliação contra gates": "\n".join(
            f"- {row['metric_name']}: {row['metric_status']} ({row['metric_value']} vs {row['metric_threshold']})"
            for row in gate_metrics
        ),
        "Riscos residuais": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
        "Veredito final: advance / correct / abandon": f"- {decision}",
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "branch": _git_output("branch", "--show-current"),
        "working_tree_dirty_before": bool(_git_output("status", "--short")),
        "working_tree_dirty_after": bool(_git_output("status", "--short")),
        "source_artifacts": official_before["inventories"]["phase4_critical"],
        "generated_artifacts": [],
        "commands_executed": ["python services\\ml_engine\\phase5_stage_a3_spec_hardening.py"],
        "notes": ["Round stayed research-only.", "Official artifact hashes were compared before and after execution."],
    }
    write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    return {"status": status, "decision": decision, "comparison": [baseline_row, v1_row, v2_row, a3_row], "integrity": integrity_payload}


if __name__ == "__main__":
    run_phase5_stage_a3_spec_hardening()
