#!/usr/bin/env python3
from __future__ import annotations

import hashlib
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

from services.common.gate_reports import (  # noqa: E402
    GATE_REPORT_MARKDOWN_SECTIONS,
    artifact_record,
    sha256_file,
    utc_now_iso,
    write_gate_pack,
)

GATE_SLUG = "phase5_research_unlock_shadow_feature_ablation_gate"
PHASE_FAMILY = "phase5_research_unlock_shadow_feature_ablation"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

UNLOCK_DIR = REPO_ROOT / "data" / "parquet" / "unlocks"
UNLOCK_QUALITY_PATH = REPO_ROOT / "data" / "parquet" / "unlock_diagnostics" / "unlock_quality_daily.parquet"
PHASE4_OOS_PATH = REPO_ROOT / "data" / "models" / "phase4" / "phase4_oos_predictions.parquet"

UNLOCK_FEATURE_COLUMNS = (
    "unlock_pressure_rank_observed",
    "unlock_pressure_rank_reconstructed",
    "unlock_overhang_proxy_rank_full",
    "unlock_fragility_proxy_rank_fallback",
)
UNLOCK_AUDIT_COLUMNS = (
    "unlock_pressure_rank_selected_for_reporting",
    "unlock_feature_state",
    "quality_flag",
    "reconstruction_confidence",
    "source_primary",
    "snapshot_ts",
)

FORBIDDEN_OPERATIONAL_INPUTS = {
    "pnl_real",
    "pnl_exec_meta",
    "stage_a_eligible",
    "avg_sl_train",
    "avg_tp_train",
    "label",
    "y_meta",
}


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value) if not isinstance(value, (str, bytes, bool, type(None))) else False:
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {
        "gate_slug": GATE_SLUG,
        "metric_name": name,
        "metric_value": value,
        "metric_threshold": threshold,
        "metric_status": "PASS" if passed else "FAIL",
    }


def _markdown_sections(values: dict[str, str]) -> dict[str, str]:
    sections = {section: "" for section in GATE_REPORT_MARKDOWN_SECTIONS}
    keys = list(GATE_REPORT_MARKDOWN_SECTIONS)
    sections.update(
        {
            keys[0]: values.get("summary", ""),
            keys[1]: values.get("baseline", ""),
            keys[2]: values.get("changes", ""),
            keys[3]: values.get("artifacts", ""),
            keys[4]: values.get("results", ""),
            keys[5]: values.get("evaluation", ""),
            keys[6]: values.get("risks", ""),
            keys[7]: values.get("verdict", ""),
        }
    )
    return sections


def _normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _safe_corr(left: pd.Series, right: pd.Series, *, method: str = "spearman", min_rows: int = 20) -> float | None:
    valid = left.notna() & right.notna()
    if int(valid.sum()) < min_rows or left.loc[valid].nunique(dropna=True) < 2 or right.loc[valid].nunique(dropna=True) < 2:
        return None
    value = left.loc[valid].corr(right.loc[valid], method=method)
    return None if pd.isna(value) else round(float(value), 6)


def _top_bottom_delta(feature: pd.Series, outcome: pd.Series, *, min_rows: int = 50) -> dict[str, Any]:
    valid = feature.notna() & outcome.notna()
    if int(valid.sum()) < min_rows or feature.loc[valid].nunique(dropna=True) < 5:
        return {
            "valid_rows": int(valid.sum()),
            "bottom_quintile_mean_pnl": None,
            "top_quintile_mean_pnl": None,
            "top_minus_bottom_mean_pnl": None,
        }
    ranked = feature.loc[valid].rank(method="first", pct=True)
    bottom = outcome.loc[valid].loc[ranked <= 0.2]
    top = outcome.loc[valid].loc[ranked >= 0.8]
    if bottom.empty or top.empty:
        return {
            "valid_rows": int(valid.sum()),
            "bottom_quintile_mean_pnl": None,
            "top_quintile_mean_pnl": None,
            "top_minus_bottom_mean_pnl": None,
        }
    bottom_mean = float(bottom.mean())
    top_mean = float(top.mean())
    return {
        "valid_rows": int(valid.sum()),
        "bottom_quintile_mean_pnl": round(bottom_mean, 8),
        "top_quintile_mean_pnl": round(top_mean, 8),
        "top_minus_bottom_mean_pnl": round(top_mean - bottom_mean, 8),
    }


