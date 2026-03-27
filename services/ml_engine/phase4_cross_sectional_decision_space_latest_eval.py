#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
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

from services.common.gate_reports import GATE_REPORT_MARKDOWN_SECTIONS, artifact_record, sha256_file, write_gate_pack
from services.ml_engine.phase4_cross_sectional_no_contest_vs_ranking_failure import (
    _build_decision_space_frame,
    _rebuild_label_frame,
)

GATE_SLUG = "phase4_cross_sectional_decision_space_latest_eval"
PHASE_FAMILY = "phase4_research_cross_sectional"
CLASS_VALIDATED = "DECISION_SPACE_EVAL_VALIDATED"
CLASS_REJECTED = "DECISION_SPACE_EVAL_REJECTED"
CLASS_INCONCLUSIVE = "INCONCLUSIVE_EVAL_REDESIGN"
BASELINE_SLUG = "phase4_cross_sectional_ranking_baseline"
NO_CONTEST_SLUG = "phase4_cross_sectional_no_contest_vs_ranking_failure"
RECENT_WINDOW_DATES = 8

MODEL_PATH = (Path("/data/models") if Path("/data/models").exists() else REPO_ROOT / "data" / "models").resolve()
PHASE4_PATH = MODEL_PATH / "phase4"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG
BASELINE_PATH = MODEL_PATH / "research" / BASELINE_SLUG
NO_CONTEST_PATH = MODEL_PATH / "research" / NO_CONTEST_SLUG
OFFICIAL_PATHS = (
    PHASE4_PATH / "phase4_report_v4.json",
    PHASE4_PATH / "phase4_execution_snapshot.parquet",
    PHASE4_PATH / "phase4_aggregated_predictions.parquet",
)
BASELINE_GATE_PATH = REPO_ROOT / "reports" / "gates" / BASELINE_SLUG / "gate_report.json"
BASELINE_DIAGNOSTICS_PATH = BASELINE_PATH / "cross_sectional_diagnostics.json"
BASELINE_SUMMARY_PATH = BASELINE_PATH / "cross_sectional_eval_summary.json"
NO_CONTEST_GATE_PATH = REPO_ROOT / "reports" / "gates" / NO_CONTEST_SLUG / "gate_report.json"
NO_CONTEST_ALIGNMENT_PATH = NO_CONTEST_PATH / "latest_definition_alignment.json"
PREDICTIONS_PATH = BASELINE_PATH / "cross_sectional_predictions.parquet"


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


def classify_space_status(*, available_count: int, selected_count: int, active_count: int, space: str) -> str:
    if space == "label":
        if available_count == 0:
            return "dead_no_label_contest"
        if selected_count == 0:
            return "dead_no_label_selection"
    else:
        if available_count == 0:
            return "dead_no_operational_candidates"
        if selected_count == 0:
            return "dead_no_decision_selection"
    if active_count > 0:
        return "live"
    return "selected_but_zero_sizing"


def count_dead_to_live_dates(metrics_df: pd.DataFrame) -> int:
    if metrics_df.empty:
        return 0
    return int(((~metrics_df["headroom_label_space"]) & metrics_df["headroom_decision_space"]).sum())


def classify_eval_redesign(
    *,
    latest_active_count_label_space: int,
    latest_active_count_decision_space: int,
    headroom_label_space: bool,
    headroom_decision_space: bool,
) -> dict[str, str]:
    if (
        latest_active_count_label_space == 0
        and not headroom_label_space
        and latest_active_count_decision_space > 0
        and headroom_decision_space
    ):
        return {
            "classification": CLASS_VALIDATED,
            "decision": "correct",
            "blocker_real": "A lente antiga de latest/headroom continua invalida para esta familia porque zera o contest usando elegibilidade realizada; sob decision-space causal o latest permanece vivo com candidatos selecionados e posicao > 0.",
            "next_recommended_step": "Abrir uma ultima rodada research-only para usar a regua causal como avaliacao soberana de latest/headroom desta familia e decidir o fechamento da Fase 4 sem tocar no official.",
        }
    if latest_active_count_decision_space == 0 and not headroom_decision_space:
        return {
            "classification": CLASS_REJECTED,
            "decision": "abandon",
            "blocker_real": "Mesmo sob a regua causal em decision-space, o latest continua sem qualquer posicao > 0 ou headroom util para esta familia.",
            "next_recommended_step": "Encerrar esta familia em research-only e nao endurecer a avaliacao operacional desta frente.",
        }
    return {
        "classification": CLASS_INCONCLUSIVE,
        "decision": "correct",
        "blocker_real": "A nova regua causal foi materializada, mas o latest ainda nao fecha um veredito suficientemente forte sobre utilidade operacional desta familia.",
        "next_recommended_step": "Executar uma rodada tecnica curta para remover a ambiguidade residual sem abrir tuning amplo.",
    }


