from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Awaitable
from typing import Callable

from services.nautilus_bridge.acceptance import AcceptanceContext
from services.nautilus_bridge.acceptance import evaluate_acceptance
from services.nautilus_bridge.acceptance import evaluate_deferred_signal
from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.config import ManagedUniverse
from services.nautilus_bridge.contract import EnvelopeMismatchError
from services.nautilus_bridge.contract import SchemaValidationError
from services.nautilus_bridge.contract import StoredSignal
from services.nautilus_bridge.contract import envelope_from_stream_fields
from services.nautilus_bridge.contract import get_decoded_stream_field
from services.nautilus_bridge.contract import payload_from_json
from services.nautilus_bridge.contract import validate_envelope_matches_payload
from services.nautilus_bridge.state import BridgeStateStore
from services.nautilus_bridge.status import RedisStatusPublisher
from services.nautilus_bridge.status import STATUS_ACCEPTED
from services.nautilus_bridge.status import STATUS_DEFERRED_NOT_READY
from services.nautilus_bridge.status import STATUS_FAILED
from services.nautilus_bridge.status import STATUS_NOOP_BAND
from services.nautilus_bridge.status import STATUS_RECEIVED
from services.nautilus_bridge.status import STATUS_REJECTED_ENVELOPE_MISMATCH
from services.nautilus_bridge.status import STATUS_REJECTED_SCHEMA
from services.nautilus_bridge.status import STATUS_SUBMITTED
from services.nautilus_bridge.status import should_commit_cursor_for_status


StoredSignalHandler = Callable[[StoredSignal], Awaitable["SignalApplyResult"]]


def _normalize_stream_id(stream_id: Any) -> str:
    if isinstance(stream_id, bytes):
        return stream_id.decode("utf-8")
    return str(stream_id)


@dataclass(frozen=True)
class SignalApplyResult:
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    should_persist_applied: bool = False
    should_store_deferred: bool = False
    should_clear_deferred: bool = False
    should_commit_cursor: bool | None = None

    def commit_cursor(self) -> bool:
        if self.should_commit_cursor is not None:
            return self.should_commit_cursor
        return should_commit_cursor_for_status(self.status)

    @classmethod
    def submitted(cls, details: dict[str, Any] | None = None) -> "SignalApplyResult":
        return cls(
            status=STATUS_SUBMITTED,
            details=details or {},
            should_persist_applied=True,
            should_clear_deferred=True,
        )

    @classmethod
    def noop_band(cls, details: dict[str, Any] | None = None) -> "SignalApplyResult":
        return cls(
            status=STATUS_NOOP_BAND,
            details=details or {},
            should_persist_applied=True,
            should_clear_deferred=True,
        )

    @classmethod
    def deferred(cls, details: dict[str, Any] | None = None) -> "SignalApplyResult":
        return cls(
            status=STATUS_DEFERRED_NOT_READY,
            details=details or {},
            should_store_deferred=True,
        )

    @classmethod
    def failed(cls, details: dict[str, Any] | None = None) -> "SignalApplyResult":
        return cls(status=STATUS_FAILED, details=details or {}, should_commit_cursor=False)


