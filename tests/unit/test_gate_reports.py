from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from services.common.gate_reports import (
    GATE_METRICS_REQUIRED_COLUMNS,
    GATE_REPORT_MARKDOWN_SECTIONS,
    render_gate_report_markdown,
    validate_markdown_sections,
    write_gate_pack,
)


def _sample_report() -> dict:
    return {
        "gate_slug": "bootstrap_gates",
        "phase_family": "governance_bootstrap",
        "status": "PASS",
        "decision": "advance",
        "baseline_commit": "abc123",
        "working_tree_dirty": False,
        "branch": "codex/bootstrap-gates",
        "official_artifacts_used": [],
        "research_artifacts_generated": [],
        "summary": {
            "sharpe_operational": None,
            "dsr_honest": None,
            "latest_active_count": None,
            "headroom_real": None,
            "historical_active_events": None,
        },
        "gates": [],
        "blockers": [],
        "risks_residual": [],
        "next_recommended_step": "Use the shared writer in the next gated round.",
    }


def _sample_manifest() -> dict:
    return {
        "gate_slug": "bootstrap_gates",
        "timestamp_utc": "2026-03-26T00:00:00+00:00",
        "baseline_commit": "abc123",
        "branch": "codex/bootstrap-gates",
        "working_tree_dirty_before": False,
        "working_tree_dirty_after": False,
        "source_artifacts": [],
        "generated_artifacts": [],
        "commands_executed": ["pytest -q"],
        "notes": [],
    }


def _sample_sections() -> dict[str, str]:
    return {title: f"Conteudo de teste para {title}." for title in GATE_REPORT_MARKDOWN_SECTIONS}


def test_render_gate_report_markdown_has_required_sections_in_order():
    markdown = render_gate_report_markdown(_sample_sections())
    assert validate_markdown_sections(markdown) is True
    positions = [markdown.index(f"## {title}") for title in GATE_REPORT_MARKDOWN_SECTIONS]
    assert positions == sorted(positions)


def test_write_gate_pack_writes_all_expected_files(tmp_path: Path):
    output_dir = tmp_path / "reports" / "gates" / "bootstrap_gates"
    paths = write_gate_pack(
        output_dir=output_dir,
        gate_report=_sample_report(),
        gate_manifest=_sample_manifest(),
        gate_metrics=[
            {
                "gate_slug": "bootstrap_gates",
                "metric_name": "tests_passed",
                "metric_value": True,
                "metric_threshold": "true",
                "metric_status": "PASS",
            }
        ],
        markdown_sections=_sample_sections(),
    )

    assert set(paths.keys()) == {
        "gate_report_json",
        "gate_report_md",
        "gate_manifest_json",
        "gate_metrics_parquet",
    }
    for path in paths.values():
        assert path.exists()

    report_payload = json.loads(paths["gate_report_json"].read_text(encoding="utf-8"))
    manifest_payload = json.loads(paths["gate_manifest_json"].read_text(encoding="utf-8"))
    markdown = paths["gate_report_md"].read_text(encoding="utf-8")
    metrics = pd.read_parquet(paths["gate_metrics_parquet"])

    assert report_payload["gate_slug"] == "bootstrap_gates"
    assert manifest_payload["gate_slug"] == "bootstrap_gates"
    assert validate_markdown_sections(markdown) is True
    for column in GATE_METRICS_REQUIRED_COLUMNS:
        assert column in metrics.columns
    assert len(manifest_payload["generated_artifacts"]) == 4
