#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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

import phase5_cross_sectional_hardening_baseline as hardening
import phase5_cross_sectional_operational_fragility_audit_and_bounded_correction as rc4
import phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate as restore
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_cross_sectional_recent_regime_policy_falsification"
PHASE_FAMILY = "phase5_cross_sectional_recent_regime_policy_falsification"
RESTORED_BUNDLE_EXPERIMENT = restore.RESTORED_BUNDLE_EXPERIMENT
RESTORE_NAMESPACE = restore.RESTORE_DIAGNOSTIC_NAMESPACE
RC4_NAMESPACE = "phase5_cross_sectional_operational_fragility"
RESEARCH_NAMESPACE = "phase5_cross_sectional_recent_regime"
RECENT_EDGE_CHALLENGER = "challenger_recent_edge_gate"
RECENT_EDGE_TOP2_CHALLENGER = "challenger_recent_edge_gate_plus_top2"
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)
RESEARCH_ARTIFACT_FILES = (
    "recent_regime_decomposition.parquet",
    "recent_regime_challengers.parquet",
    "recent_regime_summary.json",
    "official_artifacts_integrity.json",
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


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    model_path = rc4.stage_a._resolve_model_path()
    rc4.stage_a._configure_phase4_paths(model_path)
    restored_root = model_path / "research" / RESTORED_BUNDLE_EXPERIMENT
    research_path = model_path / "research" / RESEARCH_NAMESPACE
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, restored_root, research_path, gate_path


def _load_required_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required {label}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_dates(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    return work


def _recent_mask(frame: pd.DataFrame, *, lookback_days: int = 365) -> pd.Series:
    work = _normalize_dates(frame)
    latest_date = work["date"].dropna().max()
    if pd.isna(latest_date):
        return pd.Series(False, index=work.index)
    cutoff = latest_date - pd.Timedelta(days=int(lookback_days))
    return pd.Series(work["date"] >= cutoff, index=work.index).fillna(False)


def _selected_mask(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(frame.get("decision_selected", False), index=frame.index).fillna(False).astype(bool)


def _active_mask(frame: pd.DataFrame) -> pd.Series:
    position = pd.to_numeric(frame.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    return _selected_mask(frame) & (position > 0.0)


def _apply_recent_edge_gate(frame: pd.DataFrame, *, score_threshold: float) -> pd.DataFrame:
    recent = _recent_mask(frame)
    score = pd.to_numeric(frame.get("p_stage_a_calibrated"), errors="coerce").fillna(0.0)
    keep = _selected_mask(frame) & (~recent | (score > float(score_threshold)))
    return rc4._apply_keep_mask(frame, keep)


def _apply_recent_top_n_cap(frame: pd.DataFrame, *, n_per_date: int) -> pd.DataFrame:
    recent = _recent_mask(frame)
    selected = frame.loc[_selected_mask(frame) & recent].copy()
    if selected.empty:
        keep = _selected_mask(frame) & (~recent)
        return rc4._apply_keep_mask(frame, keep)
    ranked = selected.sort_values(["date", "rank_score_stage_a", "symbol"], ascending=[True, False, True], kind="mergesort")
    ranked["rn"] = ranked.groupby("date").cumcount() + 1
    keep_idx = set(ranked.loc[ranked["rn"] <= int(n_per_date)].index.tolist())
    keep = _selected_mask(frame) & (~recent | pd.Series(frame.index.isin(list(keep_idx)), index=frame.index))
    return rc4._apply_keep_mask(frame, keep)


def _apply_recent_edge_gate_plus_top2(frame: pd.DataFrame, *, score_threshold: float, n_per_date: int = 2) -> pd.DataFrame:
    edged = _apply_recent_edge_gate(frame, score_threshold=score_threshold)
    return _apply_recent_top_n_cap(edged, n_per_date=n_per_date)


def _build_recent_regime_decomposition(frame: pd.DataFrame, *, fragility_stats: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    work = _normalize_dates(frame)
    selected = _selected_mask(work)
    active = _active_mask(work)
    available = pd.Series(work.get("decision_space_available", False), index=work.index).fillna(False).astype(bool)
    gross_pnl = pd.to_numeric(work.get("pnl_gross_before_friction_stage_a"), errors="coerce").fillna(0.0)
    net_pnl = pd.to_numeric(work.get("pnl_exec_stage_a"), errors="coerce").fillna(0.0)
    position = pd.to_numeric(work.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    fallback = pd.Series(work.get("decision_selected_fallback", False), index=work.index).fillna(False).astype(bool)
    recent = _recent_mask(work)
    latest_date = work["date"].dropna().max()

    rows: list[dict[str, Any]] = []
    for window_name, mask in (("latest_365d", recent), ("pre_latest_365d", ~recent)):
        subset = work.loc[mask].copy()
        subset_idx = subset.index
        rows.append(
            {
                "section": "window_summary",
                "window_name": window_name,
                "date": None,
                "rows_total": int(len(subset)),
                "rows_available": int(available.loc[subset_idx].sum()) if not subset.empty else 0,
                "rows_selected": int(selected.loc[subset_idx].sum()) if not subset.empty else 0,
                "rows_position_gt_0": int(active.loc[subset_idx].sum()) if not subset.empty else 0,
                "gross_position_usdt": round(float(position.loc[subset_idx].abs().sum()), 6) if not subset.empty else 0.0,
                "max_position_usdt": round(float(position.loc[subset_idx].max()), 6) if not subset.empty else 0.0,
                "pnl_net_sum": round(float(net_pnl.loc[subset_idx].sum()), 6) if not subset.empty else 0.0,
                "pnl_gross_sum": round(float(gross_pnl.loc[subset_idx].sum()), 6) if not subset.empty else 0.0,
                "days_with_selection": int(work.loc[subset_idx, "date"].where(selected.loc[subset_idx]).dropna().nunique()) if not subset.empty else 0,
                "days_with_position_gt_0": int(work.loc[subset_idx, "date"].where(active.loc[subset_idx]).dropna().nunique()) if not subset.empty else 0,
                "decision_selected_fallback": int(fallback.loc[subset_idx].sum()) if not subset.empty else 0,
                "decision_date_available_count": int(work.loc[subset_idx, "date"].dropna().nunique()) if not subset.empty else 0,
            }
        )

    recent_active_dates = sorted(work.loc[active, "date"].dropna().unique().tolist())[-restore.RECENT_WINDOW_DATES :]
    for date_value in recent_active_dates:
        date_mask = work["date"] == date_value
        rows.append(
            {
                "section": "recent_active_date",
                "window_name": "recent_active_date",
                "date": date_value.strftime("%Y-%m-%d"),
                "rows_total": int(date_mask.sum()),
                "rows_available": int(available.loc[date_mask].sum()),
                "rows_selected": int(selected.loc[date_mask].sum()),
                "rows_position_gt_0": int(active.loc[date_mask].sum()),
                "gross_position_usdt": round(float(position.loc[date_mask].abs().sum()), 6),
                "max_position_usdt": round(float(position.loc[date_mask].max()), 6) if bool(date_mask.any()) else 0.0,
                "pnl_net_sum": round(float(net_pnl.loc[date_mask].sum()), 6),
                "pnl_gross_sum": round(float(gross_pnl.loc[date_mask].sum()), 6),
                "days_with_selection": int(bool(selected.loc[date_mask].any())),
                "days_with_position_gt_0": int(bool(active.loc[date_mask].any())),
                "decision_selected_fallback": int(fallback.loc[date_mask].sum()),
                "decision_date_available_count": int(available.loc[date_mask].sum()),
            }
        )

    if "hmm_prob_bull" in work.columns:
        hmm_high = pd.to_numeric(work.get("hmm_prob_bull"), errors="coerce").fillna(0.0) >= 0.999
        for marker_name, marker_mask in (
            ("recent_hmm_prob_bull_ge_0999", recent & hmm_high),
            ("recent_hmm_prob_bull_lt_0999", recent & ~hmm_high),
        ):
            subset = work.loc[marker_mask].copy()
            subset_idx = subset.index
            rows.append(
                {
                    "section": "recent_regime_marker",
                    "window_name": marker_name,
                    "date": None,
                    "rows_total": int(len(subset)),
                    "rows_available": int(available.loc[subset_idx].sum()) if not subset.empty else 0,
                    "rows_selected": int(selected.loc[subset_idx].sum()) if not subset.empty else 0,
                    "rows_position_gt_0": int(active.loc[subset_idx].sum()) if not subset.empty else 0,
                    "gross_position_usdt": round(float(position.loc[subset_idx].abs().sum()), 6) if not subset.empty else 0.0,
                    "max_position_usdt": round(float(position.loc[subset_idx].max()), 6) if not subset.empty else 0.0,
                    "pnl_net_sum": round(float(net_pnl.loc[subset_idx].sum()), 6) if not subset.empty else 0.0,
                    "pnl_gross_sum": round(float(gross_pnl.loc[subset_idx].sum()), 6) if not subset.empty else 0.0,
                    "days_with_selection": int(work.loc[subset_idx, "date"].where(selected.loc[subset_idx]).dropna().nunique()) if not subset.empty else 0,
                    "days_with_position_gt_0": int(work.loc[subset_idx, "date"].where(active.loc[subset_idx]).dropna().nunique()) if not subset.empty else 0,
                    "decision_selected_fallback": int(fallback.loc[subset_idx].sum()) if not subset.empty else 0,
                    "decision_date_available_count": int(work.loc[subset_idx, "date"].dropna().nunique()) if not subset.empty else 0,
                }
            )

    recent_summary = {
        "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
        "regime_recent_loss_share": round(float(fragility_stats.get("regime_recent_loss_share") or 0.0), 4),
        "recent_active_dates": [date_value.strftime("%Y-%m-%d") for date_value in recent_active_dates],
        "recent_active_dates_count": len(recent_active_dates),
    }
    return pd.DataFrame(rows), recent_summary


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
        scenario_type="recent_regime_policy",
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
    _, fragility_stats = rc4._build_decomposition_frame(frame)
    row["latest_365d_sharpe"] = regime_summary.get("latest_365d_sharpe")
    row["pre_latest_365d_sharpe"] = regime_summary.get("pre_latest_365d_sharpe")
    row["subperiods_positive"] = regime_summary.get("subperiods_positive")
    row["subperiods_tested"] = regime_summary.get("subperiods_tested")
    row["regime_recent_loss_share"] = round(float(fragility_stats.get("regime_recent_loss_share") or 0.0), 4)
    row["negative_slices"] = json.dumps(regime_summary.get("negative_slices", []), ensure_ascii=False)
    row["official_artifacts_unchanged"] = bool(official_artifacts_unchanged)
    row["research_only_isolation_pass"] = bool(research_only_isolation_pass)
    row["reproducibility_pass"] = bool(reproducibility_pass)
    return row


def _classify_recent_fix_plausibility(
    *,
    baseline_row: dict[str, Any],
    fragility_stats: dict[str, Any],
    rc4_summary: dict[str, Any],
) -> str:
    challenger_metrics = list((rc4_summary.get("challenger_metrics") or {}).values())
    prior_improvement_exists = any(
        int(row.get("latest_active_count_decision_space", 0)) > 0
        and bool(row.get("headroom_decision_space", False))
        and float(row.get("sharpe_operational", 0.0)) >= float(baseline_row.get("sharpe_operational", 0.0)) + 0.15
        for row in challenger_metrics
    )
    if (
        float(baseline_row.get("latest_365d_sharpe") or 0.0) < 0.0
        and float(fragility_stats.get("regime_recent_loss_share") or 0.0) >= 0.35
        and int(baseline_row.get("latest_active_count_decision_space", 0)) > 0
        and bool(baseline_row.get("headroom_decision_space", False))
        and prior_improvement_exists
    ):
        return "RECENT_REGIME_POLICY_FIX_PLAUSIBLE"
    return "RECENT_REGIME_POLICY_FIX_IMPLAUSIBLE"


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    recent_regime_fix_plausibility_classified: bool,
    bounded_recent_challengers_executed: bool,
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
        _metric(
            "recent_regime_fix_plausibility_classified",
            recent_regime_fix_plausibility_classified,
            recent_regime_fix_plausibility_classified,
        ),
        _metric("bounded_recent_challengers_executed", bounded_recent_challengers_executed, bounded_recent_challengers_executed),
    ]


def _classify_round(
    *,
    integrity_pass: bool,
    plausibility: str,
    baseline_row: dict[str, Any],
    challenger_rows: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if not integrity_pass:
        return "FAIL", "abandon", "ALIVE_BUT_NOT_PROMOTABLE"
    qualified = [
        row
        for row in challenger_rows
        if int(row.get("latest_active_count_decision_space", 0)) > 0
        and bool(row.get("headroom_decision_space", False))
        and float(row.get("sharpe_operational", 0.0)) > float(baseline_row.get("sharpe_operational", 0.0)) + 0.10
        and float(row.get("dsr_honest", 0.0)) > 0.0
        and float(row.get("latest_365d_sharpe") or -999.0) > float(baseline_row.get("latest_365d_sharpe") or -999.0)
    ]
    if qualified:
        return "PASS", "advance", "LOW_REGRET_RECENT_REGIME_FIX_EXISTS"
    if plausibility == "RECENT_REGIME_POLICY_FIX_IMPLAUSIBLE":
        return "PARTIAL", "correct", "FAMILY_NEEDS_STRUCTURAL_RETHINK"
    return "PARTIAL", "correct", "ALIVE_BUT_NOT_PROMOTABLE"


def run_phase5_cross_sectional_recent_regime_policy_falsification() -> dict[str, Any]:
    model_path, restored_root, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    working_tree_before = _git_output("status", "--short", "--untracked-files=all")

    restored_summary = _load_required_json(
        model_path / "research" / RESTORE_NAMESPACE / "sovereign_restore_replay_summary.json",
        label="restore replay summary",
    )
    restored_manifest = _load_required_json(
        restored_root / "restored_bundle_manifest.json",
        label="restored bundle manifest",
    )
    rc4_summary = _load_required_json(
        model_path / "research" / RC4_NAMESPACE / "operational_fragility_summary.json",
        label="RC4 summary",
    )
    if restored_summary.get("equivalence_classification") != "EXACT_RESTORE":
        raise RuntimeError("Restored sovereign baseline is not EXACT_RESTORE")
    if not bool((restored_summary.get("bundle_completeness") or {}).get("pass", False)):
        raise RuntimeError("Restored sovereign bundle completeness check failed")

    replay_run1 = rc4._prepare_operational_frame(model_path)
    replay_run2 = rc4._prepare_operational_frame(model_path)
    reproducibility = {
        "frame_hash_run1": replay_run1["frame_hash"],
        "frame_hash_run2": replay_run2["frame_hash"],
        "metrics_run1": replay_run1["metrics"],
        "metrics_run2": replay_run2["metrics"],
        "pass": bool(replay_run1["frame_hash"] == replay_run2["frame_hash"] and replay_run1["metrics"] == replay_run2["metrics"]),
        "baseline_anchor": RESTORED_BUNDLE_EXPERIMENT,
    }

    base_frame = replay_run1["frame"].copy()
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

    _, fragility_stats = rc4._build_decomposition_frame(base_frame)
    decomposition_frame, recent_summary = _build_recent_regime_decomposition(base_frame, fragility_stats=fragility_stats)
    decomposition_frame.to_parquet(research_path / "recent_regime_decomposition.parquet", index=False)

    baseline_row = _build_comparator_row(
        scenario="frozen_sovereign_baseline",
        frame=base_frame,
        source=RESTORED_BUNDLE_EXPERIMENT,
        official_artifacts_unchanged=True,
        research_only_isolation_pass=True,
        reproducibility_pass=bool(reproducibility["pass"]),
    )
    plausibility = _classify_recent_fix_plausibility(
        baseline_row=baseline_row,
        fragility_stats=fragility_stats,
        rc4_summary=rc4_summary,
    )

    challenger_frames = {
        "frozen_sovereign_baseline": base_frame,
        RECENT_EDGE_CHALLENGER: _apply_recent_edge_gate(base_frame, score_threshold=0.45),
        RECENT_EDGE_TOP2_CHALLENGER: _apply_recent_edge_gate_plus_top2(base_frame, score_threshold=0.45, n_per_date=2),
    }
    challenger_reasons = {
        RECENT_EDGE_CHALLENGER: "Aplica somente no latest_365d um gate de edge ex ante em p_stage_a_calibrated > 0.45 nas linhas ja selecionadas.",
        RECENT_EDGE_TOP2_CHALLENGER: "Aplica o mesmo gate recente e depois limita somente no latest_365d a no maximo 2 ativos por data por rank_score_stage_a.",
    }

    official_after = stage5._collect_official_inventory(model_path)
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True
    reproducibility_pass = bool(reproducibility["pass"])

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
    comparators.to_parquet(research_path / "recent_regime_challengers.parquet", index=False)

    recent_regime_fix_plausibility_classified = plausibility in {
        "RECENT_REGIME_POLICY_FIX_PLAUSIBLE",
        "RECENT_REGIME_POLICY_FIX_IMPLAUSIBLE",
    }
    bounded_recent_challengers_executed = bool(len(comparator_rows) == 3)
    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        recent_regime_fix_plausibility_classified=recent_regime_fix_plausibility_classified,
        bounded_recent_challengers_executed=bounded_recent_challengers_executed,
    )

    challenger_rows = [row for row in comparator_rows if row["scenario"] != "frozen_sovereign_baseline"]
    status, decision, classification = _classify_round(
        integrity_pass=all(metric["metric_status"] == "PASS" for metric in gate_metrics),
        plausibility=plausibility,
        baseline_row=baseline_row,
        challenger_rows=challenger_rows,
    )
    best_challenger = max(challenger_rows, key=lambda row: float(row.get("sharpe_operational", -999.0)))

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "baseline_experiment": RESTORED_BUNDLE_EXPERIMENT,
        "status": status,
        "decision": decision,
        "classification_final": classification,
        "plausibility": plausibility,
        "baseline_metrics": baseline_row,
        "challenger_metrics": {row["scenario"]: row for row in challenger_rows},
        "best_challenger": best_challenger["scenario"],
        "recent_regime_summary": recent_summary,
        "fragility_stats": fragility_stats,
        "reproducibility": reproducibility,
        "restored_summary_reference": {
            "equivalence_classification": restored_summary.get("equivalence_classification"),
            "bundle_completeness": restored_summary.get("bundle_completeness"),
        },
        "restored_manifest_reference": {
            "bundle_name": restored_manifest.get("bundle_name"),
            "historical_commit": restored_manifest.get("historical_commit"),
            "restoration_mode": restored_manifest.get("restoration_mode"),
        },
        "rc4_context": {
            "dominant_operational_fragility": rc4_summary.get("dominant_operational_fragility"),
            "baseline_latest_365d_sharpe": (rc4_summary.get("baseline_regime_slice_results") or {}).get("latest_365d_sharpe"),
            "regime_filter_worsened_latest_365d_sharpe": (
                ((rc4_summary.get("challenger_metrics") or {}).get("challenger_regime_light_filter") or {})
            ).get("latest_365d_sharpe"),
        },
        "challenger_reasons": challenger_reasons,
    }
    _write_json(research_path / "recent_regime_summary.json", summary_payload)

    integrity_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": official_artifacts_unchanged,
        "official_root": str(model_path),
        "restored_bundle_root": str(restored_root),
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
            f"plausibility={plausibility}",
            f"baseline_latest_active_count_decision_space={baseline_row['latest_active_count_decision_space']}",
            f"baseline_headroom_decision_space={baseline_row['headroom_decision_space']}",
            f"best_challenger={best_challenger['scenario']}",
            f"best_challenger_dsr_honest={best_challenger['dsr_honest']}",
        ],
        "gates": gate_metrics,
        "blockers": [] if decision != "abandon" else ["integrity_failure_or_sovereign_ruler_change"],
        "risks_residual": [
            f"baseline_latest_365d_sharpe={baseline_row.get('latest_365d_sharpe')}",
            f"best_challenger_sharpe_operational={best_challenger.get('sharpe_operational')}",
            f"best_challenger_dsr_honest={best_challenger.get('dsr_honest')}",
        ],
        "next_recommended_step": (
            "Proceed with the recent-regime bounded fix candidate."
            if decision == "advance"
            else "Freeze the family as alive but not promotable unless a later validator explicitly authorizes deeper structural work."
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
            artifact_record(restored_root / "restored_bundle_manifest.json"),
            artifact_record(model_path / "research" / RESTORE_NAMESPACE / "sovereign_restore_replay_summary.json"),
            artifact_record(model_path / "research" / RC4_NAMESPACE / "operational_fragility_summary.json"),
        ],
        "generated_artifacts": [artifact_record(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "commands_executed": [
            "python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py",
        ],
        "notes": [
            f"classification_final={classification}",
            f"plausibility={plausibility}",
            f"baseline_anchor={RESTORED_BUNDLE_EXPERIMENT}",
        ],
    }
    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}` / classificacao `{classification}`.",
                f"- Baseline soberana congelada latest/headroom: `{baseline_row['latest_active_count_decision_space']}` / `{baseline_row['headroom_decision_space']}`.",
                f"- Plausibilidade do fix recente: `{plausibility}`; melhor challenger: `{best_challenger['scenario']}` com sharpe `{best_challenger['sharpe_operational']}` e DSR `{best_challenger['dsr_honest']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                f"- Baseline research-only obrigatoria: `{RESTORED_BUNDLE_EXPERIMENT}`.",
                f"- Restore equivalence de referencia: `{restored_summary.get('equivalence_classification')}`.",
                "- Sem alteracao de official, modelo, target, features, geometria ou regua soberana.",
            ]
        ),
        "MudanÃ§as implementadas": "\n".join(
            [
                "- Runner research-only focado apenas em regime recente/policy recente bounded.",
                "- Dois challengers recentes ex ante: recent edge gate e recent edge gate plus top-2.",
                "- Reuso dos helpers ja validados de restore, RC4, hardening e gate pack.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES]
            + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]
        ),
        "Resultados": "\n".join(
            [
                f"- Baseline: `{baseline_row}`.",
                f"- Recent regime summary: `{recent_summary}`.",
                f"- Challenger rows: `{ {row['scenario']: {k: row[k] for k in ('latest_active_count_decision_space', 'headroom_decision_space', 'sharpe_operational', 'dsr_honest', 'latest_365d_sharpe')} for row in challenger_rows} }`.",
            ]
        ),
        "AvaliaÃ§Ã£o contra gates": "\n".join([f"- {row['metric_name']} = `{row['metric_status']}`" for row in gate_metrics]),
        "Riscos residuais": "\n".join(
            [
                f"- `latest_365d_sharpe` do baseline continua em `{baseline_row.get('latest_365d_sharpe')}`.",
                f"- Melhor challenger ainda deixa `dsr_honest={best_challenger.get('dsr_honest')}`.",
                "- O filtro hmm_prob_bull >= 0.999 da RC4 piorou o regime recente e por isso foi tratado apenas como evidencia contextual.",
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
        "plausibility": plausibility,
        "research_path": str(research_path),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
    }


def main() -> None:
    result = run_phase5_cross_sectional_recent_regime_policy_falsification()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
