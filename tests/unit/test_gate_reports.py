from __future__ import annotations

import json

import pandas as pd

from services.common.gate_reports import (
    GATE_MANIFEST_REQUIRED_KEYS,
    GATE_METRICS_REQUIRED_COLUMNS,
    GATE_REPORT_MARKDOWN_SECTIONS,
    GATE_REPORT_REQUIRED_KEYS,
    validate_markdown_sections,
    write_gate_pack,
)


def test_write_gate_pack_materializes_required_outputs(tmp_path):
    gate_report = {key: None for key in GATE_REPORT_REQUIRED_KEYS}
    gate_report.update(
        {
            "gate_slug": "phase5_stage_a3_spec_hardening",
            "phase_family": "phase5_stage_a3_spec_hardening",
            "status": "PARTIAL",
            "decision": "correct",
            "baseline_commit": "abc123",
            "working_tree_dirty": False,
            "branch": "main",
            "official_artifacts_used": [],
            "research_artifacts_generated": [],
            "summary": [],
            "gates": [],
            "blockers": [],
            "risks_residual": [],
            "next_recommended_step": "research-only correction",
        }
    )
    gate_manifest = {key: [] if key in {"source_artifacts", "generated_artifacts", "commands_executed", "notes"} else None for key in GATE_MANIFEST_REQUIRED_KEYS}
    gate_manifest.update(
        {
            "gate_slug": "phase5_stage_a3_spec_hardening",
            "timestamp_utc": "2026-03-29T00:00:00+00:00",
            "baseline_commit": "abc123",
            "branch": "main",
            "working_tree_dirty_before": False,
            "working_tree_dirty_after": False,
        }
    )
    gate_metrics = [
        {
            "gate_slug": "phase5_stage_a3_spec_hardening",
            "metric_name": "positive_rate_oos",
            "metric_value": 0.07,
            "metric_threshold": ">= 0.05",
            "metric_status": "PASS",
        }
    ]
    markdown_sections = {section: f"body {idx}" for idx, section in enumerate(GATE_REPORT_MARKDOWN_SECTIONS)}

    outputs = write_gate_pack(
        output_dir=tmp_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )

    assert set(outputs.keys()) == {
        "gate_report_json",
        "gate_report_markdown",
        "gate_manifest_json",
        "gate_metrics_parquet",
    }
    assert all(path.exists() for path in outputs.values())
    markdown_text = outputs["gate_report_markdown"].read_text(encoding="utf-8")
    assert validate_markdown_sections(markdown_text) is True
    manifest = json.loads(outputs["gate_manifest_json"].read_text(encoding="utf-8"))
    assert len(manifest["generated_artifacts"]) == 4
    metrics_df = pd.read_parquet(outputs["gate_metrics_parquet"])
    assert metrics_df.columns.tolist() == list(GATE_METRICS_REQUIRED_COLUMNS)
