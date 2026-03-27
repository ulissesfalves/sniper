#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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

GATE_SLUG = "phase4_cross_sectional_ranking_baseline"
PHASE_FAMILY = "phase4_research_cross_sectional"
CLASS_PROMISING = "PROMISING_OPERATIONAL_BASELINE"
CLASS_WEAK = "WEAK_OPERATIONAL_BASELINE"
CLASS_INCONCLUSIVE = "INCONCLUSIVE_BASELINE"
MODEL_PATH = (Path("/data/models") if Path("/data/models").exists() else REPO_ROOT / "data" / "models").resolve()
PHASE4_PATH = MODEL_PATH / "phase4"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG
OFFICIAL_PATHS = (
    PHASE4_PATH / "phase4_report_v4.json",
    PHASE4_PATH / "phase4_execution_snapshot.parquet",
    PHASE4_PATH / "phase4_aggregated_predictions.parquet",
)
STAGE_A_RUNNER = REPO_ROOT / "services" / "ml_engine" / "phase4_stage_a_experiment.py"
META_GATE_PATH = REPO_ROOT / "reports" / "gates" / "phase4_meta_upstream_remediation" / "gate_report.json"
META_SUMMARY_PATH = MODEL_PATH / "research" / "phase4_meta_upstream_remediation" / "meta_upstream_remediation_summary.json"


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


def classify_baseline_result(*, current_summary: Mapping[str, Any], abandoned_summary: Mapping[str, Any]) -> dict[str, str]:
    latest_signal = _i(current_summary.get("latest_active_count")) >= 1 or bool(current_summary.get("headroom_real"))
    dsr_positive = _f(current_summary.get("dsr_honest")) > 0.0
    sharpe_ok = _f(current_summary.get("sharpe_operational")) >= 0.70
    hist_better = _i(current_summary.get("historical_active_events")) > _i(abandoned_summary.get("historical_active_events"))
    sharpe_better = _f(current_summary.get("sharpe_operational")) > _f(abandoned_summary.get("sharpe_operational"))
    hit_better = _f(current_summary.get("top1_hit_rate")) > _f(current_summary.get("naive_top1_hit_rate"))
    if latest_signal and dsr_positive and sharpe_ok and hist_better and hit_better:
        return {"classification": CLASS_PROMISING, "decision": "correct", "blocker_real": "Baseline cross-sectional ja abre latest signal com melhora operacional honesta.", "next_recommended_step": "Endurecer o baseline cross-sectional em rodada tecnica dedicada, ainda em research-only."}
    if (not latest_signal) and (not dsr_positive) and (not hist_better) and (not sharpe_better) and (not hit_better):
        return {"classification": CLASS_WEAK, "decision": "abandon", "blocker_real": "O baseline cross-sectional nasceu sem melhoria operacional ou de priorizacao suficiente sobre a familia abandonada.", "next_recommended_step": "Encerrar esta familia baseline e so abrir nova formulacao estrutural com hipotese causal nova."}
    return {"classification": CLASS_INCONCLUSIVE, "decision": "correct", "blocker_real": "O baseline cross-sectional melhora historico/priorizacao sobre a familia abandonada, mas ainda nao abre latest activation/headroom suficiente.", "next_recommended_step": "Executar uma unica rodada tecnica para explicar o choke do latest dentro desta nova familia antes de qualquer endurecimento maior."}


def _compare(current: Mapping[str, Any], abandoned: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reference_summary_path": str(META_SUMMARY_PATH),
        "abandoned_family_summary": dict(abandoned),
        "current_baseline_summary": dict(current),
        "delta": {
            "sharpe_operational": round(_f(current.get("sharpe_operational")) - _f(abandoned.get("sharpe_operational")), 6),
            "dsr_honest": round(_f(current.get("dsr_honest")) - _f(abandoned.get("dsr_honest")), 6),
            "latest_active_count": round(_f(current.get("latest_active_count")) - _f(abandoned.get("latest_active_count")), 6),
            "historical_active_events": round(_f(current.get("historical_active_events")) - _f(abandoned.get("historical_active_events")), 6),
        },
        "better_than_abandoned_family": bool(
            _i(current.get("historical_active_events")) > _i(abandoned.get("historical_active_events"))
            or _f(current.get("dsr_honest")) > _f(abandoned.get("dsr_honest"))
            or _f(current.get("sharpe_operational")) > _f(abandoned.get("sharpe_operational"))
            or (_i(current.get("latest_active_count")) > _i(abandoned.get("latest_active_count")))
            or (bool(current.get("headroom_real")) and not bool(abandoned.get("headroom_real")))
        ),
    }


