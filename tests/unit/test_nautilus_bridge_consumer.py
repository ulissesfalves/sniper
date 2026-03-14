from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.config import ManagedUniverse
from services.nautilus_bridge.consumer import RedisSignalConsumer
from services.nautilus_bridge.consumer import SignalApplyResult
from services.nautilus_bridge.contract import build_signal_payload
from services.nautilus_bridge.contract import build_stream_envelope
from services.nautilus_bridge.contract import StoredSignal


class FakeRedis:
    def __init__(self, response):
        self._response = response

    async def xread(self, *_args, **_kwargs):
        return self._response


@dataclass
class FakeStateStore:
    cursor: str = "$"
    last_stream_cursor: str | None = None
    last_accepted_signal: object | None = None
    last_applied_signal: object | None = None
    deferred_signal: object | None = None
    deferred_cleared: tuple[str, str] | None = None
    last_revision_accepted: int | None = None
    last_accepted_fingerprint: str | None = None

    async def get_stream_cursor(self) -> str:
        return self.cursor

    async def set_stream_cursor(self, stream_id: str) -> None:
        self.last_stream_cursor = stream_id

    async def get_last_revision_accepted(self, *_args) -> int | None:
        return self.last_revision_accepted

    async def get_last_accepted_fingerprint(self, *_args) -> str | None:
        return self.last_accepted_fingerprint

    async def set_last_accepted_target(self, signal) -> None:
        self.last_accepted_signal = signal
        self.last_revision_accepted = signal.payload.portfolio_revision
        self.last_accepted_fingerprint = signal.payload.signal_fingerprint

    async def set_last_applied_target(self, signal) -> None:
        self.last_applied_signal = signal

    async def set_deferred_target(self, signal) -> None:
        self.deferred_signal = signal

    async def get_deferred_target(self, *_args):
        return self.deferred_signal

    async def clear_deferred_target(self, portfolio_id: str, environment: str) -> None:
        self.deferred_cleared = (portfolio_id, environment)
        self.deferred_signal = None


@dataclass
class FakeStatusPublisher:
    events: list[dict]

    async def publish_raw(self, **kwargs) -> str:
        self.events.append(kwargs)
        return "status-1"

    async def publish_for_signal(self, *, status: str, signal, details=None) -> str:
        self.events.append(
            {
                "status": status,
                "stream_id": signal.stream_id,
                "portfolio_revision": signal.payload.portfolio_revision,
                "details": details or {},
            },
        )
        return "status-2"


def _payload() -> dict:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "portfolio_id": "sniper-paper",
        "environment": "paper",
        "portfolio_revision": 1,
        "signal_version": "sniper.portfolio_target.v1",
        "managed_universe_version": "calibration.v1",
        "as_of": now,
        "published_at": now,
        "replace_semantics": "FULL_SNAPSHOT",
        "capital_reference": {"currency": "USD", "notional": 200000.0},
        "risk_envelope": {"max_gross_weight": 0.98},
        "targets": [
            {"instrument_id": "ADAUSDT.BINANCE_SPOT", "target_weight": 0.03},
            {"instrument_id": "SOLUSDT.BINANCE_SPOT", "target_weight": 0.04},
        ],
    }


def _managed_universe() -> ManagedUniverse:
    return ManagedUniverse(
        version="calibration.v1",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        instruments_by_symbol={
            "ADA": "ADAUSDT.BINANCE_SPOT",
            "SOL": "SOLUSDT.BINANCE_SPOT",
        },
    )


def test_consumer_normalizes_stream_id_from_xread_bytes() -> None:
    payload = build_signal_payload(_payload())
    envelope = build_stream_envelope(payload)
    fields = {
        key.encode("utf-8"): value.encode("utf-8")
        for key, value in envelope.to_stream_fields().items()
    }
    stream_id = b"1773505624555-0"
    redis = FakeRedis([(b"sniper:portfolio_targets:v1", [(stream_id, fields)])])
    state_store = FakeStateStore()
    status_publisher = FakeStatusPublisher(events=[])
    consumer = RedisSignalConsumer(
        redis=redis,
        config=BridgeConfig(),
        managed_universe=_managed_universe(),
        state_store=state_store,
        status_publisher=status_publisher,
        accepted_handler=_accepted_handler,
    )

    result = asyncio.run(consumer.consume_once(block_ms=1))

    assert result is True
    assert state_store.last_stream_cursor == "1773505624555-0"
    assert state_store.last_accepted_signal.stream_id == "1773505624555-0"
    assert state_store.last_applied_signal.stream_id == "1773505624555-0"
    assert [event["status"] for event in status_publisher.events] == [
        "received",
        "accepted",
        "noop_band",
    ]
    assert all(event["stream_id"] == "1773505624555-0" for event in status_publisher.events)
    assert all("b'" not in event["stream_id"] for event in status_publisher.events)


def test_consumer_stores_deferred_target_and_does_not_commit_cursor() -> None:
    payload = build_signal_payload(_payload())
    envelope = build_stream_envelope(payload)
    fields = {
        key.encode("utf-8"): value.encode("utf-8")
        for key, value in envelope.to_stream_fields().items()
    }
    redis = FakeRedis([(b"sniper:portfolio_targets:v1", [(b"1773505624555-0", fields)])])
    state_store = FakeStateStore()
    status_publisher = FakeStatusPublisher(events=[])
    consumer = RedisSignalConsumer(
        redis=redis,
        config=BridgeConfig(),
        managed_universe=_managed_universe(),
        state_store=state_store,
        status_publisher=status_publisher,
        accepted_handler=_deferred_handler,
    )

    result = asyncio.run(consumer.consume_once(block_ms=1))

    assert result is True
    assert [event["status"] for event in status_publisher.events] == [
        "received",
        "accepted",
        "deferred_not_ready",
    ]
    assert state_store.deferred_signal is not None
    assert state_store.last_stream_cursor == "1773505624555-0"
    assert state_store.last_applied_signal is None


def test_process_deferred_target_rejects_superseded_revision() -> None:
    payload = build_signal_payload({**_payload(), "portfolio_revision": 2})
    deferred_signal = _stored_signal(payload, "1773505624555-0")
    state_store = FakeStateStore(
        deferred_signal=deferred_signal,
        last_revision_accepted=3,
        last_accepted_fingerprint="sha256:newer",
    )
    status_publisher = FakeStatusPublisher(events=[])
    consumer = RedisSignalConsumer(
        redis=FakeRedis([]),
        config=BridgeConfig(),
        managed_universe=_managed_universe(),
        state_store=state_store,
        status_publisher=status_publisher,
        accepted_handler=_accepted_handler,
    )

    status = asyncio.run(consumer.process_deferred_target("sniper-paper", "paper"))

    assert status == "rejected_superseded"
    assert state_store.deferred_signal is None
    assert state_store.deferred_cleared == ("sniper-paper", "paper")
    assert status_publisher.events == [
        {
            "status": "rejected_superseded",
            "stream_id": "1773505624555-0",
            "portfolio_revision": 2,
            "details": {"reason": "Deferred revision is older than the latest accepted revision"},
        },
    ]


async def _accepted_handler(_signal) -> SignalApplyResult:
    return SignalApplyResult.noop_band()


async def _deferred_handler(_signal) -> SignalApplyResult:
    return SignalApplyResult.deferred({"missing": ["prices_missing:ADAUSDT.BINANCE_SPOT"]})


def _stored_signal(payload, stream_id: str):
    envelope = build_stream_envelope(payload)
    return StoredSignal(envelope=envelope, payload=payload, stream_id=stream_id)
