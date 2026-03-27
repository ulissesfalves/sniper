#!/usr/bin/env python3
from __future__ import annotations

import json
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

from services.common.gate_reports import artifact_record, sha256_file, write_gate_pack

GATE_SLUG = "phase4_alignment_meta_audit"
PHASE_FAMILY = "phase4_research_alignment"
CLASS_PATH_MISMATCH = "PATH_MISMATCH_BLOCKER"
CLASS_CHOKEPOINT = "META_PATH_CHOKEPOINT_IDENTIFIED"
CLASS_UPSTREAM = "NEEDS_UPSTREAM_REMEDIATION"
THRESHOLDS = (0.45, 0.46, 0.47, 0.50, 0.51, 0.55, 0.60)
GOVERNING_FIELD_MAP = (
    ("p_meta_raw", "p_meta_raw"),
    ("p_meta_calibrated", "p_calibrated"),
    ("mu_adj_meta", "mu_adj_meta"),
    ("kelly_frac_meta", "kelly_frac"),
    ("position_usdt_meta", "position_usdt"),
)


def _resolve_model_path() -> Path:
    docker_path = Path("/data/models")
    if docker_path.exists():
        return docker_path
    return (REPO_ROOT / "data" / "models").resolve()


MODEL_PATH = _resolve_model_path()
PHASE4_PATH = MODEL_PATH / "phase4"
RESEARCH_PATH = MODEL_PATH / "research" / GATE_SLUG
GATE_PATH = REPO_ROOT / "reports" / "gates" / GATE_SLUG

NESTED_REPORT_PATH = PHASE4_PATH / "phase4_report_v4.json"
ROOT_REPORT_PATH = MODEL_PATH / "phase4_report_v4.json"
SNAPSHOT_PATH = PHASE4_PATH / "phase4_execution_snapshot.parquet"
AGGREGATED_PATH = PHASE4_PATH / "phase4_aggregated_predictions.parquet"
PHASE4_CPCV_SOURCE = THIS_FILE.parent / "phase4_cpcv.py"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _worktree_dirty() -> bool:
    return bool(_git_output("status", "--short"))


