from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable

import redis.asyncio as redis_async

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.run_phase4_paper_once import Phase4PaperOnceResult
from services.nautilus_bridge.run_phase4_paper_once import run_phase4_paper_once


RedisFactory = Callable[[str], Any]
RunOnceFn = Callable[..., Awaitable[Phase4PaperOnceResult]]
NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
TokenFactory = Callable[[], str]

LOCK_ACTIVE_REASON = "daemon_lock_active"
LOCK_LOST_REASON = "daemon_lock_lost"
AWAITING_TERMINAL_RESULT = "awaiting_terminal_status"
TIMEOUT_TERMINAL_RESULT = "timeout_waiting_terminal_status"
SUCCESS_AFTER_TIMEOUT_RESULT = "success_after_timeout_reconciliation"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _snapshot_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_payload(
    *,
    daemon_token: str,
    started_at: datetime,
    updated_at: datetime,
    result: str,
    cycles_requested: int,
    cycles_completed: int,
    succeeded: int,
    failed: int,
    skipped_unchanged: int,
    last_snapshot_sha256: str,
    last_message_id: str,
    last_final_statuses: tuple[str, ...],
    reason: str | None = None,
    finished_at: datetime | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "daemon_token": daemon_token,
        "started_at": started_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "updated_at": updated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "result": result,
        "cycles_requested": cycles_requested,
        "cycles_completed": cycles_completed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped_unchanged": skipped_unchanged,
        "last_snapshot_sha256": last_snapshot_sha256,
        "last_message_id": last_message_id,
        "last_final_statuses": list(last_final_statuses),
    }
    if reason:
        payload["reason"] = reason
    if finished_at is not None:
        payload["finished_at"] = finished_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return payload


async def _write_runtime_state(
    *,
    redis: Any,
    config: BridgeConfig,
    payload: dict[str, Any],
) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    await redis.set(config.heartbeat_key(), encoded)
    await redis.set(config.run_summary_key(), encoded)


async def _acquire_lock(redis: Any, key: str, token: str, ttl_secs: int) -> bool:
    return bool(await redis.set(key, token, nx=True, ex=ttl_secs))


async def _refresh_lock(redis: Any, key: str, token: str, ttl_secs: int) -> bool:
    if _decode(await redis.get(key)) != token:
        return False
    await redis.set(key, token, ex=ttl_secs)
    return True


async def _release_lock(redis: Any, key: str, token: str) -> None:
    if _decode(await redis.get(key)) == token:
        await redis.delete(key)


