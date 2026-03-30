#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score

THIS_FILE = Path(__file__).resolve()
THIS_DIR = THIS_FILE.parent
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import phase4_cpcv as phase4

EXPERIMENT_NAME = os.environ.get("STAGE_A_EXPERIMENT_NAME", "phase4_stage_a_experiment").strip() or "phase4_stage_a_experiment"
REFERENCE_EXPERIMENT_NAME = os.environ.get("STAGE_A_REFERENCE_EXPERIMENT_NAME", "phase4_stage_a_experiment").strip() or "phase4_stage_a_experiment"
BASELINE_EXPERIMENT_NAMES = [
    name.strip()
    for name in os.environ.get(
        "STAGE_A_BASELINE_EXPERIMENT_NAMES",
        "phase4_stage_a_experiment,phase4_stage_a_experiment_v2,phase4_stage_a_experiment_v3,phase4_stage_a_experiment_v4",
    ).split(",")
    if name.strip()
]
PROBLEM_TYPE = os.environ.get("STAGE_A_PROBLEM_TYPE", "binary_classification").strip().lower() or "binary_classification"
TARGET_MODE = os.environ.get("STAGE_A_TARGET_MODE", "sl_mult").strip().lower() or "sl_mult"
TARGET_SL_MULT = float(os.environ.get("STAGE_A_TARGET_SL_MULT", "1.0"))
TARGET_CLUSTER_Q = float(os.environ.get("STAGE_A_TARGET_CLUSTER_Q", "0.60"))
TARGET_Q_CANDIDATES = tuple(
    float(item.strip())
    for item in os.environ.get("STAGE_A_Q_CANDIDATES", "0.40,0.50,0.60").split(",")
    if item.strip()
)
PRIMARY_Q = float(os.environ.get("STAGE_A_PRIMARY_Q", "0.60"))
MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER = int(os.environ.get("STAGE_A_MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER", "100"))
MIN_ELIGIBLE_PER_DATE_CLUSTER = int(os.environ.get("STAGE_A_MIN_ELIGIBLE_PER_DATE_CLUSTER", "2"))
MIN_STAGE2_TRAIN_ROWS = int(os.environ.get("STAGE_A_MIN_STAGE2_TRAIN_ROWS", "25"))
BASELINE_HISTORICAL_ACTIVE = 60
MIN_ALLOC_FRAC = 0.001
TARGET_FALLBACK_POLICY = "global_positive_q_train_fallback"
TARGET_GROUP_FALLBACK_POLICY = "date_universe_top1_when_cluster_support_lt_min"
TARGET_ACTIVATION_THRESHOLD = 0.50


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _combined_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.as_posix().encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def _resolve_model_path() -> Path:
    env_override = str(os.environ.get("SNIPER_MODEL_PATH", "")).strip()
    if env_override:
        return Path(env_override).resolve()
    docker_path = Path("/data/models")
    if docker_path.exists():
        return docker_path
    return (REPO_ROOT / "data" / "models").resolve()


def _configure_phase4_paths(model_path: Path) -> None:
    phase4.MODEL_PATH = model_path
    phase4.FEATURES_PATH = model_path / "features"
    phase4.PHASE3_PATH = model_path / "phase3"
    phase4.OUTPUT_PATH = model_path / "phase4"
    phase4.VI_CLUSTER_ASSET_PATHS = [
        model_path / "global_vi_clusters.json",
        model_path / "vi_asset_clusters.json",
        model_path / "calibration" / "global_vi_clusters.json",
    ]


def _research_path(model_path: Path) -> Path:
    return model_path / "research" / EXPERIMENT_NAME


def _target_name() -> str:
    if PROBLEM_TYPE == "cross_sectional_ranking":
        return "cross_sectional_relative_rank_score"
    if TARGET_MODE == "two_stage_activation_utility":
        return f"two_stage_activation_utility_q{int(round(PRIMARY_Q * 100)):02d}"
    if TARGET_MODE == "cross_sectional_relative_activation":
        return "cross_sectional_relative_activation_binary"
    if TARGET_MODE == "cluster_local_q_positive":
        return "cluster_local_actionable_edge_binary"
    if np.isclose(TARGET_SL_MULT, 1.0):
        return "cost_adjusted_edge_binary"
    if np.isclose(TARGET_SL_MULT, 2.0):
        return "strong_cost_adjusted_edge_binary"
    return f"cost_adjusted_edge_binary_x{TARGET_SL_MULT:g}"


def _target_definition() -> str:
    if PROBLEM_TYPE == "cross_sectional_ranking":
        return (
            "rank_target_stage_a = pnl_real / avg_sl_train for eligible = (pnl_real > avg_sl_train); "
            "proxy selection = top1(rank_score_stage_a) within (date, cluster_name) among eligible rows; "
            "fallback to top1(date-universe) when eligible_count(date, cluster_name) < "
            f"{MIN_ELIGIBLE_PER_DATE_CLUSTER}"
        )
    if TARGET_MODE == "two_stage_activation_utility":
        return (
            "stage1: y_activate = 1[u_real >= Q"
            f"{int(round(PRIMARY_Q * 100)):02d}_train(u_real | cluster_name, u_real > 1)] "
            "with u_real = pnl_real / avg_sl_train and global fallback when train support is sparse; "
            "stage2: regress utility_surplus = max(u_real - threshold_train, 0) on activated-train rows only; "
            "decision proxy = activated rows with p_activate_calibrated > 0.50 ranked by predicted utility_surplus, "
            "top1 within (date, cluster_name), fallback to top1(date-universe) when activated_count(date, cluster_name) < "
            f"{MIN_ELIGIBLE_PER_DATE_CLUSTER}"
        )
    if TARGET_MODE == "cross_sectional_relative_activation":
        return (
            "y_stage_a = 1[top1(score_realized = pnl_real / avg_sl_train) within "
            "(date, cluster_name) among eligible = (pnl_real > avg_sl_train); "
            "fallback to top1(date-universe) when eligible_count(date, cluster_name) < "
            f"{MIN_ELIGIBLE_PER_DATE_CLUSTER}]"
        )
    if TARGET_MODE == "cluster_local_q_positive":
        return (
            f"y_stage_a = 1[(pnl_real > avg_sl_train) and "
            f"(pnl_real >= Q{int(TARGET_CLUSTER_Q * 100):02d}_train("
            "pnl_real | cluster_name, pnl_real > 0))]"
        )
    return f"y_stage_a = 1[pnl_real > {TARGET_SL_MULT:g} * avg_sl_train]"


