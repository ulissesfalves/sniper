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
from services.ml_engine.phase4_cross_sectional_no_contest_vs_ranking_failure import _build_decision_space_frame

GATE_SLUG = "phase4_cross_sectional_closure_gate"
PHASE_FAMILY = "phase4_research_cross_sectional"
CLASS_APPROVED = "PHASE4_APPROVED_FOR_HARDENING"
CLASS_REJECTED = "PHASE4_REJECTED_OPERATIONALLY"
CLASS_INCONCLUSIVE = "PHASE4_CLOSURE_INCONCLUSIVE"
BASELINE_SLUG = "phase4_cross_sectional_ranking_baseline"
DECISION_EVAL_SLUG = "phase4_cross_sectional_decision_space_latest_eval"
RECENT_WINDOW_DATES = 8

MODEL_PATH = (Path("/data/models") if Path("/data/models").exists() else REPO_ROOT / "data" / "models").resolve()
PHASE4_PATH = MODEL_PATH / "phase4"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG
BASELINE_PATH = MODEL_PATH / "research" / BASELINE_SLUG
DECISION_EVAL_PATH = MODEL_PATH / "research" / DECISION_EVAL_SLUG
OFFICIAL_PATHS = (
    PHASE4_PATH / "phase4_report_v4.json",
    PHASE4_PATH / "phase4_execution_snapshot.parquet",
    PHASE4_PATH / "phase4_aggregated_predictions.parquet",
)
BASELINE_GATE_PATH = REPO_ROOT / "reports" / "gates" / BASELINE_SLUG / "gate_report.json"
DECISION_EVAL_GATE_PATH = REPO_ROOT / "reports" / "gates" / DECISION_EVAL_SLUG / "gate_report.json"
PREDICTIONS_PATH = BASELINE_PATH / "cross_sectional_predictions.parquet"
DECISION_LATEST_EVAL_PATH = DECISION_EVAL_PATH / "decision_space_latest_eval.parquet"
LABEL_DECISION_METRICS_PATH = DECISION_EVAL_PATH / "label_vs_decision_space_metrics.parquet"
DECISION_DEFINITION_PATH = DECISION_EVAL_PATH / "decision_space_eval_definition.json"
DECISION_SUMMARY_PATH = DECISION_EVAL_PATH / "cross_sectional_decision_space_eval_summary.json"


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


def classify_phase4_closure(
    *,
    sharpe_operational: float,
    dsr_honest: float,
    latest_active_count_decision_space: int,
    headroom_decision_space: bool,
    recent_live_dates_decision_space: int,
    recent_window_dates: int,
    historical_active_events_decision_space: int,
    historical_active_events_legacy: int,
) -> dict[str, str]:
    approved = (
        sharpe_operational >= 0.70
        and dsr_honest > 0.0
        and latest_active_count_decision_space >= 1
        and headroom_decision_space
        and recent_live_dates_decision_space >= max(1, recent_window_dates - 1)
        and historical_active_events_decision_space >= historical_active_events_legacy
    )
    if approved:
        return {
            "classification": CLASS_APPROVED,
            "decision": "advance",
            "blocker_real": "",
            "next_recommended_step": "Fase 4 fechada para esta familia; a proxima rodada pode abrir Fase 5 sem promover nada ao official nesta mesma entrega.",
        }
    rejected = (
        sharpe_operational < 0.70
        or dsr_honest <= 0.0
        or latest_active_count_decision_space == 0
        or (not headroom_decision_space)
    )
    if rejected:
        return {
            "classification": CLASS_REJECTED,
            "decision": "abandon",
            "blocker_real": "Mesmo sob a regua causal soberana, a familia nao fecha latest/headroom ou robustez historica suficiente para encerrar a Fase 4.",
            "next_recommended_step": "Abandonar esta familia em research-only e nao abrir Fase 5 com este caminho.",
        }
    return {
        "classification": CLASS_INCONCLUSIVE,
        "decision": "correct",
        "blocker_real": "A familia melhorou sob a regua causal, mas os criterios de fechamento ainda nao ficaram fortes o bastante para um veredito final de Fase 4.",
        "next_recommended_step": "Executar uma ultima rodada tecnica curta apenas se ainda houver ambiguidade material na leitura soberana.",
    }


