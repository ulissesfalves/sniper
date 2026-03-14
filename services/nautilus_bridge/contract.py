from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any
from typing import Mapping


class ContractError(ValueError):
    """Base error for bridge contract failures."""


class SchemaValidationError(ContractError):
    """Raised when the payload schema is invalid."""


class EnvelopeMismatchError(ContractError):
    """Raised when the stream envelope and payload diverge."""


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    if key not in mapping:
        raise SchemaValidationError(f"Missing required field: {key}")
    return mapping[key]


def _ensure_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"Invalid string field: {field_name}")
    return value.strip()


def _to_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise SchemaValidationError(f"Invalid integer field: {field_name}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"Invalid integer field: {field_name}") from exc


def _to_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise SchemaValidationError(f"Invalid numeric field: {field_name}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SchemaValidationError(f"Invalid numeric field: {field_name}") from exc


def _decimal_to_string(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal_to_json(value: Decimal) -> float:
    return float(value)


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    text = _ensure_non_empty_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaValidationError(f"Invalid timestamp field: {field_name}") from exc
    if parsed.tzinfo is None:
        raise SchemaValidationError(f"Timestamp must be timezone aware: {field_name}")
    return parsed.astimezone(UTC)


def _timestamp_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _decode_stream_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def get_stream_field(fields: Mapping[Any, Any], key: str, *, required: bool = True) -> Any:
    if key in fields:
        return fields[key]
    key_bytes = key.encode("utf-8")
    if key_bytes in fields:
        return fields[key_bytes]
    if required:
        raise SchemaValidationError(f"Missing required field: {key}")
    return None


def get_decoded_stream_field(
    fields: Mapping[Any, Any],
    key: str,
    *,
    required: bool = True,
    default: str | None = None,
) -> str | None:
    value = get_stream_field(fields, key, required=required)
    if value is None:
        return default
    return _decode_stream_value(value)


@dataclass(frozen=True)
class CapitalReference:
    currency: str
    notional: Decimal

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapitalReference":
        return cls(
            currency=_ensure_non_empty_string(
                _required(payload, "currency"),
                "capital_reference.currency",
            ),
            notional=_to_decimal(
                _required(payload, "notional"),
                "capital_reference.notional",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "notional": _decimal_to_json(self.notional),
        }


@dataclass(frozen=True)
class RiskEnvelope:
    max_gross_weight: Decimal
    rebalance_band_bps: int | None = None
    min_order_notional_usd: Decimal | None = None
    cash_reserve_weight: Decimal | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RiskEnvelope":
        rebalance_band_bps = payload.get("rebalance_band_bps")
        min_order_notional = payload.get("min_order_notional_usd")
        cash_reserve_weight = payload.get("cash_reserve_weight")
        return cls(
            max_gross_weight=_to_decimal(
                _required(payload, "max_gross_weight"),
                "risk_envelope.max_gross_weight",
            ),
            rebalance_band_bps=(
                _to_int(rebalance_band_bps, "risk_envelope.rebalance_band_bps")
                if rebalance_band_bps is not None
                else None
            ),
            min_order_notional_usd=(
                _to_decimal(min_order_notional, "risk_envelope.min_order_notional_usd")
                if min_order_notional is not None
                else None
            ),
            cash_reserve_weight=(
                _to_decimal(cash_reserve_weight, "risk_envelope.cash_reserve_weight")
                if cash_reserve_weight is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "max_gross_weight": _decimal_to_json(self.max_gross_weight),
        }
        if self.rebalance_band_bps is not None:
            payload["rebalance_band_bps"] = self.rebalance_band_bps
        if self.min_order_notional_usd is not None:
            payload["min_order_notional_usd"] = _decimal_to_json(self.min_order_notional_usd)
        if self.cash_reserve_weight is not None:
            payload["cash_reserve_weight"] = _decimal_to_json(self.cash_reserve_weight)
        return payload


@dataclass(frozen=True)
class TargetAllocation:
    instrument_id: str
    target_weight: Decimal
    symbol: str | None = None
    target_notional_usd: Decimal | None = None
    confidence: Decimal | None = None
    p_meta: Decimal | None = None
    regime: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TargetAllocation":
        return cls(
            instrument_id=_ensure_non_empty_string(
                _required(payload, "instrument_id"),
                "targets.instrument_id",
            ),
            target_weight=_to_decimal(
                _required(payload, "target_weight"),
                "targets.target_weight",
            ),
            symbol=(
                _ensure_non_empty_string(payload.get("symbol"), "targets.symbol")
                if payload.get("symbol") is not None
                else None
            ),
            target_notional_usd=(
                _to_decimal(payload.get("target_notional_usd"), "targets.target_notional_usd")
                if payload.get("target_notional_usd") is not None
                else None
            ),
            confidence=(
                _to_decimal(payload.get("confidence"), "targets.confidence")
                if payload.get("confidence") is not None
                else None
            ),
            p_meta=(
                _to_decimal(payload.get("p_meta"), "targets.p_meta")
                if payload.get("p_meta") is not None
                else None
            ),
            regime=(
                _ensure_non_empty_string(payload.get("regime"), "targets.regime")
                if payload.get("regime") is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instrument_id": self.instrument_id,
            "target_weight": _decimal_to_json(self.target_weight),
        }
        if self.symbol is not None:
            payload["symbol"] = self.symbol
        if self.target_notional_usd is not None:
            payload["target_notional_usd"] = _decimal_to_json(self.target_notional_usd)
        if self.confidence is not None:
            payload["confidence"] = _decimal_to_json(self.confidence)
        if self.p_meta is not None:
            payload["p_meta"] = _decimal_to_json(self.p_meta)
        if self.regime is not None:
            payload["regime"] = self.regime
        return payload


@dataclass(frozen=True)
class SignalPayload:
    portfolio_id: str
    environment: str
    portfolio_revision: int
    signal_version: str
    managed_universe_version: str
    signal_fingerprint: str
    as_of: datetime
    published_at: datetime
    replace_semantics: str
    capital_reference: CapitalReference
    risk_envelope: RiskEnvelope
    targets: tuple[TargetAllocation, ...]
    policy_name: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        validate_fingerprint: bool = True,
    ) -> "SignalPayload":
        targets_payload = _required(payload, "targets")
        if not isinstance(targets_payload, list) or not targets_payload:
            raise SchemaValidationError("targets must be a non-empty list")
        targets = tuple(TargetAllocation.from_dict(item) for item in targets_payload)
        seen_instruments: set[str] = set()
        for target in targets:
            if target.instrument_id in seen_instruments:
                raise SchemaValidationError(f"Duplicate target instrument_id: {target.instrument_id}")
            seen_instruments.add(target.instrument_id)
            if target.target_weight < Decimal("0"):
                raise SchemaValidationError("target_weight must be >= 0")
        instance = cls(
            portfolio_id=_ensure_non_empty_string(
                _required(payload, "portfolio_id"),
                "portfolio_id",
            ),
            environment=_ensure_non_empty_string(
                _required(payload, "environment"),
                "environment",
            ),
            portfolio_revision=_to_int(
                _required(payload, "portfolio_revision"),
                "portfolio_revision",
            ),
            signal_version=_ensure_non_empty_string(
                _required(payload, "signal_version"),
                "signal_version",
            ),
            managed_universe_version=_ensure_non_empty_string(
                _required(payload, "managed_universe_version"),
                "managed_universe_version",
            ),
            signal_fingerprint=_ensure_non_empty_string(
                _required(payload, "signal_fingerprint"),
                "signal_fingerprint",
            ),
            as_of=_parse_timestamp(_required(payload, "as_of"), "as_of"),
            published_at=_parse_timestamp(_required(payload, "published_at"), "published_at"),
            replace_semantics=_ensure_non_empty_string(
                _required(payload, "replace_semantics"),
                "replace_semantics",
            ),
            capital_reference=CapitalReference.from_dict(_required(payload, "capital_reference")),
            risk_envelope=RiskEnvelope.from_dict(_required(payload, "risk_envelope")),
            targets=targets,
            policy_name=(
                _ensure_non_empty_string(payload.get("policy_name"), "policy_name")
                if payload.get("policy_name") is not None
                else None
            ),
            expires_at=(
                _parse_timestamp(payload.get("expires_at"), "expires_at")
                if payload.get("expires_at") is not None
                else None
            ),
            metadata=dict(payload.get("metadata", {})),
        )
        instance.validate(validate_fingerprint=validate_fingerprint)
        return instance

    def validate(self, *, validate_fingerprint: bool = True) -> None:
        if self.portfolio_revision <= 0:
            raise SchemaValidationError("portfolio_revision must be > 0")
        if self.replace_semantics != "FULL_SNAPSHOT":
            raise SchemaValidationError("replace_semantics must be FULL_SNAPSHOT")
        if self.risk_envelope.max_gross_weight <= Decimal("0"):
            raise SchemaValidationError("max_gross_weight must be > 0")
        total_weight = sum((target.target_weight for target in self.targets), Decimal("0"))
        if total_weight > self.risk_envelope.max_gross_weight + Decimal("0.000001"):
            raise SchemaValidationError("Sum of target_weight exceeds max_gross_weight")
        if self.expires_at is not None and self.expires_at < self.published_at:
            raise SchemaValidationError("expires_at must be >= published_at")
        if validate_fingerprint:
            expected = compute_signal_fingerprint(self)
            if self.signal_fingerprint != expected:
                raise SchemaValidationError("signal_fingerprint does not match semantic payload")

    def sorted_targets(self) -> tuple[TargetAllocation, ...]:
        return tuple(sorted(self.targets, key=lambda item: item.instrument_id))

    def with_fingerprint(self) -> "SignalPayload":
        return replace(self, signal_fingerprint=compute_signal_fingerprint(self))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "portfolio_id": self.portfolio_id,
            "environment": self.environment,
            "portfolio_revision": self.portfolio_revision,
            "signal_version": self.signal_version,
            "managed_universe_version": self.managed_universe_version,
            "signal_fingerprint": self.signal_fingerprint,
            "as_of": _timestamp_to_iso(self.as_of),
            "published_at": _timestamp_to_iso(self.published_at),
            "replace_semantics": self.replace_semantics,
            "capital_reference": self.capital_reference.to_dict(),
            "risk_envelope": self.risk_envelope.to_dict(),
            "targets": [target.to_dict() for target in self.sorted_targets()],
        }
        if self.policy_name is not None:
            payload["policy_name"] = self.policy_name
        if self.expires_at is not None:
            payload["expires_at"] = _timestamp_to_iso(self.expires_at)
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class StreamEnvelope:
    message_id: str
    portfolio_id: str
    environment: str
    portfolio_revision: int
    signal_fingerprint: str
    payload_json: str

    def to_stream_fields(self) -> dict[str, str]:
        return {
            "message_id": self.message_id,
            "portfolio_id": self.portfolio_id,
            "environment": self.environment,
            "portfolio_revision": str(self.portfolio_revision),
            "signal_fingerprint": self.signal_fingerprint,
            "payload_json": self.payload_json,
        }


@dataclass(frozen=True)
class StoredSignal:
    envelope: StreamEnvelope
    payload: SignalPayload
    stream_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "envelope": self.envelope.to_stream_fields(),
            "payload": self.payload.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StoredSignal":
        return cls(
            envelope=envelope_from_stream_fields(_required(payload, "envelope")),
            payload=SignalPayload.from_dict(_required(payload, "payload")),
            stream_id=str(payload.get("stream_id")) if payload.get("stream_id") is not None else None,
        )


def load_signal_schema() -> dict[str, Any]:
    schema_path = Path(__file__).with_name("signal.schema.json")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def semantic_payload_for_fingerprint(payload: SignalPayload) -> dict[str, Any]:
    return {
        "portfolio_id": payload.portfolio_id,
        "environment": payload.environment,
        "managed_universe_version": payload.managed_universe_version,
        "replace_semantics": payload.replace_semantics,
        "risk_envelope": {
            "max_gross_weight": _decimal_to_string(payload.risk_envelope.max_gross_weight),
        },
        "targets": [
            {
                "instrument_id": target.instrument_id,
                "target_weight": _decimal_to_string(target.target_weight),
            }
            for target in payload.sorted_targets()
        ],
    }


def compute_signal_fingerprint(payload: SignalPayload) -> str:
    canonical = json.dumps(
        semantic_payload_for_fingerprint(payload),
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_signal_payload(payload: Mapping[str, Any]) -> SignalPayload:
    seed = dict(payload)
    seed.setdefault("signal_fingerprint", "sha256:pending")
    instance = SignalPayload.from_dict(seed, validate_fingerprint=False)
    return instance.with_fingerprint()


def payload_from_json(payload_json: str, *, validate_fingerprint: bool = True) -> SignalPayload:
    try:
        raw_payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError("payload_json is not valid JSON") from exc
    if not isinstance(raw_payload, dict):
        raise SchemaValidationError("payload_json must decode to an object")
    return SignalPayload.from_dict(raw_payload, validate_fingerprint=validate_fingerprint)


def payload_to_json(payload: SignalPayload) -> str:
    return json.dumps(payload.to_dict(), separators=(",", ":"), sort_keys=True)


def generate_message_id() -> str:
    unix_ms = int(time.time() * 1000)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def build_stream_envelope(
    payload: SignalPayload,
    *,
    message_id: str | None = None,
) -> StreamEnvelope:
    message_id_value = message_id or generate_message_id()
    try:
        uuid.UUID(message_id_value)
    except ValueError as exc:
        raise SchemaValidationError("message_id must be a UUID string") from exc
    return StreamEnvelope(
        message_id=message_id_value,
        portfolio_id=payload.portfolio_id,
        environment=payload.environment,
        portfolio_revision=payload.portfolio_revision,
        signal_fingerprint=payload.signal_fingerprint,
        payload_json=payload_to_json(payload),
    )


def envelope_from_stream_fields(fields: Mapping[str, Any]) -> StreamEnvelope:
    envelope = StreamEnvelope(
        message_id=_ensure_non_empty_string(get_decoded_stream_field(fields, "message_id"), "message_id"),
        portfolio_id=_ensure_non_empty_string(
            get_decoded_stream_field(fields, "portfolio_id"),
            "portfolio_id",
        ),
        environment=_ensure_non_empty_string(
            get_decoded_stream_field(fields, "environment"),
            "environment",
        ),
        portfolio_revision=_to_int(
            get_decoded_stream_field(fields, "portfolio_revision"),
            "portfolio_revision",
        ),
        signal_fingerprint=_ensure_non_empty_string(
            get_decoded_stream_field(fields, "signal_fingerprint"),
            "signal_fingerprint",
        ),
        payload_json=_ensure_non_empty_string(
            get_decoded_stream_field(fields, "payload_json"),
            "payload_json",
        ),
    )
    try:
        uuid.UUID(envelope.message_id)
    except ValueError as exc:
        raise SchemaValidationError("message_id must be a UUID string") from exc
    return envelope


def validate_envelope_matches_payload(envelope: StreamEnvelope, payload: SignalPayload) -> None:
    mismatches: list[str] = []
    if envelope.portfolio_id != payload.portfolio_id:
        mismatches.append("portfolio_id")
    if envelope.environment != payload.environment:
        mismatches.append("environment")
    if envelope.portfolio_revision != payload.portfolio_revision:
        mismatches.append("portfolio_revision")
    if envelope.signal_fingerprint != payload.signal_fingerprint:
        mismatches.append("signal_fingerprint")
    if mismatches:
        raise EnvelopeMismatchError(
            "Envelope does not match payload for fields: " + ", ".join(mismatches),
        )