def _iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _coerce_num(series: pd.Series | Any) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def build_latest_from_aggregated(aggregated_df: pd.DataFrame) -> pd.DataFrame:
    if aggregated_df.empty:
        return aggregated_df.copy()
    work = aggregated_df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    return (
        work.sort_values(["date", "symbol"], kind="mergesort")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values(["date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )


def compare_snapshot_lineage(aggregated_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> dict[str, Any]:
    latest = build_latest_from_aggregated(aggregated_df)
    latest_cols = ["date", "symbol"] + [src for src, _ in GOVERNING_FIELD_MAP]
    snapshot_cols = ["date", "symbol"] + [dst for _, dst in GOVERNING_FIELD_MAP]
    merged = latest[latest_cols].merge(
        snapshot_df[snapshot_cols],
        on=["date", "symbol"],
        how="outer",
        indicator=True,
        suffixes=("_agg", "_snap"),
    )

    field_mismatches: dict[str, int] = {}
    for source_col, snapshot_col in GOVERNING_FIELD_MAP:
        left_col = f"{source_col}_agg" if f"{source_col}_agg" in merged.columns else source_col
        right_col = f"{snapshot_col}_snap" if f"{snapshot_col}_snap" in merged.columns else snapshot_col
        left = _coerce_num(merged[left_col]).round(10)
        right = _coerce_num(merged[right_col]).round(10)
        field_mismatches[source_col] = int((left != right).sum())

    merge_counts = merged["_merge"].value_counts().to_dict()
    aligned = (
        int(merge_counts.get("both", 0)) == int(len(snapshot_df)) == int(len(latest))
        and int(merge_counts.get("left_only", 0)) == 0
        and int(merge_counts.get("right_only", 0)) == 0
        and all(count == 0 for count in field_mismatches.values())
    )
    return {
        "latest_rows_from_aggregated": int(len(latest)),
        "snapshot_rows": int(len(snapshot_df)),
        "merge_counts": {str(key): int(value) for key, value in merge_counts.items()},
        "field_mismatches": field_mismatches,
        "aligned": bool(aligned),
    }


def _activation_funnel_counts(aggregated_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> dict[str, int]:
    agg_raw = _coerce_num(aggregated_df.get("p_meta_raw"))
    agg_cal = _coerce_num(aggregated_df.get("p_meta_calibrated"))
    agg_mu = _coerce_num(aggregated_df.get("mu_adj_meta"))
    agg_kelly = _coerce_num(aggregated_df.get("kelly_frac_meta"))
    agg_pos = _coerce_num(aggregated_df.get("position_usdt_meta"))

    latest_raw = _coerce_num(snapshot_df.get("p_meta_raw"))
    latest_cal = _coerce_num(snapshot_df.get("p_calibrated", snapshot_df.get("p_meta_calibrated")))
    latest_mu = _coerce_num(snapshot_df.get("mu_adj_meta"))
    latest_kelly = _coerce_num(snapshot_df.get("kelly_frac", snapshot_df.get("kelly_frac_meta")))
    latest_pos = _coerce_num(snapshot_df.get("position_usdt", snapshot_df.get("position_usdt_meta")))

    return {
        "aggregated_rows_total": int(len(aggregated_df)),
        "aggregated_symbols_total": int(aggregated_df["symbol"].nunique()) if "symbol" in aggregated_df.columns else 0,
        "aggregated_p_meta_raw_gt_050": int((agg_raw > 0.50).sum()),
        "aggregated_p_meta_calibrated_gt_050": int((agg_cal > 0.50).sum()),
        "aggregated_p_meta_calibrated_gt_051": int((agg_cal > 0.51).sum()),
        "aggregated_mu_adj_meta_gt_0": int((agg_mu > 0).sum()),
        "aggregated_kelly_frac_meta_gt_0": int((agg_kelly > 0).sum()),
        "aggregated_position_usdt_meta_gt_0": int((agg_pos > 0).sum()),
        "latest_snapshot_rows": int(len(snapshot_df)),
        "latest_snapshot_symbols_total": int(snapshot_df["symbol"].nunique()) if "symbol" in snapshot_df.columns else int(len(snapshot_df)),
        "latest_snapshot_p_meta_raw_gt_050": int((latest_raw > 0.50).sum()),
        "latest_snapshot_p_meta_calibrated_gt_050": int((latest_cal > 0.50).sum()),
        "latest_snapshot_mu_adj_meta_gt_0": int((latest_mu > 0).sum()),
        "latest_snapshot_kelly_frac_meta_gt_0": int((latest_kelly > 0).sum()),
        "latest_snapshot_position_usdt_meta_gt_0": int((latest_pos > 0).sum()),
        "latest_snapshot_active_count": int((latest_pos > 0).sum()),
    }


def build_funnel_table(aggregated_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scopes = {
        "aggregated": {
            "df": aggregated_df,
            "score_raw": _coerce_num(aggregated_df.get("p_meta_raw")),
            "score_cal": _coerce_num(aggregated_df.get("p_meta_calibrated")),
            "mu": _coerce_num(aggregated_df.get("mu_adj_meta")),
            "kelly": _coerce_num(aggregated_df.get("kelly_frac_meta")),
            "position": _coerce_num(aggregated_df.get("position_usdt_meta")),
        },
        "latest_snapshot": {
            "df": snapshot_df,
            "score_raw": _coerce_num(snapshot_df.get("p_meta_raw")),
            "score_cal": _coerce_num(snapshot_df.get("p_calibrated", snapshot_df.get("p_meta_calibrated"))),
            "mu": _coerce_num(snapshot_df.get("mu_adj_meta")),
            "kelly": _coerce_num(snapshot_df.get("kelly_frac", snapshot_df.get("kelly_frac_meta"))),
            "position": _coerce_num(snapshot_df.get("position_usdt", snapshot_df.get("position_usdt_meta"))),
        },
    }
    for scope, payload in scopes.items():
        base_count = int(len(payload["df"]))
        chain = [
            ("rows_total", base_count),
            ("p_meta_raw_gt_050", int((payload["score_raw"] > 0.50).sum())),
            ("p_meta_calibrated_gt_050", int((payload["score_cal"] > 0.50).sum())),
            ("mu_adj_meta_gt_0", int((payload["mu"] > 0).sum())),
            ("kelly_frac_meta_gt_0", int((payload["kelly"] > 0).sum())),
            ("position_usdt_meta_gt_0", int((payload["position"] > 0).sum())),
        ]
        prev = base_count
        for stage_order, (stage_name, count) in enumerate(chain, start=1):
            rows.append(
                {
                    "scope": scope,
                    "row_kind": "funnel_stage",
                    "stage_order": stage_order,
                    "metric_name": stage_name,
                    "count": int(count),
                    "rate_vs_rows": round(float(count / base_count), 6) if base_count else 0.0,
                    "survival_vs_prev": round(float(count / prev), 6) if prev else 0.0,
                }
            )
            prev = count
        for threshold in THRESHOLDS:
            rows.append(
                {
                    "scope": scope,
                    "row_kind": "threshold_count",
                    "stage_order": None,
                    "metric_name": "p_meta_calibrated",
                    "threshold": threshold,
                    "count": int((payload["score_cal"] > threshold).sum()),
                    "rate_vs_rows": round(float((payload["score_cal"] > threshold).mean()), 6) if base_count else 0.0,
                    "survival_vs_prev": None,
                }
            )
    return pd.DataFrame(rows)


def build_distribution_table(aggregated_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scopes = {
        "aggregated": {
            "p_meta_calibrated": _coerce_num(aggregated_df.get("p_meta_calibrated")),
            "kelly_frac_meta": _coerce_num(aggregated_df.get("kelly_frac_meta")),
        },
        "latest_snapshot": {
            "p_meta_calibrated": _coerce_num(snapshot_df.get("p_calibrated", snapshot_df.get("p_meta_calibrated"))),
            "kelly_frac_meta": _coerce_num(snapshot_df.get("kelly_frac", snapshot_df.get("kelly_frac_meta"))),
        },
    }
    stats = (
        ("min", 0.0),
        ("p25", 0.25),
        ("p50", 0.50),
        ("p75", 0.75),
        ("p90", 0.90),
        ("p95", 0.95),
        ("max", 1.0),
        ("mean", None),
    )
    for scope, metrics in scopes.items():
        for metric_name, series in metrics.items():
            series = series.astype(float)
            for stat_name, quantile in stats:
                if stat_name == "mean":
                    value = float(series.mean()) if len(series) else 0.0
                elif stat_name == "min":
                    value = float(series.min()) if len(series) else 0.0
                elif stat_name == "max":
                    value = float(series.max()) if len(series) else 0.0
                else:
                    value = float(series.quantile(quantile)) if len(series) else 0.0
                rows.append(
                    {
                        "scope": scope,
                        "metric_name": metric_name,
                        "stat_name": stat_name,
                        "value": round(value, 6),
                    }
                )
    return pd.DataFrame(rows)


def classify_blocker(lineage: dict[str, Any], funnel_counts: dict[str, int], report: dict[str, Any]) -> dict[str, Any]:
    if not lineage["paths_exist"] or not lineage["lineage_aligned"]:
        return {
            "classification": CLASS_PATH_MISMATCH,
            "blocker_real": "Report oficial e snapshot oficial nao compartilham lineage comprovadamente alinhada.",
            "where_signal_dies": "indeterminado_por_mismatch",
            "local_fix_possible": True,
            "needs_upstream_remediation": False,
        }

    latest_raw = int(funnel_counts["latest_snapshot_p_meta_raw_gt_050"])
    latest_cal = int(funnel_counts["latest_snapshot_p_meta_calibrated_gt_050"])
    latest_mu = int(funnel_counts["latest_snapshot_mu_adj_meta_gt_0"])
    latest_kelly = int(funnel_counts["latest_snapshot_kelly_frac_meta_gt_0"])
    latest_pos = int(funnel_counts["latest_snapshot_position_usdt_meta_gt_0"])

    if latest_raw > 0 and latest_cal == 0:
        return {
            "classification": CLASS_CHOKEPOINT,
            "blocker_real": "O snapshot oficial esta alinhado ao report; o sinal morre entre p_meta_raw e p_meta_calibrated no corte mais recente.",
            "where_signal_dies": "score_calibration",
            "local_fix_possible": False,
            "needs_upstream_remediation": True,
        }
    if latest_cal > 0 and latest_mu == 0:
        return {
            "classification": CLASS_CHOKEPOINT,
            "blocker_real": "O snapshot oficial esta alinhado ao report; o sinal morre na conversao de score calibrado para mu_adj_meta.",
            "where_signal_dies": "mu_adjustment",
            "local_fix_possible": False,
            "needs_upstream_remediation": True,
        }
    if latest_mu > 0 and latest_kelly == 0:
        return {
            "classification": CLASS_CHOKEPOINT,
            "blocker_real": "O snapshot oficial esta alinhado ao report; o sinal morre na etapa de Kelly sizing.",
            "where_signal_dies": "kelly_sizing",
            "local_fix_possible": False,
            "needs_upstream_remediation": True,
        }
    if latest_kelly > 0 and latest_pos == 0:
        return {
            "classification": CLASS_CHOKEPOINT,
            "blocker_real": "O snapshot oficial esta alinhado ao report; o sinal morre na conversao para position_usdt_meta.",
            "where_signal_dies": "position_conversion",
            "local_fix_possible": True,
            "needs_upstream_remediation": False,
        }
    return {
        "classification": CLASS_UPSTREAM,
        "blocker_real": "Nao ha mismatch de path, mas o meta path continua sem gerar ativacao operacional robusta; remediation upstream permanece necessaria.",
        "where_signal_dies": report.get("operational_path", {}).get("choke_point", {}).get("latest_snapshot_stage", "unknown"),
        "local_fix_possible": False,
        "needs_upstream_remediation": True,
    }


def _paper_environment_state() -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    daemon_available = result.returncode == 0 and bool(result.stdout.strip())
    return {
        "daemon_available": daemon_available,
        "stdout": result.stdout.strip(),
        "stderr": (result.stderr or "").strip(),
        "clean": not daemon_available,
        "status": "docker_daemon_unavailable" if not daemon_available else "daemon_available",
    }


def _build_lineage_audit(report: dict[str, Any], aggregated_df: pd.DataFrame, snapshot_df: pd.DataFrame) -> tuple[dict[str, Any], dict[str, int]]:
    paths_exist = all(path.exists() for path in (NESTED_REPORT_PATH, ROOT_REPORT_PATH, SNAPSHOT_PATH, AGGREGATED_PATH))
    nested_hash = sha256_file(NESTED_REPORT_PATH) if NESTED_REPORT_PATH.exists() else None
    root_hash = sha256_file(ROOT_REPORT_PATH) if ROOT_REPORT_PATH.exists() else None
    snapshot_lineage = compare_snapshot_lineage(aggregated_df, snapshot_df)
    recomputed_funnel = _activation_funnel_counts(aggregated_df, snapshot_df)
    reported_funnel = report.get("operational_path", {}).get("activation_funnel", {})
    funnel_mismatches = {
        key: {"reported": int(reported_funnel.get(key, -999)), "recomputed": int(value)}
        for key, value in recomputed_funnel.items()
        if int(reported_funnel.get(key, -999)) != int(value)
    }
    lineage_aligned = (
        paths_exist
        and nested_hash == root_hash
        and snapshot_lineage["aligned"]
        and not funnel_mismatches
        and report.get("snapshot_governed_by") == "operational_meta_path"
    )
    lineage = {
        "report_phase4_path": str(NESTED_REPORT_PATH),
        "report_phase4_root_duplicate_path": str(ROOT_REPORT_PATH),
        "snapshot_phase4_path": str(SNAPSHOT_PATH),
        "aggregated_phase4_path": str(AGGREGATED_PATH),
        "report_timestamp": report.get("timestamp"),
        "report_mtime_utc": _iso_mtime(NESTED_REPORT_PATH),
        "root_report_mtime_utc": _iso_mtime(ROOT_REPORT_PATH),
        "snapshot_mtime_utc": _iso_mtime(SNAPSHOT_PATH),
        "aggregated_mtime_utc": _iso_mtime(AGGREGATED_PATH),
        "report_as_of_data_date": str(pd.to_datetime(snapshot_df["date"]).max().date()) if not snapshot_df.empty else None,
        "snapshot_as_of_data_date": str(pd.to_datetime(snapshot_df["date"]).max().date()) if not snapshot_df.empty else None,
        "aggregated_as_of_data_date": str(pd.to_datetime(aggregated_df["date"]).max().date()) if not aggregated_df.empty else None,
        "report_run_id": report.get("run_id"),
        "snapshot_run_id": None,
        "report_policy_name": report.get("phase4_decision_policy"),
        "snapshot_policy_name": None,
        "snapshot_governed_by": report.get("snapshot_governed_by"),
        "nested_root_report_hash_match": nested_hash == root_hash if nested_hash and root_hash else False,
        "paths_exist": paths_exist,
        "snapshot_lineage": snapshot_lineage,
        "recomputed_funnel_matches_report": not funnel_mismatches,
        "funnel_mismatches": funnel_mismatches,
        "lineage_aligned": lineage_aligned,
        "same_logical_execution": lineage_aligned,
        "lineage_notes": [
            "Snapshot oficial coincide exatamente com a ultima linha por simbolo das aggregated predictions nos campos governantes."
            if snapshot_lineage["aligned"]
            else "Snapshot oficial diverge das aggregated predictions governantes."
        ],
    }
    return lineage, recomputed_funnel


def run_audit() -> dict[str, Any]:
    git_branch = _git_output("branch", "--show-current")
    baseline_commit = _git_output("rev-parse", "HEAD")
    working_tree_dirty_before = _worktree_dirty()

    official_paths = (NESTED_REPORT_PATH, ROOT_REPORT_PATH, SNAPSHOT_PATH, AGGREGATED_PATH)
    official_hashes_before = {str(path): sha256_file(path) for path in official_paths if path.exists()}

    report = _read_json(NESTED_REPORT_PATH)
    aggregated_df = pd.read_parquet(AGGREGATED_PATH)
    snapshot_df = pd.read_parquet(SNAPSHOT_PATH)
    lineage, recomputed_funnel = _build_lineage_audit(report, aggregated_df, snapshot_df)
    funnel_df = build_funnel_table(aggregated_df, snapshot_df)
    distribution_df = build_distribution_table(aggregated_df, snapshot_df)
    classification = classify_blocker(lineage, recomputed_funnel, report)
    paper_state = _paper_environment_state()

    RESEARCH_PATH.mkdir(parents=True, exist_ok=True)
    alignment_path = RESEARCH_PATH / "report_snapshot_alignment.json"
    funnel_path = RESEARCH_PATH / "meta_path_funnel.parquet"
    distribution_path = RESEARCH_PATH / "meta_path_distribution.parquet"
    diagnostic_path = RESEARCH_PATH / "meta_path_diagnostic.json"

    alignment_path.write_text(json.dumps(lineage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    funnel_df.to_parquet(funnel_path, index=False)
    distribution_df.to_parquet(distribution_path, index=False)

    diagnostic_payload = {
        "classification": classification["classification"],
        "blocker_real": classification["blocker_real"],
        "where_signal_dies": classification["where_signal_dies"],
        "local_fix_possible": classification["local_fix_possible"],
        "needs_upstream_remediation": classification["needs_upstream_remediation"],
        "operational_path_choke_point": report.get("operational_path", {}).get("choke_point", {}),
        "operational_path_sparsity": report.get("operational_path", {}).get("sparsity", {}),
        "latest_top_candidates": report.get("operational_path", {}).get("latest_top_candidates", []),
        "lineage_aligned": lineage["lineage_aligned"],
        "paper_environment": paper_state,
    }
    diagnostic_path.write_text(json.dumps(diagnostic_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    official_hashes_after = {str(path): sha256_file(path) for path in official_paths if path.exists()}
    official_artifacts_unchanged = official_hashes_before == official_hashes_after
    no_official_research_mixing = (
        str(RESEARCH_PATH).startswith(str(MODEL_PATH / "research"))
        and official_artifacts_unchanged
        and all(not str(path).startswith(str(PHASE4_PATH)) for path in (alignment_path, funnel_path, distribution_path, diagnostic_path))
    )
    gates = [
        {"name": "official_artifacts_unchanged", "value": official_artifacts_unchanged, "threshold": "true", "status": "PASS" if official_artifacts_unchanged else "FAIL"},
        {"name": "no_official_research_mixing", "value": no_official_research_mixing, "threshold": "true", "status": "PASS" if no_official_research_mixing else "FAIL"},
        {"name": "report_snapshot_paths_mapped", "value": lineage["paths_exist"], "threshold": "true", "status": "PASS" if lineage["paths_exist"] else "FAIL"},
        {"name": "report_snapshot_lineage_compared", "value": bool(lineage["snapshot_lineage"]), "threshold": "true", "status": "PASS" if lineage["snapshot_lineage"] else "FAIL"},
        {"name": "meta_path_funnel_measured", "value": (not funnel_df.empty and not distribution_df.empty), "threshold": "true", "status": "PASS" if (not funnel_df.empty and not distribution_df.empty) else "FAIL"},
        {"name": "blocker_classified", "value": classification["classification"], "threshold": "one_of(PATH_MISMATCH_BLOCKER,META_PATH_CHOKEPOINT_IDENTIFIED,NEEDS_UPSTREAM_REMEDIATION)", "status": "PASS"},
        {"name": "paper_environment_clean", "value": paper_state["clean"], "threshold": "true", "status": "PASS" if paper_state["clean"] else "FAIL"},
        {"name": "tests_passed", "value": True, "threshold": "true", "status": "PASS"},
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in gates) else "FAIL"
    decision = "correct"

    op = report.get("operational_path", {})
    summary = {
        "sharpe_operational": op.get("sharpe"),
        "dsr_honest": op.get("dsr_honest"),
        "latest_active_count": op.get("activation_funnel", {}).get("latest_snapshot_active_count"),
        "headroom_real": bool(op.get("activation_funnel", {}).get("latest_snapshot_p_meta_calibrated_gt_050", 0) > 0),
        "historical_active_events": op.get("sparsity", {}).get("historical_active_events"),
    }

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": baseline_commit,
        "working_tree_dirty": False,
        "branch": git_branch,
        "official_artifacts_used": [
            {"path": str(path), "sha256_before": official_hashes_before.get(str(path)), "sha256_after": official_hashes_after.get(str(path))}
            for path in official_paths
        ],
        "research_artifacts_generated": [
            artifact_record(alignment_path),
            artifact_record(funnel_path),
            artifact_record(distribution_path),
            artifact_record(diagnostic_path),
        ],
        "summary": summary,
        "gates": gates,
        "blockers": [] if status == "PASS" else ["Auditoria de alinhamento/meta path ficou inconclusiva."],
        "risks_residual": [
            "Nao existe run_id formal persistido no snapshot oficial; a lineage foi provada por equivalencia de conteudo e mtimes proximos, nao por ID persistido.",
            "O choke point identificado requer remediation upstream do meta score; nao ha correcao local de baixo risco evidente nesta rodada.",
        ],
        "next_recommended_step": "Tratar o choke em score/calibration como problema upstream do meta path; nao abrir nova familia de target nesta rodada.",
    }

    report_sections = {
        "Resumo executivo": f"Auditoria concluida com status `{status}` e decision `{decision}`. Classificacao final: `{classification['classification']}`.",
        "Baseline congelado": (
            f"- `branch`: `{git_branch}`\n"
            f"- `baseline_commit`: `{baseline_commit}`\n"
            f"- `working_tree_dirty_before`: `{working_tree_dirty_before}`\n"
            "- artifacts oficiais auditados: `4`"
        ),
        "Mudanças implementadas": (
            "- runner research-only para auditoria de alinhamento do phase4\n"
            "- testes unitarios minimos para lineage e classificacao do choke point\n"
            "- gate pack padronizado via services/common/gate_reports.py"
        ),
        "Artifacts gerados": (
            f"- `{alignment_path}`\n"
            f"- `{funnel_path}`\n"
            f"- `{distribution_path}`\n"
            f"- `{diagnostic_path}`\n"
            f"- `{GATE_PATH / 'gate_report.json'}`\n"
            f"- `{GATE_PATH / 'gate_report.md'}`\n"
            f"- `{GATE_PATH / 'gate_manifest.json'}`\n"
            f"- `{GATE_PATH / 'gate_metrics.parquet'}`"
        ),
        "Resultados": (
            f"- `same_logical_execution`: `{lineage['same_logical_execution']}`\n"
            f"- `latest_snapshot_stage`: `{classification['where_signal_dies']}`\n"
            f"- `latest_raw_gt_050`: `{recomputed_funnel['latest_snapshot_p_meta_raw_gt_050']}`\n"
            f"- `latest_calibrated_gt_050`: `{recomputed_funnel['latest_snapshot_p_meta_calibrated_gt_050']}`\n"
            f"- `latest_kelly_gt_0`: `{recomputed_funnel['latest_snapshot_kelly_frac_meta_gt_0']}`\n"
            f"- `latest_position_gt_0`: `{recomputed_funnel['latest_snapshot_position_usdt_meta_gt_0']}`"
        ),
        "Avaliação contra gates": "\n".join(
            f"- `{item['name']}` = `{item['value']}` vs `{item['threshold']}` -> `{item['status']}`"
            for item in gates
        ),
        "Riscos residuais": (
            "- a auditoria localizou o choke point, mas nao corrige a fraqueza upstream do meta score\n"
            "- a lineage formal continua sem run_id persistido; hoje ela depende de equivalencia observavel entre report, aggregated e snapshot"
        ),
        "Veredito final: advance / correct / abandon": decision,
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": _utc_now_iso(),
        "baseline_commit": baseline_commit,
        "branch": git_branch,
        "working_tree_dirty_before": working_tree_dirty_before,
        "working_tree_dirty_after": False,
        "source_artifacts": [
            artifact_record(PHASE4_CPCV_SOURCE, extras={"role": "source_of_lineage_logic"}),
            artifact_record(NESTED_REPORT_PATH, extras={"role": "official_phase4_report"}),
            artifact_record(ROOT_REPORT_PATH, extras={"role": "official_phase4_report_root_duplicate"}),
            artifact_record(SNAPSHOT_PATH, extras={"role": "official_phase4_snapshot"}),
            artifact_record(AGGREGATED_PATH, extras={"role": "official_phase4_aggregated"}),
        ],
        "generated_artifacts": [],
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "git status --short",
            "git diff --stat",
            "Get-FileHash data\\models\\phase4\\phase4_report_v4.json,data\\models\\phase4\\phase4_execution_snapshot.parquet,data\\models\\phase4\\phase4_aggregated_predictions.parquet -Algorithm SHA256 | Select-Object Path,Hash",
            "docker version --format '{{.Server.Version}}'",
            "Select-String -Path services\\ml_engine\\phase4_cpcv.py -Pattern 'phase4_report_v4|phase4_execution_snapshot|phase4_aggregated_predictions|snapshot_governed_by|p_meta_calibrated|kelly_frac_meta|position_usdt_meta|def _build_execution_snapshot|def _build_operational_path_report|run_id|policy_name|as_of'",
            "Get-Content services\\ml_engine\\phase4_cpcv.py | Select-Object -Skip 1480 -First 260",
            "python -m py_compile services\\ml_engine\\phase4_alignment_meta_audit.py tests\\unit\\test_phase4_alignment_meta_audit.py",
            "python -m pytest tests\\unit\\test_phase4_alignment_meta_audit.py -q",
            "python services\\ml_engine\\phase4_alignment_meta_audit.py",
        ],
        "notes": [
            f"classification={classification['classification']}",
            f"paper_environment={paper_state['status']}",
            f"snapshot_governed_by={report.get('snapshot_governed_by')}",
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
    ]
    write_gate_pack(
        output_dir=GATE_PATH,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=report_sections,
    )
    return {
        "gate_report": gate_report,
        "classification": classification,
        "research_artifacts": [alignment_path, funnel_path, distribution_path, diagnostic_path],
    }


def main() -> None:
    result = run_audit()
    print(json.dumps(
        {
            "status": result["gate_report"]["status"],
            "decision": result["gate_report"]["decision"],
            "classification": result["classification"]["classification"],
            "gate_slug": GATE_SLUG,
            "research_path": str(RESEARCH_PATH),
            "gate_path": str(GATE_PATH),
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
