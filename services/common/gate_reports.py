from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def artifact_record(
    path: Path,
    *,
    include_sha256: bool = True,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    file_path = Path(path)
    record: dict[str, Any] = {
        "path": str(file_path),
        "exists": file_path.exists(),
    }
    if file_path.exists():
        stat = file_path.stat()
        record["size_bytes"] = int(stat.st_size)
        record["mtime_utc"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        if include_sha256:
            record["sha256"] = sha256_file(file_path)
    if extras:
        record.update(dict(extras))
    return record


def build_gate_metrics_frame(metrics: list[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(metrics or []))
    for column in GATE_METRICS_REQUIRED_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    frame = frame.loc[:, list(GATE_METRICS_REQUIRED_COLUMNS)].copy()
    for column in ("gate_slug", "metric_name", "metric_status"):
        frame[column] = frame[column].map(lambda value: "" if value is None else str(value))
    for column in ("metric_value", "metric_threshold"):
        frame[column] = frame[column].map(
            lambda value: "N/A"
            if value is None
            else json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list, bool))
            else str(value)
        )
    return frame


def validate_gate_report_schema(report: Mapping[str, Any]) -> list[str]:
    missing = [key for key in GATE_REPORT_REQUIRED_KEYS if key not in report]
    return [f"missing report key: {key}" for key in missing]


def validate_gate_manifest_schema(manifest: Mapping[str, Any]) -> list[str]:
    missing = [key for key in GATE_MANIFEST_REQUIRED_KEYS if key not in manifest]
    return [f"missing manifest key: {key}" for key in missing]


def render_gate_report_markdown(section_bodies: Mapping[str, str]) -> str:
    parts: list[str] = []
    for section in GATE_REPORT_MARKDOWN_SECTIONS:
        body = str(section_bodies.get(section, "") or "").rstrip()
        parts.append(f"## {section}\n")
        if body:
            parts.append(f"{body}\n")
    return "\n".join(parts).strip() + "\n"


def validate_markdown_sections(markdown_text: str) -> bool:
    cursor = 0
    for section in GATE_REPORT_MARKDOWN_SECTIONS:
        token = f"## {section}"
        found = markdown_text.find(token, cursor)
        if found < 0:
            return False
        cursor = found + len(token)
    return True


def write_gate_pack(
    *,
    output_dir: Path,
    gate_report: dict[str, Any],
    gate_manifest: dict[str, Any],
    gate_metrics: list[Mapping[str, Any]],
    markdown_sections: Mapping[str, str],
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_errors = validate_gate_report_schema(gate_report)
    manifest_errors = validate_gate_manifest_schema(gate_manifest)
    if report_errors or manifest_errors:
        messages = report_errors + manifest_errors
        raise ValueError("; ".join(messages))

    metrics_frame = build_gate_metrics_frame(gate_metrics)
    markdown_text = render_gate_report_markdown(markdown_sections)
    if not validate_markdown_sections(markdown_text):
        raise ValueError("markdown sections do not match required section order")

    report_path = output_dir / "gate_report.json"
    markdown_path = output_dir / "gate_report.md"
    manifest_path = output_dir / "gate_manifest.json"
    metrics_path = output_dir / "gate_metrics.parquet"

    _write_json(report_path, gate_report)
    markdown_path.write_text(markdown_text, encoding="utf-8")
    metrics_frame.to_parquet(metrics_path, index=False)

    generated = list(gate_manifest.get("generated_artifacts", []))
    generated.extend(
        [
            artifact_record(report_path),
            artifact_record(markdown_path),
            artifact_record(metrics_path),
            artifact_record(
                manifest_path,
                include_sha256=False,
                extras={"note": "self hash omitted inside manifest to avoid self-reference"},
            ),
        ]
    )
    gate_manifest = dict(gate_manifest)
    gate_manifest["generated_artifacts"] = generated
    _write_json(manifest_path, gate_manifest)

    return {
        "gate_report_json": report_path,
        "gate_report_markdown": markdown_path,
        "gate_manifest_json": manifest_path,
        "gate_metrics_parquet": metrics_path,
    }
