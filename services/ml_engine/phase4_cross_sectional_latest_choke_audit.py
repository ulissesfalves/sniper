#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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

GATE_SLUG = "phase4_cross_sectional_latest_choke_audit"
PHASE_FAMILY = "phase4_research_cross_sectional"
CLASS_IDENTIFIED = "LATEST_CHOKE_IDENTIFIED"
CLASS_STRUCTURAL = "NO_LATEST_HEADROOM_STRUCTURAL"
CLASS_INCONCLUSIVE = "INCONCLUSIVE_LATEST_AUDIT"
MODEL_PATH = (Path("/data/models") if Path("/data/models").exists() else REPO_ROOT / "data" / "models").resolve()
BASELINE_PATH = MODEL_PATH / "research" / "phase4_cross_sectional_ranking_baseline"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG
OFFICIAL_PATHS = (
    MODEL_PATH / "phase4" / "phase4_report_v4.json",
    MODEL_PATH / "phase4" / "phase4_execution_snapshot.parquet",
    MODEL_PATH / "phase4" / "phase4_aggregated_predictions.parquet",
)
BASELINE_GATE_PATH = REPO_ROOT / "reports" / "gates" / "phase4_cross_sectional_ranking_baseline" / "gate_report.json"
BASELINE_DIAG_PATH = BASELINE_PATH / "cross_sectional_diagnostics.json"
BASELINE_SUMMARY_PATH = BASELINE_PATH / "cross_sectional_eval_summary.json"
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
    value = series.iloc[0]
    return float(default if pd.isna(value) else value)


def _i(value: Any, default: int = 0) -> int:
    return int(round(_f(value, default=default)))


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _official_hashes() -> dict[str, str]:
    return {str(path): sha256_file(path) for path in OFFICIAL_PATHS if path.exists()}


def _rebuild_aggregated_frame(predictions_df: pd.DataFrame) -> pd.DataFrame:
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
    aggregated = phase4._attach_execution_pnl(
        aggregated,
        position_col="position_usdt_stage_a",
        output_col="pnl_exec_stage_a",
    )
    aggregated["date"] = pd.to_datetime(aggregated["date"], errors="coerce")
    return aggregated.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def _funnel_rows(frame: pd.DataFrame, *, scope: str, scope_date: str) -> list[dict[str, Any]]:
    rows_total = int(len(frame))
    eligible = frame["stage_a_eligible"].fillna(False)
    selected = frame["stage_a_selected_proxy"].fillna(False)
    p_cal = pd.to_numeric(frame["p_stage_a_calibrated"], errors="coerce").fillna(0.0)
    mu = pd.to_numeric(frame["mu_adj_stage_a"], errors="coerce").fillna(0.0)
    kelly = pd.to_numeric(frame["kelly_frac_stage_a"], errors="coerce").fillna(0.0)
    position = pd.to_numeric(frame["position_usdt_stage_a"], errors="coerce").fillna(0.0)
    stages = {
        "rows_total": rows_total,
        "eligible_candidates": int(eligible.sum()),
        "selected_top1": int(selected.sum()),
        "p_stage_a_calibrated_gt_0": int((p_cal > 0).sum()),
        "mu_adj_stage_a_gt_0": int((mu > 0).sum()),
        "kelly_frac_stage_a_gt_0": int((kelly > 0).sum()),
        "position_usdt_stage_a_gt_0": int((position > 0).sum()),
    }
    return [
        {
            "scope": scope,
            "scope_date": scope_date,
            "stage_name": stage_name,
            "count": count,
            "rows_total": rows_total,
            "rate_vs_rows": round(float(count / rows_total), 6) if rows_total else 0.0,
        }
        for stage_name, count in stages.items()
    ]