def _build_cross_sectional_relative_target(
    df: pd.DataFrame,
    *,
    min_eligible_per_date_cluster: int = MIN_ELIGIBLE_PER_DATE_CLUSTER,
) -> tuple[pd.DataFrame, dict]:
    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")

    pnl_real = pd.to_numeric(work.get("pnl_real"), errors="coerce")
    avg_sl_train = pd.to_numeric(work.get("avg_sl_train"), errors="coerce")
    score_realized = pnl_real / avg_sl_train.replace(0, np.nan)
    eligible = (pnl_real > avg_sl_train) & pnl_real.notna() & avg_sl_train.notna() & (avg_sl_train > 0)

    work["stage_a_eligible"] = eligible
    work["stage_a_score_realized"] = score_realized.where(eligible, np.nan)
    work["stage_a_group_eligible_count"] = 0
    work["stage_a_date_eligible_count"] = 0
    work["stage_a_selection_mode"] = "no_eligible"
    work["stage_a_selected_local"] = False
    work["stage_a_selected_fallback"] = False

    eligible_df = work.loc[eligible].copy()
    date_top_idx: dict[pd.Timestamp, int] = {}
    date_eligible_counts: dict[pd.Timestamp, int] = {}
    eligible_candidates_per_date: list[dict] = []

    for date, grp in eligible_df.groupby("date", sort=True):
        ranked = grp.sort_values(
            ["stage_a_score_realized", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        )
        date_top_idx[pd.Timestamp(date)] = int(ranked.index[0])
        date_eligible_counts[pd.Timestamp(date)] = int(len(ranked))
        eligible_candidates_per_date.append(
            {
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "eligible_candidates": int(len(ranked)),
            }
        )

    work["stage_a_date_eligible_count"] = work["date"].map(date_eligible_counts).fillna(0).astype(int)

    target = pd.Series(0, index=work.index, dtype=int)
    group_rows: list[dict] = []

    for (date, cluster_name), grp in work.groupby(["date", "cluster_name"], sort=True):
        eligible_grp = grp.loc[grp["stage_a_eligible"].fillna(False)]
        eligible_count = int(len(eligible_grp))
        work.loc[grp.index, "stage_a_group_eligible_count"] = eligible_count

        if eligible_count >= int(min_eligible_per_date_cluster):
            ranked = eligible_grp.sort_values(
                ["stage_a_score_realized", "symbol"],
                ascending=[False, True],
                kind="mergesort",
            )
            top_idx = int(ranked.index[0])
            target.loc[top_idx] = 1
            work.loc[grp.index, "stage_a_selection_mode"] = "cluster_local_top1"
            work.loc[top_idx, "stage_a_selected_local"] = True
            selection_mode = "cluster_local_top1"
        elif eligible_count > 0:
            top_idx = date_top_idx.get(pd.Timestamp(date))
            if top_idx is not None:
                target.loc[top_idx] = 1
                work.loc[top_idx, "stage_a_selected_fallback"] = True
            work.loc[grp.index, "stage_a_selection_mode"] = "date_universe_fallback"
            selection_mode = "date_universe_fallback"
        else:
            work.loc[grp.index, "stage_a_selection_mode"] = "no_eligible"
            selection_mode = "no_eligible"

        group_rows.append(
            {
                "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                "cluster_name": str(cluster_name),
                "eligible_count": eligible_count,
                "selection_mode": selection_mode,
            }
        )

    work["y_stage_a"] = target.astype(int)
    work["stage_a_target_source"] = np.select(
        [
            work["stage_a_selected_local"] & work["stage_a_selected_fallback"],
            work["stage_a_selected_local"],
            work["stage_a_selected_fallback"],
            work["stage_a_eligible"],
        ],
        [
            "local_and_fallback_selected",
            "cluster_local_top1_selected",
            "date_universe_fallback_selected",
            "eligible_not_selected",
        ],
        default="no_eligible",
    )

    target_positive_count_per_date = (
        work.assign(_date_str=work["date"].dt.strftime("%Y-%m-%d"))
        .groupby("_date_str", sort=True)["y_stage_a"]
        .sum()
        .reset_index()
        .rename(columns={"_date_str": "date", "y_stage_a": "target_positive_count"})
        .to_dict(orient="records")
    )

    group_df = pd.DataFrame(group_rows)
    summary = {
        "target_mode": TARGET_MODE,
        "min_eligible_per_date_cluster": int(min_eligible_per_date_cluster),
        "fallback_policy": TARGET_GROUP_FALLBACK_POLICY,
        "support_rationale": (
            "Top1 within a (date, cluster_name) group of size 1 is tautological. "
            f"Requiring at least {int(min_eligible_per_date_cluster)} eligible candidates preserves "
            "local competition before falling back to the date-universe winner."
        ),
        "groups_local_target": int((group_df["selection_mode"] == "cluster_local_top1").sum()),
        "groups_fallback_target": int((group_df["selection_mode"] == "date_universe_fallback").sum()),
        "groups_without_eligible": int((group_df["selection_mode"] == "no_eligible").sum()),
        "groups_total": int(len(group_df)),
        "eligible_candidates_per_date": eligible_candidates_per_date,
        "target_positive_count_per_date": target_positive_count_per_date,
    }
    return work, summary


def _build_cross_sectional_ranking_frame(
    df: pd.DataFrame,
    *,
    min_eligible_per_date_cluster: int = MIN_ELIGIBLE_PER_DATE_CLUSTER,
) -> tuple[pd.DataFrame, dict]:
    work, summary = _build_cross_sectional_relative_target(
        df,
        min_eligible_per_date_cluster=min_eligible_per_date_cluster,
    )
    work = work.copy()
    work["y_stage_a_truth_top1"] = pd.to_numeric(work["y_stage_a"], errors="coerce").fillna(0).astype(int)
    work["rank_target_stage_a"] = pd.to_numeric(work["stage_a_score_realized"], errors="coerce").fillna(0.0)
    return work, summary


def _compute_stage_a_utility_real(df: pd.DataFrame) -> pd.Series:
    pnl_source = df.get("pnl_real", pd.Series(np.nan, index=df.index))
    sl_source = df.get("avg_sl_train", pd.Series(np.nan, index=df.index))
    pnl_real = pd.to_numeric(pnl_source, errors="coerce")
    avg_sl_train = pd.to_numeric(sl_source, errors="coerce")
    if not isinstance(pnl_real, pd.Series):
        pnl_real = pd.Series(pnl_real, index=df.index)
    if not isinstance(avg_sl_train, pd.Series):
        avg_sl_train = pd.Series(avg_sl_train, index=df.index)
    avg_sl_train = avg_sl_train.replace(0, np.nan)
    utility_real = pnl_real / avg_sl_train
    return utility_real.where(pnl_real.notna() & avg_sl_train.notna() & (avg_sl_train > 0))


def _compute_two_stage_activation_thresholds(
    train_df: pd.DataFrame,
    *,
    quantile: float = PRIMARY_Q,
    min_positive_count_per_cluster: int = MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER,
) -> tuple[dict[str, dict[str, float | int | str]], dict]:
    work = train_df.copy()
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")
    work["stage_a_utility_real"] = _compute_stage_a_utility_real(work)
    eligible_train = work.loc[work["stage_a_utility_real"] > 1.0].copy()

    global_positive_count = int(len(eligible_train))
    if global_positive_count > 0:
        global_threshold = float(eligible_train["stage_a_utility_real"].quantile(quantile))
    else:
        global_threshold = float("inf")

    cluster_thresholds: dict[str, dict[str, float | int | str]] = {}
    cluster_rows: list[dict] = []

    for cluster_name in sorted(work["cluster_name"].dropna().astype(str).unique().tolist()):
        cluster_eligible = eligible_train.loc[
            eligible_train["cluster_name"].astype(str) == cluster_name,
            "stage_a_utility_real",
        ].dropna()
        train_positive_count = int(len(cluster_eligible))
        if train_positive_count >= int(min_positive_count_per_cluster):
            threshold = float(cluster_eligible.quantile(quantile))
            threshold_source = "cluster_local_q_train_positive"
        else:
            threshold = global_threshold
            threshold_source = TARGET_FALLBACK_POLICY
        row = {
            "cluster_name": cluster_name,
            "train_positive_count_cluster": train_positive_count,
            "train_positive_count_global": global_positive_count,
            "threshold_train": None if not np.isfinite(threshold) else round(float(threshold), 6),
            "threshold_source": threshold_source,
        }
        cluster_rows.append(row)
        cluster_thresholds[cluster_name] = row

    summary = {
        "target_mode": TARGET_MODE,
        "primary_q": round(float(quantile), 4),
        "min_train_positive_count_per_cluster": int(min_positive_count_per_cluster),
        "fallback_policy": TARGET_FALLBACK_POLICY,
        "train_positive_count_global": global_positive_count,
        "global_threshold_train": None if not np.isfinite(global_threshold) else round(float(global_threshold), 6),
        "cluster_rows": cluster_rows,
        "activation_definition": "y_activate = 1[u_real >= threshold_train(cluster)] with u_real = pnl_real / avg_sl_train",
        "stage2_training_policy": "activated_train_subset_only",
    }
    return cluster_thresholds, summary


def _attach_two_stage_target_metadata(
    df: pd.DataFrame,
    *,
    cluster_thresholds: dict[str, dict[str, float | int | str]] | None,
    threshold_summary: dict | None,
) -> pd.DataFrame:
    work = df.copy()
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")
    work["stage_a_utility_real"] = _compute_stage_a_utility_real(work)
    cluster_thresholds = cluster_thresholds or {}
    threshold_summary = threshold_summary or {}
    global_threshold = threshold_summary.get("global_threshold_train")
    global_positive_count = int(threshold_summary.get("train_positive_count_global", 0))
    fallback_threshold = float(global_threshold) if global_threshold is not None else float("inf")

    def _meta_for_cluster(cluster_name: str) -> dict[str, float | int | str]:
        meta = cluster_thresholds.get(cluster_name)
        if meta is None:
            return {
                "cluster_name": cluster_name,
                "train_positive_count_cluster": 0,
                "train_positive_count_global": global_positive_count,
                "threshold_train": None if not np.isfinite(fallback_threshold) else round(float(fallback_threshold), 6),
                "threshold_source": TARGET_FALLBACK_POLICY,
            }
        return meta

    mapped = work["cluster_name"].map(lambda cluster: _meta_for_cluster(str(cluster)))
    work["stage_a_threshold_train"] = pd.to_numeric(mapped.map(lambda meta: meta.get("threshold_train")), errors="coerce")
    work["stage_a_threshold_source"] = mapped.map(lambda meta: meta.get("threshold_source"))
    work["stage_a_train_positive_count_cluster"] = mapped.map(lambda meta: int(meta.get("train_positive_count_cluster", 0)))
    work["stage_a_train_positive_count_global"] = mapped.map(lambda meta: int(meta.get("train_positive_count_global", 0)))
    work["stage_a_positive_support"] = work["stage_a_utility_real"] > 1.0
    work["stage_a_eligible"] = (
        work["stage_a_utility_real"].notna()
        & work["stage_a_threshold_train"].notna()
        & (work["stage_a_utility_real"] >= work["stage_a_threshold_train"])
    )
    work["stage_a_score_realized"] = work["stage_a_utility_real"]
    work["stage_a_utility_surplus"] = (
        work["stage_a_utility_real"] - work["stage_a_threshold_train"]
    ).clip(lower=0.0)
    work["y_stage_a"] = work["stage_a_eligible"].astype(int)
    work["stage_a_target_source"] = np.where(
        work["stage_a_eligible"],
        "two_stage_activation_target",
        "below_cluster_threshold",
    )
    return work


def _build_stage2_training_payload(
    train_df: pd.DataFrame,
    X_tr,
    w_tr,
    *,
    min_rows: int = MIN_STAGE2_TRAIN_ROWS,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None, dict]:
    activated_mask = pd.to_numeric(train_df.get("y_stage_a"), errors="coerce").fillna(0).astype(int) == 1
    utility_surplus = pd.to_numeric(train_df.get("stage_a_utility_surplus"), errors="coerce").fillna(0.0)
    selected_idx = np.where(activated_mask.to_numpy(dtype=bool))[0]
    payload = {
        "stage2_training_policy": "activated_train_subset_only",
        "train_rows_total": int(len(train_df)),
        "train_rows_stage2": int(len(selected_idx)),
        "min_rows_required": int(min_rows),
        "all_zero_target": False,
        "is_valid": False,
        "reason": "insufficient_support",
    }
    if len(selected_idx) < int(min_rows):
        return None, None, None, payload
    y_stage2 = utility_surplus.iloc[selected_idx].to_numpy(dtype=float, copy=True)
    if len(y_stage2) == 0 or np.allclose(y_stage2, 0.0):
        payload["all_zero_target"] = True
        payload["reason"] = "all_zero_stage2_target"
        return None, None, None, payload
    payload["is_valid"] = True
    payload["reason"] = "ok"
    return X_tr[selected_idx], y_stage2, np.asarray(w_tr, dtype=float)[selected_idx], payload


def _compute_cluster_local_target_thresholds(
    train_df: pd.DataFrame,
    *,
    quantile: float = TARGET_CLUSTER_Q,
    min_positive_count_per_cluster: int = MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER,
) -> tuple[dict[str, dict[str, float | int | str]], dict]:
    work = train_df.copy()
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")
    pnl_real = pd.to_numeric(work.get("pnl_real"), errors="coerce")
    positive_train = work.loc[pnl_real > 0].copy()
    positive_train["pnl_real"] = pd.to_numeric(positive_train.get("pnl_real"), errors="coerce")

    global_positive_count = int(len(positive_train))
    if global_positive_count > 0:
        global_threshold = float(positive_train["pnl_real"].quantile(quantile))
    else:
        global_threshold = float("inf")

    cluster_thresholds: dict[str, dict[str, float | int | str]] = {}
    cluster_rows: list[dict] = []

    for cluster_name in sorted(work["cluster_name"].dropna().astype(str).unique().tolist()):
        cluster_positive = positive_train.loc[positive_train["cluster_name"].astype(str) == cluster_name, "pnl_real"].dropna()
        train_positive_count = int(len(cluster_positive))
        if train_positive_count >= int(min_positive_count_per_cluster):
            threshold = float(cluster_positive.quantile(quantile))
            threshold_source = "cluster_local_q_train_positive"
        else:
            threshold = global_threshold
            threshold_source = TARGET_FALLBACK_POLICY
        row = {
            "cluster_name": cluster_name,
            "train_positive_count_cluster": train_positive_count,
            "train_positive_count_global": global_positive_count,
            "threshold_train": None if not np.isfinite(threshold) else round(float(threshold), 6),
            "threshold_source": threshold_source,
        }
        cluster_rows.append(row)
        cluster_thresholds[cluster_name] = row

    summary = {
        "target_mode": TARGET_MODE,
        "quantile_train_positive": round(float(quantile), 4),
        "min_train_positive_count_per_cluster": int(min_positive_count_per_cluster),
        "fallback_policy": TARGET_FALLBACK_POLICY,
        "train_positive_count_global": global_positive_count,
        "global_threshold_train": None if not np.isfinite(global_threshold) else round(float(global_threshold), 6),
        "cluster_rows": cluster_rows,
    }
    return cluster_thresholds, summary


def _attach_stage_a_target_metadata(
    df: pd.DataFrame,
    *,
    cluster_thresholds: dict[str, dict[str, float | int | str]] | None = None,
    threshold_summary: dict | None = None,
) -> pd.DataFrame:
    work = df.copy()
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")
    if TARGET_MODE != "cluster_local_q_positive":
        return work

    cluster_thresholds = cluster_thresholds or {}
    threshold_summary = threshold_summary or {}
    global_threshold = threshold_summary.get("global_threshold_train")
    global_positive_count = int(threshold_summary.get("train_positive_count_global", 0))
    fallback_threshold = float(global_threshold) if global_threshold is not None else float("inf")

    def _meta_for_cluster(cluster_name: str) -> dict[str, float | int | str]:
        meta = cluster_thresholds.get(cluster_name)
        if meta is None:
            return {
                "cluster_name": cluster_name,
                "train_positive_count_cluster": 0,
                "train_positive_count_global": global_positive_count,
                "threshold_train": None if not np.isfinite(fallback_threshold) else round(float(fallback_threshold), 6),
                "threshold_source": TARGET_FALLBACK_POLICY,
            }
        return meta

    mapped = work["cluster_name"].map(lambda cluster: _meta_for_cluster(str(cluster)))
    work["stage_a_threshold_train"] = mapped.map(lambda meta: meta.get("threshold_train"))
    work["stage_a_threshold_source"] = mapped.map(lambda meta: meta.get("threshold_source"))
    work["stage_a_train_positive_count_cluster"] = mapped.map(lambda meta: int(meta.get("train_positive_count_cluster", 0)))
    work["stage_a_train_positive_count_global"] = mapped.map(lambda meta: int(meta.get("train_positive_count_global", 0)))
    return work


def _build_stage_a_target(df: pd.DataFrame) -> pd.Series:
    pnl_real = pd.to_numeric(df.get("pnl_real"), errors="coerce")
    avg_sl_train = pd.to_numeric(df.get("avg_sl_train"), errors="coerce")
    if TARGET_MODE == "two_stage_activation_utility":
        utility_real = pd.to_numeric(df.get("stage_a_utility_real"), errors="coerce")
        threshold_train = pd.to_numeric(df.get("stage_a_threshold_train"), errors="coerce")
        target = utility_real.notna() & threshold_train.notna() & (utility_real >= threshold_train)
        return target.astype(int)
    if TARGET_MODE == "cross_sectional_relative_activation":
        selected = pd.to_numeric(df.get("y_stage_a"), errors="coerce")
        if selected.notna().any():
            return selected.fillna(0).astype(int)
        selected_local = pd.Series(df.get("stage_a_selected_local", False), index=df.index).fillna(False)
        selected_fallback = pd.Series(df.get("stage_a_selected_fallback", False), index=df.index).fillna(False)
        return (selected_local | selected_fallback).astype(int)
    if TARGET_MODE == "cluster_local_q_positive":
        threshold_train = pd.to_numeric(df.get("stage_a_threshold_train"), errors="coerce")
        target = (
            (pnl_real > avg_sl_train)
            & (pnl_real >= threshold_train)
            & pnl_real.notna()
            & avg_sl_train.notna()
            & threshold_train.notna()
        )
    else:
        target = (pnl_real > (TARGET_SL_MULT * avg_sl_train)) & pnl_real.notna() & avg_sl_train.notna()
    return target.astype(int)


def _coerce_float(value, default: float = 0.0) -> float:
    coerced = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(default if pd.isna(coerced) else coerced)


def _train_stage_a_ranker(X_tr, y_tr, w_tr, n_eff):
    try:
        from lightgbm import LGBMRegressor

        has_lgbm = True
    except ImportError:
        has_lgbm = False

    if n_eff < 60 or not has_lgbm:
        model = Ridge(alpha=1.0)
        model.fit(X_tr, y_tr, sample_weight=w_tr)
        return model

    if n_eff < 120:
        model = LGBMRegressor(
            n_estimators=100,
            max_depth=2,
            learning_rate=0.05,
            min_child_samples=50,
            subsample=0.8,
            reg_lambda=1.0,
            random_state=42,
            verbose=-1,
        )
    else:
        model = LGBMRegressor(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            min_child_samples=30,
            subsample=0.8,
            reg_lambda=0.5,
            random_state=42,
            verbose=-1,
        )
    model.fit(X_tr, y_tr, sample_weight=w_tr)
    return model


def _prepare_stage_a_fold_frame(
    df: pd.DataFrame,
    *,
    symbol_stats: dict[str, dict[str, float]],
    global_tp: float,
    global_sl: float,
    symbol_to_cluster: dict[str, str],
    cluster_thresholds: dict[str, dict[str, float | int | str]] | None = None,
    threshold_summary: dict | None = None,
) -> pd.DataFrame:
    work = phase4._attach_trade_stats(
        df,
        symbol_stats,
        global_tp,
        global_sl,
        tp_col="avg_tp_train",
        sl_col="avg_sl_train",
    )
    work = work.copy()
    work["cluster_name"] = work["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
    if PROBLEM_TYPE == "cross_sectional_ranking":
        work, _ = _build_cross_sectional_ranking_frame(work)
        return work
    if TARGET_MODE == "cross_sectional_relative_activation":
        work, _ = _build_cross_sectional_relative_target(work)
        return work
    if TARGET_MODE == "two_stage_activation_utility":
        return _attach_two_stage_target_metadata(
            work,
            cluster_thresholds=cluster_thresholds,
            threshold_summary=threshold_summary,
        )
    work = _attach_stage_a_target_metadata(
        work,
        cluster_thresholds=cluster_thresholds,
        threshold_summary=threshold_summary,
    )
    work["y_stage_a"] = _build_stage_a_target(work)
    return work


def _aggregate_stage_a_predictions(oos_df: pd.DataFrame) -> pd.DataFrame:
    alias_df = oos_df.rename(
        columns={
            "y_stage_a": "y_meta",
            "p_stage_a_raw": "p_meta_raw",
            "p_stage_a_calibrated": "p_meta_calibrated",
            "pnl_exec_stage_a": "pnl_exec_meta",
            "mu_adj_stage_a": "mu_adj_meta",
            "kelly_frac_stage_a": "kelly_frac_meta",
            "position_usdt_stage_a": "position_usdt_meta",
        }
    )
    if "p_meta_calibrated" not in alias_df.columns:
        alias_df["p_meta_calibrated"] = pd.to_numeric(alias_df.get("p_meta_raw"), errors="coerce").fillna(0.0)
    aggregated = phase4._aggregate_oos_predictions(alias_df)
    aggregated = aggregated.rename(
        columns={
            "y_meta": "y_stage_a",
            "p_meta_raw": "p_stage_a_raw",
            "p_meta_calibrated": "p_stage_a_calibrated",
            "pnl_exec_meta": "pnl_exec_stage_a",
            "mu_adj_meta": "mu_adj_stage_a",
            "kelly_frac_meta": "kelly_frac_stage_a",
            "position_usdt_meta": "position_usdt_stage_a",
        }
    )
    extra_agg_spec: dict[str, str] = {}
    for col in [
        "stage_a_utility_real",
        "stage_a_utility_surplus",
        "stage_a_threshold_train",
        "utility_surplus_pred_stage_a",
        "p_activate_raw_stage_a",
        "p_activate_calibrated_stage_a",
    ]:
        if col in oos_df.columns:
            extra_agg_spec[col] = "mean"
    for col in [
        "stage_a_threshold_source",
        "stage2_training_policy",
    ]:
        if col in oos_df.columns:
            extra_agg_spec[col] = "first"
    if extra_agg_spec:
        extras = (
            oos_df.groupby(["date", "symbol"], as_index=False)
            .agg(extra_agg_spec)
            .sort_values(["date", "symbol"], kind="mergesort")
            .reset_index(drop=True)
        )
        aggregated = aggregated.merge(extras, on=["date", "symbol"], how="left")
    return aggregated


def _build_stage_a_snapshot_proxy(predictions_df: pd.DataFrame) -> pd.DataFrame:
    if predictions_df.empty:
        return pd.DataFrame()
    latest = (
        predictions_df.sort_values(["date", "symbol"], kind="mergesort")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values(["date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )
    latest = latest.copy()
    latest["p_stage_a"] = pd.to_numeric(latest.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0)
    latest["decision_selected"] = pd.Series(latest.get("decision_selected", False), index=latest.index).fillna(False).astype(bool)
    latest["decision_score_stage_a"] = pd.to_numeric(latest.get("decision_score_stage_a"), errors="coerce").fillna(0.0)
    latest["kelly_frac_stage_a_proxy"] = pd.to_numeric(latest.get("kelly_frac_stage_a"), errors="coerce").fillna(0.0)
    latest["position_usdt_stage_a_proxy"] = pd.to_numeric(latest.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    latest["side"] = np.where(latest["position_usdt_stage_a_proxy"] > 0, "BUY", "FLAT")
    latest["is_active"] = latest["position_usdt_stage_a_proxy"] > 0
    latest["proxy_type"] = "research_stage_a_activation_only"
    latest["governs_official_snapshot"] = False
    return latest


def _apply_cross_sectional_ranking_proxy(predictions_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if predictions_df.empty:
        return predictions_df.copy(), {
            "eligible_candidates_per_date": [],
            "truth_top1_count_per_date": [],
            "groups_local_selection": 0,
            "groups_fallback_selection": 0,
            "groups_without_eligible": 0,
            "groups_total": 0,
            "top1_hit_rate": 0.0,
            "naive_top1_hit_rate": 0.0,
            "mrr": 0.0,
            "rank_margin_latest": 0.0,
            "predicted_top_candidate_per_date": [],
        }

    work, truth_summary = _build_cross_sectional_ranking_frame(predictions_df)
    work = work.copy()
    work["rank_score_stage_a"] = pd.to_numeric(
        work.get("rank_score_stage_a", work.get("p_stage_a_raw")),
        errors="coerce",
    ).fillna(0.0)
    work["stage_a_selected_proxy_local"] = False
    work["stage_a_selected_proxy_fallback"] = False
    work["stage_a_selected_proxy"] = False

    group_df = (
        work[["date", "cluster_name", "stage_a_group_eligible_count", "stage_a_selection_mode"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    contest_rows: list[dict] = []
    predicted_rows: list[dict] = []

    def _evaluate_contest(contest_df: pd.DataFrame, *, date_value, cluster_name: str, selection_mode: str) -> None:
        if contest_df.empty:
            return
        predicted = contest_df.sort_values(
            ["rank_score_stage_a", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        )
        truth = contest_df.sort_values(
            ["stage_a_score_realized", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        )
        naive = contest_df.sort_values(
            ["p_bma_pkf", "symbol"],
            ascending=[False, True],
            kind="mergesort",
        )

        pred_top_idx = int(predicted.index[0])
        truth_top_idx = int(truth.index[0])
        naive_top_idx = int(naive.index[0])
        truth_rank = int(np.where(predicted.index.to_numpy() == truth_top_idx)[0][0]) + 1
        top_score = float(predicted.iloc[0]["rank_score_stage_a"])
        second_score = float(predicted.iloc[1]["rank_score_stage_a"]) if len(predicted) > 1 else 0.0
        rank_margin = round(float(top_score - second_score), 6)

        if selection_mode == "cluster_local_top1":
            work.loc[pred_top_idx, "stage_a_selected_proxy_local"] = True
        else:
            work.loc[pred_top_idx, "stage_a_selected_proxy_fallback"] = True

        contest_rows.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "cluster_name": cluster_name,
                "selection_mode": selection_mode,
                "eligible_count": int(len(contest_df)),
                "pred_top_symbol": str(predicted.iloc[0]["symbol"]),
                "truth_top_symbol": str(truth.iloc[0]["symbol"]),
                "naive_top_symbol": str(naive.iloc[0]["symbol"]),
                "hit": bool(pred_top_idx == truth_top_idx),
                "naive_hit": bool(naive_top_idx == truth_top_idx),
                "reciprocal_rank": round(float(1.0 / truth_rank), 6),
                "rank_margin": rank_margin,
            }
        )
        predicted_rows.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "cluster_name": cluster_name,
                "selection_mode": selection_mode,
                "symbol": str(predicted.iloc[0]["symbol"]),
                "rank_score_stage_a": round(float(predicted.iloc[0]["rank_score_stage_a"]), 6),
                "score_realized": round(float(predicted.iloc[0]["stage_a_score_realized"]), 6),
                "p_bma_pkf": round(float(pd.to_numeric(predicted.iloc[0]["p_bma_pkf"], errors="coerce") or 0.0), 6),
                "is_truth_top1": bool(pred_top_idx == truth_top_idx),
                "rank_margin": rank_margin,
                "eligible_count": int(len(contest_df)),
            }
        )

    local_groups = group_df.loc[group_df["stage_a_selection_mode"] == "cluster_local_top1"]
    for _, row in local_groups.iterrows():
        contest_df = work.loc[
            (work["date"] == row["date"])
            & (work["cluster_name"].astype(str) == str(row["cluster_name"]))
            & work["stage_a_eligible"].fillna(False)
        ].copy()
        _evaluate_contest(
            contest_df,
            date_value=row["date"],
            cluster_name=str(row["cluster_name"]),
            selection_mode="cluster_local_top1",
        )

    fallback_dates = sorted(
        pd.to_datetime(
            group_df.loc[group_df["stage_a_selection_mode"] == "date_universe_fallback", "date"],
            errors="coerce",
        ).dropna().unique().tolist()
    )
    for date_value in fallback_dates:
        contest_df = work.loc[
            (work["date"] == pd.Timestamp(date_value))
            & work["stage_a_eligible"].fillna(False)
        ].copy()
        _evaluate_contest(
            contest_df,
            date_value=pd.Timestamp(date_value),
            cluster_name="date_universe",
            selection_mode="date_universe_fallback",
        )

    work["stage_a_selected_proxy"] = work["stage_a_selected_proxy_local"] | work["stage_a_selected_proxy_fallback"]
    work["p_stage_a_calibrated"] = work["stage_a_selected_proxy"].astype(float)

    contest_df = pd.DataFrame(contest_rows)
    latest_date = pd.to_datetime(work["date"], errors="coerce").max()
    latest_date_str = latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None
    latest_predicted = [row for row in predicted_rows if row.get("date") == latest_date_str]
    if latest_predicted:
        latest_top = max(latest_predicted, key=lambda row: (row.get("rank_score_stage_a", 0.0), row.get("symbol", "")))
        rank_margin_latest = round(float(latest_top.get("rank_margin", 0.0)), 6)
    else:
        rank_margin_latest = 0.0

    summary = {
        "eligible_candidates_per_date": truth_summary.get("eligible_candidates_per_date", []),
        "truth_top1_count_per_date": truth_summary.get("target_positive_count_per_date", []),
        "groups_local_selection": int(truth_summary.get("groups_local_target", 0)),
        "groups_fallback_selection": int(truth_summary.get("groups_fallback_target", 0)),
        "groups_without_eligible": int(truth_summary.get("groups_without_eligible", 0)),
        "groups_total": int(truth_summary.get("groups_total", 0)),
        "top1_hit_rate": round(float(contest_df["hit"].mean()), 4) if not contest_df.empty else 0.0,
        "naive_top1_hit_rate": round(float(contest_df["naive_hit"].mean()), 4) if not contest_df.empty else 0.0,
        "mrr": round(float(contest_df["reciprocal_rank"].mean()), 4) if not contest_df.empty else 0.0,
        "rank_margin_latest": rank_margin_latest,
        "predicted_top_candidate_per_date": predicted_rows,
    }
    return work, summary


def _apply_two_stage_activation_utility_proxy(predictions_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if predictions_df.empty:
        return predictions_df.copy(), {
            "activated_candidates_per_date": [],
            "groups_local_selection": 0,
            "groups_fallback_selection": 0,
            "groups_without_eligible": 0,
            "groups_total": 0,
            "rank_margin_latest": 0.0,
            "predicted_top_candidate_per_date": [],
        }

    work = predictions_df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["cluster_name"] = work.get("cluster_name", pd.Series("cluster_global", index=work.index)).astype(str).fillna("cluster_global")
    work["p_activate_calibrated_stage_a"] = pd.to_numeric(
        work.get("p_activate_calibrated_stage_a", work.get("p_stage_a_calibrated")),
        errors="coerce",
    ).fillna(0.0)
    work["utility_surplus_pred_stage_a"] = pd.to_numeric(
        work.get("utility_surplus_pred_stage_a"),
        errors="coerce",
    ).fillna(0.0)
    work["stage_a_predicted_activated"] = work["p_activate_calibrated_stage_a"] > TARGET_ACTIVATION_THRESHOLD
    work["stage_a_group_eligible_count"] = 0
    work["stage_a_selection_mode"] = "no_eligible"
    work["stage_a_selected_proxy_local"] = False
    work["stage_a_selected_proxy_fallback"] = False
    work["stage_a_selected_proxy"] = False

    activated_per_date = (
        work.assign(_date_str=work["date"].dt.strftime("%Y-%m-%d"))
        .groupby("_date_str", sort=True)["stage_a_predicted_activated"]
        .sum()
        .reset_index()
        .rename(columns={"_date_str": "date", "stage_a_predicted_activated": "activated_candidates"})
        .to_dict(orient="records")
    )

    predicted_rows: list[dict] = []
    group_rows: list[dict] = []

    def _rank_contest(contest_df: pd.DataFrame) -> pd.DataFrame:
        return contest_df.sort_values(
            ["utility_surplus_pred_stage_a", "p_activate_calibrated_stage_a", "symbol"],
            ascending=[False, False, True],
            kind="mergesort",
        )

    local_groups = []
    fallback_dates = set()
    for (date_value, cluster_name), grp in work.groupby(["date", "cluster_name"], sort=True):
        activated_grp = grp.loc[grp["stage_a_predicted_activated"].fillna(False)].copy()
        activated_count = int(len(activated_grp))
        work.loc[grp.index, "stage_a_group_eligible_count"] = activated_count
        if activated_count >= int(MIN_ELIGIBLE_PER_DATE_CLUSTER):
            ranked = _rank_contest(activated_grp)
            top_idx = int(ranked.index[0])
            work.loc[grp.index, "stage_a_selection_mode"] = "cluster_local_top1"
            work.loc[top_idx, "stage_a_selected_proxy_local"] = True
            selection_mode = "cluster_local_top1"
            local_groups.append((pd.Timestamp(date_value), str(cluster_name), ranked))
        elif activated_count > 0:
            work.loc[grp.index, "stage_a_selection_mode"] = "date_universe_fallback"
            fallback_dates.add(pd.Timestamp(date_value))
            selection_mode = "date_universe_fallback"
        else:
            work.loc[grp.index, "stage_a_selection_mode"] = "no_eligible"
            selection_mode = "no_eligible"
        group_rows.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "cluster_name": str(cluster_name),
                "eligible_count": activated_count,
                "selection_mode": selection_mode,
            }
        )

    for date_value, cluster_name, ranked in local_groups:
        top_row = ranked.iloc[0]
        predicted_rows.append(
            {
                "date": date_value.strftime("%Y-%m-%d"),
                "cluster_name": cluster_name,
                "selection_mode": "cluster_local_top1",
                "symbol": str(top_row.get("symbol", "")),
                "p_activate_calibrated_stage_a": round(float(top_row["p_activate_calibrated_stage_a"]), 6),
                "utility_surplus_pred_stage_a": round(float(top_row["utility_surplus_pred_stage_a"]), 6),
                "eligible_count": int(len(ranked)),
                "rank_margin": round(
                    float(top_row["utility_surplus_pred_stage_a"] - ranked.iloc[1]["utility_surplus_pred_stage_a"]),
                    6,
                )
                if len(ranked) > 1
                else 0.0,
            }
        )

    for date_value in sorted(fallback_dates):
        contest_df = work.loc[
            (work["date"] == pd.Timestamp(date_value))
            & work["stage_a_predicted_activated"].fillna(False)
        ].copy()
        if contest_df.empty:
            continue
        ranked = _rank_contest(contest_df)
        top_idx = int(ranked.index[0])
        work.loc[top_idx, "stage_a_selected_proxy_fallback"] = True
        top_row = ranked.iloc[0]
        predicted_rows.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y-%m-%d"),
                "cluster_name": "date_universe",
                "selection_mode": "date_universe_fallback",
                "symbol": str(top_row.get("symbol", "")),
                "p_activate_calibrated_stage_a": round(float(top_row["p_activate_calibrated_stage_a"]), 6),
                "utility_surplus_pred_stage_a": round(float(top_row["utility_surplus_pred_stage_a"]), 6),
                "eligible_count": int(len(ranked)),
                "rank_margin": round(
                    float(top_row["utility_surplus_pred_stage_a"] - ranked.iloc[1]["utility_surplus_pred_stage_a"]),
                    6,
                )
                if len(ranked) > 1
                else 0.0,
            }
        )

    work["stage_a_selected_proxy"] = work["stage_a_selected_proxy_local"] | work["stage_a_selected_proxy_fallback"]
    work["decision_selected"] = work["stage_a_selected_proxy"].astype(bool)
    work["decision_score_stage_a"] = np.where(
        work["decision_selected"],
        work["p_activate_calibrated_stage_a"],
        0.0,
    )

    latest_date = pd.to_datetime(work["date"], errors="coerce").max()
    latest_date_str = latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None
    latest_predicted = [row for row in predicted_rows if row.get("date") == latest_date_str]
    rank_margin_latest = max((float(row.get("rank_margin", 0.0)) for row in latest_predicted), default=0.0)
    group_df = pd.DataFrame(group_rows)
    summary = {
        "activated_candidates_per_date": activated_per_date,
        "groups_local_selection": int((group_df["selection_mode"] == "cluster_local_top1").sum()) if not group_df.empty else 0,
        "groups_fallback_selection": int((group_df["selection_mode"] == "date_universe_fallback").sum()) if not group_df.empty else 0,
        "groups_without_eligible": int((group_df["selection_mode"] == "no_eligible").sum()) if not group_df.empty else 0,
        "groups_total": int(len(group_df)),
        "rank_margin_latest": round(float(rank_margin_latest), 6),
        "predicted_top_candidate_per_date": predicted_rows,
    }
    return work, summary


def _build_stage_a_operational_report(
    aggregated_predictions: pd.DataFrame,
    snapshot_proxy: pd.DataFrame,
) -> dict:
    if aggregated_predictions.empty:
        return {
            "status": "MISSING_PREDICTIONS",
            "path_name": "research_stage_a_operational_proxy",
            "governs_snapshot": False,
        }

    work = aggregated_predictions.rename(
        columns={
            "p_stage_a_raw": "p_meta_raw",
            "p_stage_a_calibrated": "p_meta_calibrated",
            "mu_adj_stage_a": "mu_adj_meta",
            "kelly_frac_stage_a": "kelly_frac_meta",
            "position_usdt_stage_a": "position_usdt_meta",
            "pnl_exec_stage_a": "pnl_exec_meta",
        }
    ).copy()
    snapshot = phase4._build_execution_snapshot(work)
    report = phase4._build_operational_path_report(work, snapshot)
    report["path_name"] = "research_stage_a_operational_proxy"
    report["governs_snapshot"] = False
    report["score_col"] = "p_stage_a_calibrated" if PROBLEM_TYPE != "cross_sectional_ranking" else "stage_a_selected_proxy"
    report["kelly_col"] = "kelly_frac_stage_a"
    report["position_col"] = "position_usdt_stage_a"
    report["pnl_col"] = "pnl_exec_stage_a"
    report["signal_threshold"] = 0.50
    report["snapshot_fields"] = {
        "p_stage_a": "p_stage_a_calibrated",
        "kelly_frac_stage_a_proxy": "kelly_frac_stage_a",
        "position_usdt_stage_a_proxy": "position_usdt_stage_a",
    }
    report["target_name"] = _target_name()
    report["target_definition"] = _target_definition()
    report["problem_type"] = PROBLEM_TYPE
    return report


def _build_prevalence_summary(oos_df: pd.DataFrame, group_col: str) -> list[dict]:
    if oos_df.empty or group_col not in oos_df.columns:
        return []
    rows = []
    for key, grp in oos_df.groupby(group_col, sort=True):
        y = pd.to_numeric(grp["y_stage_a"], errors="coerce").fillna(0.0)
        rows.append(
            {
                group_col: str(key),
                "n_obs": int(len(grp)),
                "positives": int(y.sum()),
                "prevalence": round(float(y.mean()), 4) if len(grp) else 0.0,
            }
        )
    return rows


def _build_prevalence_by_year(oos_df: pd.DataFrame) -> list[dict]:
    if oos_df.empty:
        return []
    work = oos_df.copy()
    work["year"] = pd.to_datetime(work["date"], errors="coerce").dt.year.astype("Int64")
    return _build_prevalence_summary(work.dropna(subset=["year"]).assign(year=lambda df: df["year"].astype(int)), "year")


def _build_per_date_count_list(oos_df: pd.DataFrame, *, value_col: str, output_col: str) -> list[dict]:
    if oos_df.empty or value_col not in oos_df.columns:
        return []
    work = oos_df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    grouped = (
        work.groupby("date", sort=True)[value_col]
        .sum()
        .reset_index()
        .assign(date=lambda df: df["date"].dt.strftime("%Y-%m-%d"))
        .rename(columns={value_col: output_col})
    )
    return grouped.to_dict(orient="records")


def _compute_latest_top_candidate_stats(snapshot_proxy: pd.DataFrame) -> dict:
    if snapshot_proxy.empty:
        return {
            "latest_top_candidate_rank_pct": 0.0,
            "latest_top_candidate_score_gap_vs_second": 0.0,
        }
    ranked = snapshot_proxy.sort_values(
        ["p_stage_a_calibrated", "symbol"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    top_score = float(pd.to_numeric(ranked.loc[0, "p_stage_a_calibrated"], errors="coerce"))
    second_score = float(pd.to_numeric(ranked.loc[1, "p_stage_a_calibrated"], errors="coerce")) if len(ranked) > 1 else 0.0
    return {
        "latest_top_candidate_rank_pct": 1.0,
        "latest_top_candidate_score_gap_vs_second": round(top_score - second_score, 6),
    }


def _summarize_cluster_threshold_coverage(threshold_rows: list[dict]) -> dict:
    if not threshold_rows:
        return {
            "min_train_positive_count_per_cluster": MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER,
            "quantile_train_positive": round(float(TARGET_CLUSTER_Q), 4),
            "fallback_policy": TARGET_FALLBACK_POLICY,
            "cluster_assignments_local": 0,
            "cluster_assignments_fallback": 0,
            "clusters_with_local_threshold": [],
            "clusters_with_fallback": [],
        }
    df = pd.DataFrame(threshold_rows)
    local_mask = df["threshold_source"] == "cluster_local_q_train_positive"
    return {
        "min_train_positive_count_per_cluster": MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER,
        "quantile_train_positive": round(float(TARGET_CLUSTER_Q), 4),
        "fallback_policy": TARGET_FALLBACK_POLICY,
        "cluster_assignments_local": int(local_mask.sum()),
        "cluster_assignments_fallback": int((~local_mask).sum()),
        "clusters_with_local_threshold": sorted(df.loc[local_mask, "cluster_name"].astype(str).unique().tolist()),
        "clusters_with_fallback": sorted(df.loc[~local_mask, "cluster_name"].astype(str).unique().tolist()),
        "global_positive_count_train_min": int(pd.to_numeric(df["train_positive_count_global"], errors="coerce").min()),
        "global_positive_count_train_max": int(pd.to_numeric(df["train_positive_count_global"], errors="coerce").max()),
    }


def _compute_final_position_counts(aggregated_predictions: pd.DataFrame) -> dict:
    if aggregated_predictions.empty:
        return {
            "position_gt_0_rows_final": 0,
            "position_gt_0_over_min_alloc_rows_final": 0,
            "signal_and_position_gt_0_rows_final": 0,
            "signal_and_position_gt_0_over_min_alloc_rows_final": 0,
            "max_position_usdt_final": 0.0,
            "max_alloc_frac_final": 0.0,
            "min_alloc_frac_threshold": MIN_ALLOC_FRAC,
        }
    pos = pd.to_numeric(aggregated_predictions["position_usdt_stage_a"], errors="coerce").fillna(0.0)
    prob = pd.to_numeric(aggregated_predictions["p_stage_a_calibrated"], errors="coerce").fillna(0.0)
    alloc = pos / float(phase4.CAPITAL_INITIAL)
    signal_and_pos = (prob > 0.50) & (pos > 0)
    over_min_alloc = alloc > MIN_ALLOC_FRAC
    return {
        "position_gt_0_rows_final": int((pos > 0).sum()),
        "position_gt_0_over_min_alloc_rows_final": int(over_min_alloc.sum()),
        "signal_and_position_gt_0_rows_final": int(signal_and_pos.sum()),
        "signal_and_position_gt_0_over_min_alloc_rows_final": int((signal_and_pos & over_min_alloc).sum()),
        "max_position_usdt_final": round(float(pos.max()), 2),
        "max_alloc_frac_final": round(float(alloc.max()), 6),
        "min_alloc_frac_threshold": MIN_ALLOC_FRAC,
    }


def _load_stage_a_report(model_path: Path, experiment_name: str) -> dict | None:
    ref_path = model_path / "research" / experiment_name / "stage_a_report.json"
    if not ref_path.exists():
        return None
    try:
        return json.loads(ref_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_stage_a_metric_sections(report: dict) -> tuple[dict, dict]:
    return (
        report.get("classification_metrics", {}) or {},
        report.get("ranking_metrics", {}) or {},
    )


def _build_comparison_vs_reference(current_report: dict, reference_report: dict | None) -> dict | None:
    if not reference_report:
        return None
    current_cls, current_rank = _extract_stage_a_metric_sections(current_report)
    current_op = current_report.get("operational_proxy", {})
    current_gate = current_report.get("gate_do_experimento_stage_a", {})
    reference_cls, reference_rank = _extract_stage_a_metric_sections(reference_report)
    reference_op = reference_report.get("operational_proxy", {})
    reference_gate = reference_report.get("gate_do_experimento_stage_a", {})
    return {
        "reference_experiment_name": reference_report.get("experiment_name", REFERENCE_EXPERIMENT_NAME),
        "status_before": reference_report.get("status"),
        "status_after": current_report.get("status"),
        "target_before": reference_report.get("target_definition"),
        "target_after": current_report.get("target_definition"),
        "metrics_before": {
            "positive_rate_oos": reference_cls.get("positive_rate_oos"),
            "auc_raw_global": reference_cls.get("auc_raw_global"),
            "auc_calibrated_global": reference_cls.get("auc_calibrated_global"),
            "ece_calibrated": reference_cls.get("ece_calibrated"),
            "top1_hit_rate": reference_rank.get("top1_hit_rate"),
            "naive_top1_hit_rate": reference_rank.get("naive_top1_hit_rate"),
            "mrr": reference_rank.get("mrr"),
            "sharpe": reference_op.get("sharpe"),
            "dsr_honest": reference_op.get("dsr_honest"),
            "historical_active_events": reference_op.get("n_active"),
            "latest_active_count": reference_op.get("activation_funnel", {}).get("latest_snapshot_active_count"),
            "latest_snapshot_max_p_stage_a_calibrated": reference_op.get("sparsity", {}).get("latest_snapshot_max_p_meta_calibrated"),
            "position_gt_0_rows_final": reference_op.get("final_position_counts", {}).get("position_gt_0_rows_final"),
            "position_gt_0_over_min_alloc_rows_final": reference_op.get("final_position_counts", {}).get("position_gt_0_over_min_alloc_rows_final"),
        },
        "metrics_after": {
            "positive_rate_oos": current_cls.get("positive_rate_oos"),
            "auc_raw_global": current_cls.get("auc_raw_global"),
            "auc_calibrated_global": current_cls.get("auc_calibrated_global"),
            "ece_calibrated": current_cls.get("ece_calibrated"),
            "top1_hit_rate": current_rank.get("top1_hit_rate"),
            "naive_top1_hit_rate": current_rank.get("naive_top1_hit_rate"),
            "mrr": current_rank.get("mrr"),
            "sharpe": current_op.get("sharpe"),
            "dsr_honest": current_op.get("dsr_honest"),
            "historical_active_events": current_op.get("n_active"),
            "latest_active_count": current_op.get("activation_funnel", {}).get("latest_snapshot_active_count"),
            "latest_snapshot_max_p_stage_a_calibrated": current_op.get("sparsity", {}).get("latest_snapshot_max_p_meta_calibrated"),
            "position_gt_0_rows_final": current_op.get("final_position_counts", {}).get("position_gt_0_rows_final"),
            "position_gt_0_over_min_alloc_rows_final": current_op.get("final_position_counts", {}).get("position_gt_0_over_min_alloc_rows_final"),
        },
        "gate_before": {
            "status": reference_gate.get("status"),
            "abort_early": reference_gate.get("abort_early"),
            "headroom_real_documented": reference_gate.get("headroom_real_documented"),
        },
        "gate_after": {
            "status": current_gate.get("status"),
            "abort_early": current_gate.get("abort_early"),
            "headroom_real_documented": current_gate.get("headroom_real_documented"),
        },
    }


def _build_comparisons_vs_baselines(model_path: Path, current_report: dict) -> dict[str, dict]:
    comparisons: dict[str, dict] = {}
    for experiment_name in BASELINE_EXPERIMENT_NAMES:
        if experiment_name == EXPERIMENT_NAME:
            continue
        reference_report = _load_stage_a_report(model_path, experiment_name)
        comparison = _build_comparison_vs_reference(current_report, reference_report)
        if comparison is not None:
            comparisons[experiment_name] = comparison
    return comparisons


def _evaluate_stage_a_gate(
    operational_report: dict,
    *,
    ece_calibrated: float | None = None,
    positive_rate_oos: float | None = None,
    top1_hit_rate: float | None = None,
    naive_top1_hit_rate: float | None = None,
    position_gt_0_over_min_alloc_rows_final: int | None = None,
) -> dict:
    historical_active = int(operational_report.get("n_active", 0))
    latest_active = int(operational_report.get("activation_funnel", {}).get("latest_snapshot_active_count", 0))
    latest_prob_gt = int(operational_report.get("activation_funnel", {}).get("latest_snapshot_p_meta_calibrated_gt_050", 0))
    latest_mu_gt = int(operational_report.get("activation_funnel", {}).get("latest_snapshot_mu_adj_meta_gt_0", 0))
    headroom_real = latest_prob_gt > 0 and latest_mu_gt > 0
    subperiod_summary = operational_report.get("subperiod_summary")
    if not subperiod_summary:
        subperiod_summary = phase4._summarize_subperiods(operational_report.get("subperiods", []))
    negative_periods = list(subperiod_summary.get("negative_periods", []))

    if PROBLEM_TYPE == "cross_sectional_ranking":
        hit_rate = float(top1_hit_rate or 0.0)
        naive_hit_rate = float(naive_top1_hit_rate or 0.0)
        min_alloc_rows = int(position_gt_0_over_min_alloc_rows_final or 0)
        checks = {
            "dsr_positive": float(operational_report.get("dsr_honest", 0.0)) > 0.0,
            "sharpe_minimum": float(operational_report.get("sharpe", 0.0)) >= 0.70,
            "historical_active_events_min": historical_active >= 10,
            "latest_active_or_headroom": latest_active >= 1 or headroom_real,
            "position_gt_0_over_min_alloc_rows_final_min": min_alloc_rows >= 5,
            "top1_hit_rate_above_naive": hit_rate > naive_hit_rate,
            "no_new_negative_subperiod": len(negative_periods) == 0,
        }
        abort_early = (
            float(operational_report.get("dsr_honest", 0.0)) == 0.0
            or float(operational_report.get("sharpe", 0.0)) < 0.70
            or (latest_active == 0 and not headroom_real)
            or hit_rate <= naive_hit_rate
            or (historical_active > 0 and min_alloc_rows < 5)
        )
    else:
        checks = {
            "dsr_positive": float(operational_report.get("dsr_honest", 0.0)) > 0.0,
            "sharpe_minimum": float(operational_report.get("sharpe", 0.0)) >= 0.70,
            "ece_calibrated_max": float(ece_calibrated) <= 0.05,
            "positive_rate_min": float(positive_rate_oos) >= 0.05,
            "historical_active_count_min": historical_active >= 90 or historical_active >= int(BASELINE_HISTORICAL_ACTIVE * 1.5),
            "latest_active_or_headroom": latest_active >= 1 or headroom_real,
            "no_new_negative_subperiod": len(negative_periods) == 0,
        }
        abort_early = (
            float(positive_rate_oos) < 0.05
            or float(operational_report.get("dsr_honest", 0.0)) == 0.0
            or (latest_active == 0 and not headroom_real)
            or (historical_active > BASELINE_HISTORICAL_ACTIVE and float(operational_report.get("sharpe", 0.0)) < 0.70)
        )
    passed = all(checks.values()) and not abort_early
    gate = {
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "headroom_real_documented": bool(headroom_real),
        "headroom_definition": "latest_snapshot_p_stage_a_calibrated_gt_050 > 0 and latest_snapshot_mu_adj_stage_a_gt_0 > 0",
        "abort_early": bool(abort_early),
        "historical_active_count": historical_active,
        "baseline_historical_active_count": BASELINE_HISTORICAL_ACTIVE,
        "latest_active_count": latest_active,
        "negative_subperiods": negative_periods,
    }
    if positive_rate_oos is not None:
        gate["positive_rate_oos"] = round(float(positive_rate_oos), 4)
    if top1_hit_rate is not None:
        gate["top1_hit_rate"] = round(float(top1_hit_rate), 4)
    if naive_top1_hit_rate is not None:
        gate["naive_top1_hit_rate"] = round(float(naive_top1_hit_rate), 4)
    if position_gt_0_over_min_alloc_rows_final is not None:
        gate["position_gt_0_over_min_alloc_rows_final"] = int(position_gt_0_over_min_alloc_rows_final)
    return gate


def _stage_a_manifest(
    *,
    model_path: Path,
    research_path: Path,
    feature_cols: list[str],
    source_hashes: dict[str, str],
    target_selection_policy: dict,
) -> dict:
    git_status_short = _git_output("status", "--short")
    return {
        "experiment_name": EXPERIMENT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "repo_root": str(REPO_ROOT),
        "model_path": str(model_path),
        "research_output_path": str(research_path),
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "working_tree_state": "clean" if not git_status_short else "dirty",
        "git_status_short": git_status_short.splitlines(),
        "git_diff_stat": _git_output("diff", "--stat").splitlines(),
        "target_name": _target_name(),
        "target_definition": _target_definition(),
        "problem_type": PROBLEM_TYPE,
        "non_circularity_note": (
            "The target uses realized economic outcome (pnl_real) and an ex-ante hurdle "
            "(avg_sl_train). It does not depend on p_meta_calibrated, kelly_frac_meta, "
            "position_usdt_meta, or the current phase4 activation gate."
        ),
        "avg_sl_train_no_leakage_note": (
            "avg_sl_train is computed inside each CPCV combo from train_df only via "
            "_compute_symbol_trade_stats(train_df), then mapped onto that combo's test rows "
            "before y_stage_a is built. The predictions artifact stores combo, avg_sl_train, "
            "pnl_real and y_stage_a for audit."
        ),
        "cross_sectional_target_no_leakage_note": (
            "When TARGET_MODE=cross_sectional_relative_activation, eligibility and realized "
            "ranking are computed within each CPCV partition using realized pnl_real, "
            "avg_sl_train and contemporaneous date/cluster grouping only. No current model "
            "outputs, official snapshot fields or policy gates enter the target."
        ),
        "cross_sectional_ranking_no_leakage_note": (
            "When PROBLEM_TYPE=cross_sectional_ranking, the runner trains on a continuous "
            "realized rank target (pnl_real / avg_sl_train for eligible rows) and evaluates "
            "top1 selection within each CPCV partition. The local-vs-fallback contest scope "
            "is built only from realized pnl_real, avg_sl_train, date and cluster_name. "
            "No official phase4 score or activation field is used to define the target."
        ),
        "cluster_q_train_no_leakage_note": (
            "When TARGET_MODE=cluster_local_q_positive, Q-train thresholds are computed "
            "inside each CPCV combo from train_df only, using positive pnl_real rows in the "
            "same cluster_name. If a cluster does not meet the minimum positive support, the "
            "runner falls back to the train-fold global positive pnl_real quantile. The "
            "predictions artifact stores threshold value, threshold source and train support."
        ),
        "target_mode": TARGET_MODE,
        "avg_sl_train_multiplier": TARGET_SL_MULT,
        "cluster_quantile_train_positive": round(float(TARGET_CLUSTER_Q), 4),
        "min_train_positive_count_per_cluster": MIN_TRAIN_POSITIVE_COUNT_PER_CLUSTER,
        "min_eligible_per_date_cluster": MIN_ELIGIBLE_PER_DATE_CLUSTER,
        "target_selection_policy": target_selection_policy,
        "selected_features": feature_cols,
        "source_artifacts": [
            str(model_path / "phase3"),
            str(model_path / "features"),
            str(model_path / "phase4" / "phase4_report_v4.json"),
            str(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
        ],
        "source_hashes": source_hashes,
    }


def _collect_source_hashes(model_path: Path) -> dict[str, str]:
    phase3_meta = sorted((model_path / "phase3").glob("*_meta.parquet"))
    phase3_sizing = sorted((model_path / "phase3").glob("*_sizing.parquet"))
    features = sorted((model_path / "features").glob("*.parquet"))
    return {
        "phase3_meta_combined_sha256": _combined_hash(phase3_meta) if phase3_meta else "",
        "phase3_sizing_combined_sha256": _combined_hash(phase3_sizing) if phase3_sizing else "",
        "features_combined_sha256": _combined_hash(features) if features else "",
        "phase4_report_v4_sha256": _sha256_file(model_path / "phase4" / "phase4_report_v4.json"),
        "phase4_aggregated_predictions_sha256": _sha256_file(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
    }


def run_stage_a_experiment() -> dict:
    model_path = _resolve_model_path()
    _configure_phase4_paths(model_path)
    research_path = _research_path(model_path)
    research_path.mkdir(parents=True, exist_ok=True)

    pooled_df = phase4.load_pooled_meta_df()
    feature_cols = phase4.select_features(pooled_df)
    _, symbol_to_cluster, target_cluster_mode, target_cluster_artifact_path = phase4._load_symbol_vi_clusters(
        pooled_df["symbol"].astype(str).unique().tolist()
    )

    n = len(pooled_df)
    embargo = max(1, int(n * phase4.EMBARGO_PCT))
    splits = np.array_split(np.arange(n), phase4.N_SPLITS)
    combos = list(combinations(range(phase4.N_SPLITS), phase4.N_TEST_SPLITS))
    prediction_rows: list[dict] = []
    trajectories: list[dict] = []
    target_policy_rows: list[dict] = []

    print(f"[Stage A] CPCV combos={len(combos)} N={n} embargo={embargo}")
    print(f"[Stage A] Features ({len(feature_cols)}): {feature_cols}")

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
        cluster_thresholds = None
        threshold_summary = None
        if TARGET_MODE == "cluster_local_q_positive":
            cluster_thresholds, threshold_summary = _compute_cluster_local_target_thresholds(train_df)
            for row in threshold_summary.get("cluster_rows", []):
                target_policy_rows.append(
                    {
                        "combo": str(combo),
                        **row,
                    }
                )
        elif TARGET_MODE == "two_stage_activation_utility":
            cluster_thresholds, threshold_summary = _compute_two_stage_activation_thresholds(
                train_df_with_stats.assign(cluster_name=train_df["cluster_name"].values),
                quantile=PRIMARY_Q,
            )
            for row in threshold_summary.get("cluster_rows", []):
                target_policy_rows.append(
                    {
                        "combo": str(combo),
                        "primary_q": round(float(PRIMARY_Q), 4),
                        **row,
                    }
                )
        train_df = _prepare_stage_a_fold_frame(
            train_df,
            symbol_stats=symbol_stats,
            global_tp=global_tp,
            global_sl=global_sl,
            symbol_to_cluster=symbol_to_cluster,
            cluster_thresholds=cluster_thresholds,
            threshold_summary=threshold_summary,
        )
        test_df = _prepare_stage_a_fold_frame(
            test_df,
            symbol_stats=symbol_stats,
            global_tp=global_tp,
            global_sl=global_sl,
            symbol_to_cluster=symbol_to_cluster,
            cluster_thresholds=cluster_thresholds,
            threshold_summary=threshold_summary,
        )

        uniq = train_df["uniqueness"].fillna(1.0) if "uniqueness" in train_df.columns else pd.Series(1.0, index=train_df.index)
        n_eff = float(uniq.sum())
        X_tr = phase4._prepare_feature_matrix(train_df, feature_cols)
        X_te = phase4._prepare_feature_matrix(test_df, feature_cols)
        w_tr = phase4.compute_sample_weights(train_df)

        if PROBLEM_TYPE == "cross_sectional_ranking":
            y_tr_truth = train_df["y_stage_a_truth_top1"].values
            y_te_truth = test_df["y_stage_a_truth_top1"].values
            y_tr_rank = pd.to_numeric(train_df["rank_target_stage_a"], errors="coerce").fillna(0.0).values
            if np.allclose(y_tr_rank, 0.0):
                print(f"  [SKIP] combo={combo} zero-signal rank target")
                continue
            model = _train_stage_a_ranker(X_tr, y_tr_rank, w_tr, n_eff)
            p_oos_raw = np.asarray(model.predict(X_te), dtype=float)
            auc_raw = roc_auc_score(y_te_truth, p_oos_raw) if len(np.unique(y_te_truth)) >= 2 else 0.5
            trajectories.append(
                {
                    "combo": str(combo),
                    "n_train": int(len(train_idx)),
                    "n_test": int(len(test_idx)),
                    "n_eff": round(n_eff, 1),
                    "truth_top1_rate_train": round(float(np.mean(y_tr_truth)), 4),
                    "truth_top1_rate_test": round(float(np.mean(y_te_truth)), 4),
                    "rank_target_mean_train": round(float(np.mean(y_tr_rank)), 4),
                    "auc_raw_vs_truth_top1": round(float(auc_raw), 4),
                }
            )
            print(f"  combo={combo} rank_AUC_vs_truth_top1={auc_raw:.4f} train={len(train_idx)} test={len(test_idx)}")
        else:
            y_tr = train_df["y_stage_a"].values
            y_te = test_df["y_stage_a"].values
            if len(np.unique(y_tr)) < 2:
                print(f"  [SKIP] combo={combo} single-class train target")
                continue

            model = phase4.train_meta_model(X_tr, y_tr, w_tr, n_eff)
            p_oos_raw = model.predict_proba(X_te)[:, 1]
            if phase4._hmm_hard_gate_enabled() and "hmm_prob_bull" in test_df.columns:
                hmm_te = pd.to_numeric(test_df["hmm_prob_bull"], errors="coerce").fillna(0.0).values
                p_oos_raw = np.where(hmm_te < 0.50, 0.0, p_oos_raw)
            auc_raw = roc_auc_score(y_te, p_oos_raw) if len(np.unique(y_te)) >= 2 else 0.5

            stage2_payload = {
                "stage2_training_policy": "not_applicable",
                "train_rows_total": int(len(train_df)),
                "train_rows_stage2": int(len(train_df)),
                "min_rows_required": 0,
                "all_zero_target": False,
                "is_valid": True,
                "reason": "not_applicable",
            }
            utility_pred_oos = np.full(len(test_df), np.nan, dtype=float)
            if TARGET_MODE == "two_stage_activation_utility":
                X_tr_stage2, y_tr_stage2, w_tr_stage2, stage2_payload = _build_stage2_training_payload(
                    train_df,
                    X_tr,
                    w_tr,
                )
                if not stage2_payload.get("is_valid"):
                    print(
                        f"  [SKIP] combo={combo} invalid stage2 support "
                        f"rows={stage2_payload.get('train_rows_stage2')} reason={stage2_payload.get('reason')}"
                    )
                    continue
                stage2_model = _train_stage_a_ranker(
                    X_tr_stage2,
                    y_tr_stage2,
                    w_tr_stage2,
                    float(len(y_tr_stage2)),
                )
                utility_pred_oos = np.asarray(stage2_model.predict(X_te), dtype=float)
                utility_pred_oos = np.maximum(utility_pred_oos, 0.0)

            trajectories.append(
                {
                    "combo": str(combo),
                    "n_train": int(len(train_idx)),
                    "n_test": int(len(test_idx)),
                    "n_eff": round(n_eff, 1),
                    "positive_rate_train": round(float(np.mean(y_tr)), 4),
                    "positive_rate_test": round(float(np.mean(y_te)), 4),
                    "auc_raw": round(float(auc_raw), 4),
                    "stage2_train_rows": int(stage2_payload.get("train_rows_stage2", 0)),
                    "stage2_valid": bool(stage2_payload.get("is_valid", True)),
                    "stage2_reason": str(stage2_payload.get("reason", "not_applicable")),
                }
            )
            print(f"  combo={combo} AUC_raw={auc_raw:.4f} train={len(train_idx)} test={len(test_idx)}")

        if TARGET_MODE == "cross_sectional_relative_activation" or PROBLEM_TYPE == "cross_sectional_ranking":
            group_cols = ["date", "cluster_name", "stage_a_selection_mode", "stage_a_group_eligible_count"]
            dedup = (
                test_df[group_cols]
                .drop_duplicates()
                .assign(combo=str(combo))
                .rename(columns={"stage_a_group_eligible_count": "eligible_count"})
            )
            target_policy_rows.extend(dedup.to_dict(orient="records"))

        test_rows = test_df.reset_index(drop=True)
        for row_idx, (_, row) in enumerate(test_rows.iterrows()):
            prediction_rows.append(
                {
                    "combo": str(combo),
                    "date": row["date"],
                    "event_date": row["date"],
                    "symbol": row["symbol"],
                    "y_stage_a": int(
                        (
                            y_te_truth[row_idx]
                            if PROBLEM_TYPE == "cross_sectional_ranking"
                            else y_te[row_idx]
                        )
                    ),
                    "p_stage_a_raw": float(p_oos_raw[row_idx]),
                    "rank_score_stage_a": float(p_oos_raw[row_idx]) if PROBLEM_TYPE == "cross_sectional_ranking" else np.nan,
                    "avg_tp_train": _coerce_float(row.get("avg_tp_train")),
                    "avg_sl_train": _coerce_float(row.get("avg_sl_train")),
                    "pnl_real": _coerce_float(row.get("pnl_real")),
                    "label": int(row["label"]) if pd.notna(row.get("label")) else np.nan,
                    "p_bma_pkf": _coerce_float(row.get("p_bma_pkf")),
                    "sigma_ewma": _coerce_float(row.get("sigma_ewma")),
                    "uniqueness": _coerce_float(row.get("uniqueness"), default=1.0),
                    "hmm_prob_bull": _coerce_float(row.get("hmm_prob_bull")),
                    "slippage_frac": _coerce_float(row.get("slippage_frac")),
                    "barrier_sl": _coerce_float(row.get("barrier_sl")),
                    "p0": _coerce_float(row.get("p0")),
                    "cluster_name": str(row.get("cluster_name") or ""),
                    "stage_a_eligible": bool(row.get("stage_a_eligible", False)),
                    "stage_a_score_realized": _coerce_float(row.get("stage_a_score_realized"), default=np.nan),
                    "stage_a_group_eligible_count": int(_coerce_float(row.get("stage_a_group_eligible_count"))),
                    "stage_a_date_eligible_count": int(_coerce_float(row.get("stage_a_date_eligible_count"))),
                    "stage_a_selection_mode": str(row.get("stage_a_selection_mode") or ""),
                    "stage_a_target_source": str(row.get("stage_a_target_source") or ""),
                    "stage_a_threshold_train": _coerce_float(row.get("stage_a_threshold_train"), default=np.nan),
                    "stage_a_threshold_source": str(row.get("stage_a_threshold_source") or ""),
                    "stage_a_train_positive_count_cluster": int(_coerce_float(row.get("stage_a_train_positive_count_cluster"))),
                    "stage_a_train_positive_count_global": int(_coerce_float(row.get("stage_a_train_positive_count_global"))),
                    "stage_a_utility_real": _coerce_float(row.get("stage_a_utility_real"), default=np.nan),
                    "stage_a_utility_surplus": _coerce_float(row.get("stage_a_utility_surplus"), default=np.nan),
                    "p_activate_raw_stage_a": float(p_oos_raw[row_idx]) if TARGET_MODE == "two_stage_activation_utility" else np.nan,
                    "utility_surplus_pred_stage_a": float(utility_pred_oos[row_idx]) if TARGET_MODE == "two_stage_activation_utility" else np.nan,
                    "stage2_training_policy": str(stage2_payload.get("stage2_training_policy", "not_applicable")),
                    "y_stage_a_truth_top1": int(_coerce_float(row.get("y_stage_a_truth_top1"))),
                    "rank_target_stage_a": _coerce_float(row.get("rank_target_stage_a")),
                }
            )

    if not prediction_rows:
        raise RuntimeError("Stage A CPCV did not produce any OOS predictions")

    oos_df = pd.DataFrame(prediction_rows)
    if PROBLEM_TYPE == "cross_sectional_ranking":
        cluster_summary = []
        cluster_mode = "not_applicable_rank_score"
        artifact_path = None
        positive_rate_oos = float(oos_df["y_stage_a"].mean())
        auc_raw_global = roc_auc_score(oos_df["y_stage_a"], oos_df["p_stage_a_raw"]) if oos_df["y_stage_a"].nunique() >= 2 else 0.5
        auc_cal_global = None
        ece_raw = None
        ece_cal = None

        aggregated_predictions = _aggregate_stage_a_predictions(oos_df)
        aggregated_predictions["rank_score_stage_a"] = pd.to_numeric(
            aggregated_predictions.get("p_stage_a_raw"),
            errors="coerce",
        ).fillna(0.0)
        aggregated_predictions, ranking_summary = _apply_cross_sectional_ranking_proxy(aggregated_predictions)
        aggregated_predictions = phase4._compute_phase4_sizing(
            aggregated_predictions,
            prob_col="p_stage_a_calibrated",
            prefix="stage_a",
            avg_tp_col="avg_tp_train",
            avg_sl_col="avg_sl_train",
        )
        aggregated_predictions = phase4._attach_execution_pnl(
            aggregated_predictions,
            position_col="position_usdt_stage_a",
            output_col="pnl_exec_stage_a",
        )
        snapshot_proxy = _build_stage_a_snapshot_proxy(aggregated_predictions)
        operational_report = _build_stage_a_operational_report(aggregated_predictions, snapshot_proxy)
        final_position_counts = _compute_final_position_counts(aggregated_predictions)
        gate = _evaluate_stage_a_gate(
            operational_report,
            top1_hit_rate=ranking_summary.get("top1_hit_rate"),
            naive_top1_hit_rate=ranking_summary.get("naive_top1_hit_rate"),
            position_gt_0_over_min_alloc_rows_final=final_position_counts.get("position_gt_0_over_min_alloc_rows_final"),
        )
        target_selection_policy = {
            "problem_type": PROBLEM_TYPE,
            "min_eligible_per_date_cluster": MIN_ELIGIBLE_PER_DATE_CLUSTER,
            "fallback_policy": TARGET_GROUP_FALLBACK_POLICY,
            "support_rationale": (
                f"Top1 within a group of size 1 is tautological; min support {MIN_ELIGIBLE_PER_DATE_CLUSTER} "
                "forces actual within-cluster competition before delegating to the date-universe winner."
            ),
            "groups_local_selection": int(ranking_summary.get("groups_local_selection", 0)),
            "groups_fallback_selection": int(ranking_summary.get("groups_fallback_selection", 0)),
            "groups_without_eligible": int(ranking_summary.get("groups_without_eligible", 0)),
            "groups_total": int(ranking_summary.get("groups_total", 0)),
            "target_cluster_mode": target_cluster_mode,
            "target_cluster_artifact_path": target_cluster_artifact_path,
        }
        latest_top_candidate_stats = {
            "latest_top_candidate_rank_pct": 1.0 if ranking_summary.get("predicted_top_candidate_per_date") else 0.0,
            "latest_top_candidate_score_gap_vs_second": ranking_summary.get("rank_margin_latest", 0.0),
        }
    else:
        calibration_df = oos_df.rename(columns={"y_stage_a": "y_meta", "p_stage_a_raw": "p_meta_raw"})
        calibrators, cluster_summary, symbol_to_cluster, cluster_mode, artifact_path = phase4._fit_cluster_calibrators(calibration_df)
        oos_df["cluster_name"] = oos_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
        oos_df["p_stage_a_calibrated"] = phase4._apply_cluster_calibration(calibration_df, calibrators, symbol_to_cluster)
        aggregated_predictions = _aggregate_stage_a_predictions(oos_df)
        ranking_summary = {}
        if TARGET_MODE == "two_stage_activation_utility":
            oos_df["p_activate_calibrated_stage_a"] = oos_df["p_stage_a_calibrated"]
            aggregated_predictions["p_activate_calibrated_stage_a"] = pd.to_numeric(
                aggregated_predictions.get("p_activate_calibrated_stage_a", aggregated_predictions.get("p_stage_a_calibrated")),
                errors="coerce",
            ).fillna(0.0)
            aggregated_predictions, ranking_summary = _apply_two_stage_activation_utility_proxy(aggregated_predictions)
            sizing_prob_col = "decision_score_stage_a"
        else:
            sizing_prob_col = "p_stage_a_calibrated"
        oos_df = phase4._compute_phase4_sizing(
            oos_df,
            prob_col="p_stage_a_calibrated",
            prefix="stage_a",
            avg_tp_col="avg_tp_train",
            avg_sl_col="avg_sl_train",
        )
        oos_df = phase4._attach_execution_pnl(oos_df, position_col="position_usdt_stage_a", output_col="pnl_exec_stage_a")
        aggregated_predictions = phase4._compute_phase4_sizing(
            aggregated_predictions,
            prob_col=sizing_prob_col,
            prefix="stage_a",
            avg_tp_col="avg_tp_train",
            avg_sl_col="avg_sl_train",
        )
        aggregated_predictions = phase4._attach_execution_pnl(
            aggregated_predictions,
            position_col="position_usdt_stage_a",
            output_col="pnl_exec_stage_a",
        )

        snapshot_proxy = _build_stage_a_snapshot_proxy(aggregated_predictions)
        operational_report = _build_stage_a_operational_report(aggregated_predictions, snapshot_proxy)
        final_position_counts = _compute_final_position_counts(aggregated_predictions)

        auc_raw_global = roc_auc_score(oos_df["y_stage_a"], oos_df["p_stage_a_raw"]) if oos_df["y_stage_a"].nunique() >= 2 else 0.5
        auc_cal_global = (
            roc_auc_score(oos_df["y_stage_a"], oos_df["p_stage_a_calibrated"]) if oos_df["y_stage_a"].nunique() >= 2 else 0.5
        )
        ece_raw = phase4._compute_ece(oos_df["p_stage_a_raw"].values, oos_df["y_stage_a"].values)
        ece_cal = phase4._compute_ece(oos_df["p_stage_a_calibrated"].values, oos_df["y_stage_a"].values)
        positive_rate_oos = float(oos_df["y_stage_a"].mean())
        gate = _evaluate_stage_a_gate(operational_report, ece_calibrated=ece_cal, positive_rate_oos=positive_rate_oos)
        if TARGET_MODE == "cross_sectional_relative_activation":
            target_selection_policy = {
                "target_mode": TARGET_MODE,
                "min_eligible_per_date_cluster": MIN_ELIGIBLE_PER_DATE_CLUSTER,
                "fallback_policy": TARGET_GROUP_FALLBACK_POLICY,
                "support_rationale": (
                    f"Top1 within a group of size 1 is tautological; min support {MIN_ELIGIBLE_PER_DATE_CLUSTER} "
                    "forces actual within-cluster competition before delegating to the date-universe winner."
                ),
                "groups_local_target": int(sum(1 for row in target_policy_rows if row.get("stage_a_selection_mode") == "cluster_local_top1")),
                "groups_fallback_target": int(sum(1 for row in target_policy_rows if row.get("stage_a_selection_mode") == "date_universe_fallback")),
                "groups_without_eligible": int(sum(1 for row in target_policy_rows if row.get("stage_a_selection_mode") == "no_eligible")),
                "groups_total": int(len(target_policy_rows)),
                "target_cluster_mode": target_cluster_mode,
                "target_cluster_artifact_path": target_cluster_artifact_path,
            }
        elif TARGET_MODE == "two_stage_activation_utility":
            target_selection_policy = {
                "target_mode": TARGET_MODE,
                "primary_q": round(float(PRIMARY_Q), 4),
                "q_candidates": [round(float(item), 4) for item in TARGET_Q_CANDIDATES],
                "selection_basis": "ex_ante_precommitted",
                "stage1_training": "calibrated_binary_activation_classifier",
                "stage2_training_policy": "activated_train_subset_only",
                "min_stage2_train_rows": int(MIN_STAGE2_TRAIN_ROWS),
                "min_eligible_per_date_cluster": MIN_ELIGIBLE_PER_DATE_CLUSTER,
                "fallback_policy": TARGET_GROUP_FALLBACK_POLICY,
                "groups_local_selection": int(ranking_summary.get("groups_local_selection", 0)),
                "groups_fallback_selection": int(ranking_summary.get("groups_fallback_selection", 0)),
                "groups_without_eligible": int(ranking_summary.get("groups_without_eligible", 0)),
                "groups_total": int(ranking_summary.get("groups_total", 0)),
                "target_cluster_mode": target_cluster_mode,
                "target_cluster_artifact_path": target_cluster_artifact_path,
                "threshold_coverage": _summarize_cluster_threshold_coverage(target_policy_rows),
            }
        else:
            target_selection_policy = _summarize_cluster_threshold_coverage(target_policy_rows)
            target_selection_policy["target_cluster_mode"] = target_cluster_mode
            target_selection_policy["target_cluster_artifact_path"] = target_cluster_artifact_path
        latest_top_candidate_stats = _compute_latest_top_candidate_stats(snapshot_proxy)

    source_hashes = _collect_source_hashes(model_path)
    manifest = _stage_a_manifest(
        model_path=model_path,
        research_path=research_path,
        feature_cols=feature_cols,
        source_hashes=source_hashes,
        target_selection_policy=target_selection_policy,
    )
    reference_report = _load_stage_a_report(model_path, REFERENCE_EXPERIMENT_NAME)

    report = {
        "experiment_name": EXPERIMENT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "status": gate["status"],
        "target_name": _target_name(),
        "target_definition": _target_definition(),
        "problem_type": PROBLEM_TYPE,
        "n_cpcv_trajectories": int(len(trajectories)),
        "selected_features": feature_cols,
        "operational_proxy": {
            **operational_report,
            "final_position_counts": final_position_counts,
            "headroom_real": gate["headroom_real_documented"],
            "latest_snapshot_max_p_stage_a_calibrated": operational_report.get("sparsity", {}).get("latest_snapshot_max_p_meta_calibrated"),
            **latest_top_candidate_stats,
            "subperiod_summary": phase4._summarize_subperiods(operational_report.get("subperiods", [])),
        },
        "gate_do_experimento_stage_a": gate,
        "recommend_continue_to_stage_b": gate["status"] == "PASS",
        "comparison_vs_previous_stage_a": None,
        "comparison_vs_stage_a_baselines": {},
        "source_hashes": source_hashes,
    }
    if PROBLEM_TYPE == "cross_sectional_ranking":
        report["classification_metrics"] = {
            "positive_rate_oos": round(float(positive_rate_oos), 4),
            "auc_raw_global": round(float(auc_raw_global), 4),
            "auc_calibrated_global": None,
            "ece_raw": None,
            "ece_calibrated": None,
            "target_prevalence_by_year": _build_prevalence_by_year(oos_df),
            "target_prevalence_by_cluster": _build_prevalence_summary(oos_df, "cluster_name"),
            "target_prevalence_by_combo": _build_prevalence_summary(oos_df, "combo"),
            "target_selection_policy": target_selection_policy,
            "trajectories": trajectories,
        }
        report["ranking_metrics"] = {
            "eligible_candidates_per_date": ranking_summary.get("eligible_candidates_per_date", []),
            "truth_top1_count_per_date": ranking_summary.get("truth_top1_count_per_date", []),
            "groups_local_selection": ranking_summary.get("groups_local_selection", 0),
            "groups_fallback_selection": ranking_summary.get("groups_fallback_selection", 0),
            "groups_without_eligible": ranking_summary.get("groups_without_eligible", 0),
            "groups_total": ranking_summary.get("groups_total", 0),
            "top1_hit_rate": ranking_summary.get("top1_hit_rate", 0.0),
            "naive_top1_hit_rate": ranking_summary.get("naive_top1_hit_rate", 0.0),
            "mrr": ranking_summary.get("mrr", 0.0),
            "rank_margin_latest": ranking_summary.get("rank_margin_latest", 0.0),
            "predicted_top_candidate_per_date": ranking_summary.get("predicted_top_candidate_per_date", []),
            "target_selection_policy": target_selection_policy,
        }
    else:
        report["classification_metrics"] = {
            "positive_rate_oos": round(float(positive_rate_oos), 4),
            "auc_raw_global": round(float(auc_raw_global), 4),
            "auc_calibrated_global": round(float(auc_cal_global), 4),
            "ece_raw": round(float(ece_raw), 4),
            "ece_calibrated": round(float(ece_cal), 4),
            "target_prevalence_by_year": _build_prevalence_by_year(oos_df),
            "target_prevalence_by_cluster": _build_prevalence_summary(oos_df, "cluster_name"),
            "target_prevalence_by_combo": _build_prevalence_summary(oos_df, "combo"),
            "eligible_candidates_per_date": _build_per_date_count_list(oos_df, value_col="stage_a_eligible", output_col="eligible_candidates"),
            "target_positive_count_per_date": _build_per_date_count_list(oos_df, value_col="y_stage_a", output_col="target_positive_count"),
            "target_selection_policy": target_selection_policy,
            "cluster_calibration_mode": cluster_mode,
            "cluster_calibration_artifact": artifact_path,
            "cluster_calibration_summary": cluster_summary,
            "trajectories": trajectories,
        }
    report["comparison_vs_previous_stage_a"] = _build_comparison_vs_reference(report, reference_report)
    report["comparison_vs_stage_a_baselines"] = _build_comparisons_vs_baselines(model_path, report)

    predictions_path = research_path / "stage_a_predictions.parquet"
    report_path = research_path / "stage_a_report.json"
    snapshot_path = research_path / "stage_a_snapshot_proxy.parquet"
    manifest_path = research_path / "stage_a_manifest.json"

    oos_df.to_parquet(predictions_path, index=False)
    snapshot_proxy.to_parquet(snapshot_path, index=False)
    phase4._atomic_json_write(report_path, report)
    phase4._atomic_json_write(manifest_path, manifest)

    return {
        "research_path": str(research_path),
        "predictions_path": str(predictions_path),
        "report_path": str(report_path),
        "snapshot_path": str(snapshot_path),
        "manifest_path": str(manifest_path),
        "status": gate["status"],
        "continue_to_stage_b": bool(report["recommend_continue_to_stage_b"]),
        "sharpe": operational_report.get("sharpe", 0.0),
        "dsr_honest": operational_report.get("dsr_honest", 0.0),
        "historical_active_count": operational_report.get("n_active", 0),
        "latest_active_count": operational_report.get("activation_funnel", {}).get("latest_snapshot_active_count", 0),
        "target_name": _target_name(),
        "top1_hit_rate": ranking_summary.get("top1_hit_rate") if PROBLEM_TYPE == "cross_sectional_ranking" else None,
    }


def main() -> None:
    result = run_stage_a_experiment()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
