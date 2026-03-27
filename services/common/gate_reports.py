from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

GATE_REPORT_MARKDOWN_SECTIONS = (
    "Resumo executivo",
    "Baseline congelado",
    "Mudanças implementadas",
    "Artifacts gerados",
    "Resultados",
    "Avaliação contra gates",
    "Riscos residuais",
    "Veredito final: advance / correct / abandon",
)

GATE_REPORT_REQUIRED_KEYS = (
    "gate_slug",
    "phase_family",
    "status",
    "decision",
    "baseline_commit",
    "working_tree_dirty",
    "branch",
    "official_artifacts_used",
    "research_artifacts_generated",
    "summary",
    "gates",
    "blockers",
    "risks_residual",
    "next_recommended_step",
)

GATE_MANIFEST_REQUIRED_KEYS = (
    "gate_slug",
    "timestamp_utc",
    "baseline_commit",
    "branch",
    "working_tree_dirty_before",
    "working_tree_dirty_after",
    "source_artifacts",
    "generated_artifacts",
    "commands_executed",
    "notes",
)

GATE_METRICS_REQUIRED_COLUMNS = (
    "gate_slug",
    "metric_name",
    "metric_value",
    "metric_threshold",
    "metric_status",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def artifact_record(path: Path, *, include_sha256: bool = True, extras: Mapping[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(path)}
    if include_sha256 and path.exists() and path.is_file():
        record["sha256"] = sha256_file(path)
    if extras:
        record.update(dict(extras))
    return record


def validate_gate_report_schema(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in GATE_REPORT_REQUIRED_KEYS:
        if key not in report:
            errors.append(f"missing gate_report key: {key}")
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        errors.append("summary must be a mapping")
    else:
        for key in (
            "sharpe_operational",
            "dsr_honest",
            "latest_active_count",
            "headroom_real",
            "historical_active_events",
        ):
            if key not in summary:
                errors.append(f"missing summary key: {key}")
    gates = report.get("gates")
    if not isinstance(gates, list):
        errors.append("gates must be a list")
    return errors


def validate_gate_manifest_schema(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in GATE_MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            errors.append(f"missing gate_manifest key: {key}")
    return errors


def render_gate_report_markdown(section_bodies: Mapping[str, str]) -> str:
    missing = [title for title in GATE_REPORT_MARKDOWN_SECTIONS if title not in section_bodies]
    extra = [title for title in section_bodies if title not in GATE_REPORT_MARKDOWN_SECTIONS]
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing sections: {', '.join(missing)}")
        if extra:
            details.append(f"unexpected sections: {', '.join(extra)}")
        raise ValueError("; ".join(details))

    chunks: list[str] = []
    for title in GATE_REPORT_MARKDOWN_SECTIONS:
        body = str(section_bodies[title]).rstrip()
        chunks.append(f"## {title}\n{body}".rstrip())
    return "\n\n".join(chunks) + "\n"


def validate_markdown_sections(markdown_text: str) -> bool:
    positions: list[int] = []
    for title in GATE_REPORT_MARKDOWN_SECTIONS:
        marker = f"## {title}"
        idx = markdown_text.find(marker)
        if idx < 0:
            return False
        positions.append(idx)
    return positions == sorted(positions)


def build_gate_metrics_frame(metrics: list[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame([dict(item) for item in metrics])
    for column in GATE_METRICS_REQUIRED_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    for column in GATE_METRICS_REQUIRED_COLUMNS:
        frame[column] = frame[column].map(lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else ("" if value is None else str(value)))
    ordered = list(GATE_METRICS_REQUIRED_COLUMNS) + [col for col in frame.columns if col not in GATE_METRICS_REQUIRED_COLUMNS]
    return frame[ordered]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_gate_pack(
    *,
    output_dir: Path,
    gate_report: dict[str, Any],
    gate_manifest: dict[str, Any],
    gate_metrics: list[Mapping[str, Any]],
    markdown_sections: Mapping[str, str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gate_report_path = output_dir / "gate_report.json"
    gate_report_md_path = output_dir / "gate_report.md"
    gate_manifest_path = output_dir / "gate_manifest.json"
    gate_metrics_path = output_dir / "gate_metrics.parquet"

    report_errors = validate_gate_report_schema(gate_report)
    if report_errors:
        raise ValueError("; ".join(report_errors))

    markdown = render_gate_report_markdown(markdown_sections)
    if not validate_markdown_sections(markdown):
        raise ValueError("markdown sections are invalid")

    manifest_errors = validate_gate_manifest_schema(gate_manifest)
    if manifest_errors:
        raise ValueError("; ".join(manifest_errors))

    metrics_frame = build_gate_metrics_frame(gate_metrics)

    _write_json(gate_report_path, gate_report)
    gate_report_md_path.write_text(markdown, encoding="utf-8")
    _write_json(gate_manifest_path, gate_manifest)
    metrics_frame.to_parquet(gate_metrics_path, index=False)

    manifest_generated = [
        artifact_record(gate_report_path),
        artifact_record(gate_report_md_path),
        artifact_record(gate_metrics_path),
        artifact_record(
            gate_manifest_path,
            include_sha256=False,
            extras={"sha256_note": "self hash omitted inside manifest to avoid self-reference"},
        ),
    ]
    gate_manifest["generated_artifacts"] = manifest_generated
    _write_json(gate_manifest_path, gate_manifest)

    return {
        "gate_report_json": gate_report_path,
        "gate_report_md": gate_report_md_path,
        "gate_manifest_json": gate_manifest_path,
        "gate_metrics_parquet": gate_metrics_path,
    }
