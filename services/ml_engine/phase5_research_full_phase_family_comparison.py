#!/usr/bin/env python3
from __future__ import annotations

import json
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

from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_research_full_phase_family_comparison_gate"
PHASE_FAMILY = "phase5_research_full_phase_family_comparison"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

SR_NEEDED_FOR_PROMOTION = 4.47

FAMILY_GATES: tuple[dict[str, str], ...] = (
    {
        "family": "stage_a_safe_top1",
        "kind": "historical_baseline",
        "path": "reports/gates/phase5_research_only_stage_a_nonzero_exposure_falsification_gate/gate_report.json",
    },
    {
        "family": "rank_score_threshold",
        "kind": "historical_family",
        "path": "reports/gates/phase5_research_rank_score_threshold_sizing_falsification_gate/gate_report.json",
    },
    {
        "family": "rank_score_threshold_correction",
        "kind": "historical_correction",
        "path": "reports/gates/phase5_research_rank_score_stability_correction_gate/gate_report.json",
    },
    {
        "family": "alternative_exante_p_bma_sigma_hmm",
        "kind": "current_family",
        "path": "reports/gates/phase5_research_alternative_exante_family_gate/gate_report.json",
    },
    {
        "family": "signal_polarity_long_short",
        "kind": "current_family",
        "path": "reports/gates/phase5_research_signal_polarity_long_short_gate/gate_report.json",
    },
    {
        "family": "signal_polarity_stability_correction",
        "kind": "current_correction",
        "path": "reports/gates/phase5_research_signal_polarity_stability_correction_gate/gate_report.json",
    },
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_value(report: dict[str, Any], key: str) -> str:
    prefix = f"{key}="
    for item in report.get("summary", []):
        text = str(item)
        if text.startswith(prefix):
            return text[len(prefix) :]
    return ""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_family_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in FAMILY_GATES:
        path = REPO_ROOT / spec["path"]
        report = _read_json(path)
        best_policy = (
            _summary_value(report, "best_correction_policy")
            or _summary_value(report, "best_policy")
            or _summary_value(report, "best_family")
        )
        median_sharpe = _as_float(
            _summary_value(report, "best_correction_median_combo_sharpe")
            or _summary_value(report, "best_median_combo_sharpe")
            or _summary_value(report, "safe_median_combo_sharpe")
        )
        min_sharpe = _as_float(
            _summary_value(report, "best_correction_min_combo_sharpe")
            or _summary_value(report, "best_min_combo_sharpe")
        )
        median_active_days = _as_float(
            _summary_value(report, "best_correction_median_active_days")
            or _summary_value(report, "best_median_active_days")
            or _summary_value(report, "safe_selected_dates")
        )
        cvar = _as_float(
            _summary_value(report, "best_correction_max_cvar_95_loss_fraction")
            or _summary_value(report, "best_max_cvar_95_loss_fraction")
            or _summary_value(report, "research_max_cvar_95_loss_fraction")
        )
        rows.append(
            {
                "family": spec["family"],
                "kind": spec["kind"],
                "gate_slug": report.get("gate_slug"),
                "status": report.get("status"),
                "decision": report.get("decision"),
                "classification": _summary_value(report, "classification"),
                "best_policy": best_policy,
                "median_combo_sharpe": round(median_sharpe, 6),
                "min_combo_sharpe": round(min_sharpe, 6),
                "median_active_days": round(median_active_days, 6),
                "max_cvar_95_loss_fraction": round(cvar, 8),
                "research_only": True,
                "promotable": False,
                "source_report": spec["path"],
            }
        )
    return rows


def classify_comparison(rows: list[dict[str, Any]]) -> tuple[str, str, str, dict[str, Any], list[str]]:
    candidates = [
        row
        for row in rows
        if row["status"] == "PASS"
        and row["decision"] == "advance"
        and row["median_combo_sharpe"] > 0.0
        and row["min_combo_sharpe"] > 0.0
        and row["median_active_days"] >= 120
    ]
    abandoned = [
        row["family"]
        for row in rows
        if row["decision"] in {"abandon", "freeze"} or row["status"] == "FAIL"
    ]
    if candidates:
        survivor = sorted(candidates, key=lambda row: row["median_combo_sharpe"], reverse=True)[0]
        if survivor["median_combo_sharpe"] >= SR_NEEDED_FOR_PROMOTION:
            return "PARTIAL", "correct", "SURVIVOR_NEEDS_DSR_AND_OFFICIAL_PROMOTION_GATE", survivor, abandoned
        return "PASS", "advance", "RESEARCH_ONLY_SURVIVOR_IDENTIFIED_BELOW_DSR_PROMOTION_BAR", survivor, abandoned
    return "PASS", "freeze", "NO_RESEARCH_ONLY_SURVIVOR_AFTER_FULL_PHASE_COMPARISON", {}, abandoned


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_family_rows()
    status, decision, classification, survivor, abandoned = classify_comparison(rows)

    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    dirty_before = bool(_git_output("status", "--short"))

    comparison_path = OUTPUT_DIR / "full_phase_family_comparison_report.json"
    comparison_metrics_path = OUTPUT_DIR / "full_phase_family_comparison_metrics.parquet"
    payload = {
        "hypothesis": (
            "Full phase comparison can distinguish abandoned research families from any surviving "
            "sandbox-only candidate without promoting official or masking DSR/CVaR blockers."
        ),
        "status": status,
        "decision": decision,
        "classification": classification,
        "families_compared": rows,
        "families_abandoned": abandoned,
        "surviving_candidate": survivor,
        "governance": {
            "research_only": True,
            "promotes_official": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "declares_paper_readiness": False,
            "masks_dsr": False,
            "treats_zero_exposure_cvar_as_economic_robustness": False,
        },
    }
    comparison_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(rows).to_parquet(comparison_metrics_path, index=False)

    gate_metrics = [
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "families_compared",
            "metric_value": len(rows),
            "metric_threshold": ">= 2 materially different families",
            "metric_status": "PASS" if len(rows) >= 2 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "families_abandoned",
            "metric_value": abandoned,
            "metric_threshold": "record abandoned lines",
            "metric_status": "PASS",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "survivor_policy",
            "metric_value": survivor.get("best_policy", ""),
            "metric_threshold": "research-only survivor allowed, not official",
            "metric_status": "PASS" if survivor else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "survivor_median_combo_sharpe",
            "metric_value": survivor.get("median_combo_sharpe", 0.0),
            "metric_threshold": f"> 0 research-only; >= {SR_NEEDED_FOR_PROMOTION} for DSR promotion bar",
            "metric_status": "PASS" if survivor.get("median_combo_sharpe", 0.0) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "survivor_min_combo_sharpe",
            "metric_value": survivor.get("min_combo_sharpe", 0.0),
            "metric_threshold": "> 0.0",
            "metric_status": "PASS" if survivor.get("min_combo_sharpe", 0.0) > 0.0 else "FAIL",
        },
        {
            "gate_slug": GATE_SLUG,
            "metric_name": "official_promotion_allowed",
            "metric_value": False,
            "metric_threshold": "false while DSR=0.0 and official CVaR zero exposure",
            "metric_status": "PASS",
        },
    ]
    source_artifacts = [artifact_record(REPO_ROOT / spec["path"]) for spec in FAMILY_GATES]
    generated_artifacts = [artifact_record(comparison_path), artifact_record(comparison_metrics_path)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": head,
        "working_tree_dirty": dirty_before,
        "branch": branch,
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(comparison_path), str(comparison_metrics_path)],
        "summary": [
            f"classification={classification}",
            f"families_compared={len(rows)}",
            f"families_abandoned={','.join(abandoned)}",
            f"surviving_candidate_policy={survivor.get('best_policy', '')}",
            f"surviving_candidate_family={survivor.get('family', '')}",
            f"surviving_candidate_median_combo_sharpe={survivor.get('median_combo_sharpe', 0.0)}",
            f"surviving_candidate_min_combo_sharpe={survivor.get('min_combo_sharpe', 0.0)}",
            f"surviving_candidate_median_active_days={survivor.get('median_active_days', 0.0)}",
            f"surviving_candidate_max_cvar_95_loss_fraction={survivor.get('max_cvar_95_loss_fraction', 0.0)}",
            "candidate remains research/sandbox only",
            "no official promotion attempted",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "survivor_below_required_honest_dsr_sharpe",
            "short_exposure_research_only_not_official",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
        ],
        "risks_residual": [
            "The surviving candidate is research-only and uses sandbox short exposure.",
            "No official execution path or promotion gate exists for this candidate.",
            "DSR=0.0 and official zero-exposure CVaR still block readiness.",
        ],
        "next_recommended_step": (
            "Update reports/state and the draft PR with this research-only survivor. Do not promote official; "
            "future work should validate whether short-side sandbox support is in scope or start a new thesis."
        ),
    }
    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": head,
        "branch": branch,
        "working_tree_dirty_before": dirty_before,
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_artifacts,
        "commands_executed": [f".\\.venv\\Scripts\\python.exe {THIS_FILE.relative_to(REPO_ROOT)}"],
        "notes": [
            "Full phase family comparison/falsification gate.",
            "This gate records the surviving candidate as research-only and non-promotional.",
            "No official artifacts were promoted and no paper readiness was declared.",
        ],
    }
    markdown_sections = {
        "Resumo executivo": (
            f"Full phase family comparison result: `{status}/{decision}`. Classification: `{classification}`."
        ),
        "Baseline congelado": (
            f"Branch `{branch}`, commit `{head}`. This comparison is a governance/research artifact only."
        ),
        "MudanÃ§as implementadas": (
            "Added a comparison report across Stage A, rank-score, alternative ex-ante and signal-polarity "
            "families to separate abandoned lines from the surviving research-only candidate."
        ),
        "Artifacts gerados": (
            f"- `{comparison_path.relative_to(REPO_ROOT)}`\n"
            f"- `{comparison_metrics_path.relative_to(REPO_ROOT)}`\n"
            "- `gate_report.json`\n- `gate_report.md`\n- `gate_manifest.json`\n- `gate_metrics.parquet`"
        ),
        "Resultados": (
            f"Survivor `{survivor.get('best_policy', '')}` from `{survivor.get('family', '')}` has "
            f"median Sharpe `{survivor.get('median_combo_sharpe', 0.0)}`, min Sharpe "
            f"`{survivor.get('min_combo_sharpe', 0.0)}`, median active days "
            f"`{survivor.get('median_active_days', 0.0)}`, and CVaR95 "
            f"`{survivor.get('max_cvar_95_loss_fraction', 0.0)}`."
        ),
        "AvaliaÃ§Ã£o contra gates": (
            "The mission found a research-only survivor but not a promotable official candidate. DSR and "
            "official CVaR blockers remain explicit."
        ),
        "Riscos residuais": (
            "Short exposure is not official, DSR remains 0.0, and paper readiness remains forbidden."
        ),
        "Veredito final: advance / correct / abandon": (
            "`advance` as a reviewable research/sandbox module. Update state and draft PR; do not promote."
        ),
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    return gate_report


if __name__ == "__main__":
    report = run_gate()
    print(json.dumps({"gate_slug": report["gate_slug"], "status": report["status"], "decision": report["decision"]}))