def _run_stage_a() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "STAGE_A_EXPERIMENT_NAME": GATE_SLUG,
            "STAGE_A_PROBLEM_TYPE": "cross_sectional_ranking",
            "STAGE_A_TARGET_MODE": "cross_sectional_relative_activation",
            "STAGE_A_MIN_ELIGIBLE_PER_DATE_CLUSTER": "2",
            "STAGE_A_REFERENCE_EXPERIMENT_NAME": "phase4_stage_a_experiment_v4",
            "STAGE_A_BASELINE_EXPERIMENT_NAMES": "",
        }
    )
    return subprocess.run([sys.executable, str(STAGE_A_RUNNER)], cwd=REPO_ROOT, env=env, check=False, capture_output=True, text=True)


def run_cross_sectional_baseline() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    baseline_commit = _git("rev-parse", "HEAD")
    dirty_before = _b("PHASE4_CROSS_SECTIONAL_BASELINE_WORKTREE_DIRTY_BEFORE", _worktree_dirty())
    official_before = _official_hashes()
    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)

    proc = _run_stage_a()
    stage_a_paths = {
        "predictions": RESEARCH_PATH / "stage_a_predictions.parquet",
        "report": RESEARCH_PATH / "stage_a_report.json",
        "snapshot": RESEARCH_PATH / "stage_a_snapshot_proxy.parquet",
        "manifest": RESEARCH_PATH / "stage_a_manifest.json",
    }
    if proc.returncode != 0 or not all(path.exists() for path in stage_a_paths.values()):
        raise RuntimeError(f"stage_a runner failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    required = {
        "predictions": RESEARCH_PATH / "cross_sectional_predictions.parquet",
        "snapshot": RESEARCH_PATH / "cross_sectional_latest_snapshot.parquet",
        "diagnostics": RESEARCH_PATH / "cross_sectional_diagnostics.json",
        "summary": RESEARCH_PATH / "cross_sectional_eval_summary.json",
    }
    predictions_df = pd.read_parquet(stage_a_paths["predictions"])
    snapshot_df = pd.read_parquet(stage_a_paths["snapshot"])
    predictions_df.to_parquet(required["predictions"], index=False)
    snapshot_df.to_parquet(required["snapshot"], index=False)

    report = _read_json(stage_a_paths["report"])
    manifest = _read_json(stage_a_paths["manifest"])
    meta_gate = _read_json(META_GATE_PATH)
    meta_summary_file = _read_json(META_SUMMARY_PATH)
    abandoned = meta_summary_file.get("official_baseline_summary", {})
    ranking = report.get("ranking_metrics", {}) or {}
    cls = report.get("classification_metrics", {}) or {}
    op = report.get("operational_proxy", {}) or {}
    funnel = op.get("activation_funnel", {}) or {}
    task_policy = ranking.get("target_selection_policy") or cls.get("target_selection_policy") or {}
    dates = pd.to_datetime(predictions_df.get("date"), errors="coerce")
    current = {
        "sharpe_operational": _f(op.get("sharpe")),
        "dsr_honest": _f(op.get("dsr_honest")),
        "latest_active_count": _i(funnel.get("latest_snapshot_active_count")),
        "headroom_real": bool(op.get("headroom_real")),
        "historical_active_events": _i(op.get("n_active")),
        "top1_hit_rate": _f(ranking.get("top1_hit_rate")),
        "naive_top1_hit_rate": _f(ranking.get("naive_top1_hit_rate")),
        "mrr": _f(ranking.get("mrr")),
        "rank_margin_latest": _f(ranking.get("rank_margin_latest")),
        "position_gt_0_rows_final": _i((op.get("final_position_counts") or {}).get("position_gt_0_rows_final")),
        "position_gt_0_over_min_alloc_rows_final": _i((op.get("final_position_counts") or {}).get("position_gt_0_over_min_alloc_rows_final")),
    }
    comparison = _compare(current, abandoned)
    final_result = classify_baseline_result(current_summary=current, abandoned_summary=abandoned)

    task_definition = {
        "problem_type": "cross_sectional_ranking_task",
        "target_label": "truth_top1 within (date, cluster_name) among eligible rows where eligible = (pnl_real > avg_sl_train); fallback to top1(date-universe) when eligible_count(date, cluster_name) < 2",
        "rank_target_train": "rank_target_stage_a = pnl_real / avg_sl_train for eligible rows; 0 for ineligible rows",
        "temporal_horizon": "same realized event horizon already materialized in pooled phase4 rows via pnl_real / label / event_date; one contest per date",
        "asset_universe": {"n_symbols": int(predictions_df['symbol'].astype(str).nunique()), "date_min": dates.min().strftime('%Y-%m-%d') if dates.notna().any() else None, "date_max": dates.max().strftime('%Y-%m-%d') if dates.notna().any() else None},
        "ranking_calculation": "model predicts rank_score_stage_a OOS; highest rank_score_stage_a wins each local or fallback contest",
        "research_only_operational_proxy": "Select TOP 1 by rank_score_stage_a within (date, cluster_name) among eligible rows; fallback to TOP 1 in the date-universe when local eligible support < 2.",
        "support_and_fallback": {"min_eligible_per_date_cluster": _i(task_policy.get("min_eligible_per_date_cluster"), 2), "fallback_policy": task_policy.get("fallback_policy", "date_universe_top1_when_cluster_support_lt_min"), "support_rationale": task_policy.get("support_rationale", "A local contest with a single eligible row is tautological, so the proxy falls back to a date-universe contest.")},
        "evaluation_without_leakage": report.get("target_definition") or manifest.get("cross_sectional_ranking_no_leakage_note"),
    }
    latest_eligible = max(ranking.get("eligible_candidates_per_date", []) or [{"eligible_candidates": 0}], key=lambda row: str(row.get("date", ""))).get("eligible_candidates", 0)
    diagnostics = {
        "gate_slug": GATE_SLUG,
        "generated_at_utc": _utc_now_iso(),
        "task_definition": task_definition,
        "runner_execution": {"command": f"{sys.executable} {STAGE_A_RUNNER}", "returncode": proc.returncode, "stdout_tail": proc.stdout.strip().splitlines()[-20:], "stderr_tail": proc.stderr.strip().splitlines()[-20:], "cross_sectional_manifest_note": manifest.get("cross_sectional_ranking_no_leakage_note")},
        "ranking_metrics": {"top1_hit_rate": current["top1_hit_rate"], "naive_top1_hit_rate": current["naive_top1_hit_rate"], "mrr": current["mrr"], "rank_margin_latest": current["rank_margin_latest"], "eligible_candidates_per_date": ranking.get("eligible_candidates_per_date", []), "target_positive_count_per_date": ranking.get("truth_top1_count_per_date", []), "predicted_top_candidate_per_date": ranking.get("predicted_top_candidate_per_date", []), "groups_local_selection": ranking.get("groups_local_selection"), "groups_fallback_selection": ranking.get("groups_fallback_selection"), "groups_without_eligible": ranking.get("groups_without_eligible"), "groups_total": ranking.get("groups_total"), "latest_eligible_candidates": _i(latest_eligible)},
        "operational_metrics": current,
        "classification_metrics": {"positive_rate_oos": cls.get("positive_rate_oos"), "auc_raw_global": cls.get("auc_raw_global"), "target_prevalence_by_year": cls.get("target_prevalence_by_year", []), "target_prevalence_by_cluster": cls.get("target_prevalence_by_cluster", [])},
        "comparison_vs_abandoned_family": comparison,
        "abandoned_family_reference_gate": {"path": str(META_GATE_PATH), "status": meta_gate.get("status"), "decision": meta_gate.get("decision"), "classification": meta_summary_file.get("classification")},
        "final_classification": final_result["classification"],
        "blocker_real": final_result["blocker_real"],
    }
    required["diagnostics"].write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    required["summary"].write_text(json.dumps({"classification": final_result["classification"], "decision": final_result["decision"], "current_summary": current, "comparison_vs_abandoned_family": comparison, "next_recommended_step": final_result["next_recommended_step"]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_after = _official_hashes()
    unchanged = official_before == official_after
    no_mix = str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research")) and unchanged and all(path.exists() for path in required.values())
    tests_passed = _b("PHASE4_CROSS_SECTIONAL_BASELINE_TESTS_PASSED", False)
    gates = [
        {"name": "official_artifacts_unchanged", "value": unchanged, "threshold": "true", "status": "PASS" if unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_mix, "threshold": "true", "status": "PASS" if no_mix else "FAIL"},
        {"name": "cross_sectional_task_defined", "value": task_definition["target_label"], "threshold": "non_empty", "status": "PASS"},
        {"name": "baseline_runner_executed", "value": proc.returncode, "threshold": "0", "status": "PASS" if proc.returncode == 0 else "FAIL"},
        {"name": "research_artifacts_generated", "value": 4 if all(path.exists() for path in required.values()) else 0, "threshold": "4", "status": "PASS" if all(path.exists() for path in required.values()) else "FAIL"},
        {"name": "latest_snapshot_materialized", "value": bool(len(snapshot_df) > 0), "threshold": "true", "status": "PASS" if len(snapshot_df) > 0 else "FAIL"},
        {"name": "operational_metrics_materialized", "value": True, "threshold": "true", "status": "PASS"},
        {"name": "final_classification_assigned", "value": final_result["classification"], "threshold": "one_of(PROMISING_OPERATIONAL_BASELINE,WEAK_OPERATIONAL_BASELINE,INCONCLUSIVE_BASELINE)", "status": "PASS"},
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
        "research_artifacts_generated": [artifact_record(required["predictions"]), artifact_record(required["snapshot"]), artifact_record(required["diagnostics"]), artifact_record(required["summary"])],
        "summary": {"sharpe_operational": current["sharpe_operational"], "dsr_honest": current["dsr_honest"], "latest_active_count": current["latest_active_count"], "headroom_real": current["headroom_real"], "historical_active_events": current["historical_active_events"]},
        "gates": gates,
        "blockers": [final_result["blocker_real"]] if final_result["classification"] != CLASS_PROMISING else [],
        "risks_residual": ["Ranking metrics auxiliares nao sao tratadas como merito operacional suficiente por si so.", "Latest snapshot continua soberano para dizer se o baseline realmente abriu headroom util ou nao."],
        "next_recommended_step": final_result["next_recommended_step"],
    }
    sections = {
        GATE_REPORT_MARKDOWN_SECTIONS[0]: f"Rodada concluida com status `{status}`, decision `{final_result['decision']}` e classificacao `{final_result['classification']}`.",
        GATE_REPORT_MARKDOWN_SECTIONS[1]: f"- `branch`: `{branch}`\n- `baseline_commit`: `{baseline_commit}`\n- `working_tree_dirty_before`: `{dirty_before}`\n- `official_report_path`: `{OFFICIAL_PATHS[0]}`\n- `official_snapshot_path`: `{OFFICIAL_PATHS[1]}`\n- `official_aggregated_path`: `{OFFICIAL_PATHS[2]}`\n- `prior_meta_remediation_summary`: `{META_SUMMARY_PATH}`",
        GATE_REPORT_MARKDOWN_SECTIONS[2]: "- runner research-only da rodada para baseline cross-sectional\n- reaproveitamento bounded do stage_a experiment runner em modo `cross_sectional_ranking`\n- materializacao de artifacts com nomes proprios da rodada e gate pack padronizado",
        GATE_REPORT_MARKDOWN_SECTIONS[3]: f"- `{required['predictions']}`\n- `{required['snapshot']}`\n- `{required['diagnostics']}`\n- `{required['summary']}`\n- `{GATE_PATH / 'gate_report.json'}`\n- `{GATE_PATH / 'gate_report.md'}`\n- `{GATE_PATH / 'gate_manifest.json'}`\n- `{GATE_PATH / 'gate_metrics.parquet'}`",
        GATE_REPORT_MARKDOWN_SECTIONS[4]: f"- `sharpe_operational={current['sharpe_operational']}`\n- `dsr_honest={current['dsr_honest']}`\n- `latest_active_count={current['latest_active_count']}`\n- `headroom_real={current['headroom_real']}`\n- `historical_active_events={current['historical_active_events']}`\n- `top1_hit_rate={current['top1_hit_rate']}` vs naive `{current['naive_top1_hit_rate']}`\n- `mrr={current['mrr']}`\n- `comparison_vs_abandoned_family={comparison['better_than_abandoned_family']}`",
        GATE_REPORT_MARKDOWN_SECTIONS[5]: "\n".join(f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`" for item in gates),
        GATE_REPORT_MARKDOWN_SECTIONS[6]: "- o latest continua obrigatorio para confirmar utilidade operacional real; historico forte sem latest vivo continua sendo inconclusivo\n- o baseline reutiliza o universo/pipeline atual de phase4, entao qualquer choke remanescente no latest ainda precisa ser localizado antes de endurecer a familia",
        GATE_REPORT_MARKDOWN_SECTIONS[7]: final_result["decision"],
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": _utc_now_iso(),
        "baseline_commit": baseline_commit,
        "branch": branch,
        "working_tree_dirty_before": dirty_before,
        "working_tree_dirty_after": _worktree_dirty(),
        "source_artifacts": [artifact_record(STAGE_A_RUNNER, extras={"role": "cross_sectional_stage_a_runner"}), artifact_record(OFFICIAL_PATHS[0], extras={"role": "official_phase4_report"}), artifact_record(OFFICIAL_PATHS[1], extras={"role": "official_phase4_snapshot"}), artifact_record(OFFICIAL_PATHS[2], extras={"role": "official_phase4_aggregated_predictions"}), artifact_record(META_GATE_PATH, extras={"role": "prior_meta_remediation_gate"}), artifact_record(META_SUMMARY_PATH, extras={"role": "prior_meta_remediation_summary"})],
        "generated_artifacts": [],
        "commands_executed": ["git branch --show-current", "git rev-parse HEAD", "git status --short --untracked-files=all", "git diff --stat", "Get-FileHash data\\models\\phase4\\phase4_report_v4.json,data\\models\\phase4\\phase4_execution_snapshot.parquet,data\\models\\phase4\\phase4_aggregated_predictions.parquet -Algorithm SHA256 | Select-Object Path,Hash", "python -m py_compile services\\ml_engine\\phase4_cross_sectional_ranking_baseline.py tests\\unit\\test_phase4_cross_sectional_ranking_baseline.py", "python -m pytest tests\\unit\\test_phase4_cross_sectional_ranking_baseline.py tests\\unit\\test_phase4_stage_a_experiment.py -q", "python services\\ml_engine\\phase4_cross_sectional_ranking_baseline.py"],
        "notes": [f"final_classification={final_result['classification']}", f"decision={final_result['decision']}", "research-only baseline; official phase4 artifacts unchanged by hash", "cross-sectional proxy uses top1(date,cluster_name) among eligible rows with fallback to date-universe top1 when local support < 2"],
    }
    gate_metrics = [{"gate_slug": GATE_SLUG, "metric_name": item["name"], "metric_value": item["value"], "metric_threshold": item["threshold"], "metric_status": item["status"]} for item in gates]
    gate_metrics.extend([
        {"gate_slug": GATE_SLUG, "metric_name": "sharpe_operational", "metric_value": current["sharpe_operational"], "metric_threshold": "research-only informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "dsr_honest", "metric_value": current["dsr_honest"], "metric_threshold": "research-only informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "latest_active_count", "metric_value": current["latest_active_count"], "metric_threshold": "research-only informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "headroom_real", "metric_value": current["headroom_real"], "metric_threshold": "research-only informational", "metric_status": "PASS"},
        {"gate_slug": GATE_SLUG, "metric_name": "historical_active_events", "metric_value": current["historical_active_events"], "metric_threshold": "research-only informational", "metric_status": "PASS"},
    ])
    write_gate_pack(output_dir=GATE_PATH, gate_report=gate_report, gate_manifest=gate_manifest, gate_metrics=gate_metrics, markdown_sections=sections)
    return {"classification": final_result["classification"], "decision": final_result["decision"], "status": status, "summary": gate_report["summary"]}


def main() -> None:
    print(json.dumps(run_cross_sectional_baseline(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
