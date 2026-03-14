from __future__ import annotations

from services.nautilus_bridge.contract import EnvelopeMismatchError
from services.nautilus_bridge.contract import build_signal_payload
from services.nautilus_bridge.contract import build_stream_envelope
from services.nautilus_bridge.contract import compute_signal_fingerprint
from services.nautilus_bridge.contract import envelope_from_stream_fields
from services.nautilus_bridge.contract import payload_from_json
from services.nautilus_bridge.contract import validate_envelope_matches_payload
from services.nautilus_bridge.status import StatusEvent


def _base_payload() -> dict:
    return {
        "portfolio_id": "sniper-paper",
        "environment": "paper",
        "portfolio_revision": 7,
        "signal_version": "sniper.portfolio_target.v1",
        "managed_universe_version": "calibration.v1",
        "as_of": "2026-03-13T00:00:00Z",
        "published_at": "2026-03-13T12:00:00Z",
        "replace_semantics": "FULL_SNAPSHOT",
        "capital_reference": {"currency": "USD", "notional": 200000.0},
        "risk_envelope": {
            "max_gross_weight": 0.98,
            "rebalance_band_bps": 25,
            "min_order_notional_usd": 10.0,
        },
        "targets": [
            {"instrument_id": "SOLUSDT.BINANCE_SPOT", "symbol": "SOL", "target_weight": 0.08},
            {"instrument_id": "ADAUSDT.BINANCE_SPOT", "symbol": "ADA", "target_weight": 0.02},
        ],
        "metadata": {"source": "unit-test"},
    }


def test_signal_fingerprint_ignores_operational_fields() -> None:
    payload_a = build_signal_payload(_base_payload())
    payload_b = build_signal_payload(
        {
            **_base_payload(),
            "published_at": "2026-03-13T14:00:00Z",
            "metadata": {"source": "different"},
        },
    )
    assert payload_a.signal_fingerprint == payload_b.signal_fingerprint
    assert payload_a.signal_fingerprint == compute_signal_fingerprint(payload_a)


def test_signal_fingerprint_sorts_targets_deterministically() -> None:
    original = _base_payload()
    reversed_targets = {**_base_payload(), "targets": list(reversed(_base_payload()["targets"]))}
    assert build_signal_payload(original).signal_fingerprint == build_signal_payload(
        reversed_targets,
    ).signal_fingerprint


def test_payload_round_trip_validates_fingerprint() -> None:
    payload = build_signal_payload(_base_payload())
    decoded = payload_from_json(build_stream_envelope(payload).payload_json)
    assert decoded.signal_fingerprint == payload.signal_fingerprint


def test_payload_json_does_not_contain_message_id() -> None:
    payload = build_signal_payload(_base_payload())
    envelope = build_stream_envelope(payload)
    assert '"message_id"' not in envelope.payload_json


def test_envelope_from_stream_fields_accepts_bytes_keys_and_values() -> None:
    payload = build_signal_payload(_base_payload())
    envelope = build_stream_envelope(payload)
    raw_fields = {
        key.encode("utf-8"): value.encode("utf-8")
        for key, value in envelope.to_stream_fields().items()
    }
    decoded = envelope_from_stream_fields(raw_fields)
    assert decoded.message_id == envelope.message_id
    assert decoded.portfolio_id == envelope.portfolio_id
    assert decoded.environment == envelope.environment
    assert decoded.signal_fingerprint == envelope.signal_fingerprint


def test_envelope_payload_mismatch_is_rejected() -> None:
    payload = build_signal_payload(_base_payload())
    envelope = build_stream_envelope(payload)
    mismatched = build_signal_payload({**_base_payload(), "environment": "prod"})
    try:
        validate_envelope_matches_payload(envelope, mismatched)
    except EnvelopeMismatchError as exc:
        assert "environment" in str(exc)
    else:
        raise AssertionError("Expected EnvelopeMismatchError")


def test_status_event_serializes_bytes_as_plain_strings() -> None:
    event = StatusEvent(
        status="received",
        message_id=b"message-123",  # type: ignore[arg-type]
        portfolio_id=b"portfolio-1",  # type: ignore[arg-type]
        environment=b"paper",  # type: ignore[arg-type]
        portfolio_revision=7,
        signal_fingerprint=b"sha256:" + b"a" * 64,  # type: ignore[arg-type]
        stream_id=b"1741980000000-0",  # type: ignore[arg-type]
    )
    fields = event.to_stream_fields()
    assert fields["message_id"] == "message-123"
    assert fields["portfolio_id"] == "portfolio-1"
    assert fields["environment"] == "paper"
    assert fields["signal_fingerprint"] == "sha256:" + ("a" * 64)
    assert fields["stream_id"] == "1741980000000-0"
    assert "b'" not in fields["message_id"]
    assert "b'" not in fields["portfolio_id"]
