from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from typing import Awaitable
from typing import Callable

import redis.asyncio as redis_async

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.contract import get_decoded_stream_field
from services.nautilus_bridge.phase4_publisher import publish_phase4_snapshot
from services.nautilus_bridge.status import STATUS_ACCEPTED
from services.nautilus_bridge.status import STATUS_DEFERRED_NOT_READY
from services.nautilus_bridge.status import STATUS_FAILED
from services.nautilus_bridge.status import STATUS_FILLED
from services.nautilus_bridge.status import STATUS_NOOP_BAND
from services.nautilus_bridge.status import STATUS_SUBMITTED


PublishPhase4Snapshot = Callable[[], Awaitable[str]]
MonotonicClock = Callable[[], float]


def _decode_stream_id(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass(frozen=True)
class Phase4PaperOnceResult:
    success: bool
    message_id: str
    statuses: tuple[str, ...]
    reason: str | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    def render(self) -> str:
        lines = [
            f"RESULT={'SUCCESS' if self.success else 'FAILURE'}",
            f"MESSAGE_ID={self.message_id}",
            f"FINAL_STATUSES={','.join(self.statuses)}",
        ]
        if not self.success and self.reason:
            lines.append(f"REASON={self.reason}")
        return "\n".join(lines)


async def _status_stream_baseline(redis: Any, stream_key: str) -> str:
    entries = await redis.xrevrange(stream_key, "+", "-", count=1)
    if not entries:
        return "0-0"
    stream_id, _fields = entries[0]
    return _decode_stream_id(stream_id)


def _success_result(message_id: str, statuses: list[str]) -> Phase4PaperOnceResult:
    return Phase4PaperOnceResult(
        success=True,
        message_id=message_id,
        statuses=tuple(statuses),
    )


def _failure_result(message_id: str, statuses: list[str], reason: str) -> Phase4PaperOnceResult:
    return Phase4PaperOnceResult(
        success=False,
        message_id=message_id,
        statuses=tuple(statuses),
        reason=reason,
    )


async def wait_for_phase4_message_status(
    *,
    redis: Any,
    stream_key: str,
    start_after_stream_id: str,
    message_id: str,
    timeout_total_secs: float = 60.0,
    poll_interval_secs: float = 1.0,
    settle_after_submitted_secs: float = 5.0,
    monotonic: MonotonicClock = time.monotonic,
) -> Phase4PaperOnceResult:
    deadline = monotonic() + timeout_total_secs
    cursor = start_after_stream_id
    statuses: list[str] = []
    accepted_seen = False
    submitted_seen = False
    settle_deadline: float | None = None

    while True:
        now = monotonic()
        if settle_deadline is not None and now >= settle_deadline:
            return _success_result(message_id, statuses)
        if now >= deadline:
            if not accepted_seen:
                return _failure_result(message_id, statuses, "Timed out waiting for accepted status")
            if submitted_seen:
                return _failure_result(message_id, statuses, "Timed out waiting for submit settle window")
            return _failure_result(message_id, statuses, "Timed out waiting for terminal operational status")

        block_ms = max(1, int(min(poll_interval_secs, deadline - now) * 1000))
        response = await redis.xread({stream_key: cursor}, block=block_ms, count=100)
        if not response:
            continue

        for _stream_name, entries in response:
            for stream_id, fields in entries:
                cursor = _decode_stream_id(stream_id)
                status_message_id = (
                    get_decoded_stream_field(fields, "message_id", required=False, default="") or ""
                )
                if status_message_id != message_id:
                    continue

                status = get_decoded_stream_field(fields, "status", required=False, default="") or ""
                if not status:
                    continue

                statuses.append(status)

                if status.startswith("rejected_") or status == STATUS_FAILED:
                    return _failure_result(message_id, statuses, f"Observed terminal failure status: {status}")
                if status == STATUS_ACCEPTED:
                    accepted_seen = True
                    continue
                if status == STATUS_NOOP_BAND and accepted_seen:
                    return _success_result(message_id, statuses)
                if status == STATUS_SUBMITTED and accepted_seen:
                    submitted_seen = True
                    settle_deadline = monotonic() + settle_after_submitted_secs
                    continue
                if status in {STATUS_DEFERRED_NOT_READY, STATUS_FILLED}:
                    continue


async def run_phase4_paper_once(
    *,
    config: BridgeConfig | None = None,
    redis_factory: Callable[[str], Any] = redis_async.from_url,
    publish_snapshot: PublishPhase4Snapshot = publish_phase4_snapshot,
    timeout_total_secs: float = 60.0,
    poll_interval_secs: float = 1.0,
    settle_after_submitted_secs: float = 5.0,
    monotonic: MonotonicClock = time.monotonic,
) -> Phase4PaperOnceResult:
    bridge_config = config or BridgeConfig()
    redis = redis_factory(bridge_config.redis_url)
    message_id = ""
    try:
        baseline_stream_id = await _status_stream_baseline(redis, bridge_config.status_stream_key)
        message_id = await publish_snapshot()
        return await wait_for_phase4_message_status(
            redis=redis,
            stream_key=bridge_config.status_stream_key,
            start_after_stream_id=baseline_stream_id,
            message_id=message_id,
            timeout_total_secs=timeout_total_secs,
            poll_interval_secs=poll_interval_secs,
            settle_after_submitted_secs=settle_after_submitted_secs,
            monotonic=monotonic,
        )
    except Exception as exc:
        return _failure_result(message_id, [], str(exc))
    finally:
        await redis.aclose()


def main() -> int:
    result = asyncio.run(run_phase4_paper_once())
    print(result.render())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
