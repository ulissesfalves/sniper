from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta

from services.nautilus_bridge.acceptance import AcceptanceContext
from services.nautilus_bridge.acceptance import evaluate_acceptance
from services.nautilus_bridge.acceptance import evaluate_deferred_signal
from services.nautilus_bridge.contract import build_signal_payload


MANAGED = frozenset({"ADAUSDT.BINANCE_SPOT", "SOLUSDT.BINANCE_SPOT"})


def _payload(revision: int = 2) -> dict:
    return {
        "portfolio_id": "sniper-paper",
        "environment": "paper",
        "portfolio_revision": revision,
        "signal_version": "sniper.portfolio_target.v1",
        "managed_universe_version": "calibration.v1",
        "as_of": "2026-03-13T00:00:00Z",
        "published_at": "2026-03-13T12:00:00Z",
        "replace_semantics": "FULL_SNAPSHOT",
        "capital_reference": {"currency": "USD", "notional": 200000.0},
        "risk_envelope": {"max_gross_weight": 0.98},
        "targets": [
            {"instrument_id": "ADAUSDT.BINANCE_SPOT", "target_weight": 0.03},
            {"instrument_id": "SOLUSDT.BINANCE_SPOT", "target_weight": 0.04}
        ]
    }


def _context(**overrides: object) -> AcceptanceContext:
    params = {
        "managed_instruments": MANAGED,
        "last_revision_accepted": 1,
        "last_accepted_fingerprint": "sha256:old",
        "now": datetime(2026, 3, 13, 12, 1, tzinfo=UTC),
        "max_signal_age": timedelta(hours=12),
    }
    params.update(overrides)
    return AcceptanceContext(**params)


def test_accepts_newer_revision() -> None:
    decision = evaluate_acceptance(build_signal_payload(_payload()), _context())
    assert decision.accepted


def test_rejects_duplicate_revision_with_same_fingerprint() -> None:
    payload = build_signal_payload(_payload(revision=3))
    decision = evaluate_acceptance(
        payload,
        _context(last_revision_accepted=3, last_accepted_fingerprint=payload.signal_fingerprint),
    )
    assert decision.status == "rejected_duplicate"


def test_rejects_out_of_order_revision() -> None:
    decision = evaluate_acceptance(build_signal_payload(_payload(revision=2)), _context(last_revision_accepted=3))
    assert decision.status == "rejected_out_of_order"


def test_rejects_revision_conflict() -> None:
    decision = evaluate_acceptance(
        build_signal_payload(_payload(revision=3)),
        _context(last_revision_accepted=3, last_accepted_fingerprint="sha256:other"),
    )
    assert decision.status == "rejected_revision_conflict"


def test_rejects_stale_payload() -> None:
    stale_payload = build_signal_payload(
        {
            **_payload(),
            "published_at": "2026-03-12T00:00:00Z",
        },
    )
    decision = evaluate_acceptance(stale_payload, _context(max_signal_age=timedelta(hours=1)))
    assert decision.status == "rejected_stale"


def test_rejects_incomplete_snapshot() -> None:
    incomplete = build_signal_payload({**_payload(), "targets": _payload()["targets"][:1]})
    decision = evaluate_acceptance(incomplete, _context())
    assert decision.status == "rejected_incomplete_snapshot"


def test_deferred_signal_is_superseded_by_newer_revision() -> None:
    decision = evaluate_deferred_signal(deferred_revision=2, last_revision_accepted=3)
    assert decision.status == "rejected_superseded"
    assert decision.should_clear_deferred