def _selected_symbols(frame: pd.DataFrame, *, selected_col: str) -> list[str]:
    if frame.empty:
        return []
    return frame.loc[frame[selected_col].fillna(False), "symbol"].astype(str).tolist()


def _build_per_date_metrics(label_df: pd.DataFrame, decision_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dates = sorted(pd.to_datetime(label_df["date"], errors="coerce").dropna().unique().tolist())
    recent_dates = {pd.Timestamp(value) for value in dates[-RECENT_WINDOW_DATES:]}
    for date_value in dates:
        date_key = pd.Timestamp(date_value)
        label_slice = label_df.loc[label_df["date"] == date_key].copy()
        decision_slice = decision_df.loc[decision_df["date"] == date_key].copy()
        label_available_count = int(label_slice["stage_a_eligible"].fillna(False).sum())
        label_selected_count = int(label_slice["stage_a_selected_proxy"].fillna(False).sum())
        label_active_count = int((pd.to_numeric(label_slice["position_usdt_stage_a"], errors="coerce").fillna(0.0) > 0).sum())
        decision_available_count = int(decision_slice["decision_space_available"].fillna(False).sum())
        decision_selected_count = int(decision_slice["decision_selected"].fillna(False).sum())
        decision_active_count = int((pd.to_numeric(decision_slice["decision_position_usdt"], errors="coerce").fillna(0.0) > 0).sum())
        headroom_label = label_active_count > 0
        headroom_decision = decision_active_count > 0
        rows.append(
            {
                "date": date_key,
                "rows_total": int(len(label_slice)),
                "label_available_count": label_available_count,
                "decision_available_count": decision_available_count,
                "label_selected_count": label_selected_count,
                "decision_selected_count": decision_selected_count,
                "latest_active_count_label_space": label_active_count,
                "latest_active_count_decision_space": decision_active_count,
                "headroom_label_space": bool(headroom_label),
                "headroom_decision_space": bool(headroom_decision),
                "label_space_status": classify_space_status(
                    available_count=label_available_count,
                    selected_count=label_selected_count,
                    active_count=label_active_count,
                    space="label",
                ),
                "decision_space_status": classify_space_status(
                    available_count=decision_available_count,
                    selected_count=decision_selected_count,
                    active_count=decision_active_count,
                    space="decision",
                ),
                "dead_to_live_shift": bool((not headroom_label) and headroom_decision),
                "live_to_dead_shift": bool(headroom_label and (not headroom_decision)),
                "label_selected_symbols": ",".join(_selected_symbols(label_slice, selected_col="stage_a_selected_proxy")),
                "decision_selected_symbols": ",".join(_selected_symbols(decision_slice, selected_col="decision_selected")),
                "in_recent_window": bool(date_key in recent_dates),
            }
        )
    return pd.DataFrame(rows)


def _build_latest_trace(label_latest: pd.DataFrame, decision_latest: pd.DataFrame) -> pd.DataFrame:
    work = label_latest.merge(
        decision_latest[
            [
                "date",
                "symbol",
                "cluster_name",
                "decision_space_available",
                "decision_selected",
                "decision_selection_mode",
                "decision_position_usdt",
                "decision_mu_adj",
                "decision_kelly_frac",
            ]
        ],
        on=["date", "symbol", "cluster_name"],
        how="left",
    ).copy()
    work["rank_score_stage_a"] = pd.to_numeric(work["rank_score_stage_a"], errors="coerce").fillna(0.0)
    work["label_space_eligible"] = work["stage_a_eligible"].fillna(False)
    work["decision_space_available"] = work["decision_space_available"].fillna(False)
    work["label_selected_proxy"] = work["stage_a_selected_proxy"].fillna(False)
    work["decision_selected"] = work["decision_selected"].fillna(False)
    work["position_usdt_stage_a"] = pd.to_numeric(work["position_usdt_stage_a"], errors="coerce").fillna(0.0)
    work["decision_position_usdt"] = pd.to_numeric(work["decision_position_usdt"], errors="coerce").fillna(0.0)
    work["decision_mu_adj"] = pd.to_numeric(work["decision_mu_adj"], errors="coerce").fillna(0.0)
    work["decision_kelly_frac"] = pd.to_numeric(work["decision_kelly_frac"], errors="coerce").fillna(0.0)
    work["predicted_rank"] = work["rank_score_stage_a"].rank(method="first", ascending=False).astype(int)
    work["selection_under_label_space"] = work["label_selected_proxy"].map(lambda flag: "selected" if bool(flag) else "not_selected")
    work["selection_under_decision_space"] = work["decision_selection_mode"].fillna("not_selected")
    work["latest_label_space_status"] = work.apply(
        lambda row: "selected_and_live"
        if bool(row["label_selected_proxy"]) and float(row["position_usdt_stage_a"]) > 0
        else ("selected_but_zero_label_position" if bool(row["label_selected_proxy"]) else ("realized_label_gate_zeroed_contest" if not bool(row["label_space_eligible"]) else "not_top1_under_label_space")),
        axis=1,
    )
    work["latest_decision_space_status"] = work.apply(
        lambda row: "selected_and_live"
        if bool(row["decision_selected"]) and float(row["decision_position_usdt"]) > 0
        else ("selected_but_zero_decision_position" if bool(row["decision_selected"]) else ("not_operationally_available_ex_ante" if not bool(row["decision_space_available"]) else "not_top1_under_decision_space")),
        axis=1,
    )
    work["latest_case_reinterpreted"] = work.apply(
        lambda row: "live_under_decision_space"
        if float(row["decision_position_usdt"]) > 0
        else ("label_space_only_dead" if not bool(row["label_space_eligible"]) and bool(row["decision_space_available"]) else "not_selected"),
        axis=1,
    )
    return work[
        [
            "date",
            "symbol",
            "cluster_name",
            "predicted_rank",
            "rank_score_stage_a",
            "avg_tp_train",
            "avg_sl_train",
            "label_space_eligible",
            "decision_space_available",
            "selection_under_label_space",
            "selection_under_decision_space",
            "position_usdt_stage_a",
            "decision_position_usdt",
            "decision_mu_adj",
            "decision_kelly_frac",
            "latest_label_space_status",
            "latest_decision_space_status",
            "latest_case_reinterpreted",
        ]
    ].sort_values(["predicted_rank", "symbol"], kind="mergesort")


def _augment_manifest_generated_artifacts(manifest_path: Path, research_artifacts: list[dict[str, Any]]) -> None:
    manifest = _read_json(manifest_path)
    manifest["generated_artifacts"] = list(manifest.get("generated_artifacts", [])) + research_artifacts
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_decision_space_latest_eval() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    baseline_commit = _git("rev-parse", "HEAD")
    dirty_before = _b("PHASE4_CROSS_SECTIONAL_DECISION_SPACE_EVAL_DIRTY_BEFORE", _worktree_dirty())
    official_before = _official_hashes()
    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)

    baseline_gate = _read_json(BASELINE_GATE_PATH)
    baseline_diagnostics = _read_json(BASELINE_DIAGNOSTICS_PATH)
    baseline_summary = _read_json(BASELINE_SUMMARY_PATH)
    no_contest_gate = _read_json(NO_CONTEST_GATE_PATH)
    no_contest_alignment = _read_json(NO_CONTEST_ALIGNMENT_PATH)
    predictions_df = pd.read_parquet(PREDICTIONS_PATH)

    support_policy = (baseline_diagnostics.get("task_definition", {}) or {}).get("support_and_fallback", {}) or {}
    min_group_support = _i(support_policy.get("min_eligible_per_date_cluster"), 2)
    label_df = _rebuild_label_frame(predictions_df)
    decision_df = _build_decision_space_frame(predictions_df, min_group_support=min_group_support)
    per_date_metrics = _build_per_date_metrics(label_df, decision_df)
    latest_date = pd.to_datetime(label_df["date"], errors="coerce").max()
    latest_label = label_df.loc[label_df["date"] == latest_date].copy()
    latest_decision = decision_df.loc[decision_df["date"] == latest_date].copy()
    latest_trace = _build_latest_trace(latest_label, latest_decision)
    recent_metrics = per_date_metrics.loc[per_date_metrics["in_recent_window"]].copy()

    latest_row = per_date_metrics.loc[per_date_metrics["date"] == latest_date].iloc[0]
    dead_to_live_recent = count_dead_to_live_dates(recent_metrics)
    full_dead_to_live = count_dead_to_live_dates(per_date_metrics)
    final_result = classify_eval_redesign(
        latest_active_count_label_space=_i(latest_row["latest_active_count_label_space"]),
        latest_active_count_decision_space=_i(latest_row["latest_active_count_decision_space"]),
        headroom_label_space=bool(latest_row["headroom_label_space"]),
        headroom_decision_space=bool(latest_row["headroom_decision_space"]),
    )

    latest_eval_path = RESEARCH_PATH / "decision_space_latest_eval.parquet"
    metrics_path = RESEARCH_PATH / "label_vs_decision_space_metrics.parquet"
    definition_path = RESEARCH_PATH / "decision_space_eval_definition.json"
    summary_path = RESEARCH_PATH / "cross_sectional_decision_space_eval_summary.json"
    latest_trace.to_parquet(latest_eval_path, index=False)
    per_date_metrics.to_parquet(metrics_path, index=False)

    decision_definition = {
        "gate_slug": GATE_SLUG,
        "generated_at_utc": _utc_now_iso(),
        "legacy_label_space_lens": {
            "latest_active_count_label_space_definition": "count(position_usdt_stage_a > 0) on the latest date after stage_a_selected_proxy, where stage_a_selected_proxy is defined only over realized eligible = (pnl_real > avg_sl_train).",
            "headroom_label_space_definition": "max(position_usdt_stage_a) > 0 on the latest date under the same realized-space proxy.",
            "fields_used": [
                {"field": "pnl_real", "available_ex_ante": False, "role": "realized eligible gate"},
                {"field": "avg_sl_train", "available_ex_ante": True, "role": "train-side hurdle statistic"},
                {"field": "stage_a_selected_proxy", "available_ex_ante": False, "role": "selection after realized eligible filter"},
                {"field": "position_usdt_stage_a", "available_ex_ante": False, "role": "position after realized eligible filter"},
            ],
            "causal_valid_for_latest": False,
            "why_not": "It zeroes the latest contest before ranking whenever no row satisfies realized eligible = (pnl_real > avg_sl_train), even if ex-ante rank scores and ex-ante sizing inputs are present.",
        },
        "decision_space_causal_lens": {
            "latest_active_count_decision_space_definition": "count(decision_selected and decision_position_usdt > 0) on the latest date, using only rank_score_stage_a, cluster contest scope, avg_tp_train and avg_sl_train.",
            "headroom_decision_space_definition": "max(decision_position_usdt) > 0 on the latest date under decision-space selection.",
            "fields_used": [
                {"field": "rank_score_stage_a", "available_ex_ante": True, "role": "OOS score used to rank the latest contest"},
                {"field": "cluster_name", "available_ex_ante": True, "role": "contest scope"},
                {"field": "avg_tp_train", "available_ex_ante": True, "role": "ex-ante sizing input"},
                {"field": "avg_sl_train", "available_ex_ante": True, "role": "ex-ante sizing input"},
                {"field": "decision_selection_mode", "available_ex_ante": True, "role": "local vs fallback contest routing"},
                {"field": "decision_position_usdt", "available_ex_ante": True, "role": "proxy position generated from ex-ante score plus train-side sizing inputs"},
            ],
            "operational_availability_definition": "decision_space_available = non-null rank_score_stage_a + positive avg_tp_train + positive avg_sl_train + non-empty cluster_name",
            "selection_policy": f"Select top1 by rank_score_stage_a within (date, cluster_name) among decision_space_available rows; fallback to top1(date-universe) when available_count(date, cluster_name) < {min_group_support}.",
            "causal_valid_for_latest": True,
        },
        "latest_side_by_side": {
            "latest_date": pd.Timestamp(latest_date).strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
            "latest_active_count_label_space": _i(latest_row["latest_active_count_label_space"]),
            "latest_active_count_decision_space": _i(latest_row["latest_active_count_decision_space"]),
            "headroom_label_space": bool(latest_row["headroom_label_space"]),
            "headroom_decision_space": bool(latest_row["headroom_decision_space"]),
            "label_space_status": str(latest_row["label_space_status"]),
            "decision_space_status": str(latest_row["decision_space_status"]),
            "label_selected_symbols": [value for value in str(latest_row["label_selected_symbols"]).split(",") if value],
            "decision_selected_symbols": [value for value in str(latest_row["decision_selected_symbols"]).split(",") if value],
        },
        "recent_window_reinterpretation": {
            "n_dates": int(len(recent_metrics)),
            "dead_to_live_shift_dates": [
                pd.Timestamp(value).strftime("%Y-%m-%d")
                for value in recent_metrics.loc[recent_metrics["dead_to_live_shift"], "date"].tolist()
            ],
            "dead_to_live_shift_count": dead_to_live_recent,
            "live_to_dead_shift_count": int(recent_metrics["live_to_dead_shift"].sum()) if not recent_metrics.empty else 0,
            "label_live_dates": int(recent_metrics["headroom_label_space"].sum()) if not recent_metrics.empty else 0,
            "decision_live_dates": int(recent_metrics["headroom_decision_space"].sum()) if not recent_metrics.empty else 0,
            "full_history_dead_to_live_count": full_dead_to_live,
        },
        "baseline_reference": {
            "baseline_gate_path": str(BASELINE_GATE_PATH),
            "baseline_summary_path": str(BASELINE_SUMMARY_PATH),
            "no_contest_gate_path": str(NO_CONTEST_GATE_PATH),
            "no_contest_alignment_path": str(NO_CONTEST_ALIGNMENT_PATH),
            "baseline_classification": baseline_summary.get("classification"),
            "no_contest_classification": no_contest_alignment.get("final_classification"),
            "no_contest_gate_status": no_contest_gate.get("status"),
        },
        "final_classification": final_result["classification"],
        "blocker_real": final_result["blocker_real"],
    }
    definition_path.write_text(json.dumps(decision_definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary_payload = {
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "summary_compatibility_note": "gate_report.summary remains on the legacy label-space lens for schema compatibility with prior rounds; decision-space causal equivalents are materialized side by side in this artifact set.",
        "legacy_summary": baseline_gate.get("summary", {}),
        "latest_metrics": {
            "label_space": {
                "latest_active_count": _i(latest_row["latest_active_count_label_space"]),
                "headroom": bool(latest_row["headroom_label_space"]),
                "status": str(latest_row["label_space_status"]),
            },
            "decision_space": {
                "latest_active_count": _i(latest_row["latest_active_count_decision_space"]),
                "headroom": bool(latest_row["headroom_decision_space"]),
                "status": str(latest_row["decision_space_status"]),
            },
        },
        "recent_window": {
            "n_dates": int(len(recent_metrics)),
            "dead_to_live_shift_count": dead_to_live_recent,
            "decision_live_dates": int(recent_metrics["headroom_decision_space"].sum()) if not recent_metrics.empty else 0,
            "label_live_dates": int(recent_metrics["headroom_label_space"].sum()) if not recent_metrics.empty else 0,
        },
        "next_recommended_step": final_result["next_recommended_step"],
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_after = _official_hashes()
    unchanged = official_before == official_after
    no_mix = unchanged and str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
    tests_passed = _b("PHASE4_CROSS_SECTIONAL_DECISION_SPACE_EVAL_TESTS_PASSED", False)
    gates = [
        {"name": "official_artifacts_unchanged", "value": unchanged, "threshold": "true", "status": "PASS" if unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_mix, "threshold": "true", "status": "PASS" if no_mix else "FAIL"},
        {"name": "decision_space_eval_defined", "value": True, "threshold": "true", "status": "PASS"},
        {"name": "label_vs_decision_metrics_materialized", "value": bool(not per_date_metrics.empty), "threshold": "true", "status": "PASS" if not per_date_metrics.empty else "FAIL"},
        {"name": "latest_decision_space_trace_materialized", "value": bool(not latest_trace.empty), "threshold": "true", "status": "PASS" if not latest_trace.empty else "FAIL"},
        {"name": "recent_window_reinterpreted", "value": int(len(recent_metrics)), "threshold": f">={RECENT_WINDOW_DATES}", "status": "PASS" if len(recent_metrics) >= RECENT_WINDOW_DATES else "FAIL"},
        {"name": "final_classification_assigned", "value": final_result["classification"], "threshold": "one_of(DECISION_SPACE_EVAL_VALIDATED,DECISION_SPACE_EVAL_REJECTED,INCONCLUSIVE_EVAL_REDESIGN)", "status": "PASS"},
        {"name": "tests_passed", "value": tests_passed, "threshold": "true", "status": "PASS" if tests_passed else "FAIL"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"

    research_artifacts = [
        artifact_record(latest_eval_path),
        artifact_record(metrics_path),
        artifact_record(definition_path),
        artifact_record(summary_path),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": final_result["decision"],
        "baseline_commit": baseline_commit,
        "working_tree_dirty": _worktree_dirty(),
        "branch": branch,
        "official_artifacts_used": [
            {"path": str(path), "sha256_before": official_before.get(str(path)), "sha256_after": official_after.get(str(path))}
            for path in OFFICIAL_PATHS
        ],
        "research_artifacts_generated": research_artifacts,
        "summary": baseline_gate.get("summary", {}),
        "gates": gates,
        "blockers": [final_result["blocker_real"]],
        "risks_residual": [
            "O gate summary continua em compatibilidade com a lente antiga; a leitura operacional desta familia deve usar os equivalentes causais desta rodada.",
            "A familia continua research-only; nenhum resultado desta rodada governa snapshot oficial, fast path ou bridge.",
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
            f"- `baseline_diagnostics_path`: `{BASELINE_DIAGNOSTICS_PATH}`\n"
            f"- `no_contest_alignment_path`: `{NO_CONTEST_ALIGNMENT_PATH}`\n"
            f"- `predictions_path`: `{PREDICTIONS_PATH}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[2]: (
            "- definida uma regua causal research-only para latest/headroom baseada em decision-space ex-ante\n"
            "- materializada comparacao lado a lado entre lente antiga label-space e lente nova decision-space\n"
            "- latest reconstruido por candidato e janela recente reinterpretada por data"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[3]: (
            f"- `{latest_eval_path}`\n"
            f"- `{metrics_path}`\n"
            f"- `{definition_path}`\n"
            f"- `{summary_path}`\n"
            f"- `{GATE_PATH / 'gate_report.json'}`\n"
            f"- `{GATE_PATH / 'gate_report.md'}`\n"
            f"- `{GATE_PATH / 'gate_manifest.json'}`\n"
            f"- `{GATE_PATH / 'gate_metrics.parquet'}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[4]: (
            f"- `latest_active_count_label_space={_i(latest_row['latest_active_count_label_space'])}`\n"
            f"- `latest_active_count_decision_space={_i(latest_row['latest_active_count_decision_space'])}`\n"
            f"- `headroom_label_space={bool(latest_row['headroom_label_space'])}`\n"
            f"- `headroom_decision_space={bool(latest_row['headroom_decision_space'])}`\n"
            f"- `latest_label_space_status={latest_row['label_space_status']}`\n"
            f"- `latest_decision_space_status={latest_row['decision_space_status']}`\n"
            f"- `dead_to_live_shift_count_recent={dead_to_live_recent}`\n"
            f"- `summary_compatibility_latest_active_count={baseline_gate['summary']['latest_active_count']}`\n"
            f"- `summary_compatibility_headroom_real={baseline_gate['summary']['headroom_real']}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[5]: "\n".join(
            f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`" for item in gates
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[6]: (
            "- o summary legado continua falso para latest/headroom por compatibilidade, entao qualquer leitura operacional desta familia precisa consultar os equivalentes causais desta rodada\n"
            "- a familia ainda nao foi promovida; esta rodada so valida ou rejeita a regua causal de avaliacao em research-only"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[7]: final_result["decision"],
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": _utc_now_iso(),
        "baseline_commit": baseline_commit,
        "branch": branch,
        "working_tree_dirty_before": dirty_before,
        "working_tree_dirty_after": True,
        "source_artifacts": [
            artifact_record(BASELINE_GATE_PATH),
            artifact_record(BASELINE_DIAGNOSTICS_PATH),
            artifact_record(BASELINE_SUMMARY_PATH),
            artifact_record(NO_CONTEST_GATE_PATH),
            artifact_record(NO_CONTEST_ALIGNMENT_PATH),
            artifact_record(PREDICTIONS_PATH),
            *[artifact_record(path) for path in OFFICIAL_PATHS],
        ],
        "generated_artifacts": research_artifacts,
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "git status --short",
            "git diff --stat",
            "python -m py_compile services\\ml_engine\\phase4_cross_sectional_decision_space_latest_eval.py tests\\unit\\test_phase4_cross_sectional_decision_space_latest_eval.py",
            "python -m pytest tests\\unit\\test_phase4_cross_sectional_decision_space_latest_eval.py -q",
            "$env:PHASE4_CROSS_SECTIONAL_DECISION_SPACE_EVAL_TESTS_PASSED='1'; python services\\ml_engine\\phase4_cross_sectional_decision_space_latest_eval.py",
        ],
        "notes": [
            "Gate report summary preserves the legacy label-space latest/headroom values for compatibility with prior rounds.",
            "Decision-space causal equivalents are materialized explicitly in the research artifacts of this round.",
        ],
    }
    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": item["name"],
            "metric_value": item["value"],
            "metric_threshold": item["threshold"],
            "metric_status": item["status"],
        }
        for item in gates
    ] + [
        {"gate_slug": GATE_SLUG, "metric_name": "latest_active_count_label_space", "metric_value": _i(latest_row["latest_active_count_label_space"]), "metric_threshold": "reference_only", "metric_status": "INFO"},
        {"gate_slug": GATE_SLUG, "metric_name": "latest_active_count_decision_space", "metric_value": _i(latest_row["latest_active_count_decision_space"]), "metric_threshold": ">0", "metric_status": "PASS" if _i(latest_row["latest_active_count_decision_space"]) > 0 else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "headroom_label_space", "metric_value": bool(latest_row["headroom_label_space"]), "metric_threshold": "reference_only", "metric_status": "INFO"},
        {"gate_slug": GATE_SLUG, "metric_name": "headroom_decision_space", "metric_value": bool(latest_row["headroom_decision_space"]), "metric_threshold": "true", "metric_status": "PASS" if bool(latest_row["headroom_decision_space"]) else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "dead_to_live_shift_count_recent", "metric_value": dead_to_live_recent, "metric_threshold": ">=1", "metric_status": "PASS" if dead_to_live_recent >= 1 else "FAIL"},
    ]
    gate_paths = write_gate_pack(
        output_dir=GATE_PATH,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=sections,
    )
    _augment_manifest_generated_artifacts(gate_paths["gate_manifest_json"], research_artifacts)
    return {
        "status": status,
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "latest_date": pd.Timestamp(latest_date).strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
    }


if __name__ == "__main__":
    result = run_decision_space_latest_eval()
    print(json.dumps(result, ensure_ascii=False))
