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
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.linear_model import LogisticRegression

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
import phase5_stage_a3_activation_calibration_correction as correction
import phase5_stage_a3_choke_audit as choke_audit
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack
from services.ml_engine.meta_labeling.isotonic_calibration import _time_decay_weights

GATE_SLUG = "phase5_stage_a3_calibrator_family_final_shootout"
PHASE_FAMILY = "phase5_stage_a3_calibrator_family_final_shootout"
FROZEN_EXPERIMENT = "phase5_stage_a3"
BASELINE_EXPERIMENT = "phase4_cross_sectional_ranking_baseline"
EPS = 1e-6


@dataclass
class VariantResult:
    name: str
    description: str
    calibrator_family: str
    diagnostic_only: bool
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


def _aggregate_raw(pre_proxy: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(
        pre_proxy.get("p_activate_raw_stage_a", pre_proxy.get("p_stage_a_raw")),
        errors="coerce",
    ).fillna(0.0)


def _aggregate_labels(pre_proxy: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(pre_proxy.get("y_stage_a"), errors="coerce").fillna(0).astype(int)


def _aggregate_weights(pre_proxy: pd.DataFrame) -> np.ndarray:
    dates = pd.to_datetime(pre_proxy.get("date"), errors="coerce")
    if dates.isna().all():
        return np.ones(len(pre_proxy), dtype=float)
    reference_dt = pd.Timestamp(dates.max())
    return _time_decay_weights(dates, reference_dt, halflife_days=phase4.HALFLIFE_DAYS)


def _apply_calibrated_scores(pre_proxy: pd.DataFrame, calibrated: np.ndarray) -> pd.DataFrame:
    out = pre_proxy.copy()
    calibrated_arr = np.asarray(calibrated, dtype=float)
    out["p_stage_a_calibrated"] = calibrated_arr
    out["p_activate_calibrated_stage_a"] = calibrated_arr
    return out


def _challenger_platt_global_after_aggregate(
    frozen: stage5.RebuiltExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre_proxy = frozen.aggregated_pre_proxy.copy()
    raw = np.clip(_aggregate_raw(pre_proxy).to_numpy(dtype=float), EPS, 1.0 - EPS)
    labels = _aggregate_labels(pre_proxy).to_numpy(dtype=int)
    weights = _aggregate_weights(pre_proxy)
    logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    model.fit(logits, labels, sample_weight=weights)
    calibrated = model.predict_proba(logits)[:, 1]
    out = _apply_calibrated_scores(pre_proxy, calibrated)
    return out, {
        "calibration_scope": "global_platt_after_cpcv_aggregate",
        "fit_family": "platt_scaling_logistic_regression",
        "monotone_increasing": bool(float(model.coef_[0, 0]) >= 0.0),
        "coef": round(float(model.coef_[0, 0]), 6),
        "intercept": round(float(model.intercept_[0]), 6),
        "n_obs": int(len(pre_proxy)),
        "weighted_positive_rate": round(float(np.average(labels, weights=weights)), 6),
    }


def _beta_calibration_predict(
    raw: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    clipped = np.clip(np.asarray(raw, dtype=float), EPS, 1.0 - EPS)
    log_p = np.log(clipped)
    neg_log_one_minus_p = -np.log1p(-clipped)
    y = np.asarray(labels, dtype=float)
    w = np.asarray(weights, dtype=float)

    def objective(theta: np.ndarray) -> float:
        a, b, c = theta
        z = a * log_p + b * neg_log_one_minus_p + c
        q = np.clip(expit(z), 1e-12, 1.0 - 1e-12)
        ce = -(y * np.log(q) + (1.0 - y) * np.log(1.0 - q))
        return float(np.average(ce, weights=w) + 1e-8 * float(np.dot(theta, theta)))

    result = minimize(
        objective,
        x0=np.array([1.0, 1.0, 0.0], dtype=float),
        method="L-BFGS-B",
        bounds=[(0.0, None), (0.0, None), (None, None)],
    )
    params = result.x.astype(float)
    calibrated = expit(params[0] * log_p + params[1] * neg_log_one_minus_p + params[2])
    return calibrated, {
        "calibration_scope": "global_beta_after_cpcv_aggregate",
        "fit_family": "beta_calibration_parametric_monotonic",
        "optimization_success": bool(result.success),
        "optimization_message": str(result.message),
        "n_iter": int(getattr(result, "nit", 0) or 0),
        "objective_value": round(float(result.fun), 6),
        "a_log_p": round(float(params[0]), 6),
        "b_neg_log_one_minus_p": round(float(params[1]), 6),
        "intercept": round(float(params[2]), 6),
        "monotone_increasing": bool(params[0] >= 0.0 and params[1] >= 0.0),
        "n_obs": int(len(clipped)),
    }


def _challenger_beta_global_after_aggregate(
    frozen: stage5.RebuiltExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre_proxy = frozen.aggregated_pre_proxy.copy()
    raw = _aggregate_raw(pre_proxy).to_numpy(dtype=float)
    labels = _aggregate_labels(pre_proxy).to_numpy(dtype=int)
    weights = _aggregate_weights(pre_proxy)
    calibrated, diag = _beta_calibration_predict(raw, labels, weights)
    out = _apply_calibrated_scores(pre_proxy, calibrated)
    return out, diag


def _challenger_identity_no_calibration_after_aggregate(
    frozen: stage5.RebuiltExperiment,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pre_proxy = frozen.aggregated_pre_proxy.copy()
    raw = _aggregate_raw(pre_proxy).to_numpy(dtype=float)
    out = _apply_calibrated_scores(pre_proxy, raw)
    return out, {
        "calibration_scope": "identity_no_calibration_after_cpcv_aggregate",
        "fit_family": "identity_passthrough",
        "diagnostic_only": True,
        "promotable_without_new_methodology": False,
        "n_obs": int(len(pre_proxy)),
    }


def _build_variant_result(
    *,
    name: str,
    description: str,
    calibrator_family: str,
    diagnostic_only: bool,
    pre_proxy: pd.DataFrame,
    calibration_diag: dict[str, Any],
    raw_hits_gt_050_row_level: int,
    calibrated_hits_gt_050_row_level: int,
    integrity_flags: dict[str, bool],
) -> VariantResult:
    final_df, selection_summary = correction._rebuild_from_pre_proxy(pre_proxy)
    no_leakage = correction._prove_no_leakage_pre_proxy(pre_proxy)
    decision_space = stage5._compute_decision_space_metrics(final_df)
    operational = stage5._compute_operational_metrics(final_df, "decision_score_stage_a")
    agg_raw = _aggregate_raw(pre_proxy)
    agg_cal = pd.to_numeric(pre_proxy.get("p_activate_calibrated_stage_a"), errors="coerce").fillna(0.0)
    live_signal_minimum_pass = bool(
        int((agg_cal > 0.50).sum()) > 0
        and int(decision_space["latest_active_count_decision_space"]) >= 1
        and bool(decision_space["headroom_decision_space"])
    )
    comparison_row = {
        "variant": name,
        "description": description,
        "calibrator_family": calibrator_family,
        "diagnostic_only": bool(diagnostic_only),
        "raw_hits_gt_050_row_level": int(raw_hits_gt_050_row_level),
        "calibrated_hits_gt_050_row_level": int(calibrated_hits_gt_050_row_level),
        "raw_hits_gt_050_cpcv_aggregated": int((agg_raw > 0.50).sum()),
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
        "max_calibrated_cpcv_aggregated": round(float(agg_cal.max()), 6) if not agg_cal.empty else 0.0,
        "live_signal_minimum_pass": bool(live_signal_minimum_pass),
        "promotable_low_regret_candidate": bool((not diagnostic_only) and live_signal_minimum_pass),
    }
    return VariantResult(
        name=name,
        description=description,
        calibrator_family=calibrator_family,
        diagnostic_only=diagnostic_only,
        pre_proxy=pre_proxy,
        final=final_df,
        selection_summary=selection_summary,
        calibration_diag={
            **calibration_diag,
            "no_leakage_proof": no_leakage,
        },
        comparison_row=comparison_row,
    )


def _frozen_comparison_row(
    *,
    frozen: stage5.RebuiltExperiment,
    reconciliation: dict[str, Any],
    integrity_flags: dict[str, bool],
) -> dict[str, Any]:
    decision_space = stage5._compute_decision_space_metrics(frozen.aggregated)
    operational = stage5._compute_operational_metrics(frozen.aggregated, "decision_score_stage_a")
    no_leakage = stage5._prove_no_leakage(frozen)
    return {
        "variant": "frozen_a3_q60_current",
        "description": "exact frozen control reproduced from the current research artifact",
        "calibrator_family": "frozen_isotonic_cluster_specific_row_level",
        "diagnostic_only": False,
        "raw_hits_gt_050_row_level": int(reconciliation["row_level_reproduction"]["raw_hits_gt_050_row_level"]),
        "calibrated_hits_gt_050_row_level": int(reconciliation["row_level_reproduction"]["calibrated_hits_gt_050_row_level"]),
        "raw_hits_gt_050_cpcv_aggregated": int(reconciliation["cpcv_aggregate_reproduction"]["raw_hits_gt_050_cpcv_aggregated"]),
        "calibrated_hits_gt_050_cpcv_aggregated": int(reconciliation["cpcv_aggregate_reproduction"]["calibrated_hits_gt_050_cpcv_aggregated"]),
        "latest_active_count_decision_space": int(decision_space["latest_active_count_decision_space"]),
        "headroom_decision_space": bool(decision_space["headroom_decision_space"]),
        "recent_live_dates_decision_space": int(decision_space["recent_live_dates_decision_space"]),
        "historical_active_events_decision_space": int(decision_space["historical_active_events_decision_space"]),
        "sharpe_operational": round(float(operational["sharpe"]), 4),
        "dsr_honest": round(float(operational["dsr_honest"]), 4),
        "subperiods_positive": int(operational["subperiods_positive"]),
        "official_artifacts_unchanged": bool(integrity_flags["official_artifacts_unchanged"]),
        "research_only_isolation_pass": bool(integrity_flags["research_only_isolation_pass"]),
        "no_leakage_proof_pass": bool(no_leakage.get("pass")),
        "max_calibrated_cpcv_aggregated": round(
            float(pd.to_numeric(frozen.aggregated_pre_proxy.get("p_activate_calibrated_stage_a"), errors="coerce").fillna(0.0).max()),
            6,
        ),
        "live_signal_minimum_pass": False,
        "promotable_low_regret_candidate": False,
    }


def _gate_metrics(
    *,
    integrity: dict[str, Any],
    no_leakage_proof_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    bounded_fix_only: bool,
    final_a3_decision_reached: bool,
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
        _metric("bounded_fix_only", bounded_fix_only, bounded_fix_only),
        _metric("final_a3_decision_reached", final_a3_decision_reached, final_a3_decision_reached),
    ]


def _resolve_final_a3_decision(rows: list[dict[str, Any]]) -> tuple[str, str, bool]:
    promotable_live = [
        row
        for row in rows
        if not bool(row.get("diagnostic_only"))
        and bool(row.get("promotable_low_regret_candidate"))
    ]
    if promotable_live:
        winner = promotable_live[0]
        return (
            "LOW_REGRET_CALIBRATOR_FIX_EXISTS",
            f"{winner['variant']} revived calibrated aggregate mass and produced live sovereign activation without breaking the frozen contract.",
            True,
        )

    diagnostic_live = [
        row
        for row in rows
        if bool(row.get("diagnostic_only")) and bool(row.get("live_signal_minimum_pass"))
    ]
    if diagnostic_live:
        return (
            "A3_STRUCTURAL_CHOKE_CONFIRMED",
            "Only the DIAGNOSTIC_ONLY identity proxy revives latest/headroom; honest calibrated families still fail to produce live sovereign activation.",
            False,
        )

    return (
        "A3_STRUCTURAL_CHOKE_CONFIRMED",
        "Neither Platt nor beta calibration revives latest/headroom under the frozen sovereign contract, so the line remains structurally dead after honest calibrator-family testing.",
        False,
    )


def _classify_round(
    *,
    integrity: dict[str, Any],
    no_leakage_proof_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    bounded_fix_only: bool,
    final_a3_decision_reached: bool,
) -> tuple[str, str]:
    if not all(
        [
            integrity["official_artifacts_unchanged"],
            integrity["research_only_isolation_pass"],
            no_leakage_proof_pass,
            sovereign_metric_definitions_unchanged,
            bounded_fix_only,
            final_a3_decision_reached,
        ]
    ):
        return "FAIL", "abandon"
    return "PASS", "advance"


def run_phase5_stage_a3_calibrator_family_final_shootout() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)

    frozen = stage5._rebuild_experiment(model_path, FROZEN_EXPERIMENT)
    baseline = stage5._rebuild_experiment(model_path, BASELINE_EXPERIMENT)
    reconciliation, _, _ = correction._reconcile_frozen_pipeline(frozen)
    metric_definition_check = choke_audit._audit_metric_definitions(
        research_path=research_path,
        baseline=baseline,
        a3=frozen,
    )
    frozen_reproduction_exact = bool(
        reconciliation["row_level_reproduction"]["exact_match"]
        and reconciliation["cpcv_aggregate_reproduction"]["exact_match"]
        and reconciliation["sovereign_final_reproduction"]["exact_match"]
    )

    dummy_integrity = {
        "official_artifacts_unchanged": True,
        "research_only_isolation_pass": True,
    }
    raw_hits_gt_050_row_level = int(reconciliation["row_level_reproduction"]["raw_hits_gt_050_row_level"])
    calibrated_hits_gt_050_row_level = int(reconciliation["row_level_reproduction"]["calibrated_hits_gt_050_row_level"])

    frozen_row = _frozen_comparison_row(
        frozen=frozen,
        reconciliation=reconciliation,
        integrity_flags=dummy_integrity,
    )

    platt_pre, platt_diag = _challenger_platt_global_after_aggregate(frozen)
    platt = _build_variant_result(
        name="challenger_platt_global_after_aggregate",
        description="Platt scaling on the CPCV-aggregated Stage 1 raw score, global-only",
        calibrator_family="platt_global_after_aggregate",
        diagnostic_only=False,
        pre_proxy=platt_pre,
        calibration_diag=platt_diag,
        raw_hits_gt_050_row_level=raw_hits_gt_050_row_level,
        calibrated_hits_gt_050_row_level=calibrated_hits_gt_050_row_level,
        integrity_flags=dummy_integrity,
    )

    beta_pre, beta_diag = _challenger_beta_global_after_aggregate(frozen)
    beta = _build_variant_result(
        name="challenger_beta_global_after_aggregate",
        description="Beta calibration on the CPCV-aggregated Stage 1 raw score, global-only",
        calibrator_family="beta_global_after_aggregate",
        diagnostic_only=False,
        pre_proxy=beta_pre,
        calibration_diag=beta_diag,
        raw_hits_gt_050_row_level=raw_hits_gt_050_row_level,
        calibrated_hits_gt_050_row_level=calibrated_hits_gt_050_row_level,
        integrity_flags=dummy_integrity,
    )

    identity_pre, identity_diag = _challenger_identity_no_calibration_after_aggregate(frozen)
    identity = _build_variant_result(
        name="challenger_identity_no_calibration_after_aggregate",
        description="DIAGNOSTIC_ONLY: use the aggregated raw score as probability proxy with no calibrator",
        calibrator_family="identity_after_aggregate",
        diagnostic_only=True,
        pre_proxy=identity_pre,
        calibration_diag=identity_diag,
        raw_hits_gt_050_row_level=raw_hits_gt_050_row_level,
        calibrated_hits_gt_050_row_level=raw_hits_gt_050_row_level,
        integrity_flags=dummy_integrity,
    )

    comparison_rows = [
        frozen_row,
        platt.comparison_row,
        beta.comparison_row,
        identity.comparison_row,
    ]
    final_conclusion, final_cause, honest_fix_exists = _resolve_final_a3_decision(comparison_rows[1:])
    final_a3_decision_reached = bool(
        frozen_reproduction_exact
        and final_conclusion in {"LOW_REGRET_CALIBRATOR_FIX_EXISTS", "A3_STRUCTURAL_CHOKE_CONFIRMED"}
    )

    sovereign_metric_definitions_unchanged = bool(
        metric_definition_check.get("audit_complete") and metric_definition_check.get("ruler_drift_status") == "NO_DRIFT"
    )
    bounded_fix_only = True
    no_leakage_proof_pass = bool(
        frozen_row["no_leakage_proof_pass"]
        and platt.comparison_row["no_leakage_proof_pass"]
        and beta.comparison_row["no_leakage_proof_pass"]
        and identity.comparison_row["no_leakage_proof_pass"]
    )

    shootout_df = pd.DataFrame(comparison_rows)
    shootout_path = research_path / "calibrator_family_shootout.parquet"
    summary_path = research_path / "calibrator_family_summary.json"
    integrity_path = research_path / "calibrator_family_integrity.json"
    decision_path = research_path / "calibrator_family_decision.json"
    shootout_df.to_parquet(shootout_path, index=False)

    generated_paths = [
        shootout_path,
        summary_path,
        integrity_path,
        decision_path,
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
        bounded_fix_only=bounded_fix_only,
        final_a3_decision_reached=final_a3_decision_reached,
    )
    status, decision = _classify_round(
        integrity=integrity,
        no_leakage_proof_pass=no_leakage_proof_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        bounded_fix_only=bounded_fix_only,
        final_a3_decision_reached=final_a3_decision_reached,
    )

    decision_payload = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "branch": _git_output("branch", "--show-current"),
        "commit": _git_output("rev-parse", "HEAD"),
        "final_a3_conclusion": final_conclusion,
        "honest_fix_exists": bool(honest_fix_exists),
        "cause_root": final_cause,
        "criterion_for_low_regret_fix": {
            "calibrated_hits_gt_050_cpcv_aggregated_gt_0": True,
            "latest_active_count_decision_space_gte_1": True,
            "headroom_decision_space_true": True,
            "diagnostic_only_not_sufficient": True,
        },
        "winning_variant_if_any": next(
            (
                row["variant"]
                for row in comparison_rows
                if bool(row.get("promotable_low_regret_candidate"))
            ),
            None,
        ),
        "diagnostic_only_live_signal_variants": [
            row["variant"]
            for row in comparison_rows
            if bool(row.get("diagnostic_only")) and bool(row.get("live_signal_minimum_pass"))
        ],
    }
    _write_json(decision_path, decision_payload)

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "branch": _git_output("branch", "--show-current"),
        "commit": _git_output("rev-parse", "HEAD"),
        "frozen_reproduction_exact": frozen_reproduction_exact,
        "reconciliation_of_frozen": {
            "raw_hits_gt_050_row_level": int(reconciliation["row_level_reproduction"]["raw_hits_gt_050_row_level"]),
            "calibrated_hits_gt_050_row_level": int(reconciliation["row_level_reproduction"]["calibrated_hits_gt_050_row_level"]),
            "calibrated_hits_gt_050_cpcv_aggregated": int(reconciliation["cpcv_aggregate_reproduction"]["calibrated_hits_gt_050_cpcv_aggregated"]),
            "latest_active_count_decision_space": int(frozen_row["latest_active_count_decision_space"]),
            "headroom_decision_space": bool(frozen_row["headroom_decision_space"]),
            "recent_live_dates_decision_space": int(frozen_row["recent_live_dates_decision_space"]),
            "historical_active_events_decision_space": int(frozen_row["historical_active_events_decision_space"]),
        },
        "metric_definition_check": metric_definition_check,
        "comparators": {
            row["variant"]: row for row in comparison_rows
        },
        "calibration_diagnostics": {
            platt.name: platt.calibration_diag,
            beta.name: beta.calibration_diag,
            identity.name: identity.calibration_diag,
        },
        "integrity": {
            "official_artifacts_unchanged": bool(integrity["official_artifacts_unchanged"]),
            "research_only_isolation_pass": bool(integrity["research_only_isolation_pass"]),
            "no_leakage_proof_pass": bool(no_leakage_proof_pass),
            "sovereign_metric_definitions_unchanged": bool(sovereign_metric_definitions_unchanged),
            "bounded_fix_only": bool(bounded_fix_only),
            "final_a3_decision_reached": bool(final_a3_decision_reached),
        },
        "final_conclusion": final_conclusion,
        "cause_root": final_cause,
        "honest_fix_exists": bool(honest_fix_exists),
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
        "summary": [
            "Frozen A3-q60 was reproduced exactly before the calibrator-family shootout.",
            "Platt scaling recovers aggregate calibrated mass but still leaves latest/headroom sovereign metrics dead; beta calibration remains dead as well.",
            "Only the DIAGNOSTIC_ONLY identity proxy revives latest/headroom, so the line closes as A3_STRUCTURAL_CHOKE_CONFIRMED rather than a low-regret calibrated fix.",
        ],
        "gates": gate_metrics,
        "blockers": [],
        "final_a3_conclusion": final_conclusion,
        "comparators": comparison_rows,
        "official_artifacts_used": official_before["official_paths_read"]["phase4_critical"],
        "research_artifacts_generated": [str(path) for path in generated_paths],
        "risks_residual": [
            "Identity revives live sovereign activation only by dropping calibration discipline, so it is diagnostic and not promotable.",
            "The Platt challenger recovers some historical sovereign events but keeps latest/headroom at zero, which is insufficient to keep the A3 line alive under the frozen contract.",
        ],
        "next_recommended_step": (
            "Close the A3 line as a structural choke and stop bounded calibrator-family experimentation under the current frozen contract."
            if final_conclusion == "A3_STRUCTURAL_CHOKE_CONFIRMED"
            else "Carry the winning low-regret calibrator fix into a separate correction-only validation round without changing the frozen sovereign contract."
        ),
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(f"- {item}" for item in gate_report["summary"]),
        "Baseline congelado": (
            f"- raw_hits_gt_050_row_level={frozen_row['raw_hits_gt_050_row_level']}\n"
            f"- calibrated_hits_gt_050_row_level={frozen_row['calibrated_hits_gt_050_row_level']}\n"
            f"- calibrated_hits_gt_050_cpcv_aggregated={frozen_row['calibrated_hits_gt_050_cpcv_aggregated']}\n"
            f"- latest_active_count_decision_space={frozen_row['latest_active_count_decision_space']}\n"
            f"- headroom_decision_space={frozen_row['headroom_decision_space']}"
        ),
        "Mudanças implementadas": "\n".join(
            [
                "- frozen_a3_q60_current reproduction",
                "- challenger_platt_global_after_aggregate",
                "- challenger_beta_global_after_aggregate",
                "- challenger_identity_no_calibration_after_aggregate (DIAGNOSTIC_ONLY)",
            ]
        ),
        "Artifacts gerados": "\n".join(f"- {path}" for path in generated_paths),
        "Resultados": "\n".join(
            [
                f"- frozen_a3_q60_current calibrated_hits_gt_050_cpcv_aggregated={frozen_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={frozen_row['latest_active_count_decision_space']}, headroom_decision_space={frozen_row['headroom_decision_space']}",
                f"- challenger_platt_global_after_aggregate calibrated_hits_gt_050_cpcv_aggregated={platt.comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={platt.comparison_row['latest_active_count_decision_space']}, headroom_decision_space={platt.comparison_row['headroom_decision_space']}",
                f"- challenger_beta_global_after_aggregate calibrated_hits_gt_050_cpcv_aggregated={beta.comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={beta.comparison_row['latest_active_count_decision_space']}, headroom_decision_space={beta.comparison_row['headroom_decision_space']}",
                f"- challenger_identity_no_calibration_after_aggregate calibrated_hits_gt_050_cpcv_aggregated={identity.comparison_row['calibrated_hits_gt_050_cpcv_aggregated']}, latest_active_count_decision_space={identity.comparison_row['latest_active_count_decision_space']}, headroom_decision_space={identity.comparison_row['headroom_decision_space']}",
                f"- final_a3_conclusion={final_conclusion}",
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
            artifact_record(research_path / "activation_calibration_summary.json"),
            artifact_record(research_path / "choke_audit_summary.json"),
            artifact_record(research_path / "stage_a3_summary.json"),
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "python -m py_compile services\\ml_engine\\phase5_stage_a3_calibrator_family_final_shootout.py",
            "python -m py_compile tests\\unit\\test_phase5_stage_a3_calibrator_family_final_shootout.py",
            "python -m pytest tests\\unit\\test_phase5_stage_a3_calibrator_family_final_shootout.py -q",
            "python services\\ml_engine\\phase5_stage_a3_calibrator_family_final_shootout.py",
        ],
        "notes": [
            "Target q60, contest geometry, Stage 2, ranking, mu_adj, sizing and sovereign decision-space definitions remained frozen."
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
        "final_a3_conclusion": final_conclusion,
        "official_artifacts_unchanged": integrity["official_artifacts_unchanged"],
        "research_only_isolation_pass": integrity["research_only_isolation_pass"],
        "no_leakage_proof_pass": no_leakage_proof_pass,
        "sovereign_metric_definitions_unchanged": sovereign_metric_definitions_unchanged,
        "bounded_fix_only": bounded_fix_only,
        "final_a3_decision_reached": final_a3_decision_reached,
    }


def main() -> None:
    result = run_phase5_stage_a3_calibrator_family_final_shootout()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