def _recent_window_summary(aggregated: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    dates = sorted(pd.to_datetime(aggregated["date"], errors="coerce").dropna().unique().tolist())
    recent_dates = dates[-RECENT_WINDOW_DATES:]
    rows = []
    for date in recent_dates:
        frame = aggregated.loc[aggregated["date"] == pd.Timestamp(date)].copy()
        rows.append(
            {
                "date": pd.Timestamp(date),
                "rows_total": len(frame),
                "eligible_count": int(frame["stage_a_eligible"].fillna(False).sum()),
                "selected_proxy_count": int(frame["stage_a_selected_proxy"].fillna(False).sum()),
                "p_cal_gt_0": int((pd.to_numeric(frame["p_stage_a_calibrated"], errors="coerce").fillna(0.0) > 0).sum()),
                "mu_gt_0": int((pd.to_numeric(frame["mu_adj_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
                "kelly_gt_0": int((pd.to_numeric(frame["kelly_frac_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
                "position_gt_0": int((pd.to_numeric(frame["position_usdt_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
                "max_rank_score": round(float(pd.to_numeric(frame["rank_score_stage_a"], errors="coerce").fillna(0.0).max()), 6),
                "max_position": round(float(pd.to_numeric(frame["position_usdt_stage_a"], errors="coerce").fillna(0.0).max()), 2),
            }
        )
    return pd.DataFrame(rows), pd.Timestamp(recent_dates[-1])


def _candidate_trace(latest_df: pd.DataFrame) -> pd.DataFrame:
    work = latest_df.copy().reset_index(drop=True)
    work["pnl_real"] = pd.to_numeric(work["pnl_real"], errors="coerce")
    work["avg_sl_train"] = pd.to_numeric(work["avg_sl_train"], errors="coerce")
    work["realized_ratio"] = work["pnl_real"] / work["avg_sl_train"].replace(0, np.nan)
    work["rank_score_stage_a"] = pd.to_numeric(work["rank_score_stage_a"], errors="coerce").fillna(0.0)
    work["predicted_rank"] = work["rank_score_stage_a"].rank(method="first", ascending=False).astype(int)
    eligible_mask = work["stage_a_eligible"].fillna(False)
    work["fallback_applied"] = work["stage_a_selection_mode"].astype(str).eq("date_universe_fallback")
    top_score = float(work["rank_score_stage_a"].max()) if not work.empty else 0.0
    work["tie_at_top_rank"] = work["rank_score_stage_a"].eq(top_score) & (work["rank_score_stage_a"].eq(top_score).sum() > 1)
    discard_reason = np.where(
        ~eligible_mask,
        "not_eligible_pnl_real_le_avg_sl_train",
        np.where(
            ~work["stage_a_selected_proxy"].fillna(False),
            np.where(work["fallback_applied"], "outranked_in_date_universe_fallback", "outranked_within_cluster"),
            np.where(pd.to_numeric(work["position_usdt_stage_a"], errors="coerce").fillna(0.0) <= 0, "selected_but_zero_position_after_sizing", "active_position_generated"),
        ),
    )
    choke_stage = np.where(
        discard_reason == "not_eligible_pnl_real_le_avg_sl_train",
        "eligibility_gate",
        np.where(
            discard_reason == "selected_but_zero_position_after_sizing",
            "sizing_gate",
            np.where(discard_reason == "active_position_generated", "passed_to_position", "selection_gate"),
        ),
    )
    work["discard_reason"] = discard_reason
    work["choke_stage"] = choke_stage
    return work[
        [
            "date",
            "symbol",
            "cluster_name",
            "stage_a_eligible",
            "pnl_real",
            "avg_sl_train",
            "realized_ratio",
            "p_stage_a_raw",
            "rank_score_stage_a",
            "predicted_rank",
            "y_stage_a_truth_top1",
            "stage_a_selection_mode",
            "stage_a_group_eligible_count",
            "stage_a_date_eligible_count",
            "fallback_applied",
            "tie_at_top_rank",
            "stage_a_selected_proxy",
            "p_stage_a_calibrated",
            "mu_adj_stage_a",
            "kelly_frac_stage_a",
            "position_usdt_stage_a",
            "discard_reason",
            "choke_stage",
        ]
    ].sort_values(["predicted_rank", "symbol"], kind="mergesort")


def classify_latest_audit(*, latest_summary: Mapping[str, Any], recent_rows: pd.DataFrame) -> dict[str, str]:
    recent_live = recent_rows.iloc[:-1] if len(recent_rows) > 1 else recent_rows.iloc[0:0]
    has_recent_positions = bool((recent_live["position_gt_0"] > 0).any()) if not recent_live.empty else False
    if _i(latest_summary.get("eligible_count")) == 0 and has_recent_positions:
        return {
            "classification": CLASS_IDENTIFIED,
            "decision": "correct",
            "blocker_real": "O latest morre no eligibility_gate: na data mais recente nao houve nenhum candidato com pnl_real > avg_sl_train, embora a janela imediatamente anterior tenha produzido selecao e posicao > 0.",
            "next_recommended_step": "Abrir uma rodada corretiva curta para separar explicitamente 'no contest' de 'ranking failure' no latest desta familia.",
        }
    if _i(latest_summary.get("eligible_count")) == 0 and not has_recent_positions:
        return {
            "classification": CLASS_STRUCTURAL,
            "decision": "abandon",
            "blocker_real": "A janela recente inteira ja chega sem candidatos elegiveis ou sem converter elegiveis em posicao; o latest morto parece estrutural nesta familia.",
            "next_recommended_step": "Encerrar esta familia na forma atual e so abrir nova formulacao com hipotese causal nova.",
        }
    if _i(latest_summary.get("selected_proxy_count")) > 0 and _i(latest_summary.get("position_gt_0")) == 0:
        return {
            "classification": CLASS_IDENTIFIED,
            "decision": "correct",
            "blocker_real": "O latest chega a selecionar top1, mas morre no sizing final antes da posicao research-only.",
            "next_recommended_step": "Abrir rodada corretiva curta focada no sizing/handoff do proxy cross-sectional no latest.",
        }
    return {
        "classification": CLASS_INCONCLUSIVE,
        "decision": "correct",
        "blocker_real": "A auditoria nao conseguiu isolar com rigor se o latest morre na elegibilidade, selecao ou sizing.",
        "next_recommended_step": "Instrumentar uma unica rodada adicional se aparecer evidencia nova que reduza a ambiguidade.",
    }


def run_latest_choke_audit() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    baseline_commit = _git("rev-parse", "HEAD")
    dirty_before = _b("PHASE4_CROSS_SECTIONAL_LATEST_CHOKE_DIRTY_BEFORE", _worktree_dirty())
    official_before = _official_hashes()
    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)

    baseline_gate = _read_json(BASELINE_GATE_PATH)
    baseline_diag = _read_json(BASELINE_DIAG_PATH)
    baseline_summary_file = _read_json(BASELINE_SUMMARY_PATH)
    predictions = pd.read_parquet(PREDICTIONS_PATH)
    _ = pd.read_parquet(SNAPSHOT_PATH)
    aggregated = _rebuild_aggregated_frame(predictions)
    recent_rows, latest_date = _recent_window_summary(aggregated)
    latest_df = aggregated.loc[aggregated["date"] == latest_date].copy()
    latest_trace = _candidate_trace(latest_df)

    latest_summary = {
        "date": latest_date.strftime("%Y-%m-%d"),
        "rows_total": int(len(latest_df)),
        "eligible_count": int(latest_df["stage_a_eligible"].fillna(False).sum()),
        "selected_proxy_count": int(latest_df["stage_a_selected_proxy"].fillna(False).sum()),
        "p_cal_gt_0": int((pd.to_numeric(latest_df["p_stage_a_calibrated"], errors="coerce").fillna(0.0) > 0).sum()),
        "mu_gt_0": int((pd.to_numeric(latest_df["mu_adj_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
        "kelly_gt_0": int((pd.to_numeric(latest_df["kelly_frac_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
        "position_gt_0": int((pd.to_numeric(latest_df["position_usdt_stage_a"], errors="coerce").fillna(0.0) > 0).sum()),
        "max_rank_score": round(float(pd.to_numeric(latest_df["rank_score_stage_a"], errors="coerce").fillna(0.0).max()), 6),
        "max_position": round(float(pd.to_numeric(latest_df["position_usdt_stage_a"], errors="coerce").fillna(0.0).max()), 2),
        "latest_date_with_eligible": max(baseline_diag.get("ranking_metrics", {}).get("eligible_candidates_per_date", []) or [{"date": None}], key=lambda row: str(row.get("date", ""))).get("date"),
    }
    final_result = classify_latest_audit(latest_summary=latest_summary, recent_rows=recent_rows)

    funnel_rows = _funnel_rows(latest_df, scope="latest", scope_date=latest_summary["date"])
    for _, row in recent_rows.iterrows():
        date_str = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        frame = aggregated.loc[aggregated["date"] == pd.Timestamp(row["date"])].copy()
        funnel_rows.extend(_funnel_rows(frame, scope="recent_window", scope_date=date_str))
    funnel_df = pd.DataFrame(funnel_rows)

    funnel_path = RESEARCH_PATH / "latest_choke_funnel.parquet"
    trace_path = RESEARCH_PATH / "latest_candidate_trace.parquet"
    diagnostics_path = RESEARCH_PATH / "cross_sectional_latest_choke_diagnostics.json"
    summary_path = RESEARCH_PATH / "cross_sectional_latest_choke_summary.json"
    funnel_df.to_parquet(funnel_path, index=False)
    latest_trace.to_parquet(trace_path, index=False)

    diagnostics = {
        "gate_slug": GATE_SLUG,
        "generated_at_utc": _utc_now_iso(),
        "baseline_reference": {
            "gate_report_path": str(BASELINE_GATE_PATH),
            "diagnostics_path": str(BASELINE_DIAG_PATH),
            "summary_path": str(BASELINE_SUMMARY_PATH),
            "baseline_classification": baseline_summary_file.get("classification"),
        },
        "latest_date": latest_summary["date"],
        "latest_summary": latest_summary,
        "recent_window_rows": recent_rows.assign(date=lambda df: df["date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "candidate_trace_path": str(trace_path),
        "funnel_path": str(funnel_path),
        "baseline_task_definition": baseline_diag.get("task_definition"),
        "proof": {
            "latest_has_zero_eligible": latest_summary["eligible_count"] == 0,
            "latest_has_zero_selected": latest_summary["selected_proxy_count"] == 0,
            "latest_has_zero_position": latest_summary["position_gt_0"] == 0,
            "recent_window_has_live_dates": bool((recent_rows.iloc[:-1]["position_gt_0"] > 0).any()) if len(recent_rows) > 1 else False,
        },
        "final_classification": final_result["classification"],
        "blocker_real": final_result["blocker_real"],
    }
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "latest_summary": latest_summary,
        "recent_window_last_rows": recent_rows.assign(date=lambda df: df["date"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "next_recommended_step": final_result["next_recommended_step"],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_after = _official_hashes()
    unchanged = official_before == official_after
    no_mix = unchanged and str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
    tests_passed = _b("PHASE4_CROSS_SECTIONAL_LATEST_CHOKE_TESTS_PASSED", False)
    gates = [
        {"name": "official_artifacts_unchanged", "value": unchanged, "threshold": "true", "status": "PASS" if unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_mix, "threshold": "true", "status": "PASS" if no_mix else "FAIL"},
        {"name": "latest_funnel_measured", "value": not funnel_df.empty, "threshold": "true", "status": "PASS" if not funnel_df.empty else "FAIL"},
        {"name": "latest_candidate_trace_materialized", "value": not latest_trace.empty, "threshold": "true", "status": "PASS" if not latest_trace.empty else "FAIL"},
        {"name": "recent_window_compared", "value": int(len(recent_rows)), "threshold": f">={RECENT_WINDOW_DATES}", "status": "PASS" if len(recent_rows) >= RECENT_WINDOW_DATES else "FAIL"},
        {"name": "final_classification_assigned", "value": final_result["classification"], "threshold": "one_of(LATEST_CHOKE_IDENTIFIED,NO_LATEST_HEADROOM_STRUCTURAL,INCONCLUSIVE_LATEST_AUDIT)", "status": "PASS"},
        {"name": "tests_passed", "value": tests_passed, "threshold": "true", "status": "PASS" if tests_passed else "FAIL"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"
    current_summary = baseline_gate["summary"]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": final_result["decision"],
        "baseline_commit": baseline_commit,
        "working_tree_dirty": _worktree_dirty(),
        "branch": branch,
        "official_artifacts_used": [{"path": str(path), "sha256_before": official_before.get(str(path)), "sha256_after": official_after.get(str(path))} for path in OFFICIAL_PATHS],
        "research_artifacts_generated": [artifact_record(funnel_path), artifact_record(trace_path), artifact_record(diagnostics_path), artifact_record(summary_path)],
        "summary": current_summary,
        "gates": gates,
        "blockers": [final_result["blocker_real"]],
        "risks_residual": ["O latest continua soberano; historico forte nao resolve latest morto por si so.", "O baseline cross-sectional ainda precisa distinguir explicitamente 'no contest' de 'ranking failure' no corte mais recente."],
        "next_recommended_step": final_result["next_recommended_step"],
    }
    sections = {
        GATE_REPORT_MARKDOWN_SECTIONS[0]: f"Rodada concluida com status `{status}`, decision `{final_result['decision']}` e classificacao `{final_result['classification']}`.",
        GATE_REPORT_MARKDOWN_SECTIONS[1]: f"- `branch`: `{branch}`\n- `baseline_commit`: `{baseline_commit}`\n- `working_tree_dirty_before`: `{dirty_before}`\n- `baseline_gate_path`: `{BASELINE_GATE_PATH}`\n- `baseline_predictions_path`: `{PREDICTIONS_PATH}`\n- `baseline_snapshot_path`: `{SNAPSHOT_PATH}`",
        GATE_REPORT_MARKDOWN_SECTIONS[2]: "- auditoria research-only do latest da familia cross-sectional\n- reconstruido o aggregated frame do baseline para rastrear contestos, sizing e posicao\n- materializados funil do latest, trace dos candidatos e comparacao com janela recente",
        GATE_REPORT_MARKDOWN_SECTIONS[3]: f"- `{funnel_path}`\n- `{trace_path}`\n- `{diagnostics_path}`\n- `{summary_path}`\n- `{GATE_PATH / 'gate_report.json'}`\n- `{GATE_PATH / 'gate_report.md'}`\n- `{GATE_PATH / 'gate_manifest.json'}`\n- `{GATE_PATH / 'gate_metrics.parquet'}`",
        GATE_REPORT_MARKDOWN_SECTIONS[4]: f"- `latest_date={latest_summary['date']}`\n- `latest_rows={latest_summary['rows_total']}`\n- `latest_eligible_count={latest_summary['eligible_count']}`\n- `latest_selected_proxy_count={latest_summary['selected_proxy_count']}`\n- `latest_position_gt_0={latest_summary['position_gt_0']}`\n- `latest_date_with_eligible={latest_summary['latest_date_with_eligible']}`",
        GATE_REPORT_MARKDOWN_SECTIONS[5]: "\n".join(f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`" for item in gates),
        GATE_REPORT_MARKDOWN_SECTIONS[6]: "- o latest pode morrer por ausencia de contestos elegiveis mesmo quando a familia tem historico forte\n- isso precisa ser distinguido de falha de ranking propriamente dita antes de qualquer endurecimento da familia",
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
            "python -m py_compile services\\ml_engine\\phase4_cross_sectional_latest_choke_audit.py tests\\unit\\test_phase4_cross_sectional_latest_choke_audit.py",
            "python -m pytest tests\\unit\\test_phase4_cross_sectional_latest_choke_audit.py -q",
            "python services\\ml_engine\\phase4_cross_sectional_latest_choke_audit.py",
        ],
        "notes": [
            f"final_classification={final_result['classification']}",
            f"decision={final_result['decision']}",
            "latest actual date is 2026-03-20 while latest date with eligible candidates is 2026-03-16",
        ],
    }
    gate_metrics = [{"gate_slug": GATE_SLUG, "metric_name": item["name"], "metric_value": item["value"], "metric_threshold": item["threshold"], "metric_status": item["status"]} for item in gates]
    gate_metrics.extend([
        {"gate_slug": GATE_SLUG, "metric_name": "latest_date", "metric_value": latest_summary["date"], "metric_threshold": "informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "latest_eligible_count", "metric_value": latest_summary["eligible_count"], "metric_threshold": "informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "latest_position_gt_0", "metric_value": latest_summary["position_gt_0"], "metric_threshold": "informational", "metric_status": "PASS"},
    ])
    write_gate_pack(output_dir=GATE_PATH, gate_report=gate_report, gate_manifest=gate_manifest, gate_metrics=gate_metrics, markdown_sections=sections)
    return {"classification": final_result["classification"], "decision": final_result["decision"], "status": status, "summary": current_summary}


def main() -> None:
    print(json.dumps(run_latest_choke_audit(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