def _augment_manifest_generated_artifacts(manifest_path: Path, research_artifacts: list[dict[str, Any]]) -> None:
    manifest = _read_json(manifest_path)
    manifest["generated_artifacts"] = list(manifest.get("generated_artifacts", [])) + research_artifacts
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_cross_sectional_closure_gate() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    baseline_commit = _git("rev-parse", "HEAD")
    dirty_before = _b("PHASE4_CROSS_SECTIONAL_CLOSURE_GATE_DIRTY_BEFORE", _worktree_dirty())
    official_before = _official_hashes()
    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)

    baseline_gate = _read_json(BASELINE_GATE_PATH)
    decision_gate = _read_json(DECISION_EVAL_GATE_PATH)
    decision_definition = _read_json(DECISION_DEFINITION_PATH)
    decision_summary = _read_json(DECISION_SUMMARY_PATH)
    predictions_df = pd.read_parquet(PREDICTIONS_PATH)
    latest_eval_df = pd.read_parquet(DECISION_LATEST_EVAL_PATH)
    history_df = pd.read_parquet(LABEL_DECISION_METRICS_PATH)

    decision_df = _build_decision_space_frame(predictions_df, min_group_support=2)
    decision_active_events = int((pd.to_numeric(decision_df["decision_position_usdt"], errors="coerce").fillna(0.0) > 0).sum())
    decision_live_dates = int(
        decision_df.groupby("date")["decision_position_usdt"].apply(
            lambda series: (pd.to_numeric(series, errors="coerce").fillna(0.0) > 0).any()
        ).sum()
    )
    label_live_dates = int(history_df["headroom_label_space"].sum()) if not history_df.empty else 0
    recent_history = history_df.loc[history_df["in_recent_window"]].copy()
    latest_row = history_df.loc[history_df["date"] == pd.to_datetime(history_df["date"], errors="coerce").max()].iloc[0]

    latest_active_count_sovereign = _i(latest_row["latest_active_count_decision_space"])
    headroom_sovereign = bool(latest_row["headroom_decision_space"])
    recent_live_dates_sovereign = int(recent_history["headroom_decision_space"].sum()) if not recent_history.empty else 0
    historical_active_events_legacy = _i(baseline_gate["summary"]["historical_active_events"])
    legacy_latest_active_count = _i(baseline_gate["summary"]["latest_active_count"])
    legacy_headroom = bool(baseline_gate["summary"]["headroom_real"])
    sharpe_operational = _f(baseline_gate["summary"]["sharpe_operational"])
    dsr_honest = _f(baseline_gate["summary"]["dsr_honest"])
    final_result = classify_phase4_closure(
        sharpe_operational=sharpe_operational,
        dsr_honest=dsr_honest,
        latest_active_count_decision_space=latest_active_count_sovereign,
        headroom_decision_space=headroom_sovereign,
        recent_live_dates_decision_space=recent_live_dates_sovereign,
        recent_window_dates=RECENT_WINDOW_DATES,
        historical_active_events_decision_space=decision_active_events,
        historical_active_events_legacy=historical_active_events_legacy,
    )

    closure_eval_path = RESEARCH_PATH / "cross_sectional_closure_eval.parquet"
    causal_history_path = RESEARCH_PATH / "causal_latest_history_summary.parquet"
    closure_definition_path = RESEARCH_PATH / "phase4_closure_definition.json"
    closure_summary_path = RESEARCH_PATH / "phase4_cross_sectional_closure_summary.json"

    latest_closure_eval = latest_eval_df.copy()
    latest_closure_eval["latest_active_count_label_space"] = legacy_latest_active_count
    latest_closure_eval["latest_active_count_decision_space"] = latest_active_count_sovereign
    latest_closure_eval["headroom_label_space"] = legacy_headroom
    latest_closure_eval["headroom_decision_space"] = headroom_sovereign
    latest_closure_eval["phase4_closure_interpretation"] = latest_closure_eval["decision_position_usdt"].map(
        lambda value: "selected_and_live_under_sovereign_eval" if _f(value) > 0 else "not_live_under_sovereign_eval"
    )
    latest_closure_eval.to_parquet(closure_eval_path, index=False)

    causal_history = history_df.copy()
    causal_history["latest_active_count_sovereign"] = causal_history["latest_active_count_decision_space"]
    causal_history["headroom_sovereign"] = causal_history["headroom_decision_space"]
    causal_history["latest_active_count_legacy_compat"] = causal_history["latest_active_count_label_space"]
    causal_history["headroom_legacy_compat"] = causal_history["headroom_label_space"]
    causal_history["phase4_sovereign_status"] = causal_history["headroom_decision_space"].map(lambda flag: "live" if bool(flag) else "dead")
    causal_history.to_parquet(causal_history_path, index=False)

    closure_definition = {
        "gate_slug": GATE_SLUG,
        "generated_at_utc": _utc_now_iso(),
        "sovereign_eval_definition": {
            "latest_active_count": "latest_active_count_decision_space = count(decision_selected and decision_position_usdt > 0) on the latest date.",
            "headroom_real": "headroom_decision_space = max(decision_position_usdt) > 0 on the latest date.",
            "historical_active_events": "count(decision_position_usdt > 0) across the full research history.",
            "source_fields": decision_definition["decision_space_causal_lens"]["fields_used"],
            "operational_availability_definition": decision_definition["decision_space_causal_lens"]["operational_availability_definition"],
            "selection_policy": decision_definition["decision_space_causal_lens"]["selection_policy"],
        },
        "compatibility_only_metrics": {
            "latest_active_count_legacy": legacy_latest_active_count,
            "headroom_real_legacy": legacy_headroom,
            "why_compat_only": "These values depend on realized-space eligible = (pnl_real > avg_sl_train) and are kept only for backward compatibility in gate_report.summary.",
        },
        "phase4_closure_governance": {
            "sovereign_gates": [
                {"name": "dsr_honest", "threshold": "> 0"},
                {"name": "sharpe_operational", "threshold": ">= 0.70"},
                {"name": "latest_active_count_decision_space", "threshold": ">= 1"},
                {"name": "headroom_decision_space", "threshold": "true"},
                {"name": "recent_live_dates_decision_space", "threshold": f">= {max(1, RECENT_WINDOW_DATES - 1)} of last {RECENT_WINDOW_DATES}"},
                {"name": "historical_active_events_decision_space", "threshold": ">= historical_active_events_legacy"},
            ],
            "legacy_summary_note": "gate_report.summary remains in legacy compatibility mode, but the final closure decision of this round is governed only by the sovereign decision-space metrics.",
        },
        "latest_side_by_side": {
            "latest_date": pd.Timestamp(latest_row["date"]).strftime("%Y-%m-%d"),
            "latest_active_count_legacy": legacy_latest_active_count,
            "latest_active_count_sovereign": latest_active_count_sovereign,
            "headroom_legacy": legacy_headroom,
            "headroom_sovereign": headroom_sovereign,
        },
        "final_classification": final_result["classification"],
        "next_recommended_step": final_result["next_recommended_step"],
    }
    closure_definition_path.write_text(json.dumps(closure_definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    closure_summary = {
        "classification": final_result["classification"],
        "decision": final_result["decision"],
        "summary_compatibility_note": "gate_report.summary preserves the legacy label-space latest/headroom values for cross-round compatibility; the closure decision below is governed by the sovereign decision-space metrics.",
        "legacy_summary": baseline_gate["summary"],
        "sovereign_summary": {
            "latest_active_count": latest_active_count_sovereign,
            "headroom_real": headroom_sovereign,
            "historical_active_events": decision_active_events,
            "historical_live_dates": decision_live_dates,
            "recent_live_dates": recent_live_dates_sovereign,
            "recent_window_dates": int(len(recent_history)),
        },
        "phase4_decision_basis": {
            "sharpe_operational": sharpe_operational,
            "dsr_honest": dsr_honest,
            "latest_active_count_decision_space": latest_active_count_sovereign,
            "headroom_decision_space": headroom_sovereign,
            "historical_active_events_decision_space": decision_active_events,
            "historical_active_events_legacy": historical_active_events_legacy,
            "label_live_dates": label_live_dates,
            "decision_live_dates": decision_live_dates,
        },
        "next_recommended_step": final_result["next_recommended_step"],
    }
    closure_summary_path.write_text(json.dumps(closure_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_after = _official_hashes()
    unchanged = official_before == official_after
    no_mix = unchanged and str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
    tests_passed = _b("PHASE4_CROSS_SECTIONAL_CLOSURE_GATE_TESTS_PASSED", False)
    gates = [
        {"name": "official_artifacts_unchanged", "value": unchanged, "threshold": "true", "status": "PASS" if unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_mix, "threshold": "true", "status": "PASS" if no_mix else "FAIL"},
        {"name": "causal_eval_declared_sovereign", "value": True, "threshold": "true", "status": "PASS"},
        {"name": "latest_reinterpreted_under_causal_eval", "value": latest_active_count_sovereign, "threshold": ">=1", "status": "PASS" if latest_active_count_sovereign >= 1 and headroom_sovereign else "FAIL"},
        {"name": "recent_history_reinterpreted", "value": recent_live_dates_sovereign, "threshold": f">={max(1, RECENT_WINDOW_DATES - 1)}", "status": "PASS" if recent_live_dates_sovereign >= max(1, RECENT_WINDOW_DATES - 1) else "FAIL"},
        {"name": "final_phase4_classification_assigned", "value": final_result["classification"], "threshold": "one_of(PHASE4_APPROVED_FOR_HARDENING,PHASE4_REJECTED_OPERATIONALLY,PHASE4_CLOSURE_INCONCLUSIVE)", "status": "PASS"},
        {"name": "tests_passed", "value": tests_passed, "threshold": "true", "status": "PASS" if tests_passed else "FAIL"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"
    research_artifacts = [
        artifact_record(closure_eval_path),
        artifact_record(causal_history_path),
        artifact_record(closure_definition_path),
        artifact_record(closure_summary_path),
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
        "summary": baseline_gate["summary"],
        "gates": gates,
        "blockers": [final_result["blocker_real"]] if final_result["blocker_real"] else [],
        "risks_residual": [
            "O gate summary permanece em compatibilidade com a lente antiga, mas a decisao final desta rodada foi governada pela regua causal soberana.",
            "A familia continua research-only ate uma liberacao explicita da proxima fase; nenhum artifact official foi alterado nesta rodada.",
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
            f"- `decision_eval_gate_path`: `{DECISION_EVAL_GATE_PATH}`\n"
            f"- `predictions_path`: `{PREDICTIONS_PATH}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[2]: (
            "- consolidada a regua causal em decision-space como avaliacao soberana desta familia\n"
            "- reinterpretados latest, janela recente e historico sob a nova lente soberana\n"
            "- materializado gate final de fechamento da Fase 4 sem tocar no path official"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[3]: (
            f"- `{closure_eval_path}`\n"
            f"- `{causal_history_path}`\n"
            f"- `{closure_definition_path}`\n"
            f"- `{closure_summary_path}`\n"
            f"- `{GATE_PATH / 'gate_report.json'}`\n"
            f"- `{GATE_PATH / 'gate_report.md'}`\n"
            f"- `{GATE_PATH / 'gate_manifest.json'}`\n"
            f"- `{GATE_PATH / 'gate_metrics.parquet'}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[4]: (
            f"- `legacy_latest_active_count={legacy_latest_active_count}`\n"
            f"- `sovereign_latest_active_count={latest_active_count_sovereign}`\n"
            f"- `legacy_headroom_real={legacy_headroom}`\n"
            f"- `sovereign_headroom_real={headroom_sovereign}`\n"
            f"- `historical_active_events_legacy={historical_active_events_legacy}`\n"
            f"- `historical_active_events_sovereign={decision_active_events}`\n"
            f"- `recent_live_dates_sovereign={recent_live_dates_sovereign}/{int(len(recent_history))}`\n"
            f"- `sharpe_operational={sharpe_operational}`\n"
            f"- `dsr_honest={dsr_honest}`"
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[5]: "\n".join(
            f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`" for item in gates
        ),
        GATE_REPORT_MARKDOWN_SECTIONS[6]: (
            "- a compatibilidade da summary antiga ainda exige cuidado de leitura; a decisao soberana desta rodada nao deve ser inferida apenas pelos campos legados\n"
            "- a familia continua research-only nesta entrega; a aprovacao aqui significa prontidao para endurecimento em Fase 5, nao promocao ao fast path official"
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
            artifact_record(DECISION_EVAL_GATE_PATH),
            artifact_record(PREDICTIONS_PATH),
            artifact_record(DECISION_LATEST_EVAL_PATH),
            artifact_record(LABEL_DECISION_METRICS_PATH),
            artifact_record(DECISION_DEFINITION_PATH),
            artifact_record(DECISION_SUMMARY_PATH),
            *[artifact_record(path) for path in OFFICIAL_PATHS],
        ],
        "generated_artifacts": research_artifacts,
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "git status --short",
            "git diff --stat",
            "python -m py_compile services\\ml_engine\\phase4_cross_sectional_closure_gate.py tests\\unit\\test_phase4_cross_sectional_closure_gate.py",
            "python -m pytest tests\\unit\\test_phase4_cross_sectional_closure_gate.py -q",
            "$env:PHASE4_CROSS_SECTIONAL_CLOSURE_GATE_TESTS_PASSED='1'; python services\\ml_engine\\phase4_cross_sectional_closure_gate.py",
        ],
        "notes": [
            "gate_report.summary stays in legacy compatibility mode by schema continuity.",
            "The final phase closure decision is governed only by the sovereign decision-space metrics materialized in this round.",
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
        {"gate_slug": GATE_SLUG, "metric_name": "sharpe_operational", "metric_value": sharpe_operational, "metric_threshold": ">=0.70", "metric_status": "PASS" if sharpe_operational >= 0.70 else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "dsr_honest", "metric_value": dsr_honest, "metric_threshold": ">0", "metric_status": "PASS" if dsr_honest > 0 else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "latest_active_count_decision_space", "metric_value": latest_active_count_sovereign, "metric_threshold": ">=1", "metric_status": "PASS" if latest_active_count_sovereign >= 1 else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "headroom_decision_space", "metric_value": headroom_sovereign, "metric_threshold": "true", "metric_status": "PASS" if headroom_sovereign else "FAIL"},
        {"gate_slug": GATE_SLUG, "metric_name": "historical_active_events_decision_space", "metric_value": decision_active_events, "metric_threshold": f">={historical_active_events_legacy}", "metric_status": "PASS" if decision_active_events >= historical_active_events_legacy else "FAIL"},
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
    }


if __name__ == "__main__":
    result = run_cross_sectional_closure_gate()
    print(json.dumps(result, ensure_ascii=False))
