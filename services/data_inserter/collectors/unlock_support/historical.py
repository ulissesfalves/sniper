from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

from pypdf import PdfReader

from .providers import AssetDescriptor, WaybackCapture, build_official_url_candidates
from .utils import (
    ParsedDistributionItem,
    ParsedUnlockEvent,
    compute_insider_share,
    compute_unknown_bucket_ratio,
    normalize_bucket_label,
    parse_date_like,
    safe_float,
)

DATE_HEADERS = {"date", "unlock date", "release date", "vesting date", "event date"}
LABEL_HEADERS = {"label", "bucket", "allocation", "category", "group", "recipient", "allocation name"}
TOKEN_HEADERS = {"tokens", "amount", "token amount", "unlock amount", "qty", "quantity"}
PCT_HEADERS = {"pct", "percentage", "percent", "%", "share"}


@dataclass(slots=True)
class ParsedDocument:
    events: list[ParsedUnlockEvent]
    distribution_items: list[ParsedDistributionItem]
    parse_score: float
    is_structured: bool
    parser_name: str
    total_supply_hint: float | None = None


@dataclass(slots=True)
class ReconstructionValidation:
    source_score: float
    parse_score: float
    cross_source_agreement: float
    supply_consistency: float
    time_consistency: float
    unknown_bucket_ratio: float
    quality_flag: str

    @property
    def confidence(self) -> float:
        return float(
            0.35 * self.source_score
            + 0.20 * self.parse_score
            + 0.20 * self.cross_source_agreement
            + 0.15 * self.supply_consistency
            + 0.10 * self.time_consistency
        )


class SimpleHTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._cell_parts: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._cell_parts = []
            self._in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell and self._current_row is not None:
            self._current_row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = []
            self._in_cell = False
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


def discover_official_urls(details: dict[str, Any] | None, asset: AssetDescriptor, seen_ts: str) -> list[dict[str, Any]]:
    urls = build_official_url_candidates(details)
    rows: list[dict[str, Any]] = []
    for priority, row in enumerate(urls, start=1):
        rows.append(
            {
                "asset_id": asset.asset_id,
                "url": row["url"],
                "url_type": row["url_type"],
                "domain": row["domain"],
                "is_official": row["is_official"],
                "source_priority": priority,
                "active_flag": 1,
                "first_seen_ts": seen_ts,
                "last_seen_ts": seen_ts,
            }
        )
    return rows


def choose_best_capture(captures: list[WaybackCapture], as_of_date: date) -> WaybackCapture | None:
    eligible = [capture for capture in captures if capture.capture_dt.date() <= as_of_date]
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (
            item.capture_dt.date(),
            1 if "html" in (item.mime_type or "").lower() else 0,
            item.capture_dt,
        )
    )
    return eligible[-1]


def deterministic_parse(content: bytes, mime_type: str, source_url: str) -> ParsedDocument:
    lowered_mime = (mime_type or "").lower()
    lowered_url = source_url.lower()
    if "json" in lowered_mime or lowered_url.endswith(".json"):
        return _parse_json(content)
    if "csv" in lowered_mime or lowered_url.endswith(".csv"):
        return _parse_csv(content)
    if "pdf" in lowered_mime or lowered_url.endswith(".pdf"):
        return _parse_pdf(content)
    if "html" in lowered_mime or lowered_url.endswith(".html") or lowered_url.endswith(".htm"):
        return _parse_html(content)
    return _parse_text(content)


def validate_reconstruction(
    capture: WaybackCapture,
    parsed: ParsedDocument,
    as_of_date: date,
    circ_hint: float | None,
) -> ReconstructionValidation:
    unknown_ratio = compute_unknown_bucket_ratio(parsed.events)
    if capture.capture_dt.date() > as_of_date:
        return ReconstructionValidation(
            source_score=0.0,
            parse_score=0.0,
            cross_source_agreement=0.0,
            supply_consistency=0.0,
            time_consistency=0.0,
            unknown_bucket_ratio=unknown_ratio,
            quality_flag="lookahead_rejected",
        )

    source_score = 1.0 if _is_official_capture(capture) else 0.75
    parse_score = parsed.parse_score if parsed.events else 0.0
    cross_source_agreement = 0.5
    total_tokens = sum(max(event.tokens, 0.0) for event in parsed.events)
    if circ_hint and circ_hint > 0 and total_tokens <= circ_hint * 100.0:
        supply_consistency = 1.0
    elif total_tokens > 0:
        supply_consistency = 0.5
    else:
        supply_consistency = 0.0
    quality_flag = "ok"
    if unknown_ratio > 0.15:
        quality_flag = "review_required"
    if not parsed.is_structured:
        quality_flag = "unstructured_payload"
    return ReconstructionValidation(
        source_score=source_score,
        parse_score=parse_score,
        cross_source_agreement=cross_source_agreement,
        supply_consistency=supply_consistency,
        time_consistency=1.0,
        unknown_bucket_ratio=unknown_ratio,
        quality_flag=quality_flag,
    )


