#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

MODEL_PATH = Path(os.getenv("MODEL_ARTIFACTS_PATH", "/data/models"))
PARQUET_BASE = Path(os.getenv("PARQUET_BASE_PATH", "/data/parquet"))
FEATURES_PATH = MODEL_PATH / "features"
PHASE2_PATH = MODEL_PATH / "phase2"
PHASE3_PATH = MODEL_PATH / "phase3"
PHASE4_PATH = MODEL_PATH / "phase4"
PHASE2_REPORT_PATH = PHASE2_PATH / "diagnostic_report.json"
PHASE3_REPORT_PATH = PHASE3_PATH / "diagnostic_report.json"
PHASE3_EXCLUSIONS_PATH = PHASE3_PATH / "phase3_exclusions.json"
PHASE4_REPORT_PATH = PHASE4_PATH / "phase4_report_v4.json"
PHASE4_SNAPSHOT_PATH = PHASE4_PATH / "phase4_execution_snapshot.parquet"
PHASE4_AGGREGATED_PATH = PHASE4_PATH / "phase4_aggregated_predictions.parquet"
PHASE4_GATE_DIAGNOSTIC_PATH = PHASE4_PATH / "phase4_gate_diagnostic.json"
PHASE4_OOS_PREDICTIONS_PATH = PHASE4_PATH / "phase4_oos_predictions.parquet"
MAIN_SOURCE_PATH = Path(__file__).resolve().parent / "main.py"
PHASE4_CPCV_SOURCE_PATH = Path(__file__).resolve().parent / "phase4_cpcv.py"
KELLY_CVAR_SOURCE_PATH = Path(__file__).resolve().parent / "sizing" / "kelly_cvar.py"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

CLASS_HARD_BLOCKER = "HARD_BLOCKER"
CLASS_GOVERNANCA = "GOVERNANCA"
CLASS_NAO_AUDITADO = "NAO_AUDITADO_DIRETAMENTE"
CLASS_NAO_BLOCKER = "NAO_BLOCKER"

AUDIT_DIRECT = "DIRECT_OFFICIAL_ARTIFACT"
AUDIT_DERIVED = "DERIVED_AUXILIARY_ONLY"
AUDIT_MISSING = "NOT_MATERIALIZED"
AUDIT_STRUCTURAL = "STRUCTURAL_CODE_ONLY"

IA_FEATURE_COLUMNS = {
    "score_ia_bull",
    "score_ia_bear",
    "ias_net_score",
}
PROBABILITY_COLUMN_CANDIDATES = {
    "p_pred",
    "p_pred_oos",
    "p_gbm",
    "p_lgbm",
    "p_phase2",
    "p_oos",
    "prob",
    "prob_oos",
    "proba",
    "proba_oos",
}
LABEL_COLUMN_CANDIDATES = {
    "y",
    "y_true",
    "y_label",
    "label",
    "target",
    "y_target",
}

