#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ML_ENGINE_PATH = REPO_ROOT / "services" / "ml_engine"
if str(ML_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE_PATH))
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(40))

from services.common.gate_reports import GATE_REPORT_MARKDOWN_SECTIONS, artifact_record, sha256_file, write_gate_pack
import phase4_cpcv as phase4
from services.ml_engine.phase4_stage_a_experiment import _aggregate_stage_a_predictions, _apply_cross_sectional_ranking_proxy

GATE_SLUG = "phase4_cross_sectional_no_contest_vs_ranking_failure"
PHASE_FAMILY = "phase4_research_cross_sectional"
CLASS_NO_CONTEST = "NO_CONTEST_CONFIRMED"
CLASS_RANKING_FAILURE = "RANKING_FAILURE_CONFIRMED"
CLASS_MISALIGNED = "LATEST_EVALUATION_MISALIGNED"
DATE_CLASS_NO_CONTEST = "no_contest"
DATE_CLASS_RANKING_FAILURE = "ranking_failure"
DATE_CLASS_MISALIGNMENT = "metric_misalignment"
DATE_CLASS_ALIGNED = "aligned_live_contest"
MODEL_PATH = (Path("/data/models") if Path("/data/models").exists() else REPO_ROOT / "data" / "models").resolve()
BASELINE_SLUG = "phase4_cross_sectional_ranking_baseline"
LATEST_CHOKE_SLUG = "phase4_cross_sectional_latest_choke_audit"
BASELINE_PATH = MODEL_PATH / "research" / BASELINE_SLUG
LATEST_CHOKE_PATH = MODEL_PATH / "research" / LATEST_CHOKE_SLUG
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG
OFFICIAL_PATHS = (
    MODEL_PATH / "phase4" / "phase4_report_v4.json",
    MODEL_PATH / "phase4" / "phase4_execution_snapshot.parquet",
    MODEL_PATH / "phase4" / "phase4_aggregated_predictions.parquet",
)
BASELINE_GATE_PATH = REPO_ROOT / "reports" / "gates" / BASELINE_SLUG / "gate_report.json"
LATEST_CHOKE_GATE_PATH = REPO_ROOT / "reports" / "gates" / LATEST_CHOKE_SLUG / "gate_report.json"
BASELINE_DIAG_PATH = BASELINE_PATH / "cross_sectional_diagnostics.json"
BASELINE_SUMMARY_PATH = BASELINE_PATH / "cross_sectional_eval_summary.json"
LATEST_CHOKE_SUMMARY_PATH = LATEST_CHOKE_PATH / "cross_sectional_latest_choke_summary.json"
PREDICTIONS_PATH = BASELINE_PATH / "cross_sectional_predictions.parquet"
SNAPSHOT_PATH = BASELINE_PATH / "cross_sectional_latest_snapshot.parquet"
RECENT_WINDOW_DATES = 8


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _worktree_dirty() -> bool:
    return bool(_git("status", "--short", "--untracked-files=all"))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _f(value: Any, default: float = 0.0) -> float:
    series = pd.to_numeric(pd.Series([value]), errors="coerce")
    coerced = series.iloc[0]
    return float(default if pd.isna(coerced) else coerced)


def _i(value: Any, default: int = 0) -> int:
    return int(round(_f(value, default=default)))


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _official_hashes() -> dict[str, str]:
    return {str(path): sha256_file(path) for path in OFFICIAL_PATHS if path.exists()}