def build_normalized_event_rows(
    asset_id: str,
    as_of_date: date,
    parsed: ParsedDocument,
    source_type: str,
    source_url: str,
    confidence: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in parsed.events:
        if event.event_date <= as_of_date:
            continue
        rows.append(
            {
                "asset_id": asset_id,
                "as_of_date": as_of_date.isoformat(),
                "event_date": event.event_date.isoformat(),
                "raw_label": event.raw_label,
                "bucket": event.bucket,
                "tokens": event.tokens,
                "source_type": source_type,
                "source_url": source_url,
                "confidence": confidence,
            }
        )
    return rows


def compute_reconstructed_insider_share(parsed: ParsedDocument) -> float | None:
    return compute_insider_share(parsed.distribution_items)


def _is_official_capture(capture: WaybackCapture) -> bool:
    if "://" not in capture.original_url:
        return False
    domain = re.sub(r"^www\.", "", capture.original_url.split("/")[2].lower())
    return not any(domain.endswith(suffix) for suffix in ("medium.com", "mirror.xyz", "substack.com"))


def _parse_json(content: bytes) -> ParsedDocument:
    try:
        payload = json.loads(content.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return ParsedDocument([], [], 0.0, False, "json_invalid")
    rows = _extract_rows_from_json(payload)
    return ParsedDocument(
        events=_rows_to_events(rows),
        distribution_items=_rows_to_distribution(rows),
        parse_score=1.0 if rows else 0.30,
        is_structured=bool(rows),
        parser_name="json_v1",
        total_supply_hint=_extract_total_supply_hint(rows),
    )


def _parse_csv(content: bytes) -> ParsedDocument:
    text = content.decode("utf-8", errors="ignore")
    rows = list(csv.DictReader(io.StringIO(text)))
    return ParsedDocument(
        events=_rows_to_events(rows),
        distribution_items=_rows_to_distribution(rows),
        parse_score=1.0 if rows else 0.30,
        is_structured=bool(rows),
        parser_name="csv_v1",
        total_supply_hint=_extract_total_supply_hint(rows),
    )


def _parse_html(content: bytes) -> ParsedDocument:
    parser = SimpleHTMLTableParser()
    parser.feed(content.decode("utf-8", errors="ignore"))
    rows: list[dict[str, Any]] = []
    for table in parser.tables:
        rows.extend(_table_to_dicts(table))
    return ParsedDocument(
        events=_rows_to_events(rows),
        distribution_items=_rows_to_distribution(rows),
        parse_score=1.0 if rows else 0.30,
        is_structured=bool(rows),
        parser_name="html_table_v1",
        total_supply_hint=_extract_total_supply_hint(rows),
    )


def _parse_pdf(content: bytes) -> ParsedDocument:
    if not content.lstrip().startswith(b"%PDF-"):
        return ParsedDocument([], [], 0.0, False, "pdf_invalid")
    text_parts: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(content))
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
    except Exception:
        return ParsedDocument([], [], 0.0, False, "pdf_invalid")
    rows = _rows_from_text("\n".join(text_parts))
    return ParsedDocument(
        events=_rows_to_events(rows),
        distribution_items=_rows_to_distribution(rows),
        parse_score=0.70 if rows else 0.30,
        is_structured=bool(rows),
        parser_name="pdf_v1",
        total_supply_hint=_extract_total_supply_hint(rows),
    )


def _parse_text(content: bytes) -> ParsedDocument:
    rows = _rows_from_text(content.decode("utf-8", errors="ignore"))
    return ParsedDocument(
        events=_rows_to_events(rows),
        distribution_items=_rows_to_distribution(rows),
        parse_score=0.30 if rows else 0.0,
        is_structured=bool(rows),
        parser_name="text_v1",
        total_supply_hint=_extract_total_supply_hint(rows),
    )


def _rows_from_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<date>\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}).{0,40}?(?P<label>[A-Za-z][A-Za-z /_-]{2,80}).{0,20}?(?P<tokens>\d[\d,\.]*)"
    )
    for match in pattern.finditer(text):
        rows.append(
            {
                "date": match.group("date"),
                "label": match.group("label"),
                "tokens": match.group("tokens").replace(",", ""),
            }
        )
    return rows