VIGENTE_THRESHOLDS = {
    "dsr_honesto": {"metric": "dsr_honest", "operator": ">", "value": 0.95, "n_trials_min": 5000, "source": str(PHASE4_CPCV_SOURCE_PATH)},
    "dsr_invalidation_global": None,
    "sharpe_oos": {"metric": "fallback.sharpe", "operator": ">=", "value": 0.70, "source": str(PHASE4_CPCV_SOURCE_PATH)},
    "subperiodos": {"metric": "positive_subperiods", "operator": ">=", "value": 4, "source": str(PHASE4_CPCV_SOURCE_PATH)},
    "auc_oos_phase2": None,
    "ia_alpha_r2": None,
    "cvar_empirico_persistido": None,
}
SPEC_THRESHOLDS = {
    "dsr_honesto": {"metric": "dsr_honest", "operator": ">", "value": 0.95, "n_trials_min": 5000, "spec_section": "[10] / [10.1] / [15.2]"},
    "dsr_invalidation_global": {"metric": "dsr", "operator": ">", "value": 0.0, "spec_section": "[15.2] / [17]"},
    "sharpe_oos": {"metric": "sharpe_oos", "operator": ">=", "value": 0.70, "spec_section": "[15.2] / [17]"},
    "subperiodos": {"metric": "positive_subperiods", "operator": ">=", "value": 4, "denominator": 6, "spec_section": "[16] / [17]"},
    "auc_oos_phase2": {"metric": "auc_oos", "operator": ">", "value": 0.55, "spec_section": "[5] / [17]"},
    "ia_alpha_r2": {
        "alpha_retido_pct_pass": 0.30,
        "alpha_retido_pct_remove": 0.15,
        "r2_ortho_remove": 0.80,
        "spec_section": "[8] / [17]",
    },
    "cvar_empirico_persistido": {"metric": "portfolio_cvar", "operator": "<=", "value": 0.15, "spec_section": "[10.2] / [17]"},
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        return float(value)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fin:
        return json.load(fin)


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.read_pickle(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fout:
        json.dump(_json_ready(payload), fout, indent=2, ensure_ascii=True)
    tmp.replace(path)


def _detect_repo_root() -> Path | None:
    candidates: list[Path] = []
    env_root = os.getenv("SNIPER_WORKSPACE_PATH", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    workspace = Path("/workspace")
    if workspace.exists():
        candidates.append(workspace)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docker-compose.yml").exists():
            candidates.append(parent)
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return None


def _run_git_command(repo_root: Path, *args: str) -> tuple[bool, str]:
    if shutil.which("git") is None:
        return False, "git_cli_unavailable"
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or f"git_exit_{completed.returncode}").strip()
    return True, completed.stdout.strip()


def _read_git_head(repo_root: Path) -> tuple[str | None, str | None]:
    head_path = repo_root / ".git" / "HEAD"
    if not head_path.exists():
        return None, None
    content = head_path.read_text(encoding="utf-8", errors="ignore").strip()
    if content.startswith("ref:"):
        ref = content.split(" ", 1)[1].strip()
        ref_path = repo_root / ".git" / ref.replace("/", os.sep)
        head = ref_path.read_text(encoding="utf-8", errors="ignore").strip() if ref_path.exists() else None
        return Path(ref).name, head
    return "detached", content


def collect_git_baseline(repo_root: Path | None = None) -> dict[str, Any]:
    env_branch = os.getenv("SNIPER_GIT_BRANCH")
    env_head = os.getenv("SNIPER_GIT_HEAD")
    env_status = os.getenv("SNIPER_GIT_STATUS_SHORT")
    env_diff = os.getenv("SNIPER_GIT_DIFF_STAT")
    env_worktree = os.getenv("SNIPER_GIT_WORKTREE_STATE")
    if any(value is not None for value in [env_branch, env_head, env_status, env_diff, env_worktree]):
        repo_root = repo_root or _detect_repo_root()
        status_short = [line for line in (env_status or "").splitlines() if line.strip()]
        return {
            "repo_root": None if repo_root is None else str(repo_root),
            "branch": env_branch,
            "head": env_head,
            "status_short": status_short,
            "diff_stat": env_diff or "unavailable",
            "working_tree_state": env_worktree or ("dirty" if status_short else "clean"),
            "git_cli_available": shutil.which("git") is not None,
            "source": "env_override",
        }

    repo_root = repo_root or _detect_repo_root()
    if repo_root is None:
        return {
            "repo_root": None,
            "branch": None,
            "head": None,
            "status_short": [],
            "diff_stat": "repo_root_unavailable",
            "working_tree_state": "unknown",
            "git_cli_available": False,
        }

    branch, head = _read_git_head(repo_root)
    status_short: list[str] = []
    diff_stat = "unavailable"
    working_tree_state = "unknown"
    git_cli_available = shutil.which("git") is not None

    ok_branch, branch_out = _run_git_command(repo_root, "branch", "--show-current")
    if ok_branch and branch_out:
        branch = branch_out
    ok_head, head_out = _run_git_command(repo_root, "rev-parse", "HEAD")
    if ok_head and head_out:
        head = head_out
    ok_status, status_out = _run_git_command(repo_root, "status", "--short")
    if ok_status:
        status_short = [line for line in status_out.splitlines() if line.strip()]
        working_tree_state = "dirty" if status_short else "clean"
    ok_diff, diff_out = _run_git_command(repo_root, "diff", "--stat")
    if ok_diff:
        diff_stat = diff_out or "clean"

    return {
        "repo_root": str(repo_root),
        "branch": branch,
        "head": head,
        "status_short": status_short,
        "diff_stat": diff_stat,
        "working_tree_state": working_tree_state,
        "git_cli_available": git_cli_available,
        "source": "git_cli_or_head_fallback",
    }


def collect_paper_state(redis_url: str = REDIS_URL) -> dict[str, Any]:
    state = {
        "redis_url": redis_url,
        "status": "unavailable",
        "sniper_key_count": None,
        "sniper_key_sample": [],
        "redis_namespace_clean": None,
        "bridge_logs_check": "not_available_from_ml_container",
    }
    try:
        import redis
    except Exception as exc:
        state["error"] = f"redis_import_failed: {exc}"
        return state

    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        keys = sorted(client.scan_iter(match="sniper:*"))
    except Exception as exc:
        state["error"] = f"redis_access_failed: {exc}"
        return state

    state.update({
        "status": "ok",
        "sniper_key_count": len(keys),
        "sniper_key_sample": keys[:20],
        "redis_namespace_clean": len(keys) == 0,
    })
    return state


def summarize_snapshot(snapshot_path: Path = PHASE4_SNAPSHOT_PATH) -> dict[str, Any]:
    if not snapshot_path.exists():
        return {"path": str(snapshot_path), "exists": False}
    df = _safe_read_parquet(snapshot_path)
    if df.empty:
        return {"path": str(snapshot_path), "exists": True, "rows": 0, "symbols": 0, "active_count": 0}

    date_col = "date" if "date" in df.columns else None
    if date_col is not None:
        dates = pd.to_datetime(df[date_col], utc=True, errors="coerce").dt.tz_localize(None)
        date_min = dates.min()
        date_max = dates.max()
    else:
        date_min = None
        date_max = None
    return {
        "path": str(snapshot_path),
        "exists": True,
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()) if "symbol" in df.columns else int(len(df)),
        "active_count": int((df["side"] != "FLAT").sum()) if "side" in df.columns else 0,
        "max_p_bma_pkf": round(float(pd.to_numeric(df.get("p_bma_pkf"), errors="coerce").max()), 10) if "p_bma_pkf" in df.columns else None,
        "max_position_usdt": round(float(pd.to_numeric(df.get("position_usdt"), errors="coerce").max()), 4) if "position_usdt" in df.columns else None,
        "max_kelly_frac": round(float(pd.to_numeric(df.get("kelly_frac"), errors="coerce").max()), 6) if "kelly_frac" in df.columns else None,
        "date_min": None if pd.isna(date_min) else str(date_min.date()),
        "date_max": None if pd.isna(date_max) else str(date_max.date()),
    }


def summarize_aggregated_predictions(
    aggregated_path: Path = PHASE4_AGGREGATED_PATH,
    phase4_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not aggregated_path.exists():
        return {"path": str(aggregated_path), "exists": False}
    df = _safe_read_parquet(aggregated_path)
    if df.empty:
        return {"path": str(aggregated_path), "exists": True, "rows": 0}
    dates = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.tz_localize(None) if "date" in df.columns else pd.Series(dtype="datetime64[ns]")
    latest = dates.max() if not dates.empty else pd.NaT
    latest_rows = df.loc[dates == latest].copy() if not pd.isna(latest) else pd.DataFrame()
    threshold = float((phase4_report or _read_json(PHASE4_REPORT_PATH)).get("fallback", {}).get("threshold", 0.80))
    latest_scores = pd.to_numeric(latest_rows.get("p_bma_pkf"), errors="coerce") if not latest_rows.empty else pd.Series(dtype=float)
    return {
        "path": str(aggregated_path),
        "exists": True,
        "rows": int(len(df)),
        "symbols": int(df["symbol"].nunique()) if "symbol" in df.columns else None,
        "date_min": None if dates.empty or pd.isna(dates.min()) else str(dates.min().date()),
        "date_max": None if dates.empty or pd.isna(latest) else str(latest.date()),
        "latest_count_above_threshold": int((latest_scores >= threshold).sum()) if not latest_rows.empty else 0,
        "latest_max_p_bma_pkf": round(float(latest_scores.max()), 10) if not latest_rows.empty and latest_scores.notna().any() else None,
        "threshold": threshold,
    }


def _inspect_parquet_columns(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        frame = _safe_read_parquet(path)
    except Exception:
        return []
    return [str(col) for col in frame.columns]


def _scan_dir_for_columns(paths: list[Path], target_columns: set[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path in paths:
        columns = _inspect_parquet_columns(path)
        overlap = sorted(target_columns.intersection(columns))
        if overlap:
            hits[str(path)] = overlap
    return hits


def collect_ia_official_status(
    phase4_report: dict[str, Any] | None = None,
    features_path: Path = FEATURES_PATH,
    phase3_path: Path = PHASE3_PATH,
    phase4_paths: list[Path] | None = None,
    phase4_source_path: Path = PHASE4_CPCV_SOURCE_PATH,
) -> dict[str, Any]:
    phase4_report = phase4_report or _read_json(PHASE4_REPORT_PATH)
    selected_features = [str(feature) for feature in phase4_report.get("selected_features", [])]
    selected_feature_hits = sorted(IA_FEATURE_COLUMNS.intersection(selected_features))

    feature_files = sorted(features_path.glob("*.parquet"))
    meta_files = sorted(phase3_path.glob("*_meta.parquet"))
    phase4_paths = phase4_paths or [
        PHASE4_SNAPSHOT_PATH,
        PHASE4_AGGREGATED_PATH,
        PHASE4_OOS_PREDICTIONS_PATH,
    ]

    feature_hits = _scan_dir_for_columns(feature_files, IA_FEATURE_COLUMNS)
    meta_hits = _scan_dir_for_columns(meta_files, IA_FEATURE_COLUMNS)
    phase4_hits = _scan_dir_for_columns(phase4_paths, IA_FEATURE_COLUMNS)
    phase4_source_mentions_ia = False
    if phase4_source_path.exists():
        phase4_source_text = phase4_source_path.read_text(encoding="utf-8", errors="ignore")
        phase4_source_mentions_ia = any(token in phase4_source_text for token in IA_FEATURE_COLUMNS)

    official_path_uses_ia = bool(selected_feature_hits or feature_hits or meta_hits or phase4_hits)
    return {
        "official_path_uses_ia": official_path_uses_ia,
        "binary_answer": "SIM" if official_path_uses_ia else "NAO",
        "selected_features": selected_features,
        "selected_feature_ia_overlap": selected_feature_hits,
        "feature_store_ia_hits": feature_hits,
        "phase3_meta_ia_hits": meta_hits,
        "phase4_artifact_ia_hits": phase4_hits,
        "phase4_source_mentions_ia_candidates": phase4_source_mentions_ia,
        "cleanup_needed": [] if official_path_uses_ia else [
            "manifest_or_config_official_declaring_ia_path_disabled",
            "explicit_report_field_ia_path_status_disabled_or_not_in_current_official_path",
        ],
        "source_artifacts": [
            str(PHASE4_REPORT_PATH),
            str(features_path),
            str(phase3_path),
            *[str(path) for path in phase4_paths],
            str(phase4_source_path),
        ],
    }


def _find_direct_phase2_auc_artifacts(phase2_path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not phase2_path.exists():
        return findings
    for path in sorted(phase2_path.glob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                payload = _read_json(path)
            except Exception:
                continue
            payload_text = json.dumps(payload, ensure_ascii=True).lower()
            if "auc_oos" in payload_text or ("lightgbm" in payload_text and "auc" in payload_text):
                findings.append({"path": str(path), "type": "json_metric"})
        elif suffix == ".parquet":
            columns = set(_inspect_parquet_columns(path))
            if columns.intersection(PROBABILITY_COLUMN_CANDIDATES) and columns.intersection(LABEL_COLUMN_CANDIDATES):
                findings.append({"path": str(path), "type": "parquet_predictions", "columns": sorted(columns)})
    return findings


def _derive_auc_from_phase3_meta(phase3_path: Path) -> dict[str, Any]:
    pooled_parts: list[pd.DataFrame] = []
    per_symbol: list[dict[str, Any]] = []
    for path in sorted(phase3_path.glob("*_meta.parquet")):
        try:
            df = _safe_read_parquet(path)
        except Exception:
            continue
        if "p_bma" not in df.columns or "y_target" not in df.columns:
            continue
        frame = pd.DataFrame({
            "symbol": path.stem.replace("_meta", ""),
            "p_bma": pd.to_numeric(df["p_bma"], errors="coerce"),
            "y_target": pd.to_numeric(df["y_target"], errors="coerce"),
        }).dropna()
        if frame.empty or frame["y_target"].nunique() < 2:
            continue
        auc = float(roc_auc_score(frame["y_target"].astype(int), frame["p_bma"].astype(float)))
        per_symbol.append({"symbol": path.stem.replace("_meta", ""), "auc": round(auc, 6), "rows": int(len(frame))})
        pooled_parts.append(frame)
    if not pooled_parts:
        return {
            "available": False,
            "auc_oos_pooled": None,
            "symbols_with_auc": 0,
            "count_above_055": 0,
            "count_le_055": 0,
            "per_symbol": [],
        }
    pooled = pd.concat(pooled_parts, ignore_index=True)
    pooled_auc = float(roc_auc_score(pooled["y_target"].astype(int), pooled["p_bma"].astype(float)))
    above = sum(1 for row in per_symbol if row["auc"] > 0.55)
    below = sum(1 for row in per_symbol if row["auc"] <= 0.55)
    ordered = sorted(per_symbol, key=lambda row: row["auc"])
    return {
        "available": True,
        "auc_oos_pooled": round(pooled_auc, 6),
        "symbols_with_auc": len(per_symbol),
        "count_above_055": above,
        "count_le_055": below,
        "per_symbol": per_symbol,
        "bottom_5": ordered[:5],
        "top_5": ordered[-5:],
    }


def collect_phase2_auc_provenance(
    phase2_report: dict[str, Any] | None = None,
    phase2_path: Path = PHASE2_PATH,
    phase3_path: Path = PHASE3_PATH,
    main_source_path: Path = MAIN_SOURCE_PATH,
) -> dict[str, Any]:
    phase2_report = phase2_report or _read_json(PHASE2_REPORT_PATH)
    direct_artifacts = _find_direct_phase2_auc_artifacts(phase2_path)
    derived_auc = _derive_auc_from_phase3_meta(phase3_path)
    logical_location = (
        phase2_report.get("pbma_presence", {}).get("logical_location")
        if isinstance(phase2_report.get("pbma_presence"), dict)
        else None
    )
    main_source_text = main_source_path.read_text(encoding="utf-8", errors="ignore") if main_source_path.exists() else ""
    phase3_meta_is_downstream = (
        "Meta-Labeling pipeline" in main_source_text
        and "save_phase3_results" in main_source_text
        and '"p_bma": meta_result["p_bma"]' in main_source_text
    )
    lineage_equivalence_proven = bool(direct_artifacts)
    directly_auditable = bool(direct_artifacts or lineage_equivalence_proven)
    explanation = (
        "No direct official phase2 artifact with OOS probabilities/labels or materialized AUC was found. "
        "The phase2 diagnostic only points to /data/models/phase3/*_meta.parquet for pbma presence, and main.py "
        "materializes p_bma inside the downstream meta-labeling step before save_phase3_results(). "
        "Therefore the pooled AUC derived from phase3 meta is auxiliary and does not close the official "
        "'GBM AUC OOS > 0.55 walk-forward' gate."
        if not directly_auditable
        else "Official direct phase2 AUC artifact found."
    )
    return {
        "directly_auditable": directly_auditable,
        "official_phase2_auc_artifact_found": bool(direct_artifacts),
        "direct_phase2_auc_artifacts": direct_artifacts,
        "lineage_points_to_phase3_meta": logical_location == "/data/models/phase3/*_meta.parquet",
        "phase3_meta_is_downstream_meta_labeling": phase3_meta_is_downstream,
        "lineage_equivalence_proven": lineage_equivalence_proven,
        "derived_auc_from_phase3_meta": derived_auc,
        "official_gate_explanation": explanation,
        "source_artifacts": [
            str(PHASE2_REPORT_PATH),
            str(phase2_path),
            str(phase3_path),
            str(main_source_path),
        ],
    }


def collect_cvar_empirical_audit(
    phase4_report: dict[str, Any] | None = None,
    main_source_path: Path = MAIN_SOURCE_PATH,
    kelly_cvar_source_path: Path = KELLY_CVAR_SOURCE_PATH,
    phase3_path: Path = PHASE3_PATH,
    phase4_paths: list[Path] | None = None,
) -> dict[str, Any]:
    if phase4_report is None:
        phase4_report = _read_json(PHASE4_REPORT_PATH)
    main_text = main_source_path.read_text(encoding="utf-8", errors="ignore") if main_source_path.exists() else ""
    kelly_text = kelly_cvar_source_path.read_text(encoding="utf-8", errors="ignore") if kelly_cvar_source_path.exists() else ""
    structure_present = (
        "PORTFOLIO_CVAR_LIMIT" in main_text
        and "compute_cvar_stress" in main_text
        and "reduction = CVAR_LIMIT / max(cvar_stress, 1e-8)" in main_text
        and "MAX_KELLY_CAP       = 0.08" in kelly_text
        and "CVAR_LIMIT          = 0.15" in kelly_text
    )
    phase4_paths = phase4_paths or [PHASE4_SNAPSHOT_PATH, PHASE4_AGGREGATED_PATH, PHASE4_OOS_PREDICTIONS_PATH]
    artifact_hits: dict[str, list[str]] = {}
    for path in sorted(phase3_path.glob("*_sizing.parquet")) + phase4_paths:
        columns = [column for column in _inspect_parquet_columns(path) if "cvar" in column.lower()]
        if columns:
            artifact_hits[str(path)] = columns
    phase4_json_has_cvar = "cvar" in json.dumps(phase4_report, ensure_ascii=True).lower()
    persisted_empirical_artifact_found = bool(artifact_hits or phase4_json_has_cvar)
    return {
        "structure_present": structure_present,
        "persisted_empirical_artifact_found": persisted_empirical_artifact_found,
        "artifact_hits": artifact_hits,
        "phase4_report_mentions_cvar": phase4_json_has_cvar,
        "source_artifacts": [
            str(main_source_path),
            str(kelly_cvar_source_path),
            str(PHASE4_REPORT_PATH),
            str(phase3_path),
            *[str(path) for path in phase4_paths],
        ],
    }


def _threshold_status(metric_value: float | None, threshold: dict[str, Any] | None) -> str:
    if threshold is None or metric_value is None:
        return "N/A"
    operator = threshold.get("operator")
    value = threshold.get("value")
    if operator == ">" and metric_value > value:
        return "PASS"
    if operator == ">=" and metric_value >= value:
        return "PASS"
    if operator == "<" and metric_value < value:
        return "PASS"
    if operator == "<=" and metric_value <= value:
        return "PASS"
    return "FAIL"


def _blocker_entry(
    *,
    classification: str,
    reason: str,
    practical_impact: str,
    alone_blocks_advance: bool,
    audit_status: str,
    source_artifacts: list[str],
    measured_value: Any = None,
    threshold_vigente: Any = None,
    threshold_spec: Any = None,
    status_vigente: str = "N/A",
    status_spec: str = "N/A",
) -> dict[str, Any]:
    return {
        "classification": classification,
        "reason": reason,
        "practical_impact": practical_impact,
        "alone_blocks_advance": alone_blocks_advance,
        "audit_status": audit_status,
        "source_artifacts": source_artifacts,
        "measured_value": measured_value,
        "threshold_vigente": threshold_vigente,
        "threshold_spec": threshold_spec,
        "status_vigente": status_vigente,
        "status_spec": status_spec,
    }


def build_blocker_reclassification(
    phase4_report: dict[str, Any],
    ia_status: dict[str, Any],
    phase2_auc_provenance: dict[str, Any],
    cvar_empirical_audit: dict[str, Any],
) -> dict[str, Any]:
    dsr_report = phase4_report.get("dsr", {})
    fallback = phase4_report.get("fallback", {})
    subperiods = phase4_report.get("subperiods", [])
    positive_count = sum(1 for row in subperiods if row.get("positive") is True)
    dsr_value = float(dsr_report.get("dsr_honest", np.nan)) if dsr_report else None
    sharpe_value = float(fallback.get("sharpe", np.nan)) if fallback else None

    blockers: dict[str, Any] = {}
    blockers["DSR honesto"] = _blocker_entry(
        classification=CLASS_HARD_BLOCKER,
        reason="Direct official phase4 report shows dsr_honest=0.0 with n_trials_honest=5000, below both the current protocol threshold and the spec threshold.",
        practical_impact="Statistical robustness of the current official policy fails by itself and blocks operational promotion.",
        alone_blocks_advance=True,
        audit_status=AUDIT_DIRECT,
        source_artifacts=[str(PHASE4_REPORT_PATH)],
        measured_value={"dsr_honest": dsr_value, "n_trials_honest": dsr_report.get("n_trials_honest"), "sr_needed": dsr_report.get("sr_needed")},
        threshold_vigente=VIGENTE_THRESHOLDS["dsr_honesto"],
        threshold_spec=SPEC_THRESHOLDS["dsr_honesto"],
        status_vigente=_threshold_status(dsr_value, VIGENTE_THRESHOLDS["dsr_honesto"]),
        status_spec=_threshold_status(dsr_value, SPEC_THRESHOLDS["dsr_honesto"]),
    )
    blockers["DSR invalidação global"] = _blocker_entry(
        classification=CLASS_HARD_BLOCKER,
        reason="The official report materializes the same DSR at 0.0, which also fails the global invalidation minimum DSR>0 from the spec.",
        practical_impact="The model fails the global statistical invalidation floor even before considering stricter honest-DSR protocol.",
        alone_blocks_advance=True,
        audit_status=AUDIT_DIRECT,
        source_artifacts=[str(PHASE4_REPORT_PATH)],
        measured_value={"dsr": dsr_value},
        threshold_vigente=VIGENTE_THRESHOLDS["dsr_invalidation_global"],
        threshold_spec=SPEC_THRESHOLDS["dsr_invalidation_global"],
        status_vigente="N/A",
        status_spec=_threshold_status(dsr_value, SPEC_THRESHOLDS["dsr_invalidation_global"]),
    )
    blockers["Sharpe OOS"] = _blocker_entry(
        classification=CLASS_HARD_BLOCKER,
        reason="The official fallback policy selected in phase4_report_v4.json has Sharpe OOS 0.3494, below the current and spec minimum of 0.70.",
        practical_impact="The current official system-level policy does not meet the minimum return quality gate and cannot be advanced operationally.",
        alone_blocks_advance=True,
        audit_status=AUDIT_DIRECT,
        source_artifacts=[str(PHASE4_REPORT_PATH)],
        measured_value={"policy": fallback.get("policy"), "sharpe_oos": sharpe_value, "n_active": fallback.get("n_active")},
        threshold_vigente=VIGENTE_THRESHOLDS["sharpe_oos"],
        threshold_spec=SPEC_THRESHOLDS["sharpe_oos"],
        status_vigente=_threshold_status(sharpe_value, VIGENTE_THRESHOLDS["sharpe_oos"]),
        status_spec=_threshold_status(sharpe_value, SPEC_THRESHOLDS["sharpe_oos"]),
    )
    blockers["Subperiodos"] = _blocker_entry(
        classification=CLASS_HARD_BLOCKER,
        reason="The official subperiod report closes with only 3 positive subperiods, below the 4-of-6 gate in both the current protocol and the spec.",
        practical_impact="Temporal robustness is insufficient; the policy remains unstable across market regimes and cannot be promoted.",
        alone_blocks_advance=True,
        audit_status=AUDIT_DIRECT,
        source_artifacts=[str(PHASE4_REPORT_PATH)],
        measured_value={"positive_count": positive_count, "subperiods": subperiods},
        threshold_vigente=VIGENTE_THRESHOLDS["subperiodos"],
        threshold_spec=SPEC_THRESHOLDS["subperiodos"],
        status_vigente=_threshold_status(float(positive_count), VIGENTE_THRESHOLDS["subperiodos"]),
        status_spec=_threshold_status(float(positive_count), SPEC_THRESHOLDS["subperiodos"]),
    )

    derived_auc = phase2_auc_provenance.get("derived_auc_from_phase3_meta", {})
    blockers["AUC OOS da Fase 2"] = _blocker_entry(
        classification=CLASS_NAO_AUDITADO if not phase2_auc_provenance.get("directly_auditable") else CLASS_HARD_BLOCKER,
        reason=phase2_auc_provenance.get("official_gate_explanation", ""),
        practical_impact=(
            "This cannot be used as a hard mathematical blocker until a direct official phase2 artifact or exact lineage proof exists."
            if not phase2_auc_provenance.get("directly_auditable")
            else "Direct official phase2 AUC gate fails and blocks operational advance."
        ),
        alone_blocks_advance=bool(phase2_auc_provenance.get("directly_auditable") and _threshold_status(derived_auc.get("auc_oos_pooled"), SPEC_THRESHOLDS["auc_oos_phase2"]) == "FAIL"),
        audit_status=AUDIT_DERIVED if not phase2_auc_provenance.get("directly_auditable") else AUDIT_DIRECT,
        source_artifacts=phase2_auc_provenance.get("source_artifacts", []),
        measured_value={
            "directly_auditable": phase2_auc_provenance.get("directly_auditable"),
            "official_phase2_auc_artifact_found": phase2_auc_provenance.get("official_phase2_auc_artifact_found"),
            "derived_auc_from_phase3_meta": derived_auc,
        },
        threshold_vigente=VIGENTE_THRESHOLDS["auc_oos_phase2"],
        threshold_spec=SPEC_THRESHOLDS["auc_oos_phase2"],
        status_vigente="N/A",
        status_spec=_threshold_status(derived_auc.get("auc_oos_pooled"), SPEC_THRESHOLDS["auc_oos_phase2"]) if phase2_auc_provenance.get("directly_auditable") else "N/A",
    )

    ia_is_official = bool(ia_status.get("official_path_uses_ia"))
    blockers["IA alpha/r2"] = _blocker_entry(
        classification=CLASS_HARD_BLOCKER if ia_is_official else CLASS_GOVERNANCA,
        reason=(
            "IA features are part of the official path, but no official alpha_retido_pct/r2_ortho audit artifact exists."
            if ia_is_official else
            "No official artifact or selected feature shows IA participation in the current snapshot path; the remaining issue is governance/documentation cleanup."
        ),
        practical_impact=(
            "Without an official IA audit artifact, the current official path would be mathematically incomplete."
            if ia_is_official else
            "This does not block the current non-IA official path, but it leaves governance ambiguity against the spec."
        ),
        alone_blocks_advance=ia_is_official,
        audit_status=AUDIT_MISSING if ia_is_official else AUDIT_STRUCTURAL,
        source_artifacts=ia_status.get("source_artifacts", []),
        measured_value={
            "official_path_uses_ia": ia_status.get("official_path_uses_ia"),
            "selected_feature_ia_overlap": ia_status.get("selected_feature_ia_overlap"),
            "feature_store_ia_hits": ia_status.get("feature_store_ia_hits"),
            "phase3_meta_ia_hits": ia_status.get("phase3_meta_ia_hits"),
            "phase4_artifact_ia_hits": ia_status.get("phase4_artifact_ia_hits"),
        },
        threshold_vigente=VIGENTE_THRESHOLDS["ia_alpha_r2"],
        threshold_spec=SPEC_THRESHOLDS["ia_alpha_r2"],
        status_vigente="N/A",
        status_spec="N/A" if not ia_is_official else "INCONCLUSIVO",
    )

    blockers["CVaR empirico persistido"] = _blocker_entry(
        classification=CLASS_NAO_AUDITADO,
        reason="Structural CVaR control exists in official code, but no persisted official artifact directly audits empirical portfolio CVaR for the current official outputs.",
        practical_impact="This does not, by itself, prove the current model mathematically invalid, but it prevents a direct offline audit of the empirical CVaR block without rerun or new persistence.",
        alone_blocks_advance=False,
        audit_status=AUDIT_STRUCTURAL if cvar_empirical_audit.get("structure_present") else AUDIT_MISSING,
        source_artifacts=cvar_empirical_audit.get("source_artifacts", []),
        measured_value=cvar_empirical_audit,
        threshold_vigente=VIGENTE_THRESHOLDS["cvar_empirico_persistido"],
        threshold_spec=SPEC_THRESHOLDS["cvar_empirico_persistido"],
        status_vigente="N/A",
        status_spec="N/A",
    )
    return blockers


def build_hard_blocker_causality(
    phase4_report: dict[str, Any],
    snapshot_summary: dict[str, Any],
    aggregated_summary: dict[str, Any],
    blocker_reclassification: dict[str, Any],
) -> dict[str, Any]:
    fallback = phase4_report.get("fallback", {})
    cpcv = phase4_report.get("cpcv", {})
    dsr = phase4_report.get("dsr", {})
    temporal = fallback.get("temporal_robustness", {})
    policy_label = str(fallback.get("policy", ""))
    policy_temporal = temporal.get(policy_label, {}) if isinstance(temporal, dict) else {}
    temporal_summary = policy_temporal.get("summary", {})
    bucket_rows = fallback.get("score_bucket_diagnostics", []) if isinstance(fallback.get("score_bucket_diagnostics"), list) else []
    negative_buckets = [
        {"bucket": row.get("bucket"), "sharpe": row.get("sharpe"), "n_trades": row.get("n_trades")}
        for row in bucket_rows
        if isinstance(row, dict) and float(row.get("sharpe", 0.0)) < 0.0
    ]
    selected_bucket = next(
        (
            {
                "bucket": row.get("bucket"),
                "sharpe": row.get("sharpe"),
                "n_trades": row.get("n_trades"),
                "cum_return": row.get("cum_return"),
                "subperiods_positive": row.get("subperiods_positive"),
                "subperiods_total": row.get("subperiods_total"),
            }
            for row in bucket_rows
            if isinstance(row, dict) and row.get("bucket") == ">0.80"
        ),
        None,
    )
    threshold_sensitivity = fallback.get("local_threshold_sensitivity", {})
    threshold_evidence = {
        label: {
            "sharpe": values.get("sharpe"),
            "n_active": values.get("n_active"),
            "subperiods_positive": values.get("subperiods_positive"),
            "subperiods_total": values.get("subperiods_total"),
            "dsr_honest": values.get("dsr_honest"),
        }
        for label, values in threshold_sensitivity.items()
        if isinstance(values, dict)
    }
    current_subperiods = [
        {
            "period": row.get("period"),
            "status": row.get("status"),
            "sharpe": row.get("sharpe"),
            "n_active": row.get("n_active"),
            "cum_return": row.get("cum_return"),
        }
        for row in phase4_report.get("subperiods", [])
    ]

    items: dict[str, Any] = {}
    if blocker_reclassification.get("Sharpe OOS", {}).get("classification") == CLASS_HARD_BLOCKER:
        items["Sharpe OOS"] = {
            "root_cause_class": "fallback_policy_layer_underpowered_and_temporally_unstable",
            "explanation": (
                "The meta-model CPCV layer passes, but the official fallback policy on p_bma_pkf tail scores remains weak. "
                "Most score buckets below 0.80 are negative, and the selected >0.80 tail is only mildly positive "
                "(Sharpe 0.3494, cum_return 0.0096, 222 active trades)."
            ),
            "meta_model_layer_status": cpcv.get("status"),
            "policy_layer_status": "FAIL",
            "selected_policy": {
                "policy": fallback.get("policy"),
                "threshold": fallback.get("threshold"),
                "sharpe": fallback.get("sharpe"),
                "n_active": fallback.get("n_active"),
                "win_rate": fallback.get("win_rate"),
            },
            "negative_score_buckets": negative_buckets,
            "selected_tail_bucket": selected_bucket,
            "threshold_sensitivity": threshold_evidence,
            "snapshot_current_state": snapshot_summary,
            "aggregated_latest_state": aggregated_summary,
            "source_artifacts": [str(PHASE4_REPORT_PATH), str(PHASE4_SNAPSHOT_PATH), str(PHASE4_AGGREGATED_PATH)],
        }
    if blocker_reclassification.get("Subperiodos", {}).get("classification") == CLASS_HARD_BLOCKER:
        items["Subperiodos"] = {
            "root_cause_class": "temporal_concentration_and_policy_instability",
            "explanation": (
                "The current policy closes only 3 positive tested periods because 2023 is negative and the 0.80 threshold "
                "skips both 2020 halves entirely. The 2024+ contribution is only weakly positive, so the policy does not "
                "show stable regime coverage."
            ),
            "selected_policy": fallback.get("policy"),
            "temporal_summary": temporal_summary,
            "subperiods": current_subperiods,
            "source_artifacts": [str(PHASE4_REPORT_PATH)],
        }
    if blocker_reclassification.get("DSR honesto", {}).get("classification") == CLASS_HARD_BLOCKER:
        items["DSR honesto"] = {
            "root_cause_class": "low_sharpe_with_high_multiplicity_penalty",
            "explanation": (
                "DSR collapses to 0.0 because the selected official policy produces Sharpe 0.3494 while the honest protocol "
                "requires a much higher Sharpe under n_trials_honest=5000. This is a consequence of weak policy-layer performance, "
                "not a separate CPCV failure."
            ),
            "selected_policy": fallback.get("policy"),
            "sharpe_is": dsr.get("sharpe_is"),
            "dsr_honest": dsr.get("dsr_honest"),
            "sr_needed": dsr.get("sr_needed"),
            "n_trials_honest": dsr.get("n_trials_honest"),
            "meta_model_layer_status": cpcv.get("status"),
            "source_artifacts": [str(PHASE4_REPORT_PATH)],
        }
    if blocker_reclassification.get("DSR invalidação global", {}).get("classification") == CLASS_HARD_BLOCKER:
        items["DSR invalidação global"] = {
            "root_cause_class": "same_underlying_failure_as_honest_dsr",
            "explanation": (
                "The global invalidation DSR>0 fails for the same measured DSR value already materialized in the official report. "
                "It is not an independent root cause; it is the looser spec view of the same statistical collapse."
            ),
            "dsr_value": dsr.get("dsr_honest"),
            "source_artifacts": [str(PHASE4_REPORT_PATH)],
        }
    return {
        "summary": {
            "meta_model_layer_status": cpcv.get("status"),
            "policy_layer_status": "FAIL" if any(item for item in items if item in {"Sharpe OOS", "Subperiodos", "DSR honesto", "DSR invalidação global"}) else "PASS",
            "current_snapshot_active_count": snapshot_summary.get("active_count"),
            "current_snapshot_is_flat": snapshot_summary.get("active_count") == 0,
            "current_snapshot_max_p_bma_pkf": snapshot_summary.get("max_p_bma_pkf"),
            "selected_policy": fallback.get("policy"),
            "selected_policy_threshold": fallback.get("threshold"),
        },
        "items": items,
    }


def build_gate_diagnostic() -> dict[str, Any]:
    baseline = collect_git_baseline()
    paper_state_start = collect_paper_state()

    phase2_report = _read_json(PHASE2_REPORT_PATH)
    phase3_report = _read_json(PHASE3_REPORT_PATH)
    phase3_exclusions = _read_json(PHASE3_EXCLUSIONS_PATH)
    phase4_report = _read_json(PHASE4_REPORT_PATH)

    snapshot_summary = summarize_snapshot()
    aggregated_summary = summarize_aggregated_predictions(phase4_report=phase4_report)
    ia_status = collect_ia_official_status(phase4_report=phase4_report)
    phase2_auc_provenance = collect_phase2_auc_provenance(phase2_report=phase2_report)
    cvar_empirical_audit = collect_cvar_empirical_audit(phase4_report=phase4_report)
    blocker_reclassification = build_blocker_reclassification(
        phase4_report=phase4_report,
        ia_status=ia_status,
        phase2_auc_provenance=phase2_auc_provenance,
        cvar_empirical_audit=cvar_empirical_audit,
    )
    hard_blocker_causality = build_hard_blocker_causality(
        phase4_report=phase4_report,
        snapshot_summary=snapshot_summary,
        aggregated_summary=aggregated_summary,
        blocker_reclassification=blocker_reclassification,
    )
    paper_state_end = collect_paper_state()

    return {
        "timestamp_utc": _now_utc(),
        "spec_binding": "SNIPER v10.10 Phase 4B gate diagnostic",
        "baseline": {
            **baseline,
            "paper_state_start": paper_state_start,
            "paper_state_end": paper_state_end,
        },
        "source_scope": {
            "official_only": True,
            "excluded_domains": ["research", "sandbox", "RiskLabAI"],
            "artifacts_used": [
                str(PHASE2_REPORT_PATH),
                str(PHASE3_REPORT_PATH),
                str(PHASE3_EXCLUSIONS_PATH),
                str(PHASE4_REPORT_PATH),
                str(PHASE4_SNAPSHOT_PATH),
                str(PHASE4_AGGREGATED_PATH),
                str(FEATURES_PATH),
                str(PARQUET_BASE),
            ],
            "official_universe_symbols": snapshot_summary.get("symbols"),
            "official_snapshot_date_max": snapshot_summary.get("date_max"),
        },
        "blocker_reclassification": blocker_reclassification,
        "ia_official_status": ia_status,
        "phase2_auc_provenance": phase2_auc_provenance,
        "hard_blocker_causality": hard_blocker_causality,
        "official_state_snapshot": {
            "phase2_overall_status": phase2_report.get("overall_status"),
            "phase3_overall_status": phase3_report.get("overall_status"),
            "phase3_controlled_exclusions": phase3_report.get("summary", {}).get("controlled_exclusions"),
            "phase3_excluded_symbols": sorted((phase3_exclusions.get("exclusions") or {}).keys()),
            "phase4_checks": phase4_report.get("checks"),
            "phase4_snapshot": snapshot_summary,
            "phase4_aggregated": aggregated_summary,
        },
    }


def main() -> None:
    payload = build_gate_diagnostic()
    _atomic_write_json(PHASE4_GATE_DIAGNOSTIC_PATH, payload)
    print(json.dumps({
        "written": str(PHASE4_GATE_DIAGNOSTIC_PATH),
        "hard_blockers": [
            name for name, meta in payload.get("blocker_reclassification", {}).items()
            if meta.get("classification") == CLASS_HARD_BLOCKER
        ],
        "ia_official_path_uses_ia": payload.get("ia_official_status", {}).get("official_path_uses_ia"),
        "phase2_auc_directly_auditable": payload.get("phase2_auc_provenance", {}).get("directly_auditable"),
    }, indent=2))


if __name__ == "__main__":
    main()