def required_artifact_status(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    unlock_dir = repo_root / "data" / "parquet" / "unlocks"
    quality_path = repo_root / "data" / "parquet" / "unlock_diagnostics" / "unlock_quality_daily.parquet"
    unlock_files = sorted(unlock_dir.glob("*.parquet")) if unlock_dir.exists() else []
    return {
        "unlock_dir": str(unlock_dir),
        "unlock_dir_exists": unlock_dir.exists(),
        "unlock_file_count": len(unlock_files),
        "quality_path": str(quality_path),
        "quality_exists": quality_path.exists(),
        "required_artifacts_present": unlock_dir.exists() and bool(unlock_files) and quality_path.exists(),
    }


def build_unlock_inventory(unlock_files: list[Path]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in unlock_files:
        inventory.append(
            {
                "path": str(path),
                "symbol": path.stem,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return inventory


def aggregate_inventory_hash(inventory: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(inventory, key=lambda row: row["path"]):
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(str(item["sha256"]).encode("utf-8"))
    return digest.hexdigest().upper()


def load_unlock_frame(repo_root: Path = REPO_ROOT) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    status = required_artifact_status(repo_root)
    if not status["required_artifacts_present"]:
        return pd.DataFrame(), [], ["required H06 unlock artifacts are absent"]

    unlock_files = sorted((repo_root / "data" / "parquet" / "unlocks").glob("*.parquet"))
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for path in unlock_files:
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        if "timestamp" in frame.columns:
            frame["date"] = _normalize_date(frame["timestamp"])
        elif "date" in frame.columns:
            frame["date"] = _normalize_date(frame["date"])
        else:
            errors.append(f"{path}: missing timestamp/date column")
            continue
        if "symbol" not in frame.columns:
            frame["symbol"] = path.stem
        keep = ["date", "symbol", *UNLOCK_FEATURE_COLUMNS, *UNLOCK_AUDIT_COLUMNS]
        keep = [column for column in keep if column in frame.columns]
        frames.append(frame[keep].copy())

    if not frames:
        return pd.DataFrame(), build_unlock_inventory(unlock_files), errors or ["no readable unlock frames"]

    stacked = pd.concat(frames, ignore_index=True)
    stacked["date"] = pd.to_datetime(stacked["date"], errors="coerce").dt.normalize()
    stacked["symbol"] = stacked["symbol"].astype(str)
    stacked = stacked.dropna(subset=["date", "symbol"]).sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
    return stacked, build_unlock_inventory(unlock_files), errors


def load_quality_summary(repo_root: Path = REPO_ROOT) -> tuple[pd.DataFrame, str | None]:
    quality_path = repo_root / "data" / "parquet" / "unlock_diagnostics" / "unlock_quality_daily.parquet"
    if not quality_path.exists():
        return pd.DataFrame(), "missing unlock_quality_daily.parquet"
    try:
        frame = pd.read_parquet(quality_path)
    except Exception as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {exc}"
    if "date" not in frame.columns:
        return frame, "quality summary missing date column"
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    return frame, None


def quality_diagnostics(quality_frame: pd.DataFrame) -> dict[str, Any]:
    if quality_frame.empty:
        return {"quality_summary_present": False}
    operational = quality_frame.loc[(quality_frame["date"] >= pd.Timestamp("2000-01-01")) & (pd.to_numeric(quality_frame.get("n_assets"), errors="coerce") >= 10)].copy()
    latest_operational = operational.sort_values("date").iloc[-1].to_dict() if not operational.empty else {}
    raw_latest = quality_frame.sort_values("date").iloc[-1].to_dict()
    numeric_columns = [
        "observed_coverage",
        "reconstructed_coverage",
        "proxy_full_coverage",
        "proxy_fallback_coverage",
        "missing_rate",
        "shadow_mode_flag",
        "promotion_blocked_count",
    ]
    medians = {
        f"{column}_median": round(float(pd.to_numeric(operational[column], errors="coerce").median()), 6)
        for column in numeric_columns
        if column in operational.columns and not operational.empty
    }
    return {
        "quality_summary_present": True,
        "quality_rows": int(len(quality_frame)),
        "operational_quality_rows": int(len(operational)),
        "date_min": str(quality_frame["date"].min().date()) if quality_frame["date"].notna().any() else None,
        "date_max": str(quality_frame["date"].max().date()) if quality_frame["date"].notna().any() else None,
        "latest_operational_quality": _json_safe(latest_operational),
        "raw_latest_quality": _json_safe(raw_latest),
        "quality_medians": medians,
        "shadow_mode_detected": bool(pd.to_numeric(operational.get("shadow_mode_flag"), errors="coerce").fillna(0).max() > 0) if not operational.empty else False,
    }


def unlock_coverage_diagnostics(unlock_frame: pd.DataFrame) -> dict[str, Any]:
    if unlock_frame.empty:
        return {
            "unlock_rows": 0,
            "unlock_symbols": 0,
            "feature_coverage": {},
            "feature_state_counts": {},
        }
    operational = unlock_frame.loc[unlock_frame["date"] >= pd.Timestamp("2000-01-01")].copy()
    feature_coverage = {
        column: {
            "coverage": round(float(_numeric(operational, column).notna().mean()), 6),
            "unique_values": int(_numeric(operational, column).nunique(dropna=True)),
        }
        for column in (*UNLOCK_FEATURE_COLUMNS, "unlock_pressure_rank_selected_for_reporting")
        if column in operational.columns
    }
    return {
        "unlock_rows": int(len(unlock_frame)),
        "operational_unlock_rows": int(len(operational)),
        "unlock_symbols": int(unlock_frame["symbol"].nunique()),
        "date_min": str(operational["date"].min().date()) if not operational.empty else None,
        "date_max": str(operational["date"].max().date()) if not operational.empty else None,
        "feature_coverage": feature_coverage,
        "feature_state_counts": {str(key): int(value) for key, value in operational.get("unlock_feature_state", pd.Series(dtype=object)).value_counts(dropna=False).items()},
    }


def phase4_overlap_diagnostics(unlock_frame: pd.DataFrame, repo_root: Path = REPO_ROOT) -> tuple[pd.DataFrame, dict[str, Any]]:
    phase4_path = repo_root / "data" / "models" / "phase4" / "phase4_oos_predictions.parquet"
    if unlock_frame.empty or not phase4_path.exists():
        return pd.DataFrame(), {"phase4_exists": phase4_path.exists(), "joined_rows": 0}
    phase4 = pd.read_parquet(phase4_path).copy()
    phase4["date"] = pd.to_datetime(phase4["date"], errors="coerce").dt.normalize()
    unlock_keep = ["date", "symbol", *UNLOCK_FEATURE_COLUMNS, *UNLOCK_AUDIT_COLUMNS]
    unlock_keep = [column for column in unlock_keep if column in unlock_frame.columns]
    joined = phase4.merge(unlock_frame[unlock_keep], on=["date", "symbol"], how="left", suffixes=("", "_unlock"))
    selected = _numeric(joined, "unlock_pressure_rank_selected_for_reporting")
    has_selected = selected.notna()
    overlap = joined.loc[has_selected]
    diagnostics = {
        "phase4_exists": True,
        "phase4_rows": int(len(phase4)),
        "phase4_symbols": int(phase4["symbol"].nunique()) if "symbol" in phase4.columns else 0,
        "phase4_date_min": str(phase4["date"].min().date()) if phase4["date"].notna().any() else None,
        "phase4_date_max": str(phase4["date"].max().date()) if phase4["date"].notna().any() else None,
        "joined_rows_with_selected_unlock": int(has_selected.sum()),
        "joined_coverage_selected_unlock": round(float(has_selected.mean()), 6) if len(joined) else 0.0,
        "joined_symbols_with_selected_unlock": int(overlap["symbol"].nunique()) if not overlap.empty else 0,
        "joined_date_min": str(overlap["date"].min().date()) if not overlap.empty else None,
        "joined_date_max": str(overlap["date"].max().date()) if not overlap.empty else None,
        "phase4_outcome_column_used_for_diagnostic_only": "pnl_real",
    }
    return joined, diagnostics


def feature_ablation_metrics(joined: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if joined.empty:
        return pd.DataFrame(rows)
    outcome = _numeric(joined, "pnl_real")
    for column in (*UNLOCK_FEATURE_COLUMNS, "unlock_pressure_rank_selected_for_reporting"):
        if column not in joined.columns:
            rows.append(
                {
                    "feature": column,
                    "exists": False,
                    "joined_valid_rows": 0,
                    "joined_coverage": 0.0,
                    "spearman_to_pnl_real_diagnostic": None,
                    "pearson_to_pnl_real_diagnostic": None,
                    "top_minus_bottom_mean_pnl_diagnostic": None,
                    "forbidden_as_operational_input": column in FORBIDDEN_OPERATIONAL_INPUTS,
                }
            )
            continue
        values = _numeric(joined, column)
        valid = values.notna() & outcome.notna()
        delta = _top_bottom_delta(values, outcome)
        rows.append(
            {
                "feature": column,
                "exists": True,
                "joined_valid_rows": int(valid.sum()),
                "joined_coverage": round(float(values.notna().mean()), 6),
                "unique_values": int(values.nunique(dropna=True)),
                "spearman_to_pnl_real_diagnostic": _safe_corr(values, outcome, method="spearman"),
                "pearson_to_pnl_real_diagnostic": _safe_corr(values, outcome, method="pearson"),
                "bottom_quintile_mean_pnl_diagnostic": delta["bottom_quintile_mean_pnl"],
                "top_quintile_mean_pnl_diagnostic": delta["top_quintile_mean_pnl"],
                "top_minus_bottom_mean_pnl_diagnostic": delta["top_minus_bottom_mean_pnl"],
                "forbidden_as_operational_input": column in FORBIDDEN_OPERATIONAL_INPUTS,
            }
        )
    return pd.DataFrame(rows)


def classify_h06_gate(artifact_status: dict[str, Any], quality: dict[str, Any], overlap: dict[str, Any], metrics: pd.DataFrame) -> tuple[str, str, str]:
    if not artifact_status["required_artifacts_present"]:
        return "INCONCLUSIVE", "correct", "H06_REQUIRED_ARTIFACTS_MISSING"
    if not quality.get("quality_summary_present"):
        return "INCONCLUSIVE", "correct", "H06_QUALITY_SUMMARY_MISSING_OR_UNREADABLE"
    if int(overlap.get("joined_rows_with_selected_unlock") or 0) <= 0:
        return "INCONCLUSIVE", "correct", "H06_NO_PHASE4_OVERLAP"
    selected_rows = metrics.loc[metrics["feature"].eq("unlock_pressure_rank_selected_for_reporting")] if not metrics.empty else pd.DataFrame()
    if selected_rows.empty or int(selected_rows.iloc[0]["joined_valid_rows"]) <= 0:
        return "INCONCLUSIVE", "correct", "H06_SELECTED_UNLOCK_FEATURE_NOT_JOINABLE"
    return "PASS", "advance", "H06_UNLOCK_SHADOW_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE"


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_status = required_artifact_status()
    unlock_frame, inventory, read_errors = load_unlock_frame()
    quality_frame, quality_error = load_quality_summary()
    quality = quality_diagnostics(quality_frame)
    coverage = unlock_coverage_diagnostics(unlock_frame)
    joined, overlap = phase4_overlap_diagnostics(unlock_frame)
    ablation = feature_ablation_metrics(joined)
    status, decision, classification = classify_h06_gate(artifact_status, quality, overlap, ablation)

    git_context = {
        "branch": _git_output("branch", "--show-current"),
        "head": _git_output("rev-parse", "HEAD"),
        "dirty": bool(_git_output("status", "--short")),
    }

    inventory_path = OUTPUT_DIR / "unlock_artifact_inventory.json"
    diagnostic_path = OUTPUT_DIR / "unlock_shadow_feature_ablation_report.json"
    metrics_path = OUTPUT_DIR / "unlock_shadow_feature_ablation_metrics.parquet"

    inventory_payload = {
        "gate_slug": GATE_SLUG,
        "unlock_artifact_count": len(inventory),
        "aggregate_inventory_sha256": aggregate_inventory_hash(inventory),
        "quality_artifact": artifact_record(UNLOCK_QUALITY_PATH),
        "unlock_artifacts": inventory,
    }
    _write_json(inventory_path, inventory_payload)
    if ablation.empty:
        ablation = pd.DataFrame(
            columns=[
                "feature",
                "exists",
                "joined_valid_rows",
                "joined_coverage",
                "unique_values",
                "spearman_to_pnl_real_diagnostic",
                "pearson_to_pnl_real_diagnostic",
                "bottom_quintile_mean_pnl_diagnostic",
                "top_quintile_mean_pnl_diagnostic",
                "top_minus_bottom_mean_pnl_diagnostic",
                "forbidden_as_operational_input",
            ]
        )
    ablation.to_parquet(metrics_path, index=False)

    diagnostic_payload = {
        "gate_slug": GATE_SLUG,
        "hypothesis": "AGENDA-H06 unlock_shadow_feature_ablation",
        "status": status,
        "decision": decision,
        "classification": classification,
        "artifact_status": artifact_status,
        "unlock_inventory": {
            "unlock_artifact_count": len(inventory),
            "aggregate_inventory_sha256": inventory_payload["aggregate_inventory_sha256"],
        },
        "read_errors": read_errors,
        "quality_error": quality_error,
        "quality_diagnostics": quality,
        "unlock_coverage_diagnostics": coverage,
        "phase4_overlap_diagnostics": overlap,
        "feature_ablation_metrics": ablation.to_dict(orient="records"),
        "governance": {
            "research_only": True,
            "diagnostic_only": True,
            "creates_operational_signal": False,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "uses_pnl_real_as_diagnostic_outcome_only": True,
            "treats_shadow_artifact_as_official": False,
        },
        "candidate": {
            "research_candidate_found": False,
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
            "reason": "H06 was executed as diagnostic/preflight only. It produced coverage and ablation evidence but no operational policy candidate.",
        },
    }
    _write_json(diagnostic_path, diagnostic_payload)

    selected_metric = ablation.loc[ablation["feature"].eq("unlock_pressure_rank_selected_for_reporting")]
    selected_joined_rows = int(selected_metric.iloc[0]["joined_valid_rows"]) if not selected_metric.empty else 0
    selected_spearman = selected_metric.iloc[0]["spearman_to_pnl_real_diagnostic"] if not selected_metric.empty else None
    shadow_mode = bool(quality.get("shadow_mode_detected"))
    observed_coverage_median = quality.get("quality_medians", {}).get("observed_coverage_median")
    proxy_fallback_coverage_median = quality.get("quality_medians", {}).get("proxy_fallback_coverage_median")

    gate_metrics = [
        _metric("required_artifacts_present", artifact_status["required_artifacts_present"], "true", bool(artifact_status["required_artifacts_present"])),
        _metric("unlock_file_count", artifact_status["unlock_file_count"], ">= 1", int(artifact_status["unlock_file_count"]) >= 1),
        _metric("quality_summary_present", quality.get("quality_summary_present", False), "true", bool(quality.get("quality_summary_present", False))),
        _metric("phase4_joined_rows_with_selected_unlock", selected_joined_rows, "> 0 for diagnostic", selected_joined_rows > 0),
        _metric("phase4_joined_coverage_selected_unlock", overlap.get("joined_coverage_selected_unlock"), "> 0 for diagnostic", float(overlap.get("joined_coverage_selected_unlock") or 0.0) > 0.0),
        _metric("shadow_mode_detected", shadow_mode, "reported, not promotion", True),
        _metric("observed_coverage_median", observed_coverage_median, "reported", True),
        _metric("proxy_fallback_coverage_median", proxy_fallback_coverage_median, "reported", True),
        _metric("selected_unlock_spearman_to_pnl_real_diagnostic", selected_spearman, "diagnostic only", True),
        _metric("research_candidate_found", False, "false for diagnostic-only H06", True),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]

    generated_core = [artifact_record(inventory_path), artifact_record(diagnostic_path), artifact_record(metrics_path)]
    source_artifacts = [
        artifact_record(UNLOCK_QUALITY_PATH),
        artifact_record(PHASE4_OOS_PATH),
        artifact_record(inventory_path, include_sha256=False, extras={"note": "generated inventory hashes all unlock parquet source artifacts"}),
    ]

    summary = [
        f"classification={classification}",
        f"unlock_file_count={artifact_status['unlock_file_count']}",
        f"unlock_symbols={coverage.get('unlock_symbols')}",
        f"phase4_joined_rows_with_selected_unlock={selected_joined_rows}",
        f"phase4_joined_coverage_selected_unlock={overlap.get('joined_coverage_selected_unlock')}",
        f"shadow_mode_detected={str(shadow_mode).lower()}",
        f"research_candidate_found=false",
        f"official_promotion_allowed=false",
        f"paper_readiness_allowed=false",
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(item["path"]) for item in generated_core],
        "summary": summary,
        "gates": gate_metrics,
        "blockers": [
            "h06_diagnostic_only_no_research_candidate",
            "unlock_artifacts_shadow_or_proxy_heavy_not_official",
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "H06 used pnl_real only as a diagnostic outcome, not as an ex-ante rule.",
            "Unlock artifacts remain research/shadow diagnostics and are not official promotion evidence.",
            "The diagnostic does not authorize paper readiness, official promotion, merge, A3/A4 reopening or threshold relaxation.",
        ],
        "next_recommended_step": "Record H06 as diagnostic complete and keep final freeze/draft PR posture unless materially new evidence appears.",
    }
    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_core,
        "commands_executed": [
            "python services/ml_engine/phase5_research_unlock_shadow_feature_ablation.py",
            "python -m pytest tests/unit/test_phase5_research_unlock_shadow_feature_ablation.py -q",
        ],
        "notes": [
            "H06 unlock shadow feature ablation executed as research/diagnostic only.",
            "No official promotion, paper readiness, A3/A4 reopening, merge or threshold relaxation.",
            "pnl_real appears only as diagnostic outcome for ablation summaries.",
        ],
    }

    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=manifest,
        gate_metrics=gate_metrics,
        markdown_sections=_markdown_sections(
            {
                "summary": f"H06 diagnostic gate result: {status}/{decision}. Classification: {classification}.",
                "baseline": f"Branch `{git_context['branch']}` at `{git_context['head']}`.",
                "changes": "Added research/diagnostic-only unlock shadow feature ablation evidence from canonical unlock artifacts.",
                "artifacts": "\n".join(f"- `{item['path']}`" for item in generated_core),
                "results": "\n".join(summary),
                "evaluation": "\n".join(f"- {item['metric_name']}: {item['metric_value']} / {item['metric_threshold']} => {item['metric_status']}" for item in gate_metrics),
                "risks": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
                "verdict": f"{status}/{decision}. H06 diagnostic complete; no research candidate or official promotion.",
            }
        ),
    )
    return gate_report


def main() -> int:
    report = run_gate()
    print(json.dumps({"gate_slug": GATE_SLUG, "status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
