from __future__ import annotations

import asyncio

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.run_phase4_paper_once import run_phase4_paper_once


class FakeRedis:
    def __init__(self, *, baseline_entry, xread_responses):
        self.baseline_entry = baseline_entry
        self.xread_responses = list(xread_responses)
        self.xrevrange_calls: list[tuple[str, str, str, int]] = []
        self.xread_calls: list[dict[str, object]] = []
        self.closed = False

    async def xrevrange(self, key: str, maximum: str, minimum: str, count: int = 1):
        self.xrevrange_calls.append((key, maximum, minimum, count))
        if self.baseline_entry is None:
            return []
        return [self.baseline_entry]

    async def xread(self, streams, block: int, count: int = 100):
        self.xread_calls.append(
            {
                "streams": dict(streams),
                "block": block,
                "count": count,
            },
        )
        if not self.xread_responses:
            return []
        return self.xread_responses.pop(0)

    async def aclose(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.current = 0.0

    def __call__(self) -> float:
        self.current += 0.05
        return self.current


def _status_fields(message_id: str, status: str) -> dict[bytes, bytes]:
    return {
        b"message_id": message_id.encode("utf-8"),
        b"status": status.encode("utf-8"),
    }


def _entry(stream_id: str, message_id: str, status: str):
    return (stream_id.encode("utf-8"), _status_fields(message_id, status))


def test_run_phase4_paper_once_reads_baseline_before_publish_and_captures_immediate_events() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "accepted"),
                        _entry("1773500000003-0", message_id, "noop_band"),
                    ],
                ),
            ],
        ],
    )
    publish_calls: list[str] = []

    async def publish_snapshot() -> str:
        publish_calls.append("published")
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert publish_calls == ["published"]
    assert redis.xrevrange_calls == [("sniper:portfolio_status:v1", "+", "-", 1)]
    assert redis.xread_calls[0]["streams"] == {"sniper:portfolio_status:v1": "1773500000000-0"}
    assert result.success is True
    assert result.message_id == message_id
    assert result.statuses == ("received", "accepted", "noop_band")
    assert redis.closed is True


def test_run_phase4_paper_once_filters_other_message_ids() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    other_message_id = "019ced55-c092-747d-9038-a693d316fe400"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", other_message_id, "received"),
                        _entry("1773500000002-0", message_id, "received"),
                        _entry("1773500000003-0", message_id, "accepted"),
                        _entry("1773500000004-0", other_message_id, "failed"),
                        _entry("1773500000005-0", message_id, "noop_band"),
                    ],
                ),
            ],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is True
    assert result.statuses == ("received", "accepted", "noop_band")


def test_run_phase4_paper_once_succeeds_with_submitted_and_filled() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "accepted"),
                        _entry("1773500000003-0", message_id, "submitted"),
                        _entry("1773500000004-0", message_id, "filled"),
                    ],
                ),
            ],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is True
    assert result.statuses == ("received", "accepted", "submitted", "filled")


def test_run_phase4_paper_once_tolerates_deferred_before_terminal_success() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "accepted"),
                        _entry("1773500000003-0", message_id, "deferred_not_ready"),
                        _entry("1773500000004-0", message_id, "noop_band"),
                    ],
                ),
            ],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is True
    assert result.statuses == ("received", "accepted", "deferred_not_ready", "noop_band")


def test_run_phase4_paper_once_fails_on_rejected_status() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "rejected_schema"),
                    ],
                ),
            ],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is False
    assert result.statuses == ("received", "rejected_schema")
    assert result.reason == "Observed terminal failure status: rejected_schema"


def test_run_phase4_paper_once_fails_without_accepted_status() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "noop_band"),
                    ],
                ),
            ],
            [],
            [],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=0.2,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is False
    assert result.statuses == ("received", "noop_band")
    assert result.reason == "Timed out waiting for accepted status"


def test_run_phase4_paper_once_fails_on_failed_status_after_submitted() -> None:
    message_id = "019ced55-c092-747d-9038-a693d316fe3f"
    redis = FakeRedis(
        baseline_entry=(b"1773500000000-0", {}),
        xread_responses=[
            [
                (
                    b"sniper:portfolio_status:v1",
                    [
                        _entry("1773500000001-0", message_id, "received"),
                        _entry("1773500000002-0", message_id, "accepted"),
                        _entry("1773500000003-0", message_id, "submitted"),
                        _entry("1773500000004-0", message_id, "failed"),
                    ],
                ),
            ],
        ],
    )

    async def publish_snapshot() -> str:
        return message_id

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(redis_url="redis://redis:6379/0"),
            redis_factory=lambda _url: redis,
            publish_snapshot=publish_snapshot,
            timeout_total_secs=1.0,
            poll_interval_secs=0.01,
            settle_after_submitted_secs=0.0,
            monotonic=FakeClock(),
        ),
    )

    assert result.success is False
    assert result.statuses == ("received", "accepted", "submitted", "failed")
    assert result.reason == "Observed terminal failure status: failed"
