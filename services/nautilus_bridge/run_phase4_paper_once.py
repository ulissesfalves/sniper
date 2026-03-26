from __future__ import annotations

import argparse
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
from services.nautilus_bridge.status import STATUS_FAILED
from services.nautilus_bridge.status import STATUS_FILLED
from services.nautilus_bridge.status import STATUS_NOOP_BAND
from services.nautilus_bridge.status import STATUS_SUBMITTED


TERMINAL_SUCCESS_STATUSES = {STATUS_NOOP_BAND, STATUS_SUBMITTED, STATUS_FILLED}
OUT_OF_ORDER_TERMINAL_BEFORE_ACCEPTED = "status_out_of_order_terminal_before_accepted"
OUT_OF_ORDER_FILLED_BEFORE_SUBMITTED = "status_out_of_order_filled_before_submitted"

RedisFactory = Callable[[str], Any]
PublishFn = Callable[..., Awaitable[str]]
MonotonicFn = Callable[[], float]
AfterPublishFn = Callable[[str], Awaitable[None]]


def _normalize_stream_id(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _stream_id_key(value: str) -> tuple[int, int]:
    major, _sep, minor = value.partition("-")
    return int(major or 0), int(minor or 0)


async def _latest_stream_id(redis: Any, stream_key: str) -> str:
    entries = await redis.xrevrange(stream_key, "+", "-", count=1)
    if not entries:
        return "0-0"
    stream_id, _fields = entries[0]
    return _normalize_stream_id(stream_id)


def _maybe_result_from_statuses(*, message_id: str, seen_statuses: list[str], seen_lookup: set[str]) -> Phase4PaperOnceResult | None:
    for status in seen_statuses:
        if status.startswith("rejected_") or status == STATUS_FAILED:
            return Phase4PaperOnceResult(
                success=False,
                message_id=message_id,
                statuses=tuple(seen_statuses),
                reason=status or "status_failure",
            )

    if any(status in seen_lookup for status in TERMINAL_SUCCESS_STATUSES) and STATUS_ACCEPTED not in seen_lookup:
        return Phase4PaperOnceResult(
            success=False,
            message_id=message_id,
            statuses=tuple(seen_statuses),
            reason=OUT_OF_ORDER_TERMINAL_BEFORE_ACCEPTED,
        )

    if STATUS_FILLED in seen_lookup and STATUS_SUBMITTED not in seen_lookup:
        return Phase4PaperOnceResult(
            success=False,
            message_id=message_id,
            statuses=tuple(seen_statuses),
            reason=OUT_OF_ORDER_FILLED_BEFORE_SUBMITTED,
        )

    if STATUS_NOOP_BAND in seen_lookup:
        return Phase4PaperOnceResult(
            success=True,
            message_id=message_id,
            statuses=tuple(seen_statuses),
        )

    if STATUS_SUBMITTED in seen_lookup:
        return Phase4PaperOnceResult(
            success=True,
            message_id=message_id,
            statuses=tuple(seen_statuses),
        )

    if STATUS_FILLED in seen_lookup:
        return Phase4PaperOnceResult(
            success=True,
            message_id=message_id,
            statuses=tuple(seen_statuses),
        )

    return None


def _record_status(
    *,
    fields: dict[Any, Any],
    message_id: str,
    seen_statuses: list[str],
    seen_lookup: set[str],
) -> bool:
    record_message_id = get_decoded_stream_field(
        fields,
        "message_id",
        required=False,
        default="",
    ) or ""
    if record_message_id != message_id:
        return False
    status = get_decoded_stream_field(fields, "status", required=False, default="") or ""
    if status and status not in seen_lookup:
        seen_statuses.append(status)
        seen_lookup.add(status)
    return True


async def _load_existing_statuses(
    *,
    redis: Any,
    stream_key: str,
    message_id: str,
    start_stream_id: str,
    seen_statuses: list[str],
    seen_lookup: set[str],
) -> str:
    cursor = start_stream_id
    entries = await redis.xrange(stream_key, "-", "+")
    start_key = _stream_id_key(start_stream_id)
    for stream_id, fields in entries:
        normalized_stream_id = _normalize_stream_id(stream_id)
        if start_stream_id != "0-0" and _stream_id_key(normalized_stream_id) <= start_key:
            continue
        cursor = normalized_stream_id
        _record_status(
            fields=fields,
            message_id=message_id,
            seen_statuses=seen_statuses,
            seen_lookup=seen_lookup,
        )
    return cursor


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
            f"MESSAGE_ID={self.message_id}",
            f"FINAL_STATUSES={','.join(self.statuses)}",
            f"RESULT={'SUCCESS' if self.success else 'FAILURE'}",
        ]
        if self.reason:
            lines.append(f"REASON={self.reason}")
        return "\n".join(lines)