def _last_snapshot_hash_from_summary(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    snapshot_hash = payload.get("last_snapshot_sha256")
    return str(snapshot_hash) if snapshot_hash else None


def _parse_summary(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _register_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:  # pragma: no cover - platform specific
            continue


@dataclass(frozen=True)
class Phase4PaperDaemonResult:
    success: bool
    cycles_requested: int
    cycles_completed: int
    succeeded: int
    failed: int
    skipped_unchanged: int
    last_snapshot_sha256: str
    last_message_id: str = ""
    last_final_statuses: tuple[str, ...] = ()
    reason: str | None = None
    lock_acquired: bool = False

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1

    def render(self) -> str:
        lines = [
            f"DAEMON_RESULT={'SUCCESS' if self.success else 'FAILURE'}",
            f"CYCLES={self.cycles_completed}",
            f"SUCCEEDED={self.succeeded}",
            f"FAILED={self.failed}",
            f"SKIPPED_UNCHANGED={self.skipped_unchanged}",
            f"SNAPSHOT_SHA256={self.last_snapshot_sha256}",
        ]
        if self.last_message_id:
            lines.append(f"LAST_MESSAGE_ID={self.last_message_id}")
        if self.last_final_statuses:
            lines.append(f"LAST_FINAL_STATUSES={','.join(self.last_final_statuses)}")
        if self.reason:
            lines.append(f"REASON={self.reason}")
        return "\n".join(lines)


async def run_phase4_paper_daemon(
    *,
    cycles: int = 0,
    interval_secs: float | None = None,
    config: BridgeConfig | None = None,
    redis_factory: RedisFactory = redis_async.from_url,
    run_once_fn: RunOnceFn = run_phase4_paper_once,
    now_fn: NowFn = _utc_now,
    sleep_fn: SleepFn = asyncio.sleep,
    token_factory: TokenFactory = lambda: uuid.uuid4().hex,
) -> Phase4PaperDaemonResult:
    bridge_config = config or BridgeConfig()
    effective_interval_secs = bridge_config.daemon_interval_secs if interval_secs is None else interval_secs
    snapshot_path = Path(bridge_config.phase4_snapshot_path)
    current_snapshot_sha256 = _snapshot_sha256(snapshot_path)
    redis = redis_factory(bridge_config.redis_url)
    daemon_token = token_factory()
    lock_acquired = False
    stop_event = asyncio.Event()
    heartbeat_task: asyncio.Task[None] | None = None

    started_at = now_fn().astimezone(UTC)
    cycles_completed = 0
    succeeded = 0
    failed = 0
    skipped_unchanged = 0
    last_message_id = ""
    last_final_statuses: tuple[str, ...] = ()
    reason: str | None = None
    state: dict[str, Any] = {
        "result": "running",
        "last_snapshot_sha256": current_snapshot_sha256,
        "last_message_id": "",
        "last_final_statuses": (),
    }
    lock_state = {"lost": False}

    def state_payload(*, finished_at: datetime | None = None) -> dict[str, Any]:
        return _runtime_payload(
            daemon_token=daemon_token,
            started_at=started_at,
            updated_at=now_fn().astimezone(UTC),
            result=state["result"],
            cycles_requested=cycles,
            cycles_completed=cycles_completed,
            succeeded=succeeded,
            failed=failed,
            skipped_unchanged=skipped_unchanged,
            last_snapshot_sha256=state["last_snapshot_sha256"],
            last_message_id=state["last_message_id"],
            last_final_statuses=tuple(state["last_final_statuses"]),
            reason=reason,
            finished_at=finished_at,
        )

    async def heartbeat_loop() -> None:
        while not stop_event.is_set():
            if not await _refresh_lock(
                redis,
                bridge_config.daemon_lock_key(),
                daemon_token,
                bridge_config.daemon_lock_ttl_secs,
            ):
                lock_state["lost"] = True
                stop_event.set()
                return
            await redis.set(
                bridge_config.heartbeat_key(),
                json.dumps(state_payload(), separators=(",", ":"), sort_keys=True),
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=bridge_config.heartbeat_interval_secs)
            except asyncio.TimeoutError:
                continue

    try:
        lock_acquired = await _acquire_lock(
            redis,
            bridge_config.daemon_lock_key(),
            daemon_token,
            bridge_config.daemon_lock_ttl_secs,
        )
        if not lock_acquired:
            return Phase4PaperDaemonResult(
                success=False,
                cycles_requested=cycles,
                cycles_completed=0,
                succeeded=0,
                failed=1,
                skipped_unchanged=0,
                last_snapshot_sha256=current_snapshot_sha256,
                reason=LOCK_ACTIVE_REASON,
                lock_acquired=False,
            )

        previous_summary = _parse_summary(_decode(await redis.get(bridge_config.run_summary_key())))
        previous_snapshot_sha256 = str(previous_summary.get("last_snapshot_sha256") or "") or None
        previous_result = str(previous_summary.get("result") or "")
        previous_message_id = str(previous_summary.get("last_message_id") or "")

        await _write_runtime_state(redis=redis, config=bridge_config, payload=state_payload())
        _register_signal_handlers(stop_event)
        heartbeat_task = asyncio.create_task(heartbeat_loop())

        cycle_index = 0
        while not stop_event.is_set():
            if cycles > 0 and cycle_index >= cycles:
                break

            cycle_index += 1
            cycles_completed = cycle_index
            current_snapshot_sha256 = _snapshot_sha256(snapshot_path)
            state["last_snapshot_sha256"] = current_snapshot_sha256

            async def mark_awaiting(message_id: str) -> None:
                nonlocal last_message_id, last_final_statuses, reason
                last_message_id = message_id
                last_final_statuses = ()
                reason = None
                state["last_message_id"] = message_id
                state["last_final_statuses"] = ()
                state["result"] = AWAITING_TERMINAL_RESULT
                await _write_runtime_state(redis=redis, config=bridge_config, payload=state_payload())

            resume_message_id = (
                previous_message_id
                if previous_snapshot_sha256 == current_snapshot_sha256
                and previous_result in {AWAITING_TERMINAL_RESULT, TIMEOUT_TERMINAL_RESULT}
                and previous_message_id
                else ""
            )

            if resume_message_id:
                once_result = await run_once_fn(
                    config=bridge_config,
                    resume_message_id=resume_message_id,
                )
                last_message_id = once_result.message_id
                last_final_statuses = once_result.statuses
                state["last_message_id"] = once_result.message_id
                state["last_final_statuses"] = once_result.statuses
                if once_result.success and previous_result == TIMEOUT_TERMINAL_RESULT:
                    state["result"] = SUCCESS_AFTER_TIMEOUT_RESULT
                    reason = "reconciled_late_terminal_status"
                    succeeded += 1
                elif once_result.success:
                    state["result"] = "success"
                    reason = "resumed_inflight_message"
                    succeeded += 1
                else:
                    state["result"] = TIMEOUT_TERMINAL_RESULT if once_result.reason == TIMEOUT_TERMINAL_RESULT else "failure"
                    reason = once_result.reason
                    failed += 1
                previous_snapshot_sha256 = current_snapshot_sha256
                previous_result = state["result"]
                previous_message_id = once_result.message_id
                await _write_runtime_state(redis=redis, config=bridge_config, payload=state_payload())
            elif previous_snapshot_sha256 == current_snapshot_sha256:
                skipped_unchanged += 1
                state["result"] = "skipped_unchanged"
                reason = None
                previous_result = state["result"]
                await _write_runtime_state(redis=redis, config=bridge_config, payload=state_payload())
            else:
                once_result = await run_once_fn(
                    config=bridge_config,
                    after_publish=mark_awaiting,
                )
                last_message_id = once_result.message_id
                last_final_statuses = once_result.statuses
                state["last_message_id"] = once_result.message_id
                state["last_final_statuses"] = once_result.statuses
                state["result"] = (
                    "success"
                    if once_result.success
                    else TIMEOUT_TERMINAL_RESULT if once_result.reason == TIMEOUT_TERMINAL_RESULT else "failure"
                )
                previous_snapshot_sha256 = current_snapshot_sha256
                previous_result = state["result"]
                previous_message_id = once_result.message_id
                if once_result.success:
                    reason = None
                    succeeded += 1
                else:
                    failed += 1
                    reason = once_result.reason
                await _write_runtime_state(redis=redis, config=bridge_config, payload=state_payload())
                if not once_result.success and cycles == 0 and effective_interval_secs <= 0:
                    break

            if cycles > 0 and cycle_index >= cycles:
                break
            if effective_interval_secs > 0:
                await sleep_fn(effective_interval_secs)

        if lock_state["lost"]:
            failed += 1
            reason = LOCK_LOST_REASON
            state["result"] = "failure"

        finished_at = now_fn().astimezone(UTC)
        if failed == 0 and state["result"] not in {"skipped_unchanged", SUCCESS_AFTER_TIMEOUT_RESULT}:
            state["result"] = "success"
        await _write_runtime_state(
            redis=redis,
            config=bridge_config,
            payload=state_payload(finished_at=finished_at),
        )
        return Phase4PaperDaemonResult(
            success=failed == 0 and not lock_state["lost"],
            cycles_requested=cycles,
            cycles_completed=cycles_completed,
            succeeded=succeeded,
            failed=failed,
            skipped_unchanged=skipped_unchanged,
            last_snapshot_sha256=current_snapshot_sha256,
            last_message_id=last_message_id,
            last_final_statuses=last_final_statuses,
            reason=reason,
            lock_acquired=True,
        )
    finally:
        stop_event.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if lock_acquired:
            await _release_lock(redis, bridge_config.daemon_lock_key(), daemon_token)
        await redis.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuously publish phase4 targets into the paper bridge.")
    parser.add_argument("--cycles", type=int, default=0, help="Number of cycles to execute. Default 0 runs forever.")
    parser.add_argument(
        "--interval-secs",
        type=float,
        default=None,
        help="Sleep between cycles. Defaults to SNIPER_BRIDGE_DAEMON_INTERVAL_SECS.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run_phase4_paper_daemon(
            cycles=args.cycles,
            interval_secs=args.interval_secs,
        ),
    )
    print(result.render())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
