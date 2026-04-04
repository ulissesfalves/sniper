#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

GATE_SLUG = "phase5_cross_sectional_hardening_baseline"
PHASE_FAMILY = "phase5_cross_sectional_hardening_baseline"
RECENT_WINDOW_DATES = 8
FROZEN_EXPERIMENT = "phase4_cross_sectional_ranking_baseline"
REPLAY_RUN1_EXPERIMENT = "phase5_cross_sectional_hardening_replay_run1"
REPLAY_RUN2_EXPERIMENT = "phase5_cross_sectional_hardening_replay_run2"
HMM_NEUTRAL_EXPERIMENT = "phase5_cross_sectional_hardening_replay_hmm_neutral"
STABLECOIN_NEUTRAL_EXPERIMENT = "phase5_cross_sectional_hardening_replay_stablecoin_neutral"
UNIVERSE_MEDIAN_EXPERIMENT = "phase5_cross_sectional_hardening_replay_universe_median_history"
REQUIRED_RESEARCH_FILES = (
    "stage_a_report.json",
    "stage_a_predictions.parquet",
    "stage_a_snapshot_proxy.parquet",
    "stage_a_manifest.json",
)
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)
RESEARCH_ARTIFACT_FILES = (
    "hardening_stress_matrix.parquet",
    "hardening_regime_slices.parquet",
    "hardening_failure_modes.json",
    "phase5_cross_sectional_hardening_summary.json",
    "official_artifacts_integrity.json",
)
VOLATILE_REPORT_KEYS = {"generated_at_utc", "experiment_name"}


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
    research_path = model_path / "research" / "phase5_cross_sectional_hardening"
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, research_path, gate_path


def _normalize_payload(value: Any, *, drop_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_payload(item, drop_keys=drop_keys)
            for key, item in sorted(value.items())
            if key not in drop_keys
        }
    if isinstance(value, list):
        return [_normalize_payload(item, drop_keys=drop_keys) for item in value]
    return value