async def _wait_for_terminal_statuses(
    *,
    redis: Any,
    stream_key: str,
    message_id: str,
    start_stream_id: str,
    timeout_secs: float,
    monotonic_fn: MonotonicFn,
) -> Phase4PaperOnceResult:
    cursor = start_stream_id
    seen_statuses: list[str] = []
    seen_lookup: set[str] = set()
    deadline = monotonic_fn() + timeout_secs

    cursor = await _load_existing_statuses(
        redis=redis,
        stream_key=stream_key,
        message_id=message_id,
        start_stream_id=start_stream_id,
        seen_statuses=seen_statuses,
        seen_lookup=seen_lookup,
    )
    existing_result = _maybe_result_from_statuses(
        message_id=message_id,
        seen_statuses=seen_statuses,
        seen_lookup=seen_lookup,
    )
    if existing_result is not None:
        return existing_result

    while monotonic_fn() < deadline:
        remaining_secs = max(0.0, deadline - monotonic_fn())
        block_ms = max(1, int(min(remaining_secs, 5.0) * 1000))
        response = await redis.xread({stream_key: cursor}, count=50, block=block_ms)
        if not response:
            continue
        for _stream_name, entries in response:
            for stream_id, fields in entries:
                cursor = _normalize_stream_id(stream_id)
                if not _record_status(
                    fields=fields,
                    message_id=message_id,
                    seen_statuses=seen_statuses,
                    seen_lookup=seen_lookup,
                ):
                    continue
                current_result = _maybe_result_from_statuses(
                    message_id=message_id,
                    seen_statuses=seen_statuses,
                    seen_lookup=seen_lookup,
                )
                if current_result is not None:
                    return current_result

    return Phase4PaperOnceResult(
        success=False,
        message_id=message_id,
        statuses=tuple(seen_statuses),
        reason="timeout_waiting_terminal_status",
    )


async def run_phase4_paper_once(
    *,
    config: BridgeConfig | None = None,
    redis_factory: RedisFactory = redis_async.from_url,
    publish_fn: PublishFn = publish_phase4_snapshot,
    monotonic_fn: MonotonicFn = time.monotonic,
    resume_message_id: str | None = None,
    after_publish: AfterPublishFn | None = None,
) -> Phase4PaperOnceResult:
    bridge_config = config or BridgeConfig()
    redis = redis_factory(bridge_config.redis_url)
    try:
        if resume_message_id is None:
            start_stream_id = await _latest_stream_id(redis, bridge_config.status_stream_key)
            message_id = await publish_fn(config=bridge_config, redis=redis)
            if after_publish is not None:
                await after_publish(message_id)
        else:
            start_stream_id = "0-0"
            message_id = resume_message_id
        return await _wait_for_terminal_statuses(
            redis=redis,
            stream_key=bridge_config.status_stream_key,
            message_id=message_id,
            start_stream_id=start_stream_id,
            timeout_secs=bridge_config.status_timeout_secs,
            monotonic_fn=monotonic_fn,
        )
    finally:
        await redis.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish the official phase4 snapshot and await paper statuses.")
    parser.add_argument(
        "--status-timeout-secs",
        type=float,
        default=None,
        help="Override the terminal status timeout for this run.",
    )
    args = parser.parse_args()
    config = BridgeConfig(
        status_timeout_secs=args.status_timeout_secs
        if args.status_timeout_secs is not None
        else BridgeConfig().status_timeout_secs,
    )
    result = asyncio.run(run_phase4_paper_once(config=config))
    print(result.render())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