@dataclass
class RedisSignalConsumer:
    redis: Any
    config: BridgeConfig
    managed_universe: ManagedUniverse
    state_store: BridgeStateStore
    status_publisher: RedisStatusPublisher
    accepted_handler: StoredSignalHandler

    async def consume_once(self, *, block_ms: int | None = None) -> bool:
        cursor = await self.state_store.get_stream_cursor()
        response = await self.redis.xread(
            {self.config.target_stream_key: cursor},
            count=1,
            block=block_ms if block_ms is not None else self.config.poll_block_ms,
        )
        if not response:
            return False
        for _, entries in response:
            for stream_id, fields in entries:
                await self._process_stream_entry(_normalize_stream_id(stream_id), fields)
        return True

    async def process_deferred_target(self, portfolio_id: str, environment: str) -> str | None:
        deferred = await self.state_store.get_deferred_target(portfolio_id, environment)
        if deferred is None:
            return None
        deferred_decision = evaluate_deferred_signal(
            deferred_revision=deferred.payload.portfolio_revision,
            last_revision_accepted=await self.state_store.get_last_revision_accepted(
                portfolio_id,
                environment,
            ),
        )
        if not deferred_decision.can_apply:
            if deferred_decision.status is not None:
                await self.status_publisher.publish_for_signal(
                    status=deferred_decision.status,
                    signal=deferred,
                    details={"reason": deferred_decision.reason} if deferred_decision.reason else None,
                )
            if deferred_decision.should_clear_deferred:
                await self.state_store.clear_deferred_target(portfolio_id, environment)
            return deferred_decision.status
        apply_result = await self.accepted_handler(deferred)
        await self._finalize_apply_result(deferred, apply_result, allow_cursor_commit=False)
        return apply_result.status

    async def _process_stream_entry(self, stream_id: str, fields: Any) -> None:
        raw_message_id = ""
        raw_portfolio_id = ""
        raw_environment = self.config.environment
        raw_revision = 0
        raw_fingerprint = ""
        if isinstance(fields, dict):
            raw_message_id = get_decoded_stream_field(fields, "message_id", required=False, default="") or ""
            raw_portfolio_id = get_decoded_stream_field(
                fields,
                "portfolio_id",
                required=False,
                default="",
            ) or ""
            raw_environment = get_decoded_stream_field(
                fields,
                "environment",
                required=False,
                default=raw_environment,
            ) or raw_environment
            raw_revision_text = get_decoded_stream_field(
                fields,
                "portfolio_revision",
                required=False,
                default="0",
            ) or "0"
            raw_revision = int(raw_revision_text)
            raw_fingerprint = get_decoded_stream_field(
                fields,
                "signal_fingerprint",
                required=False,
                default="",
            ) or ""
        await self.status_publisher.publish_raw(
            status=STATUS_RECEIVED,
            message_id=raw_message_id,
            portfolio_id=raw_portfolio_id,
            environment=raw_environment,
            portfolio_revision=raw_revision,
            signal_fingerprint=raw_fingerprint,
            stream_id=stream_id,
        )
        try:
            envelope = envelope_from_stream_fields(fields)
            payload = payload_from_json(envelope.payload_json)
            validate_envelope_matches_payload(envelope, payload)
        except EnvelopeMismatchError as exc:
            await self.status_publisher.publish_raw(
                status=STATUS_REJECTED_ENVELOPE_MISMATCH,
                message_id=raw_message_id,
                portfolio_id=raw_portfolio_id,
                environment=raw_environment,
                portfolio_revision=raw_revision,
                signal_fingerprint=raw_fingerprint,
                stream_id=stream_id,
                details={"reason": str(exc)},
            )
            await self.state_store.set_stream_cursor(stream_id)
            return
        except SchemaValidationError as exc:
            await self.status_publisher.publish_raw(
                status=STATUS_REJECTED_SCHEMA,
                message_id=raw_message_id,
                portfolio_id=raw_portfolio_id,
                environment=raw_environment,
                portfolio_revision=raw_revision,
                signal_fingerprint=raw_fingerprint,
                stream_id=stream_id,
                details={"reason": str(exc)},
            )
            await self.state_store.set_stream_cursor(stream_id)
            return
        stored_signal = StoredSignal(envelope=envelope, payload=payload, stream_id=stream_id)
        context = AcceptanceContext(
            managed_instruments=frozenset(self.managed_universe.instrument_ids),
            last_revision_accepted=await self.state_store.get_last_revision_accepted(
                payload.portfolio_id,
                payload.environment,
            ),
            last_accepted_fingerprint=await self.state_store.get_last_accepted_fingerprint(
                payload.portfolio_id,
                payload.environment,
            ),
            now=datetime.now(UTC),
            max_signal_age=timedelta(seconds=self.config.max_signal_age_secs),
        )
        decision = evaluate_acceptance(payload, context)
        if not decision.accepted:
            await self.status_publisher.publish_for_signal(
                status=decision.status,
                signal=stored_signal,
                details={"reason": decision.reason} if decision.reason else None,
            )
            if decision.should_commit_cursor:
                await self.state_store.set_stream_cursor(stream_id)
            return
        await self.state_store.set_last_accepted_target(stored_signal)
        await self.status_publisher.publish_for_signal(status=STATUS_ACCEPTED, signal=stored_signal)
        apply_result = await self.accepted_handler(stored_signal)
        await self._finalize_apply_result(stored_signal, apply_result, allow_cursor_commit=True)

    async def _finalize_apply_result(
        self,
        signal: StoredSignal,
        apply_result: SignalApplyResult,
        *,
        allow_cursor_commit: bool,
    ) -> None:
        await self.status_publisher.publish_for_signal(
            status=apply_result.status,
            signal=signal,
            details=apply_result.details,
        )
        if apply_result.should_store_deferred:
            await self.state_store.set_deferred_target(signal)
        if apply_result.should_clear_deferred:
            await self.state_store.clear_deferred_target(
                signal.payload.portfolio_id,
                signal.payload.environment,
            )
        if apply_result.should_persist_applied:
            await self.state_store.set_last_applied_target(signal)
        if allow_cursor_commit and signal.stream_id is not None and apply_result.commit_cursor():
            await self.state_store.set_stream_cursor(signal.stream_id)
