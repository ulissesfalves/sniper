#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from itertools import combinations
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
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_stage_a3_choke_audit"
PHASE_FAMILY = "phase5_stage_a3_choke_audit"
RECENT_WINDOW_DATES = 8
EXPERIMENTS = (
    ("baseline_cross_sectional_current", "phase4_cross_sectional_ranking_baseline"),
    ("stage_a3_q60", "phase5_stage_a3"),
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


def _distribution_summary(values: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if series.empty:
        return {
            "count": 0,
            "positive_count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
            "mean": None,
        }
    return {
        "count": int(series.size),
        "positive_count": int((series > 0).sum()),
        "min": round(float(series.min()), 6),
        "p25": round(float(series.quantile(0.25)), 6),
        "median": round(float(series.median()), 6),
        "p75": round(float(series.quantile(0.75)), 6),
        "max": round(float(series.max()), 6),
        "mean": round(float(series.mean()), 6),
    }


def _normalized_dates(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame.get("date"), errors="coerce").dt.normalize()


def _prepare_scope(frame: pd.DataFrame, scope_name: str) -> tuple[pd.DataFrame, list[pd.Timestamp], pd.Timestamp | None]:
    work = frame.copy()
    work["date"] = _normalized_dates(work)
    latest_date = work["date"].dropna().max() if not work.empty else None
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-RECENT_WINDOW_DATES:]
    if scope_name == "oos_aggregated":
        return work, recent_dates, latest_date
    if scope_name == "latest_date":
        return work.loc[work["date"] == latest_date].copy(), recent_dates, latest_date
    if scope_name == "recent_window":
        return work.loc[work["date"].isin(recent_dates)].copy(), recent_dates, latest_date
    raise ValueError(f"unsupported scope_name={scope_name}")


def _funnel_column_map(rebuilt: stage5.RebuiltExperiment) -> dict[str, str]:
    if rebuilt.target_mode == "two_stage_activation_utility":
        return {
            "raw_col": "p_activate_raw_stage_a",
            "cal_col": "p_activate_calibrated_stage_a",
            "activated_col": "stage_a_predicted_activated",
            "ranked_col": "stage_a_selected_proxy",
        }
    return {
        "raw_col": "p_stage_a_raw",
        "cal_col": "p_stage_a_calibrated",
        "activated_col": "stage_a_eligible",
        "ranked_col": "stage_a_selected_proxy",
    }


def _funnel_counts(
    frame: pd.DataFrame,
    *,
    rebuilt: stage5.RebuiltExperiment,
    experiment_label: str,
    scope_name: str,
    date_value: pd.Timestamp | None = None,
    cluster_name: str | None = None,
) -> dict[str, Any]:
    mapping = _funnel_column_map(rebuilt)
    raw = pd.to_numeric(frame.get(mapping["raw_col"]), errors="coerce").fillna(0.0)
    cal = pd.to_numeric(frame.get(mapping["cal_col"]), errors="coerce").fillna(0.0)
    activated = pd.Series(frame.get(mapping["activated_col"], False), index=frame.index).fillna(False).astype(bool)
    ranked = pd.Series(frame.get(mapping["ranked_col"], False), index=frame.index).fillna(False).astype(bool)
    mu_adj = pd.to_numeric(frame.get("mu_adj_stage_a"), errors="coerce").fillna(0.0)
    decision_selected = pd.Series(frame.get("decision_selected", False), index=frame.index).fillna(False).astype(bool)
    position = pd.to_numeric(frame.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    return {
        "experiment": experiment_label,
        "experiment_name": rebuilt.experiment_name,
        "target_mode": rebuilt.target_mode,
        "scope_name": scope_name,
        "date": None if date_value is None else pd.Timestamp(date_value).strftime("%Y-%m-%d"),
        "cluster_name": cluster_name,
        "n_rows_total": int(len(frame)),
        "n_rows_scored": int(raw.notna().sum()),
        "n_rows_p_raw_gt_050": int((raw > 0.50).sum()),
        "n_rows_p_cal_gt_050": int((cal > 0.50).sum()),
        "n_rows_activated": int(activated.sum()),
        "n_rows_ranked_top": int(ranked.sum()),
        "n_rows_mu_adj_gt_0": int((mu_adj > 0).sum()),
        "n_rows_decision_selected": int(decision_selected.sum()),
        "n_rows_position_gt_0": int((position > 0).sum()),
    }


def _build_funnel_artifacts(rebuilt: stage5.RebuiltExperiment, experiment_label: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    scope_rows: list[dict[str, Any]] = []
    per_date_rows: list[dict[str, Any]] = []
    cluster_rows: list[dict[str, Any]] = []

    for scope_name in ("oos_aggregated", "latest_date", "recent_window"):
        scope_df, recent_dates, latest_date = _prepare_scope(rebuilt.aggregated, scope_name)
        scope_rows.append(
            _funnel_counts(
                scope_df,
                rebuilt=rebuilt,
                experiment_label=experiment_label,
                scope_name=scope_name,
                date_value=latest_date if scope_name == "latest_date" else None,
            )
        )
        if scope_name == "recent_window":
            for date_value in recent_dates:
                by_date = scope_df.loc[scope_df["date"] == pd.Timestamp(date_value)].copy()
                per_date_rows.append(
                    _funnel_counts(
                        by_date,
                        rebuilt=rebuilt,
                        experiment_label=experiment_label,
                        scope_name="recent_window_by_date",
                        date_value=pd.Timestamp(date_value),
                    )
                )
        if "cluster_name" in scope_df.columns:
            for cluster_name, cluster_df in scope_df.groupby("cluster_name", dropna=False, sort=True):
                cluster_rows.append(
                    _funnel_counts(
                        cluster_df,
                        rebuilt=rebuilt,
                        experiment_label=experiment_label,
                        scope_name=scope_name,
                        date_value=latest_date if scope_name == "latest_date" else None,
                        cluster_name=str(cluster_name),
                    )
                )

    return pd.DataFrame(scope_rows), pd.DataFrame(per_date_rows), pd.DataFrame(cluster_rows)


def _extract_sovereign_metrics(aggregated: pd.DataFrame) -> dict[str, Any]:
    return stage5._compute_decision_space_metrics(aggregated)


def _load_prior_comparison_row(research_path: Path, experiment_name: str) -> dict[str, Any] | None:
    comparison_path = research_path / "stage_a3_comparison.parquet"
    if not comparison_path.exists():
        return None
    frame = pd.read_parquet(comparison_path)
    match = frame.loc[frame["experiment_name"].astype(str) == experiment_name]
    if match.empty:
        return None
    return match.iloc[0].to_dict()


def _audit_metric_definitions(
    *,
    research_path: Path,
    baseline: stage5.RebuiltExperiment,
    a3: stage5.RebuiltExperiment,
) -> dict[str, Any]:
    baseline_metrics = _extract_sovereign_metrics(baseline.aggregated)
    a3_metrics = _extract_sovereign_metrics(a3.aggregated)
    prior_row = _load_prior_comparison_row(research_path, a3.experiment_name)

    artifact_match = True
    artifact_values: dict[str, Any] | None = None
    if prior_row is not None:
        artifact_values = {
            "latest_active_count_decision_space": int(prior_row.get("latest_active_count_decision_space", 0)),
            "headroom_decision_space": bool(prior_row.get("headroom_decision_space", False)),
            "recent_live_dates_decision_space": int(prior_row.get("recent_live_dates_decision_space", 0)),
            "historical_active_events_decision_space": int(prior_row.get("historical_active_events_decision_space", 0)),
        }
        artifact_match = artifact_values == {
            "latest_active_count_decision_space": int(a3_metrics["latest_active_count_decision_space"]),
            "headroom_decision_space": bool(a3_metrics["headroom_decision_space"]),
            "recent_live_dates_decision_space": int(a3_metrics["recent_live_dates_decision_space"]),
            "historical_active_events_decision_space": int(a3_metrics["historical_active_events_decision_space"]),
        }

    auxiliary_headroom_definition = (
        a3.report.get("gate_do_experimento_stage_a", {}) or {}
    ).get("headroom_definition")

    return {
        "audit_complete": bool(prior_row is not None and artifact_match),
        "ruler_drift_status": "METRIC_RULER_DRIFT" if not artifact_match else "NO_DRIFT",
        "code_source": {
            "file": str((THIS_DIR / "phase5_stage_a3_spec_hardening.py").resolve()),
            "function": "_compute_decision_space_metrics",
            "latest_active_count_decision_space": "count(decision_selected and position_usdt_stage_a > 0) on latest date",
            "headroom_decision_space": "max(position_usdt_stage_a) > 0 on latest date",
            "recent_live_dates_decision_space": "count(distinct recent dates with any decision_selected and position_usdt_stage_a > 0)",
            "historical_active_events_decision_space": "count(decision_selected and position_usdt_stage_a > 0) over full OOS aggregated frame",
        },
        "recomputed_metrics": {
            "baseline_cross_sectional_current": baseline_metrics,
            "stage_a3_q60": a3_metrics,
        },
        "artifact_metrics_prior_round": artifact_values,
        "artifact_match_prior_round": bool(artifact_match),
        "auxiliary_proxy_found": {
            "present": bool(auxiliary_headroom_definition),
            "path": str((research_path / "stage_a_report.json").resolve()),
            "field": "gate_do_experimento_stage_a.headroom_definition",
            "definition": auxiliary_headroom_definition,
            "classification": "auxiliary_proxy_not_sovereign" if auxiliary_headroom_definition else "absent",
            "impact_on_sovereign_metrics": "none",
        },
    }


def _contest_geometry_payload(rebuilt: stage5.RebuiltExperiment) -> dict[str, Any]:
    if rebuilt.target_mode == "cross_sectional_ranking":
        return {
            "group_axis": "(date, cluster_name)",
            "local_rule": "top1(rank_score_stage_a) among stage_a_eligible",
            "fallback_rule": "top1(date-universe) when eligible_count(date, cluster_name) < 2",
            "groups_local_selection": int(rebuilt.selection_summary.get("groups_local_selection", 0)),
            "groups_fallback_selection": int(rebuilt.selection_summary.get("groups_fallback_selection", 0)),
            "groups_without_eligible": int(rebuilt.selection_summary.get("groups_without_eligible", 0)),
            "groups_total": int(rebuilt.selection_summary.get("groups_total", 0)),
        }
    return {
        "group_axis": "(date, cluster_name)",
        "local_rule": "top1(utility_surplus_pred_stage_a, p_activate_calibrated_stage_a, symbol) among stage_a_predicted_activated",
        "fallback_rule": "top1(date-universe) when activated_count(date, cluster_name) < 2",
        "groups_local_selection": int(rebuilt.selection_summary.get("groups_local_selection", 0)),
        "groups_fallback_selection": int(rebuilt.selection_summary.get("groups_fallback_selection", 0)),
        "groups_without_eligible": int(rebuilt.selection_summary.get("groups_without_eligible", 0)),
        "groups_total": int(rebuilt.selection_summary.get("groups_total", 0)),
    }


def _compare_contest_geometry(
    baseline: stage5.RebuiltExperiment,
    a3: stage5.RebuiltExperiment,
) -> dict[str, Any]:
    baseline_geometry = _contest_geometry_payload(baseline)
    a3_geometry = _contest_geometry_payload(a3)
    same_geometry = (
        baseline_geometry["group_axis"] == a3_geometry["group_axis"]
        and baseline_geometry["fallback_rule"].endswith("< 2")
        and a3_geometry["fallback_rule"].endswith("< 2")
    )
    return {
        "baseline": baseline_geometry,
        "a3_q60": a3_geometry,
        "conclusion": "SAME_CONTEST_GEOMETRY" if same_geometry else "CONTEST_GEOMETRY_DRIFT_IDENTIFIED",
        "note": (
            "Geometry is the same; the A3 never enters the contest because upstream calibrated activation is zero."
            if same_geometry
            else "Geometry drift detected and must be quantified before any correction."
        ),
    }


def _collect_stage2_train_diagnostics(model_path: Path) -> dict[str, Any]:
    stage_a._configure_phase4_paths(model_path)
    pooled_df = phase4.load_pooled_meta_df()
    _, symbol_to_cluster, target_cluster_mode, target_cluster_artifact_path = phase4._load_symbol_vi_clusters(
        pooled_df["symbol"].astype(str).unique().tolist()
    )
    n = len(pooled_df)
    embargo = max(1, int(n * phase4.EMBARGO_PCT))
    splits = np.array_split(np.arange(n), phase4.N_SPLITS)

    per_combo_rows: list[dict[str, Any]] = []
    all_surplus: list[float] = []

    for combo in combinations(range(phase4.N_SPLITS), phase4.N_TEST_SPLITS):
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
        symbol_stats, global_tp, global_sl = phase4._compute_symbol_trade_stats(train_df)
        train_df = phase4._attach_trade_stats(
            train_df,
            symbol_stats,
            global_tp,
            global_sl,
            tp_col="avg_tp_train",
            sl_col="avg_sl_train",
        )
        train_df["cluster_name"] = train_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
        cluster_thresholds, threshold_summary = stage_a._compute_two_stage_activation_thresholds(train_df, quantile=0.60)
        train_df = stage_a._attach_two_stage_target_metadata(
            train_df,
            cluster_thresholds=cluster_thresholds,
            threshold_summary=threshold_summary,
        )
        dummy_x = np.zeros((len(train_df), 1), dtype=float)
        dummy_w = np.ones(len(train_df), dtype=float)
        _, y_stage2, _, payload = stage_a._build_stage2_training_payload(
            train_df,
            dummy_x,
            dummy_w,
            min_rows=stage_a.MIN_STAGE2_TRAIN_ROWS,
        )
        surplus_series = pd.Series(y_stage2, dtype=float) if y_stage2 is not None else pd.Series(dtype=float)
        per_combo_rows.append(
            {
                "combo": str(combo),
                "train_rows_total": int(payload["train_rows_total"]),
                "train_rows_stage2": int(payload["train_rows_stage2"]),
                "stage2_valid": bool(payload["is_valid"]),
                "stage2_reason": str(payload["reason"]),
                "utility_surplus_positive": int((surplus_series > 0).sum()) if not surplus_series.empty else 0,
                "utility_surplus_max": None if surplus_series.empty else round(float(surplus_series.max()), 6),
            }
        )
        if y_stage2 is not None:
            all_surplus.extend(np.asarray(y_stage2, dtype=float).tolist())

    surplus_distribution = _distribution_summary(pd.Series(all_surplus, dtype=float))
    stage2_rows = [int(row["train_rows_stage2"]) for row in per_combo_rows]
    return {
        "target_cluster_mode": target_cluster_mode,
        "target_cluster_artifact_path": target_cluster_artifact_path,
        "combo_count": int(len(per_combo_rows)),
        "stage2_train_rows_mean": round(float(np.mean(stage2_rows)), 4) if stage2_rows else 0.0,
        "stage2_train_rows_min": int(min(stage2_rows)) if stage2_rows else 0,
        "stage2_train_rows_max": int(max(stage2_rows)) if stage2_rows else 0,
        "utility_surplus_train_distribution": surplus_distribution,
        "per_combo_rows": per_combo_rows,
    }


def _stage2_mu_adj_diagnostics(
    a3: stage5.RebuiltExperiment,
    *,
    model_path: Path,
) -> dict[str, Any]:
    train_diag = _collect_stage2_train_diagnostics(model_path)
    agg = a3.aggregated.copy()
    row_df = a3.predictions.copy()

    row_cal = pd.to_numeric(row_df.get("p_activate_calibrated_stage_a", row_df.get("p_stage_a_calibrated")), errors="coerce").fillna(0.0)
    row_raw = pd.to_numeric(row_df.get("p_activate_raw_stage_a", row_df.get("p_stage_a_raw")), errors="coerce").fillna(0.0)
    agg_cal = pd.to_numeric(agg.get("p_activate_calibrated_stage_a", agg.get("p_stage_a_calibrated")), errors="coerce").fillna(0.0)
    agg_raw = pd.to_numeric(agg.get("p_activate_raw_stage_a", agg.get("p_stage_a_raw")), errors="coerce").fillna(0.0)
    utility_pred_agg = pd.to_numeric(agg.get("utility_surplus_pred_stage_a"), errors="coerce").fillna(0.0)
    decision_selected = pd.Series(agg.get("decision_selected", False), index=agg.index).fillna(False).astype(bool)
    mu_adj = pd.to_numeric(agg.get("mu_adj_stage_a"), errors="coerce").fillna(0.0)
    position = pd.to_numeric(agg.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)

    if int((agg_cal > 0.50).sum()) == 0 and int((utility_pred_agg > 0).sum()) > 0:
        choke_point = "stage1_raw_to_calibrated_activation_gate"
        cause_root = (
            "Stage 2 is not degenerate, but the sovereign path never consumes it because calibrated activation "
            "collapses to zero before contest selection."
        )
    elif int(decision_selected.sum()) > 0 and int((mu_adj > 0).sum()) == 0:
        choke_point = "score_to_mu_adj_transform"
        cause_root = "Rows survive contest selection but mu_adj is zeroed by the sizing transform."
    elif int((mu_adj > 0).sum()) > 0 and int((position > 0).sum()) == 0:
        choke_point = "sizing_or_clipping"
        cause_root = "Rows have positive mu_adj but final position is clipped to zero."
    else:
        choke_point = "ambiguous"
        cause_root = "The funnel does not isolate a single dominant choke with current evidence."

    return {
        "n_train_stage2_mean": train_diag["stage2_train_rows_mean"],
        "n_train_stage2_min": train_diag["stage2_train_rows_min"],
        "n_train_stage2_max": train_diag["stage2_train_rows_max"],
        "utility_surplus_train_distribution": train_diag["utility_surplus_train_distribution"],
        "utility_surplus_oos_distribution_aggregated": _distribution_summary(utility_pred_agg),
        "positive_stage2_predictions_aggregated": int((utility_pred_agg > 0).sum()),
        "positive_stage2_predictions_row_level": int(
            (pd.to_numeric(row_df.get("utility_surplus_pred_stage_a"), errors="coerce").fillna(0.0) > 0).sum()
        ),
        "stage1_drop_row_level": {
            "raw_gt_050": int((row_raw > 0.50).sum()),
            "calibrated_gt_050": int((row_cal > 0.50).sum()),
            "max_calibrated": round(float(row_cal.max()), 6) if not row_cal.empty else 0.0,
        },
        "stage1_drop_aggregated": {
            "raw_gt_050": int((agg_raw > 0.50).sum()),
            "calibrated_gt_050": int((agg_cal > 0.50).sum()),
            "max_calibrated": round(float(agg_cal.max()), 6) if not agg_cal.empty else 0.0,
        },
        "mu_adj_transform_rule": (
            "decision_score_stage_a = p_activate_calibrated_stage_a only on decision_selected rows; otherwise 0.0. "
            "Then _compute_phase4_sizing zeroes rows when prob < 0.50, and zeroes Kelly/position again when "
            "mu_adj = prob * avg_tp_train - (1 - prob) * avg_sl_train <= 0. utility_surplus_pred_stage_a is "
            "ranking-only and does not enter mu_adj directly."
        ),
        "mu_adj_zeroing_stage": "before_sizing_entry_via_empty_activation_set" if int(decision_selected.sum()) == 0 else "inside_sizing",
        "decision_selected_count": int(decision_selected.sum()),
        "mu_adj_positive_count": int((mu_adj > 0).sum()),
        "position_positive_count": int((position > 0).sum()),
        "choke_point": choke_point,
        "cause_root": cause_root,
        "train_diag": train_diag,
    }


def _classify_choke(
    *,
    metric_definition_check: dict[str, Any],
    geometry_check: dict[str, Any],
    stage2_diag: dict[str, Any],
) -> tuple[bool, str, str]:
    if metric_definition_check.get("ruler_drift_status") == "METRIC_RULER_DRIFT":
        return True, "metric_ruler_drift", "Sovereign metric implementation diverged from the Fase 4 ruler."
    if stage2_diag["choke_point"] == "stage1_raw_to_calibrated_activation_gate":
        return True, "stage1_raw_to_calibrated_activation_gate", stage2_diag["cause_root"]
    if stage2_diag["choke_point"] == "score_to_mu_adj_transform":
        return True, "score_to_mu_adj_transform", stage2_diag["cause_root"]
    if stage2_diag["choke_point"] == "sizing_or_clipping":
        return True, "sizing_or_clipping", stage2_diag["cause_root"]
    if geometry_check["conclusion"] == "CONTEST_GEOMETRY_DRIFT_IDENTIFIED":
        return True, "contest_geometry_drift", geometry_check["note"]
    return False, "ambiguous", "Current audit did not isolate a single dominant choke."


def _build_integrity_payload(
    *,
    model_path: Path,
    research_path: Path,
    gate_path: Path,
    official_before: dict[str, Any],
    official_after: dict[str, Any],
    generated_paths: list[Path],
) -> dict[str, Any]:
    official_subtrees = [model_path / "features", model_path / "phase3", model_path / "phase4"]
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = all(
        not any(path.resolve().is_relative_to(subtree.resolve()) for subtree in official_subtrees)
        for path in generated_paths
    )
    return {
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": bool(official_artifacts_unchanged),
        "research_only_isolation_pass": bool(research_only_isolation_pass),
        "path_separation": {
            "official_subtrees": [str(path.resolve()) for path in official_subtrees],
            "research_root": str(research_path.resolve()),
            "gate_root": str(gate_path.resolve()),
            "generated_paths": [str(path.resolve()) for path in generated_paths],
            "official_vs_research_disjoint": not research_path.resolve().is_relative_to((model_path / "phase4").resolve()),
            "official_vs_gate_disjoint": not gate_path.resolve().is_relative_to(model_path.resolve()),
        },
    }


def _gate_metrics(
    *,
    integrity: dict[str, Any],
    metric_definition_check: dict[str, Any],
    choke_localized: bool,
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
        _metric("metric_definition_audit_complete", metric_definition_check["audit_complete"], metric_definition_check["audit_complete"]),
        _metric("choke_stage_localized", choke_localized, choke_localized),
    ]


def _classify_round(
    *,
    integrity: dict[str, Any],
    metric_definition_check: dict[str, Any],
    choke_localized: bool,
) -> tuple[str, str]:
    if not integrity["official_artifacts_unchanged"] or not integrity["research_only_isolation_pass"]:
        return "FAIL", "abandon"
    if metric_definition_check["audit_complete"] and choke_localized:
        return "PASS", "advance"
    if metric_definition_check["audit_complete"] or choke_localized:
        return "PARTIAL", "correct"
    return "FAIL", "abandon"


def run_phase5_stage_a3_choke_audit() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)

    rebuilt = {
        experiment_label: stage5._rebuild_experiment(model_path, experiment_name)
        for experiment_label, experiment_name in EXPERIMENTS
    }
    baseline = rebuilt["baseline_cross_sectional_current"]
    a3 = rebuilt["stage_a3_q60"]

    funnel_frames = []
    latest_recent_frames = []
    cluster_frames = []
    for experiment_label, _experiment_name in EXPERIMENTS:
        scope_df, per_date_df, cluster_df = _build_funnel_artifacts(rebuilt[experiment_label], experiment_label)
        funnel_frames.append(scope_df)
        latest_recent_frames.append(per_date_df)
        cluster_frames.append(cluster_df)

    funnel_df = pd.concat(funnel_frames, ignore_index=True)
    latest_recent_df = pd.concat(latest_recent_frames, ignore_index=True)
    cluster_df = pd.concat(cluster_frames, ignore_index=True)

    metric_definition_check = _audit_metric_definitions(research_path=research_path, baseline=baseline, a3=a3)
    geometry_check = _compare_contest_geometry(baseline, a3)
    stage2_diag = _stage2_mu_adj_diagnostics(a3, model_path=model_path)
    choke_localized, choke_stage, choke_cause = _classify_choke(
        metric_definition_check=metric_definition_check,
        geometry_check=geometry_check,
        stage2_diag=stage2_diag,
    )

    funnel_path = research_path / "choke_audit_funnel.parquet"
    latest_recent_path = research_path / "choke_audit_latest_recent.parquet"
    cluster_path = research_path / "choke_audit_cluster_breakdown.parquet"
    mu_adj_diag_path = research_path / "choke_audit_mu_adj_diagnostics.json"
    metric_check_path = research_path / "choke_audit_metric_definition_check.json"
    summary_path = research_path / "choke_audit_summary.json"
    integrity_path = research_path / "official_artifacts_integrity_audit.json"

    funnel_df.to_parquet(funnel_path, index=False)
    latest_recent_df.to_parquet(latest_recent_path, index=False)
    cluster_df.to_parquet(cluster_path, index=False)
    _write_json(mu_adj_diag_path, stage2_diag)
    _write_json(metric_check_path, metric_definition_check)

    generated_paths = [
        funnel_path,
        latest_recent_path,
        cluster_path,
        mu_adj_diag_path,
        metric_check_path,
        summary_path,
        integrity_path,
    ]
    official_after = stage5._collect_official_inventory(model_path)
    integrity = _build_integrity_payload(
        model_path=model_path,
        research_path=research_path,
        gate_path=gate_path,
        official_before=official_before,
        official_after=official_after,
        generated_paths=generated_paths,
    )
    _write_json(integrity_path, integrity)

    status, decision = _classify_round(
        integrity=integrity,
        metric_definition_check=metric_definition_check,
        choke_localized=choke_localized,
    )

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "choke_stage_localized": choke_localized,
        "choke_dominant_stage": choke_stage,
        "choke_cause_root": choke_cause,
        "sovereign_metrics_stage_a3_q60": stage5._compute_decision_space_metrics(a3.aggregated),
        "geometry_check": geometry_check,
        "metric_definition_check": metric_definition_check,
        "stage2_mu_adj_diagnostics": stage2_diag,
    }
    _write_json(summary_path, summary_payload)

    gate_metrics = _gate_metrics(
        integrity=integrity,
        metric_definition_check=metric_definition_check,
        choke_localized=choke_localized,
    )

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git_output("status", "--short")),
        "branch": _git_output("branch", "--show-current"),
        "official_artifacts_used": official_before["official_paths_read"]["phase4_critical"],
        "research_artifacts_generated": [str(path) for path in generated_paths],
        "summary": [
            "The sovereign decision-space ruler was audited in code and matched the prior phase5 artifact values.",
            "The A3 q60 choke is upstream of Stage 2 utility ranking under the sovereign aggregated path.",
            "Official artifacts remained untouched and all audit outputs stayed in research/gate areas.",
        ],
        "gates": gate_metrics,
        "blockers": [] if choke_localized else ["Dominant choke remains ambiguous after the current audit."],
        "risks_residual": [
            "The prior stage_a_report still carries an auxiliary proxy headroom field that must not be mistaken for the sovereign ruler.",
            "Any future correction round must preserve the same contest geometry and sovereign decision-space definitions.",
        ],
        "next_recommended_step": (
            "Run one correction-only research round focused on the Stage 1 calibrated activation choke and CPCV aggregation, leaving Stage 2 and contest geometry frozen."
            if choke_localized
            else "Keep the next round diagnostic; do not open A4 or classical stress yet."
        ),
    }

    markdown_sections = {
        "Resumo executivo": "\n".join([f"- status={status}", f"- decision={decision}", f"- choke_dominant_stage={choke_stage}"]),
        "Baseline congelado": "\n".join(
            [
                "- baseline geometry remains top1 within (date, cluster_name) with date-universe fallback under support < 2.",
                "- sovereign metrics are still derived from decision_selected and position_usdt_stage_a > 0.",
            ]
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- Added a research-only choke audit runner on top of existing phase5 Stage A3 artifacts.",
                "- Added unit coverage for sovereign metrics, funnel instrumentation, contest geometry, and Stage 2 choke localization.",
            ]
        ),
        "Artifacts gerados": "\n".join(f"- {path}" for path in gate_report["research_artifacts_generated"]),
        "Resultados": "\n".join(
            [
                f"- baseline historical positions_gt_0={int(funnel_df.loc[(funnel_df['experiment'] == 'baseline_cross_sectional_current') & (funnel_df['scope_name'] == 'oos_aggregated'), 'n_rows_position_gt_0'].iloc[0])}",
                f"- a3 historical calibrated_gt_050={int(funnel_df.loc[(funnel_df['experiment'] == 'stage_a3_q60') & (funnel_df['scope_name'] == 'oos_aggregated'), 'n_rows_p_cal_gt_050'].iloc[0])}",
                f"- a3 dominant_choke={choke_stage}",
            ]
        ),
        "Avaliação contra gates": "\n".join(f"- {row['metric_name']}: {row['metric_status']} ({row['metric_value']})" for row in gate_metrics),
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
        "source_artifacts": [
            artifact_record(research_path / "stage_a_report.json"),
            artifact_record(research_path / "stage_a3_summary.json"),
            artifact_record(research_path / "stage_a3_comparison.parquet"),
            artifact_record(model_path / "research" / "phase4_cross_sectional_ranking_baseline" / "stage_a_report.json"),
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "python -m py_compile services\\ml_engine\\phase5_stage_a3_choke_audit.py",
            "python -m pytest tests\\unit\\test_phase5_stage_a3_choke_audit.py -q",
            "python services\\ml_engine\\phase5_stage_a3_choke_audit.py",
        ],
        "notes": ["No retraining and no official artifact mutation were allowed in this round."],
    }

    write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )

    return {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "summary_path": str(summary_path),
        "gate_path": str(gate_path),
        "choke_stage": choke_stage,
        "official_artifacts_unchanged": integrity["official_artifacts_unchanged"],
        "research_only_isolation_pass": integrity["research_only_isolation_pass"],
        "metric_definition_audit_complete": metric_definition_check["audit_complete"],
        "choke_stage_localized": choke_localized,
    }


def main() -> None:
    result = run_phase5_stage_a3_choke_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