def _extract_rows_from_json(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            rows.extend(_extract_rows_from_json(item))
    elif isinstance(payload, dict):
        if any(key.lower() in {"date", "event_date", "unlockdate", "unlock_date"} for key in payload):
            rows.append(payload)
        for value in payload.values():
            rows.extend(_extract_rows_from_json(value))
    return rows


def _table_to_dicts(table: list[list[str]]) -> list[dict[str, Any]]:
    if len(table) < 2:
        return []
    header = [cell.strip().lower() for cell in table[0]]
    rows: list[dict[str, Any]] = []
    for row in table[1:]:
        if len(row) != len(header):
            continue
        rows.append({header[idx]: value for idx, value in enumerate(row)})
    return rows


def _rows_to_events(rows: list[dict[str, Any]]) -> list[ParsedUnlockEvent]:
    events: list[ParsedUnlockEvent] = []
    for row in rows:
        event_date = None
        raw_label = None
        token_value = None
        pct_value = None
        total_supply_hint = safe_float(
            row.get("total_supply") or row.get("max_supply") or row.get("fully diluted supply")
        )
        for key, value in row.items():
            key_norm = str(key).strip().lower()
            if key_norm in DATE_HEADERS or "date" in key_norm:
                event_date = parse_date_like(value)
            elif key_norm in LABEL_HEADERS or any(fragment in key_norm for fragment in ("allocation", "bucket", "category")):
                raw_label = str(value)
            elif key_norm in TOKEN_HEADERS or "token" in key_norm or "amount" in key_norm:
                token_value = safe_float(str(value).replace(",", ""))
            elif key_norm in PCT_HEADERS:
                pct_value = safe_float(str(value).replace("%", "").replace(",", ""))
        if event_date is None or raw_label is None:
            continue
        tokens = token_value
        if tokens is None and pct_value is not None and total_supply_hint and total_supply_hint > 0:
            pct_fraction = pct_value / 100.0 if pct_value > 1 else pct_value
            tokens = pct_fraction * total_supply_hint
        if tokens is None or tokens < 0:
            continue
        events.append(
            ParsedUnlockEvent(
                event_date=event_date,
                raw_label=raw_label,
                bucket=normalize_bucket_label(raw_label),
                tokens=float(tokens),
            )
        )
    return events


def _rows_to_distribution(rows: list[dict[str, Any]]) -> list[ParsedDistributionItem]:
    items: list[ParsedDistributionItem] = []
    for row in rows:
        raw_label = None
        value = None
        for key, cell in row.items():
            key_norm = str(key).strip().lower()
            if key_norm in LABEL_HEADERS or any(fragment in key_norm for fragment in ("allocation", "bucket", "category")):
                raw_label = str(cell)
            elif key_norm in PCT_HEADERS or any(fragment in key_norm for fragment in ("token", "amount", "share")):
                value = safe_float(str(cell).replace("%", "").replace(",", ""))
        if raw_label is None or value is None:
            continue
        if value > 1.0 and value <= 100.0:
            value = value / 100.0
        items.append(
            ParsedDistributionItem(
                raw_label=raw_label,
                bucket=normalize_bucket_label(raw_label),
                value=float(value),
            )
        )
    return items


def _extract_total_supply_hint(rows: list[dict[str, Any]]) -> float | None:
    hints: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("total_supply", "max_supply", "fully diluted supply", "fully_diluted_supply"):
            hint = safe_float(row.get(key))
            if hint is not None and hint > 0:
                hints.append(float(hint))
    if not hints:
        return None
    return max(hints)
