from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.contract import StoredSignal


STATUS_RECEIVED = "received"
STATUS_REJECTED_SCHEMA = "rejected_schema"
STATUS_REJECTED_ENVELOPE_MISMATCH = "rejected_envelope_mismatch"
STATUS_REJECTED_STALE = "rejected_stale"
STATUS_REJECTED_DUPLICATE = "rejected_duplicate"
STATUS_REJECTED_OUT_OF_ORDER = "rejected_out_of_order"
STATUS_REJECTED_REVISION_CONFLICT = "rejected_revision_conflict"
STATUS_REJECTED_INCOMPLETE_SNAPSHOT = "rejected_incomplete_snapshot"
STATUS_REJECTED_SUPERSEDED = "rejected_superseded"
STATUS_ACCEPTED = "accepted"
STATUS_NOOP_BAND = "noop_band"
STATUS_DEFERRED_NOT_READY = "deferred_not_ready"
STATUS_SUBMITTED = "submitted"
STATUS_FILLED = "filled"
STATUS_FAILED = "failed"


def _normalize_stream_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def should_commit_cursor_for_status(status: str) -> bool:
    return status.startswith("rejected_") or status in {
        STATUS_NOOP_BAND,
        STATUS_DEFERRED_NOT_READY,
        STATUS_SUBMITTED,
    }


@dataclass(frozen=True)
class StatusEvent:
    status: str
    message_id: str
    portfolio_id: str
    environment: str
    portfolio_revision: int
    signal_fingerprint: str
    stream_id: str | None = None
    details: dict[str, Any] | None = None
    emitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_stream_fields(self) -> dict[str, str]:
        return {
            "status": _normalize_stream_string(self.status),
            "message_id": _normalize_stream_string(self.message_id),
            "portfolio_id": _normalize_stream_string(self.portfolio_id),
            "environment": _normalize_stream_string(self.environment),
            "portfolio_revision": str(self.portfolio_revision),
            "signal_fingerprint": _normalize_stream_string(self.signal_fingerprint),
            "stream_id": _normalize_stream_string(self.stream_id),
            "emitted_at": self.emitted_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "details_json": json.dumps(self.details or {}, separators=(",", ":"), sort_keys=True),
        }


@dataclass
class RedisStatusPublisher:
    redis: Any
    config: BridgeConfig

    async def publish(self, event: StatusEvent) -> str:
        return await self.redis.xadd(self.config.status_stream_key, event.to_stream_fields())

    async def publish_for_signal(
        self,
        *,
        status: str,
        signal: StoredSignal,
        details: dict[str, Any] | None = None,
    ) -> str:
        return await self.publish(
            StatusEvent(
                status=status,
                message_id=signal.envelope.message_id,
                portfolio_id=signal.payload.portfolio_id,
                environment=signal.payload.environment,
                portfolio_revision=signal.payload.portfolio_revision,
                signal_fingerprint=signal.payload.signal_fingerprint,
                stream_id=signal.stream_id,
                details=details,
            ),
        )

    async def publish_raw(
        self,
        *,
        status: str,
        message_id: str = "",
        portfolio_id: str = "",
        environment: str = "",
        portfolio_revision: int = 0,
        signal_fingerprint: str = "",
        stream_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        return await self.publish(
            StatusEvent(
                status=status,
                message_id=message_id,
                portfolio_id=portfolio_id,
                environment=environment,
                portfolio_revision=portfolio_revision,
                signal_fingerprint=signal_fingerprint,
                stream_id=stream_id,
                details=details,
            ),
        )