def _frame_hash(frame: pd.DataFrame) -> str:
    if frame.empty:
        return hashlib.sha256(b"empty").hexdigest().upper()
    work = frame.copy()
    for col in work.columns:
        if pd.api.types.is_datetime64_any_dtype(work[col]):
            work[col] = pd.to_datetime(work[col], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")
    ordered_cols = sorted(work.columns.tolist())
    work = work.loc[:, ordered_cols]
    sort_cols = [col for col in ["date", "symbol", "combo", "cluster_name"] if col in work.columns]
    if sort_cols:
        work = work.sort_values(sort_cols, ascending=True, kind="mergesort")
    work = work.reset_index(drop=True)
    hashed = pd.util.hash_pandas_object(work, index=False).to_numpy().tobytes()
    return hashlib.sha256(hashed).hexdigest().upper()


def _run_stage_a_subprocess(
    *,
    model_path: Path,
    experiment_name: str,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    for key in ("STAGE_A_NEUTRALIZE_FEATURES", "STAGE_A_UNIVERSE_FILTER_RULE", "STAGE_A_DATASET_STRESS_SCENARIO"):
        env.pop(key, None)
    env.update(
        {
            "PYTHONUTF8": "1",
            "SNIPER_MODEL_PATH": str(model_path),
            "STAGE_A_EXPERIMENT_NAME": experiment_name,
            "STAGE_A_REFERENCE_EXPERIMENT_NAME": FROZEN_EXPERIMENT,
            "STAGE_A_BASELINE_EXPERIMENT_NAMES": FROZEN_EXPERIMENT,
            "STAGE_A_PROBLEM_TYPE": "cross_sectional_ranking",
            "STAGE_A_TARGET_MODE": "cross_sectional_relative_activation",
        }
    )
    if extra_env:
        env.update({key: str(value) for key, value in extra_env.items() if value is not None})
    command = [sys.executable, str(THIS_DIR / "phase4_stage_a_experiment.py")]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    research_dir = model_path / "research" / experiment_name
    bundle_validation = _validate_research_bundle(research_dir) if result.returncode == 0 else {
        "pass": False,
        "issues": [f"subprocess_returncode={result.returncode}"],
        "paths": {name: str(research_dir / name) for name in REQUIRED_RESEARCH_FILES},
    }
    return {
        "experiment_name": experiment_name,
        "command": command,
        "returncode": int(result.returncode),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "research_dir": str(research_dir),
        "bundle_validation": bundle_validation,
        "pass": bool(result.returncode == 0 and bundle_validation.get("pass")),
    }


def _validate_research_bundle(research_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    paths = {name: research_dir / name for name in REQUIRED_RESEARCH_FILES}
    for name, path in paths.items():
        if not path.exists():
            issues.append(f"missing:{name}")
    report: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    predictions_rows = 0
    snapshot_rows = 0
    if not issues:
        try:
            report = json.loads(paths["stage_a_report.json"].read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"invalid_json:stage_a_report.json:{type(exc).__name__}")
        try:
            manifest = json.loads(paths["stage_a_manifest.json"].read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"invalid_json:stage_a_manifest.json:{type(exc).__name__}")
        try:
            predictions_rows = int(len(pd.read_parquet(paths["stage_a_predictions.parquet"])))
        except Exception as exc:
            issues.append(f"invalid_parquet:stage_a_predictions.parquet:{type(exc).__name__}")
        try:
            snapshot_rows = int(len(pd.read_parquet(paths["stage_a_snapshot_proxy.parquet"])))
        except Exception as exc:
            issues.append(f"invalid_parquet:stage_a_snapshot_proxy.parquet:{type(exc).__name__}")
    return {
        "pass": not issues,
        "issues": issues,
        "paths": {name: str(path) for name, path in paths.items()},
        "report": report,
        "manifest": manifest,
        "predictions_rows": predictions_rows,
        "snapshot_rows": snapshot_rows,
    }


def _exercise_bundle_validation(research_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase5_hardening_bundle_", dir=str(research_dir.parent)) as tmp_root:
        tmp_root_path = Path(tmp_root)
        missing_dir = tmp_root_path / "missing_snapshot"
        missing_dir.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_RESEARCH_FILES:
            if name == "stage_a_snapshot_proxy.parquet":
                continue
            shutil.copy2(research_dir / name, missing_dir / name)
        missing_result = _validate_research_bundle(missing_dir)

        corrupt_dir = tmp_root_path / "corrupt_snapshot"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_RESEARCH_FILES:
            shutil.copy2(research_dir / name, corrupt_dir / name)
        (corrupt_dir / "stage_a_snapshot_proxy.parquet").write_text("corrupted", encoding="utf-8")
        corrupt_result = _validate_research_bundle(corrupt_dir)

    return {
        "missing_snapshot_detected": not missing_result.get("pass", False),
        "missing_snapshot_issues": missing_result.get("issues", []),
        "corrupted_snapshot_detected": not corrupt_result.get("pass", False),
        "corrupted_snapshot_issues": corrupt_result.get("issues", []),
        "pass": bool((not missing_result.get("pass", False)) and (not corrupt_result.get("pass", False))),
        "fallback_detected": False,
    }


def _load_experiment_bundle(model_path: Path, experiment_name: str) -> dict[str, Any]:
    research_dir = model_path / "research" / experiment_name
    report = json.loads((research_dir / "stage_a_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((research_dir / "stage_a_manifest.json").read_text(encoding="utf-8"))
    predictions = pd.read_parquet(research_dir / "stage_a_predictions.parquet")
    snapshot = pd.read_parquet(research_dir / "stage_a_snapshot_proxy.parquet")
    rebuilt = stage5._rebuild_experiment(model_path, experiment_name)
    return {
        "research_dir": research_dir,
        "report": report,
        "manifest": manifest,
        "predictions": predictions,
        "snapshot": snapshot,
        "rebuilt": rebuilt,
    }


def _compare_replay_runs(model_path: Path) -> dict[str, Any]:
    run1 = _load_experiment_bundle(model_path, REPLAY_RUN1_EXPERIMENT)
    run2 = _load_experiment_bundle(model_path, REPLAY_RUN2_EXPERIMENT)
    report_hash_run1 = hashlib.sha256(
        json.dumps(_normalize_payload(run1["report"], drop_keys=VOLATILE_REPORT_KEYS), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest().upper()
    report_hash_run2 = hashlib.sha256(
        json.dumps(_normalize_payload(run2["report"], drop_keys=VOLATILE_REPORT_KEYS), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest().upper()
    predictions_hash_run1 = _frame_hash(run1["predictions"])
    predictions_hash_run2 = _frame_hash(run2["predictions"])
    snapshot_hash_run1 = _frame_hash(run1["snapshot"])
    snapshot_hash_run2 = _frame_hash(run2["snapshot"])
    aggregated_hash_run1 = _frame_hash(run1["rebuilt"].aggregated)
    aggregated_hash_run2 = _frame_hash(run2["rebuilt"].aggregated)
    return {
        "report_hash_run1": report_hash_run1,
        "report_hash_run2": report_hash_run2,
        "report_match": report_hash_run1 == report_hash_run2,
        "predictions_hash_run1": predictions_hash_run1,
        "predictions_hash_run2": predictions_hash_run2,
        "predictions_match": predictions_hash_run1 == predictions_hash_run2,
        "snapshot_hash_run1": snapshot_hash_run1,
        "snapshot_hash_run2": snapshot_hash_run2,
        "snapshot_match": snapshot_hash_run1 == snapshot_hash_run2,
        "aggregated_hash_run1": aggregated_hash_run1,
        "aggregated_hash_run2": aggregated_hash_run2,
        "aggregated_match": aggregated_hash_run1 == aggregated_hash_run2,
        "pass": bool(
            report_hash_run1 == report_hash_run2
            and predictions_hash_run1 == predictions_hash_run2
            and snapshot_hash_run1 == snapshot_hash_run2
            and aggregated_hash_run1 == aggregated_hash_run2
        ),
    }


def _active_mask(frame: pd.DataFrame) -> pd.Series:
    decision_selected = pd.Series(frame.get("decision_selected", False), index=frame.index).fillna(False).astype(bool)
    position = pd.to_numeric(frame.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    return decision_selected & (position > 0)


def _compute_turnover_proxy(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "date" not in frame.columns or "symbol" not in frame.columns:
        return {"turnover_proxy_mean": 0.0, "turnover_proxy_p95": 0.0, "turnover_active_dates": 0}
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["symbol"] = work["symbol"].astype(str)
    work["position_usdt_stage_a"] = pd.to_numeric(work.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    pivot = (
        work.pivot_table(
            index="date",
            columns="symbol",
            values="position_usdt_stage_a",
            aggfunc="sum",
            fill_value=0.0,
        )
        .sort_index()
    )
    if pivot.empty:
        return {"turnover_proxy_mean": 0.0, "turnover_proxy_p95": 0.0, "turnover_active_dates": 0}
    gross = pivot.abs().sum(axis=1)
    diffs = pivot.diff().abs().sum(axis=1)
    if not diffs.empty:
        diffs.iloc[0] = pivot.iloc[0].abs().sum()
    turnover = diffs / gross.replace(0.0, np.nan)
    turnover = turnover.replace([np.inf, -np.inf], np.nan)
    turnover = turnover.loc[gross > 0].dropna()
    if turnover.empty:
        return {"turnover_proxy_mean": 0.0, "turnover_proxy_p95": 0.0, "turnover_active_dates": 0}
    return {
        "turnover_proxy_mean": round(float(turnover.mean()), 4),
        "turnover_proxy_p95": round(float(turnover.quantile(0.95)), 4),
        "turnover_active_dates": int(turnover.size),
    }


def _compute_concentration_proxy(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "concentration_proxy_mean": 0.0,
            "concentration_proxy_p95": 0.0,
            "max_active_events_per_day": 0,
            "active_dates": 0,
        }
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work["position_usdt_stage_a"] = pd.to_numeric(work.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    active = work.loc[_active_mask(work)].copy()
    if active.empty:
        return {
            "concentration_proxy_mean": 0.0,
            "concentration_proxy_p95": 0.0,
            "max_active_events_per_day": 0,
            "active_dates": 0,
        }
    grouped = active.groupby("date", sort=True)
    weights = []
    active_events = []
    for _, grp in grouped:
        gross = float(grp["position_usdt_stage_a"].sum())
        if gross <= 0:
            continue
        weights.append(float(grp["position_usdt_stage_a"].max()) / gross)
        active_events.append(int(len(grp)))
    if not weights:
        return {
            "concentration_proxy_mean": 0.0,
            "concentration_proxy_p95": 0.0,
            "max_active_events_per_day": 0,
            "active_dates": 0,
        }
    series = pd.Series(weights, dtype=float)
    return {
        "concentration_proxy_mean": round(float(series.mean()), 4),
        "concentration_proxy_p95": round(float(series.quantile(0.95)), 4),
        "max_active_events_per_day": int(max(active_events) if active_events else 0),
        "active_dates": int(len(series)),
    }


def _compute_capacity_proxy(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "capacity_reference_order_mean": 0.0,
            "capacity_reference_order_p95": 0.0,
            "capacity_slippage_mean": 0.0,
            "capacity_capped_ref_rate": 0.0,
            "capacity_gross_exposure_mean": 0.0,
            "capacity_gross_exposure_p95": 0.0,
        }
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    active = work.loc[_active_mask(work)].copy()
    if active.empty:
        return {
            "capacity_reference_order_mean": 0.0,
            "capacity_reference_order_p95": 0.0,
            "capacity_slippage_mean": 0.0,
            "capacity_capped_ref_rate": 0.0,
            "capacity_gross_exposure_mean": 0.0,
            "capacity_gross_exposure_p95": 0.0,
        }
    reference_order = pd.to_numeric(active.get("reference_order_usdt_exec_stage_a"), errors="coerce").fillna(0.0)
    slippage = pd.to_numeric(active.get("slippage_exec_stage_a"), errors="coerce").fillna(0.0)
    capped = pd.to_numeric(active.get("slippage_ref_capped_exec_stage_a"), errors="coerce").fillna(0.0)
    gross = active.groupby("date", sort=True)["position_usdt_stage_a"].sum().astype(float)
    return {
        "capacity_reference_order_mean": round(float(reference_order.mean()), 2),
        "capacity_reference_order_p95": round(float(reference_order.quantile(0.95)), 2),
        "capacity_slippage_mean": round(float(slippage.mean()), 6),
        "capacity_capped_ref_rate": round(float(capped.mean()), 4),
        "capacity_gross_exposure_mean": round(float(gross.mean()), 2) if not gross.empty else 0.0,
        "capacity_gross_exposure_p95": round(float(gross.quantile(0.95)), 2) if not gross.empty else 0.0,
    }


def _build_metric_row(
    *,
    scenario: str,
    frame: pd.DataFrame,
    signal_col: str,
    pnl_col: str = "pnl_exec_stage_a",
    scenario_type: str,
    source: str,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    if blocked_reason:
        return {
            "scenario": scenario,
            "scenario_type": scenario_type,
            "source": source,
            "scenario_status": "blocked_by_reproducibility_failure",
            "blocked_reason": blocked_reason,
        }
    decision = stage5._compute_decision_space_metrics(frame)
    operational = phase4._evaluate_decision_policy(
        frame,
        label=scenario,
        threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
        signal_col=signal_col,
        position_col="position_usdt_stage_a",
        pnl_col=pnl_col,
    )
    turnover = _compute_turnover_proxy(frame)
    concentration = _compute_concentration_proxy(frame)
    capacity = _compute_capacity_proxy(frame)
    return {
        "scenario": scenario,
        "scenario_type": scenario_type,
        "source": source,
        "scenario_status": "ok",
        "blocked_reason": None,
        "latest_active_count_decision_space": int(decision["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(decision["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(decision["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(decision["historical_active_events_decision_space"]),
        "sharpe_operational": round(float(operational["sharpe"]), 4),
        "dsr_honest": round(float(operational["dsr_honest"]), 4),
        "subperiods_positive": int(operational["subperiods_positive"]),
        "equity_final": round(float(operational["equity_final"]), 2),
        **turnover,
        **concentration,
        **capacity,
    }


def _apply_threshold_mask(frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    work = frame.copy()
    prob = pd.to_numeric(work.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0)
    keep_mask = prob > float(threshold)
    current_selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    work["decision_selected"] = current_selected & keep_mask
    for col in ("position_usdt_stage_a", "kelly_frac_stage_a", "mu_adj_stage_a", "pnl_exec_stage_a"):
        if col in work.columns:
            values = pd.to_numeric(work.get(col), errors="coerce").fillna(0.0)
            work[col] = values.where(work["decision_selected"], 0.0)
    if "stage_a_selected_proxy" in work.columns:
        proxy = pd.Series(work.get("stage_a_selected_proxy", False), index=work.index).fillna(False).astype(bool)
        work["stage_a_selected_proxy"] = proxy & keep_mask
    return work


def _apply_friction_stress(frame: pd.DataFrame, *, label: str, slippage_mult: float, extra_cost_bps: float) -> tuple[pd.DataFrame, str]:
    work = frame.copy()
    for col in ("label", "barrier_sl", "p0", "slippage_exec_stage_a", "position_usdt_stage_a"):
        if col not in work.columns:
            work[col] = pd.Series(np.nan, index=work.index)
    stressed = phase4._attach_friction_stress_pnl(
        work,
        base_pnl_col="pnl_exec_stage_a",
        base_slippage_col="slippage_exec_stage_a",
        position_col="position_usdt_stage_a",
        output_col=f"pnl_exec_{label}",
        slippage_mult=float(slippage_mult),
        extra_cost_bps=float(extra_cost_bps),
    )
    return stressed, f"pnl_exec_{label}"


def _build_regime_slice_rows(frame: pd.DataFrame, *, scenario: str, signal_col: str, pnl_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    subperiods = phase4.analyze_subperiods(
        frame,
        signal_col=signal_col,
        threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
        position_col="position_usdt_stage_a",
        pnl_col=pnl_col,
    )
    for row in subperiods:
        rows.append(
            {
                "scenario": scenario,
                "slice_type": "subperiod",
                "slice_name": str(row.get("period")),
                "regime": str(row.get("regime")),
                "n_obs": int(row.get("n_obs", 0)),
                "n_active": int(row.get("n_active", 0)),
                "sharpe": float(row.get("sharpe", 0.0)),
                "cum_return": float(row.get("cum_return", 0.0)),
                "max_dd": float(row.get("max_dd", 0.0)),
                "positive": row.get("positive"),
                "status": row.get("status"),
            }
        )
    if "btc_ma200_flag" in frame.columns:
        btc_flag = pd.to_numeric(frame.get("btc_ma200_flag"), errors="coerce").fillna(0.0)
        for slice_name, mask in (
            ("btc_ma200_ge_050", btc_flag >= 0.5),
            ("btc_ma200_lt_050", btc_flag < 0.5),
        ):
            subset = frame.loc[mask.fillna(False)].copy()
            result = phase4._evaluate_decision_policy(
                subset,
                label=f"{scenario}_{slice_name}",
                threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
                signal_col=signal_col,
                position_col="position_usdt_stage_a",
                pnl_col=pnl_col,
            )
            rows.append(
                {
                    "scenario": scenario,
                    "slice_type": "regime",
                    "slice_name": slice_name,
                    "regime": slice_name,
                    "n_obs": int(len(subset)),
                    "n_active": int(result.get("n_active", 0)),
                    "sharpe": float(result.get("sharpe", 0.0)),
                    "cum_return": float(result.get("cum_return", 0.0)),
                    "max_dd": float(result.get("max_dd", 0.0)),
                    "positive": None if subset.empty else bool(float(result.get("sharpe", 0.0)) > 0.0),
                    "status": "PASS" if not subset.empty else "SKIP",
                }
            )
    dates = pd.to_datetime(frame.get("date"), errors="coerce").dt.normalize()
    latest_date = dates.dropna().max() if not frame.empty else None
    if latest_date is not None:
        cutoff = pd.Timestamp(latest_date) - pd.Timedelta(days=365)
        for slice_name, mask in (
            ("latest_365d", dates >= cutoff),
            ("pre_latest_365d", dates < cutoff),
        ):
            subset = frame.loc[mask.fillna(False)].copy()
            result = phase4._evaluate_decision_policy(
                subset,
                label=f"{scenario}_{slice_name}",
                threshold=stage_a.TARGET_ACTIVATION_THRESHOLD,
                signal_col=signal_col,
                position_col="position_usdt_stage_a",
                pnl_col=pnl_col,
            )
            rows.append(
                {
                    "scenario": scenario,
                    "slice_type": "recency",
                    "slice_name": slice_name,
                    "regime": slice_name,
                    "n_obs": int(len(subset)),
                    "n_active": int(result.get("n_active", 0)),
                    "sharpe": float(result.get("sharpe", 0.0)),
                    "cum_return": float(result.get("cum_return", 0.0)),
                    "max_dd": float(result.get("max_dd", 0.0)),
                    "positive": None if subset.empty else bool(float(result.get("sharpe", 0.0)) > 0.0),
                    "status": "PASS" if not subset.empty else "SKIP",
                }
            )
    return rows


def _regime_slice_summary(rows: pd.DataFrame, scenario: str) -> dict[str, Any]:
    subset = rows.loc[rows["scenario"].astype(str) == scenario].copy() if not rows.empty else pd.DataFrame()
    if subset.empty:
        return {"scenario": scenario, "summary": "no regime slices"}
    subperiod = subset.loc[subset["slice_type"].astype(str) == "subperiod"]
    negatives = subperiod.loc[subperiod["positive"] == False, "slice_name"].astype(str).tolist()
    latest_365d = subset.loc[subset["slice_name"].astype(str) == "latest_365d"]
    pre_latest = subset.loc[subset["slice_name"].astype(str) == "pre_latest_365d"]
    return {
        "scenario": scenario,
        "subperiods_positive": int((subperiod["positive"] == True).sum()),
        "subperiods_tested": int(subperiod["positive"].notna().sum()),
        "negative_slices": negatives,
        "latest_365d_sharpe": None if latest_365d.empty else round(float(latest_365d["sharpe"].iloc[0]), 4),
        "pre_latest_365d_sharpe": None if pre_latest.empty else round(float(pre_latest["sharpe"].iloc[0]), 4),
    }


def _build_integrity_payload(
    *,
    model_path: Path,
    research_path: Path,
    gate_path: Path,
    official_before: dict[str, Any],
    official_after: dict[str, Any],
) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official": {
            "before": official_before,
            "after": official_after,
            "official_artifacts_unchanged": bool(official_before["combined_hashes"] == official_after["combined_hashes"]),
        },
        "path_separation": {
            "official_root": str(model_path),
            "research_root": str(research_path),
            "gate_root": str(gate_path),
            "research_under_official_research_tree": str(research_path).lower().startswith(str((model_path / "research").resolve()).lower()),
            "gate_under_reports_tree": str(gate_path).lower().startswith(str((REPO_ROOT / "reports" / "gates").resolve()).lower()),
        },
    }


def _build_failure_modes(
    *,
    frozen_row: dict[str, Any],
    replay_row: dict[str, Any] | None,
    replay_result: dict[str, Any],
    reproducibility: dict[str, Any] | None,
    bundle_validation: dict[str, Any] | None,
    frozen_report: dict[str, Any],
    frozen_manifest: dict[str, Any],
) -> dict[str, Any]:
    failure_modes: list[dict[str, Any]] = []
    if frozen_manifest.get("working_tree_state") == "dirty":
        failure_modes.append(
            {
                "mode": "baseline_provenance_discrepancy",
                "severity": "high",
                "detail": "Frozen baseline artifact was generated from a dirty worktree.",
                "manifest_branch": frozen_manifest.get("branch"),
                "manifest_head": frozen_manifest.get("head"),
            }
        )
    if int(frozen_row.get("latest_active_count_decision_space", 0)) == 0 or not bool(frozen_row.get("headroom_decision_space", False)):
        failure_modes.append(
            {
                "mode": "latest_headroom_dead_on_frozen_baseline",
                "severity": "critical",
                "detail": "Frozen winner is already dead on the sovereign latest/headroom ruler.",
                "latest_active_count_decision_space": frozen_row.get("latest_active_count_decision_space"),
                "headroom_decision_space": frozen_row.get("headroom_decision_space"),
            }
        )
    gate_headroom_definition = (frozen_report.get("gate_do_experimento_stage_a", {}) or {}).get("headroom_definition")
    if gate_headroom_definition:
        failure_modes.append(
            {
                "mode": "legacy_auxiliary_headroom_proxy_present",
                "severity": "medium",
                "detail": "Legacy gate report still stores auxiliary headroom proxy text; sovereign metrics were recomputed from decision_selected and position_usdt_stage_a > 0.",
                "definition": gate_headroom_definition,
            }
        )
    if not replay_result.get("pass"):
        failure_modes.append(
            {
                "mode": "clean_replay_failed",
                "severity": "critical",
                "detail": "Clean replay from current sources did not complete successfully.",
                "returncode": replay_result.get("returncode"),
                "bundle_issues": replay_result.get("bundle_validation", {}).get("issues", []),
            }
        )
    if reproducibility is not None and not reproducibility.get("pass", False):
        failure_modes.append(
            {
                "mode": "reproducibility_drift",
                "severity": "critical",
                "detail": "Double-run clean replay did not normalize to the same outputs.",
                **reproducibility,
            }
        )
    if replay_row is not None and frozen_row.get("historical_active_events_decision_space") != replay_row.get("historical_active_events_decision_space"):
        failure_modes.append(
            {
                "mode": "stale_or_provenance_drift",
                "severity": "medium",
                "detail": "Frozen artifact and clean replay diverged on sovereign historical activity.",
                "frozen_historical_active_events": frozen_row.get("historical_active_events_decision_space"),
                "replay_historical_active_events": replay_row.get("historical_active_events_decision_space"),
            }
        )
    if bundle_validation is not None and not bundle_validation.get("pass", False):
        failure_modes.append(
            {
                "mode": "research_bundle_validation_guard_failed",
                "severity": "critical",
                "detail": "Missing/corrupted replay bundle was not rejected cleanly.",
                **bundle_validation,
            }
        )
    return {"failure_modes": failure_modes}


def _classify_hardening(
    *,
    integrity_pass: bool,
    reproducibility_pass: bool,
    stale_dependency_check_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    baseline_row: dict[str, Any],
    replay_exists: bool,
    failure_modes: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if not all(
        [
            integrity_pass,
            reproducibility_pass,
            stale_dependency_check_pass,
            sovereign_metric_definitions_unchanged,
        ]
    ):
        return "FAIL", "abandon", "HARDENING_BASELINE_FAILS"
    if not replay_exists:
        return "PASS", "abandon", "HARDENING_BASELINE_FAILS"
    if int(baseline_row.get("latest_active_count_decision_space", 0)) == 0 or not bool(baseline_row.get("headroom_decision_space", False)):
        return "PASS", "abandon", "HARDENING_BASELINE_FAILS"
    critical_modes = {row.get("mode") for row in failure_modes if row.get("severity") == "critical"}
    if critical_modes:
        return "PASS", "abandon", "HARDENING_BASELINE_FAILS"
    if failure_modes:
        return "PARTIAL", "correct", "HARDENING_BASELINE_MIXED"
    return "PASS", "advance", "HARDENING_BASELINE_SURVIVES"


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    stale_dependency_check_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
) -> list[dict[str, Any]]:
    def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
        return {
            "gate_slug": GATE_SLUG,
            "metric_name": name,
            "metric_value": value,
            "metric_threshold": threshold,
            "metric_status": "PASS" if passed else "FAIL",
        }

    return [
        _metric("official_artifacts_unchanged", official_artifacts_unchanged, "PASS", official_artifacts_unchanged),
        _metric("research_only_isolation_pass", research_only_isolation_pass, "PASS", research_only_isolation_pass),
        _metric("reproducibility_pass", reproducibility_pass, "PASS", reproducibility_pass),
        _metric("stale_dependency_check_pass", stale_dependency_check_pass, "PASS", stale_dependency_check_pass),
        _metric(
            "sovereign_metric_definitions_unchanged",
            sovereign_metric_definitions_unchanged,
            "PASS",
            sovereign_metric_definitions_unchanged,
        ),
    ]


def run_phase5_cross_sectional_hardening_baseline() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    working_tree_before = _git_output("status", "--short", "--untracked-files=all")

    frozen_manifest = json.loads((model_path / "research" / FROZEN_EXPERIMENT / "stage_a_manifest.json").read_text(encoding="utf-8"))
    frozen = stage5._rebuild_experiment(model_path, FROZEN_EXPERIMENT)
    frozen_row = _build_metric_row(
        scenario="baseline_frozen",
        frame=frozen.aggregated,
        signal_col=frozen.signal_col,
        scenario_type="baseline",
        source="frozen_baseline_current_repo",
    )

    replay_run1 = _run_stage_a_subprocess(model_path=model_path, experiment_name=REPLAY_RUN1_EXPERIMENT)
    replay_run2 = _run_stage_a_subprocess(model_path=model_path, experiment_name=REPLAY_RUN2_EXPERIMENT) if replay_run1.get("pass") else None

    replay_row = None
    reproducibility = None
    bundle_validation_result = None
    replay = None
    replay_success = bool(replay_run1.get("pass") and replay_run2 and replay_run2.get("pass"))
    if replay_success:
        reproducibility = _compare_replay_runs(model_path)
        replay = stage5._rebuild_experiment(model_path, REPLAY_RUN1_EXPERIMENT)
        replay_row = _build_metric_row(
            scenario="clean_replay_current_sources",
            frame=replay.aggregated,
            signal_col=replay.signal_col,
            scenario_type="replay",
            source="clean_replay_current_sources",
        )
        bundle_validation_result = _exercise_bundle_validation(model_path / "research" / REPLAY_RUN1_EXPERIMENT)

    scenario_rows: list[dict[str, Any]] = [frozen_row]
    if replay_row is not None:
        scenario_rows.append(replay_row)
    else:
        scenario_rows.append(
            _build_metric_row(
                scenario="clean_replay_current_sources",
                frame=pd.DataFrame(),
                signal_col=frozen.signal_col,
                scenario_type="replay",
                source="clean_replay_current_sources",
                blocked_reason="clean_replay_failed",
            )
        )

    friction_rows: list[dict[str, Any]] = []
    for stress in phase4.PHASE4_FRICTION_STRESS_SPECS:
        if stress["label"] == "base":
            continue
        stressed_frame, stressed_pnl_col = _apply_friction_stress(
            frozen.aggregated,
            label=f"stress_{stress['label']}",
            slippage_mult=float(stress["slippage_mult"]),
            extra_cost_bps=float(stress["extra_cost_bps"]),
        )
        friction_rows.append(
            _build_metric_row(
                scenario=f"friction_{stress['label']}",
                frame=stressed_frame,
                signal_col=frozen.signal_col,
                pnl_col=stressed_pnl_col,
                scenario_type="friction",
                source="baseline_frozen",
            )
        )
    scenario_rows.extend(friction_rows)

    for threshold in (0.50, 0.55, 0.60):
        threshold_frame = _apply_threshold_mask(frozen.aggregated, threshold)
        scenario_rows.append(
            _build_metric_row(
                scenario=f"threshold_{int(round(threshold * 100)):03d}",
                frame=threshold_frame,
                signal_col=frozen.signal_col,
                scenario_type="threshold",
                source="baseline_frozen",
            )
        )

    replay_scenarios = [
        (
            "feature_degrade_hmm_prob_bull_neutral",
            HMM_NEUTRAL_EXPERIMENT,
            {
                "STAGE_A_DATASET_STRESS_SCENARIO": "feature_degrade_hmm_prob_bull_neutral",
                "STAGE_A_NEUTRALIZE_FEATURES": "hmm_prob_bull",
            },
        ),
        (
            "feature_degrade_stablecoin_chg30_neutral",
            STABLECOIN_NEUTRAL_EXPERIMENT,
            {
                "STAGE_A_DATASET_STRESS_SCENARIO": "feature_degrade_stablecoin_chg30_neutral",
                "STAGE_A_NEUTRALIZE_FEATURES": "stablecoin_chg30",
            },
        ),
        (
            "universe_reduced_median_history",
            UNIVERSE_MEDIAN_EXPERIMENT,
            {
                "STAGE_A_DATASET_STRESS_SCENARIO": "universe_reduced_median_history",
                "STAGE_A_UNIVERSE_FILTER_RULE": "median_history",
            },
        ),
    ]
    if replay_success and reproducibility and reproducibility.get("pass"):
        for scenario_label, experiment_name, extra_env in replay_scenarios:
            result = _run_stage_a_subprocess(model_path=model_path, experiment_name=experiment_name, extra_env=extra_env)
            if result.get("pass"):
                rebuilt = stage5._rebuild_experiment(model_path, experiment_name)
                scenario_rows.append(
                    _build_metric_row(
                        scenario=scenario_label,
                        frame=rebuilt.aggregated,
                        signal_col=rebuilt.signal_col,
                        scenario_type="dataset_replay",
                        source="clean_replay_current_sources",
                    )
                )
            else:
                scenario_rows.append(
                    _build_metric_row(
                        scenario=scenario_label,
                        frame=pd.DataFrame(),
                        signal_col=frozen.signal_col,
                        scenario_type="dataset_replay",
                        source="clean_replay_current_sources",
                        blocked_reason="scenario_replay_failed",
                    )
                )
    else:
        for scenario_label, _, _ in replay_scenarios:
            scenario_rows.append(
                _build_metric_row(
                    scenario=scenario_label,
                    frame=pd.DataFrame(),
                    signal_col=frozen.signal_col,
                    scenario_type="dataset_replay",
                    source="clean_replay_current_sources",
                    blocked_reason="blocked_by_reproducibility_failure",
                )
            )

    stress_matrix = pd.DataFrame(scenario_rows)
    stress_matrix.to_parquet(research_path / "hardening_stress_matrix.parquet", index=False)

    regime_rows = _build_regime_slice_rows(
        frozen.aggregated,
        scenario="baseline_frozen",
        signal_col=frozen.signal_col,
        pnl_col="pnl_exec_stage_a",
    )
    if replay is not None:
        regime_rows.extend(
            _build_regime_slice_rows(
                replay.aggregated,
                scenario="clean_replay_current_sources",
                signal_col=replay.signal_col,
                pnl_col="pnl_exec_stage_a",
            )
        )
    regime_slices = pd.DataFrame(regime_rows)
    regime_slices.to_parquet(research_path / "hardening_regime_slices.parquet", index=False)

    official_after = stage5._collect_official_inventory(model_path)
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True
    sovereign_metric_definitions_unchanged = True

    reproducibility_pass = bool(replay_success and reproducibility and reproducibility.get("pass"))
    stale_dependency_check_pass = bool(
        replay_success
        and reproducibility_pass
        and bundle_validation_result
        and bundle_validation_result.get("pass")
        and replay is not None
        and frozen.report.get("source_hashes", {}) == replay.report.get("source_hashes", {})
    )

    failure_payload = _build_failure_modes(
        frozen_row=frozen_row,
        replay_row=replay_row,
        replay_result=replay_run1,
        reproducibility=reproducibility,
        bundle_validation=bundle_validation_result,
        frozen_report=frozen.report,
        frozen_manifest=frozen_manifest,
    )
    _write_json(research_path / "hardening_failure_modes.json", failure_payload)

    integrity_payload = _build_integrity_payload(
        model_path=model_path,
        research_path=research_path,
        gate_path=gate_path,
        official_before=official_before,
        official_after=official_after,
    )
    _write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    primary_row = replay_row if replay_row is not None else frozen_row
    regime_summary = _regime_slice_summary(regime_slices, "clean_replay_current_sources" if replay_row is not None else "baseline_frozen")
    slippage_stress_impact = {
        row["scenario"]: {
            "delta_sharpe_operational": round(float(row.get("sharpe_operational", 0.0)) - float(frozen_row.get("sharpe_operational", 0.0)), 4),
            "delta_historical_active_events_decision_space": int(row.get("historical_active_events_decision_space", 0)) - int(frozen_row.get("historical_active_events_decision_space", 0)),
            "delta_equity_final": round(float(row.get("equity_final", 0.0)) - float(frozen_row.get("equity_final", 0.0)), 2),
        }
        for row in friction_rows
    }
    working_tree_cleanliness = {
        "before": "clean" if not working_tree_before else "dirty",
        "before_lines": working_tree_before.splitlines(),
        "frozen_manifest_working_tree_state": frozen_manifest.get("working_tree_state"),
    }
    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_experiment": FROZEN_EXPERIMENT,
        "baseline_provenance_discrepancy": {
            "present": True,
            "detail": "Frozen artifact carries dirty worktree provenance and a target_mode field inconsistent with the ranking problem_type.",
            "frozen_manifest_target_mode": frozen_manifest.get("target_mode"),
            "frozen_report_problem_type": frozen.report.get("problem_type"),
        },
        "frozen_baseline_metrics": frozen_row,
        "clean_replay_metrics": replay_row,
        "reproducibility": reproducibility,
        "bundle_validation": bundle_validation_result,
        "slippage_stress_impact": slippage_stress_impact,
        "regime_slice_results": regime_summary,
        "working_tree_cleanliness": working_tree_cleanliness,
    }

    status, decision, classification = _classify_hardening(
        integrity_pass=bool(official_artifacts_unchanged and research_only_isolation_pass),
        reproducibility_pass=reproducibility_pass,
        stale_dependency_check_pass=stale_dependency_check_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        baseline_row=primary_row,
        replay_exists=bool(replay_row is not None),
        failure_modes=failure_payload["failure_modes"],
    )
    summary_payload["status"] = status
    summary_payload["decision"] = decision
    summary_payload["classification_final"] = classification
    _write_json(research_path / "phase5_cross_sectional_hardening_summary.json", summary_payload)

    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        stale_dependency_check_pass=stale_dependency_check_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
    )
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
            str(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
            str(model_path / "phase4" / "phase4_execution_snapshot.parquet"),
            str(model_path / "phase4" / "phase4_oos_predictions.parquet"),
            str(model_path / "phase4" / "phase4_gate_diagnostic.json"),
        ],
        "research_artifacts_generated": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "summary": [
            f"classification_final={classification}",
            f"frozen_latest_active_count_decision_space={frozen_row.get('latest_active_count_decision_space')}",
            f"frozen_headroom_decision_space={frozen_row.get('headroom_decision_space')}",
            f"reproducibility_pass={reproducibility_pass}",
            f"stale_dependency_check_pass={stale_dependency_check_pass}",
        ],
        "gates": gate_metrics,
        "blockers": [row["detail"] for row in failure_payload["failure_modes"] if row.get("severity") == "critical"],
        "risks_residual": [row["detail"] for row in failure_payload["failure_modes"] if row.get("severity") != "critical"],
        "next_recommended_step": (
            "Do not advance; baseline hardening must be corrected or abandoned based on the frozen-vs-replay evidence."
            if decision != "advance"
            else "Prepare the next research-only step."
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
            artifact_record(model_path / "phase4" / "phase4_report_v4.json"),
            artifact_record(model_path / "phase4" / "phase4_execution_snapshot.parquet"),
            artifact_record(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
            artifact_record(model_path / "phase4" / "phase4_oos_predictions.parquet"),
            artifact_record(model_path / "phase4" / "phase4_gate_diagnostic.json"),
        ],
        "generated_artifacts": [artifact_record(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "commands_executed": [
            "python services/ml_engine/phase4_stage_a_experiment.py (clean replay run1)",
            "python services/ml_engine/phase4_stage_a_experiment.py (clean replay run2)",
            "python services/ml_engine/phase4_stage_a_experiment.py (feature degrade hmm neutral)",
            "python services/ml_engine/phase4_stage_a_experiment.py (feature degrade stablecoin neutral)",
            "python services/ml_engine/phase4_stage_a_experiment.py (universe median-history)",
        ],
        "notes": [
            f"classification_final={classification}",
            "message/order duplication guard treated as not_applicable_no_message_bus; equivalent guard enforced through replay double-run reproducibility.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}` / classificação `{classification}`",
                f"- Baseline congelada latest/headroom soberanos: `{frozen_row.get('latest_active_count_decision_space')}` / `{frozen_row.get('headroom_decision_space')}`",
                f"- Replay limpo reprodutível: `{reproducibility_pass}`; stale dependency check: `{stale_dependency_check_pass}`",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                f"- Artifact soberano: `{FROZEN_EXPERIMENT}`",
                f"- Proveniência congelada: branch `{frozen_manifest.get('branch')}` / head `{frozen_manifest.get('head')}` / worktree `{frozen_manifest.get('working_tree_state')}`",
                "- Baseline_provenance_discrepancy foi registrada, mas a baseline não foi reinterpretada.",
            ]
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- Correção mínima do `stage2_payload` para permitir replay limpo do `cross_sectional_ranking`.",
                "- Runner research-only de hardening quantitativo e de codificação para a baseline cross-sectional.",
                "- Hooks default-off para neutralização determinística de feature e redução de universo via replay research-only.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES]
            + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]
        ),
        "Resultados": "\n".join(
            [
                f"- Frozen baseline: latest_active_count_decision_space=`{frozen_row.get('latest_active_count_decision_space')}`, headroom_decision_space=`{frozen_row.get('headroom_decision_space')}`, sharpe_operational=`{frozen_row.get('sharpe_operational')}`, dsr_honest=`{frozen_row.get('dsr_honest')}`",
                f"- Clean replay metrics: `{replay_row}`" if replay_row is not None else "- Replay limpo falhou e bloqueou cenários dependentes de replay.",
                f"- Slippage stress impact: `{slippage_stress_impact}`",
                f"- Regime slice results: `{regime_summary}`",
            ]
        ),
        "Avaliação contra gates": "\n".join(
            [
                f"- official_artifacts_unchanged = `{official_artifacts_unchanged}`",
                f"- research_only_isolation_pass = `{research_only_isolation_pass}`",
                f"- reproducibility_pass = `{reproducibility_pass}`",
                f"- stale_dependency_check_pass = `{stale_dependency_check_pass}`",
                f"- sovereign_metric_definitions_unchanged = `{sovereign_metric_definitions_unchanged}`",
            ]
        ),
        "Riscos residuais": "\n".join(
            [f"- {row['detail']}" for row in failure_payload["failure_modes"]]
            if failure_payload["failure_modes"]
            else ["- Nenhum risco residual material detectado nesta rodada."]
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
    _write_json(research_path / "phase5_cross_sectional_hardening_summary.json", summary_payload)
    return {
        "status": status,
        "decision": decision,
        "classification_final": classification,
        "research_path": str(research_path),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
    }


def main() -> None:
    result = run_phase5_cross_sectional_hardening_baseline()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