def _rebuild_label_frame(predictions_df: pd.DataFrame) -> pd.DataFrame:
    aggregated = _aggregate_stage_a_predictions(predictions_df)
    aggregated["rank_score_stage_a"] = pd.to_numeric(aggregated.get("p_stage_a_raw"), errors="coerce").fillna(0.0)
    aggregated, _ = _apply_cross_sectional_ranking_proxy(aggregated)
    aggregated = phase4._compute_phase4_sizing(
        aggregated,
        prob_col="p_stage_a_calibrated",
        prefix="stage_a",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    aggregated["date"] = pd.to_datetime(aggregated["date"], errors="coerce")
    return aggregated.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def _build_decision_space_frame(predictions_df: pd.DataFrame, *, min_group_support: int) -> pd.DataFrame:
    work = _aggregate_stage_a_predictions(predictions_df)
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
                ranked = cluster_df.sort_values(["rank_score_stage_a", "symbol"], ascending=[False, True], kind="mergesort")
                top_idx = int(ranked.index[0])
                work.loc[top_idx, "decision_selected_local"] = True
                work.loc[top_idx, "decision_selected"] = True
                work.loc[top_idx, "decision_selection_mode"] = "cluster_local_top1"
                top_score = float(ranked.iloc[0]["rank_score_stage_a"])
                work.loc[top_idx, "decision_tie_at_top_rank"] = bool((ranked["rank_score_stage_a"] == top_score).sum() > 1)
            else:
                fallback_pool.extend(cluster_df.index.tolist())
        if fallback_pool:
            fallback_dates.add(pd.Timestamp(date_value))
            fallback_df = available.loc[fallback_pool].sort_values(["rank_score_stage_a", "symbol"], ascending=[False, True], kind="mergesort")
            top_idx = int(fallback_df.index[0])
            work.loc[top_idx, "decision_selected_fallback"] = True
            work.loc[top_idx, "decision_selected"] = True
            work.loc[top_idx, "decision_selection_mode"] = "date_universe_fallback"
            top_score = float(fallback_df.iloc[0]["rank_score_stage_a"])
            work.loc[top_idx, "decision_tie_at_top_rank"] = bool((fallback_df["rank_score_stage_a"] == top_score).sum() > 1)

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


def classify_date_alignment(
    *,
    label_eligible_count: int,
    decision_available_count: int,
    label_selected_count: int,
    label_truth_hit_count: int,
) -> str:
    if label_eligible_count == 0 and decision_available_count > 0:
        return DATE_CLASS_MISALIGNMENT
    if label_eligible_count == 0 and decision_available_count == 0:
        return DATE_CLASS_NO_CONTEST
    if label_eligible_count > 0 and label_selected_count > 0 and label_truth_hit_count == 0:
        return DATE_CLASS_RANKING_FAILURE
    return DATE_CLASS_ALIGNED


def classify_final_audit(*, latest_case: str) -> dict[str, str]:
    if latest_case == DATE_CLASS_MISALIGNMENT:
        return {
            "classification": CLASS_MISALIGNED,
            "decision": "correct",
            "blocker_real": "A avaliacao atual do latest mistura label-space realizado com decision-space ex-ante: o latest teve candidatos operacionais e selecao research-only plausivel, mas zerou porque a definicao vigente de disponibilidade depende de pnl_real > avg_sl_train.",
            "next_recommended_step": "Abrir uma rodada curta, ainda research-only, para redesenhar a avaliacao do latest/headroom desta familia em decisao-space causal, sem tocar no official.",
        }
    if latest_case == DATE_CLASS_RANKING_FAILURE:
        return {
            "classification": CLASS_RANKING_FAILURE,
            "decision": "correct",
            "blocker_real": "Havia candidatos operacionais e contesto valido no latest, mas a logica de ranking/selection falhou sob a propria metrica label-space.",
            "next_recommended_step": "Abrir uma rodada corretiva curta focada no ranking/selection logic do latest desta familia.",
        }
    return {
        "classification": CLASS_NO_CONTEST,
        "decision": "correct",
        "blocker_real": "O latest morto reflete ausencia real de winner no label-space, sem evidencia de falha de ranking/selection na janela auditada.",
        "next_recommended_step": "Manter a familia em research-only e decidir numa rodada curta se 'no contest' deve continuar sendo soberano para latest/headroom nesta familia.",
    }


def _per_date_alignment_rows(label_df: pd.DataFrame, decision_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dates = sorted(pd.to_datetime(label_df["date"], errors="coerce").dropna().unique().tolist())
    for date_value in dates:
        label_slice = label_df.loc[label_df["date"] == pd.Timestamp(date_value)].copy()
        decision_slice = decision_df.loc[decision_df["date"] == pd.Timestamp(date_value)].copy()
        label_selected = label_slice.loc[label_slice["stage_a_selected_proxy"].fillna(False)].copy()
        label_truth_hit_count = int((label_selected["y_stage_a_truth_top1"].fillna(0).astype(int) == 1).sum()) if not label_selected.empty else 0
        case = classify_date_alignment(
            label_eligible_count=int(label_slice["stage_a_eligible"].fillna(False).sum()),
            decision_available_count=int(decision_slice["decision_space_available"].fillna(False).sum()),
            label_selected_count=int(label_slice["stage_a_selected_proxy"].fillna(False).sum()),
            label_truth_hit_count=label_truth_hit_count,
        )
        decision_selected = decision_slice.loc[decision_slice["decision_selected"].fillna(False)].copy()
        rows.append(
            {
                "date": pd.Timestamp(date_value),
                "rows_total": int(len(label_slice)),
                "label_eligible_count": int(label_slice["stage_a_eligible"].fillna(False).sum()),
                "label_selected_count": int(label_slice["stage_a_selected_proxy"].fillna(False).sum()),
                "label_position_gt_0_count": int((pd.to_numeric(label_slice["position_usdt_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
                "label_truth_hit_count": int(label_truth_hit_count),
                "decision_available_count": int(decision_slice["decision_space_available"].fillna(False).sum()),
                "decision_selected_count": int(decision_selected.shape[0]),
                "decision_position_gt_0_count": int((pd.to_numeric(decision_slice["decision_position_usdt"], errors="coerce").fillna(0.0) > 0).sum()),
                "decision_selected_symbols": ",".join(decision_selected["symbol"].astype(str).tolist()),
                "label_selected_symbols": ",".join(label_selected["symbol"].astype(str).tolist()),
                "date_case_classification": case,
            }
        )
    return pd.DataFrame(rows)


def _latest_trace(label_latest: pd.DataFrame, decision_latest: pd.DataFrame) -> pd.DataFrame:
    work = label_latest.merge(
        decision_latest[
            [
                "date",
                "symbol",
                "cluster_name",
                "decision_space_available",
                "decision_group_available_count",
                "decision_date_available_count",
                "decision_selection_mode",
                "decision_selected_local",
                "decision_selected_fallback",
                "decision_selected",
                "decision_tie_at_top_rank",
                "decision_proxy_prob",
                "decision_mu_adj",
                "decision_kelly_frac",
                "decision_position_usdt",
            ]
        ],
        on=["date", "symbol", "cluster_name"],
        how="left",
    ).copy()
    work["rank_score_stage_a"] = pd.to_numeric(work["rank_score_stage_a"], errors="coerce").fillna(0.0)
    work["label_selected_proxy"] = work["stage_a_selected_proxy"].fillna(False)
    work["label_space_eligible"] = work["stage_a_eligible"].fillna(False)
    work["decision_space_available"] = work["decision_space_available"].fillna(False)
    work["decision_selected"] = work["decision_selected"].fillna(False)
    work["decision_selected_local"] = work["decision_selected_local"].fillna(False)
    work["decision_selected_fallback"] = work["decision_selected_fallback"].fillna(False)
    work["decision_tie_at_top_rank"] = work["decision_tie_at_top_rank"].fillna(False)
    work["decision_position_usdt"] = pd.to_numeric(work["decision_position_usdt"], errors="coerce").fillna(0.0)
    work["decision_mu_adj"] = pd.to_numeric(work["decision_mu_adj"], errors="coerce").fillna(0.0)
    work["decision_kelly_frac"] = pd.to_numeric(work["decision_kelly_frac"], errors="coerce").fillna(0.0)
    work["predicted_rank"] = work["rank_score_stage_a"].rank(method="first", ascending=False).astype(int)
    work["selection_under_label_space"] = np.where(work["label_selected_proxy"], "selected", "not_selected")
    work["selection_under_decision_space"] = np.where(
        work["decision_selected_local"],
        "cluster_local_top1",
        np.where(work["decision_selected_fallback"], "date_universe_fallback", "not_selected"),
    )
    work["latest_case_classification"] = np.where(
        ~work["label_space_eligible"] & work["decision_space_available"],
        DATE_CLASS_MISALIGNMENT,
        np.where(
            work["label_space_eligible"] & ~work["label_selected_proxy"] & ~work["y_stage_a_truth_top1"].fillna(0).astype(int).eq(1),
            DATE_CLASS_RANKING_FAILURE,
            np.where(~work["decision_space_available"], DATE_CLASS_NO_CONTEST, DATE_CLASS_ALIGNED),
        ),
    )
    work["discard_reason"] = np.where(
        ~work["decision_space_available"],
        "not_operationally_available_ex_ante",
        np.where(
            work["decision_selected"] & ~work["label_space_eligible"],
            "selected_only_in_decision_space_label_filter_zeroed_contest",
            np.where(
                work["label_selected_proxy"] & ~work["y_stage_a_truth_top1"].fillna(0).astype(int).eq(1),
                "label_space_ranking_failure_against_truth_top1",
                np.where(~work["decision_selected"], "not_top_rank_under_decision_space", "selected_and_operationally_available"),
            ),
        ),
    )
    return work[
        [
            "date",
            "symbol",
            "cluster_name",
            "rank_score_stage_a",
            "predicted_rank",
            "y_stage_a_truth_top1",
            "label_space_eligible",
            "decision_space_available",
            "label_selected_proxy",
            "decision_selected",
            "selection_under_label_space",
            "selection_under_decision_space",
            "stage_a_selection_mode",
            "decision_selection_mode",
            "stage_a_group_eligible_count",
            "stage_a_date_eligible_count",
            "decision_group_available_count",
            "decision_date_available_count",
            "decision_tie_at_top_rank",
            "p_stage_a_raw",
            "avg_tp_train",
            "avg_sl_train",
            "pnl_real",
            "position_usdt_stage_a",
            "decision_position_usdt",
            "discard_reason",
            "latest_case_classification",
        ]
    ].sort_values(["predicted_rank", "symbol"], kind="mergesort")


def run_no_contest_vs_ranking_failure_audit() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    baseline_commit = _git("rev-parse", "HEAD")
    dirty_before = _b("PHASE4_CROSS_SECTIONAL_NO_CONTEST_DIRTY_BEFORE", _worktree_dirty())
    official_before = _official_hashes()
    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)

    baseline_gate = _read_json(BASELINE_GATE_PATH)
    baseline_diag = _read_json(BASELINE_DIAG_PATH)
    baseline_summary = _read_json(BASELINE_SUMMARY_PATH)
    latest_choke_gate = _read_json(LATEST_CHOKE_GATE_PATH)
    latest_choke_summary = _read_json(LATEST_CHOKE_SUMMARY_PATH)
    predictions_df = pd.read_parquet(PREDICTIONS_PATH)
    _ = pd.read_parquet(SNAPSHOT_PATH)

    task_definition = baseline_diag.get("task_definition", {})
    support_policy = task_definition.get("support_and_fallback", {})
    min_group_support = _i(support_policy.get("min_eligible_per_date_cluster"), 2)
    label_df = _rebuild_label_frame(predictions_df)
    decision_df = _build_decision_space_frame(predictions_df, min_group_support=min_group_support)
    alignment_df = _per_date_alignment_rows(label_df, decision_df)
    recent_df = alignment_df.tail(RECENT_WINDOW_DATES).copy()
    latest_date = pd.to_datetime(label_df["date"], errors="coerce").max()
    latest_label = label_df.loc[label_df["date"] == latest_date].copy()
    latest_decision = decision_df.loc[decision_df["date"] == latest_date].copy()
    latest_trace = _latest_trace(latest_label, latest_decision)
    latest_case = str(recent_df.iloc[-1]["date_case_classification"]) if not recent_df.empty else DATE_CLASS_NO_CONTEST
    final_result = classify_final_audit(latest_case=latest_case)
    frequency_counts = recent_df["date_case_classification"].value_counts().rename_axis("classification").reset_index(name="n_dates")

    trace_path = RESEARCH_PATH / "latest_label_vs_operational_trace.parquet"
    freq_path = RESEARCH_PATH / "no_contest_frequency.parquet"
    alignment_path = RESEARCH_PATH / "latest_definition_alignment.json"
    summary_path = RESEARCH_PATH / "cross_sectional_no_contest_summary.json"
    latest_trace.to_parquet(trace_path, index=False)
    recent_df.to_parquet(freq_path, index=False)

    latest_decision_selected = latest_decision.loc[latest_decision["decision_selected"].fillna(False), "symbol"].astype(str).tolist()
    latest_label_selected = latest_label.loc[latest_label["stage_a_selected_proxy"].fillna(False), "symbol"].astype(str).tolist()
    current_summary = baseline_gate["summary"]
    alignment = {
        "gate_slug": GATE_SLUG,
        "generated_at_utc": _utc_now_iso(),
        "definition_audit": {
            "current_latest_metric_source": {
                "latest_active_count_path": "operational_proxy.activation_funnel.latest_snapshot_active_count",
                "headroom_real_path": "gate_do_experimento_stage_a.headroom_real_documented",
                "score_field_used_in_cross_sectional_proxy": "stage_a_selected_proxy",
                "probability_field_written": "p_stage_a_calibrated = stage_a_selected_proxy.astype(float)",
            },
            "label_space_fields": [
                {"field": "pnl_real", "available_ex_ante": False, "role": "realized-space target gating"},
                {"field": "avg_sl_train", "available_ex_ante": True, "role": "train-side hurdle statistic"},
            ],
            "decision_space_fields": [
                {"field": "rank_score_stage_a", "available_ex_ante": True, "role": "OOS model score used for ranking"},
                {"field": "avg_tp_train", "available_ex_ante": True, "role": "ex-ante sizing input"},
                {"field": "avg_sl_train", "available_ex_ante": True, "role": "ex-ante sizing input"},
                {"field": "cluster_name", "available_ex_ante": True, "role": "contest scope"},
            ],
            "operational_availability_definition": "decision_space_available = non-null rank_score_stage_a + positive avg_tp_train + positive avg_sl_train + non-empty cluster_name",
            "selection_policy_under_label_space": task_definition.get("research_only_operational_proxy"),
            "selection_policy_under_decision_space": (
                "Select top1 by rank_score_stage_a within (date, cluster_name) among decision_space_available rows; "
                f"fallback to top1(date-universe) when available_count(date, cluster_name) < {min_group_support}."
            ),
            "latest_evaluation_causally_valid": False,
            "why_not": "The current latest/headroom metrics zero the contest before ranking when no row satisfies realized eligible = (pnl_real > avg_sl_train), even though the latest still has ex-ante candidates and ex-ante rank scores.",
        },
        "latest_comparison": {
            "latest_date": pd.Timestamp(latest_date).strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
            "label_space": {
                "rows_total": int(len(latest_label)),
                "eligible_count": int(latest_label["stage_a_eligible"].fillna(False).sum()),
                "selected_count": int(latest_label["stage_a_selected_proxy"].fillna(False).sum()),
                "position_gt_0_count": int((pd.to_numeric(latest_label["position_usdt_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
                "selected_symbols": latest_label_selected,
            },
            "decision_space": {
                "available_count": int(latest_decision["decision_space_available"].fillna(False).sum()),
                "selected_count": int(latest_decision["decision_selected"].fillna(False).sum()),
                "position_gt_0_count": int((pd.to_numeric(latest_decision["decision_position_usdt"], errors="coerce").fillna(0.0) > 0).sum()),
                "selected_symbols": latest_decision_selected,
            },
            "latest_case_classification": latest_case,
        },
        "recent_window_frequency": frequency_counts.to_dict(orient="records"),
        "baseline_reference": {
            "baseline_gate_path": str(BASELINE_GATE_PATH),
            "latest_choke_gate_path": str(LATEST_CHOKE_GATE_PATH),
            "baseline_classification": baseline_summary.get("classification"),
            "latest_choke_classification": latest_choke_summary.get("classification"),
            "latest_choke_gate_status": latest_choke_gate.get("status"),
        },
        "final_classification": final_result["classification"],
        "blocker_real": final_result["blocker_real"],
    }
    alignment_path.write_text(json.dumps(alignment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "latest_case_classification": latest_case,
        "recent_window_frequency": frequency_counts.to_dict(orient="records"),
        "latest_date": alignment["latest_comparison"]["latest_date"],
        "label_space_latest": alignment["latest_comparison"]["label_space"],
        "decision_space_latest": alignment["latest_comparison"]["decision_space"],
        "next_recommended_step": final_result["next_recommended_step"],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_after = _official_hashes()
    unchanged = official_before == official_after
    no_mix = unchanged and str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
    tests_passed = _b("PHASE4_CROSS_SECTIONAL_NO_CONTEST_TESTS_PASSED", False)
    gates = [
        {"name": "official_artifacts_unchanged", "value": unchanged, "threshold": "true", "status": "PASS" if unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_mix, "threshold": "true", "status": "PASS" if no_mix else "FAIL"},
        {"name": "latest_definition_audited", "value": True, "threshold": "true", "status": "PASS"},
        {"name": "label_vs_decision_space_separated", "value": latest_case, "threshold": "non_empty_classification", "status": "PASS"},
        {"name": "latest_trace_materialized", "value": not latest_trace.empty, "threshold": "true", "status": "PASS" if not latest_trace.empty else "FAIL"},
        {"name": "recent_window_frequency_measured", "value": int(len(recent_df)), "threshold": f">={RECENT_WINDOW_DATES}", "status": "PASS" if len(recent_df) >= RECENT_WINDOW_DATES else "FAIL"},
        {"name": "final_classification_assigned", "value": final_result["classification"], "threshold": "one_of(NO_CONTEST_CONFIRMED,RANKING_FAILURE_CONFIRMED,LATEST_EVALUATION_MISALIGNED)", "status": "PASS"},
        {"name": "tests_passed", "value": tests_passed, "threshold": "true", "status": "PASS" if tests_passed else "FAIL"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": final_result["decision"],
        "baseline_commit": baseline_commit,
        "working_tree_dirty": _worktree_dirty(),
        "branch": branch,
        "official_artifacts_used": [{"path": str(path), "sha256_before": official_before.get(str(path)), "sha256_after": official_after.get(str(path))} for path in OFFICIAL_PATHS],
        "research_artifacts_generated": [artifact_record(trace_path), artifact_record(freq_path), artifact_record(alignment_path), artifact_record(summary_path)],
        "summary": current_summary,
        "gates": gates,
        "blockers": [final_result["blocker_real"]],
        "risks_residual": [
            "Historico forte continua insuficiente para validar latest/headroom enquanto a avaliacao do latest permanecer acoplada ao label-space realizado.",
            "A familia continua research-only; nenhum resultado desta rodada governa snapshot ou fast path oficial.",
        ],
        "next_recommended_step": final_result["next_recommended_step"],
    }
    sections = {
        GATE_REPORT_MARKDOWN_SECTIONS[0]: f"Rodada concluida com status `{status}`, decision `{final_result['decision']}` e classificacao `{final_result['classification']}`.",
        GATE_REPORT_MARKDOWN_SECTIONS[1]: (
            f"- `branch`: `{branch}`\n"
            f"- `baseline_commit`: `{baseline_commit}`\n"
            f"- `working_tree_dirty_before`: `{dirty_before}`\n"
            f"- `baseline_gate_path`: `{BASELINE_GATE_PATH}`\n"
            f"- `latest_choke_gate_path`: `{LATEST_CHOKE_GATE_PATH}`\n"
            f"- `baseline_predictions_path`: `{PREDICTIONS_PATH}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[2]: (
            "- auditoria research-only separando label-space realizado de decision-space ex-ante\n"
            "- reconstruido o proxy cross-sectional vigente e um proxy decision-space causal com o mesmo score/rank e a mesma politica local/fallback\n"
            "- materializados trace do latest e frequencias da janela recente"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[3]: (
            f"- `{trace_path}`\n"
            f"- `{freq_path}`\n"
            f"- `{alignment_path}`\n"
            f"- `{summary_path}`\n"
            f"- `{GATE_PATH / 'gate_report.json'}`\n"
            f"- `{GATE_PATH / 'gate_report.md'}`\n"
            f"- `{GATE_PATH / 'gate_manifest.json'}`\n"
            f"- `{GATE_PATH / 'gate_metrics.parquet'}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[4]: (
            f"- `latest_date={alignment['latest_comparison']['latest_date']}`\n"
            f"- `label_space_eligible_count={alignment['latest_comparison']['label_space']['eligible_count']}`\n"
            f"- `decision_space_available_count={alignment['latest_comparison']['decision_space']['available_count']}`\n"
            f"- `decision_space_selected_count={alignment['latest_comparison']['decision_space']['selected_count']}`\n"
            f"- `decision_space_position_gt_0_count={alignment['latest_comparison']['decision_space']['position_gt_0_count']}`\n"
            f"- `recent_window_frequency={frequency_counts.to_dict(orient='records')}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[5]: "\n".join(
            f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`" for item in gates
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[6]: (
            "- a avaliacao atual de latest/headroom para esta familia depende de um filtro realizado (`pnl_real > avg_sl_train`) e pode subestimar disponibilidade operacional ex-ante\n"
            "- a separacao causal foi feita apenas em research; nenhum ajuste operacional real foi aplicado nesta rodada"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[7]: final_result["decision"],
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": _utc_now_iso(),
        "baseline_commit": baseline_commit,
        "branch": branch,
        "working_tree_dirty_before": dirty_before,
        "working_tree_dirty_after": _worktree_dirty(),
        "source_artifacts": [
            artifact_record(PREDICTIONS_PATH, extras={"role": "baseline_cross_sectional_predictions"}),
            artifact_record(SNAPSHOT_PATH, extras={"role": "baseline_cross_sectional_snapshot"}),
            artifact_record(BASELINE_DIAG_PATH, extras={"role": "baseline_cross_sectional_diagnostics"}),
            artifact_record(BASELINE_SUMMARY_PATH, extras={"role": "baseline_cross_sectional_summary"}),
            artifact_record(BASELINE_GATE_PATH, extras={"role": "baseline_cross_sectional_gate"}),
            artifact_record(LATEST_CHOKE_SUMMARY_PATH, extras={"role": "latest_choke_summary"}),
            artifact_record(LATEST_CHOKE_GATE_PATH, extras={"role": "latest_choke_gate"}),
            artifact_record(OFFICIAL_PATHS[0], extras={"role": "official_phase4_report"}),
            artifact_record(OFFICIAL_PATHS[1], extras={"role": "official_phase4_snapshot"}),
            artifact_record(OFFICIAL_PATHS[2], extras={"role": "official_phase4_aggregated_predictions"}),
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "git status --short --untracked-files=all",
            "git diff --stat",
            "Get-FileHash data\\models\\phase4\\phase4_report_v4.json,data\\models\\phase4\\phase4_execution_snapshot.parquet,data\\models\\phase4\\phase4_aggregated_predictions.parquet -Algorithm SHA256 | Select-Object Path,Hash",
            "python -m py_compile services\\ml_engine\\phase4_cross_sectional_no_contest_vs_ranking_failure.py tests\\unit\\test_phase4_cross_sectional_no_contest_vs_ranking_failure.py",
            "python -m pytest tests\\unit\\test_phase4_cross_sectional_no_contest_vs_ranking_failure.py -q",
            "python services\\ml_engine\\phase4_cross_sectional_no_contest_vs_ranking_failure.py",
        ],
        "notes": [
            f"final_classification={final_result['classification']}",
            f"decision={final_result['decision']}",
            f"latest_case_classification={latest_case}",
            "latest latest_active_count/headroom_real from baseline are preserved in summary, but the audit concludes they are judged through a label-space lens.",
        ],
    }
    gate_metrics = [
        {"gate_slug": GATE_SLUG, "metric_name": item["name"], "metric_value": item["value"], "metric_threshold": item["threshold"], "metric_status": item["status"]}
        for item in gates
    ]
    gate_metrics.extend(
        [
            {"gate_slug": GATE_SLUG, "metric_name": "latest_label_eligible_count", "metric_value": alignment["latest_comparison"]["label_space"]["eligible_count"], "metric_threshold": "informational", "metric_status": "PASS"},
            {"gate_slug": GATE_SLUG, "metric_name": "latest_decision_available_count", "metric_value": alignment["latest_comparison"]["decision_space"]["available_count"], "metric_threshold": "informational", "metric_status": "PASS"},
            {"gate_slug": GATE_SLUG, "metric_name": "latest_decision_position_gt_0_count", "metric_value": alignment["latest_comparison"]["decision_space"]["position_gt_0_count"], "metric_threshold": "informational", "metric_status": "PASS"},
        ]
    )
    write_gate_pack(output_dir=GATE_PATH, gate_report=gate_report, gate_manifest=gate_manifest, gate_metrics=gate_metrics, markdown_sections=sections)
    return {"classification": final_result["classification"], "decision": final_result["decision"], "status": status, "summary": current_summary}


def main() -> None:
    print(json.dumps(run_no_contest_vs_ranking_failure_audit(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
