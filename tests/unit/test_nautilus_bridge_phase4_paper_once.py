from __future__ import annotations

import asyncio
from dataclasses import dataclass

from tests import _path_setup  # noqa: F401
from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.run_phase4_paper_once import OUT_OF_ORDER_FILLED_BEFORE_SUBMITTED
from services.nautilus_bridge.run_phase4_paper_once import OUT_OF_ORDER_TERMINAL_BEFORE_ACCEPTED
from services.nautilus_bridge.run_phase4_paper_once import Phase4PaperOnceResult
from services.nautilus_bridge.run_phase4_paper_once import run_phase4_paper_once


MESSAGE_ID = "019cf25d-c795-7dbe-a2cf-273cafe4e13b"


class FakeRedis:
    def __init__(
        self,
        *,
        xrange_entries: list[tuple[str, dict[bytes, bytes]]] | None = None,
        xread_responses: list[list[tuple[str, list[tuple[str, dict[bytes, bytes]]]]]] | None = None,
    ):
        self._xrange_entries = list(xrange_entries or [])
        self._xread_responses = list(xread_responses or [])
        self.closed = False

    async def xrevrange(self, *_args, **_kwargs):
        return [("100-0", {})]

    async def xrange(self, *_args, **_kwargs):
        return list(self._xrange_entries)

    async def xread(self, *_args, **_kwargs):
        if self._xread_responses:
            return self._xread_responses.pop(0)
        return []

    async def aclose(self) -> None:
        self.closed = True


def _status_response(*statuses: str) -> list[tuple[str, list[tuple[str, dict[bytes, bytes]]]]]:
    entries = []
    for idx, status in enumerate(statuses, start=101):
        entries.append(
            (
                f"{idx}-0",
                {
                    b"message_id": MESSAGE_ID.encode("utf-8"),
                    b"status": status.encode("utf-8"),
                },
            ),
        )
    return [("sniper:portfolio_status:v1", entries)]


async def _fake_publish_fn(*, config: BridgeConfig, redis) -> str:  # noqa: ANN001
    _ = config
    _ = redis
    return MESSAGE_ID


async def _unexpected_publish_fn(*, config: BridgeConfig, redis) -> str:  # noqa: ANN001
    _ = config
    _ = redis
    raise AssertionError("publish_fn should not be called when resuming an existing message")


@dataclass
class FakeClock:
    values: list[float]

    def monotonic(self) -> float:
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


def test_run_phase4_paper_once_accepts_noop_band_terminal_status() -> None:
    redis = FakeRedis(xread_responses=[_status_response("received", "accepted", "noop_band")])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
        ),
    )

    assert result == Phase4PaperOnceResult(
        success=True,
        message_id=MESSAGE_ID,
        statuses=("received", "accepted", "noop_band"),
        reason=None,
    )
    assert redis.closed is True


def test_run_phase4_paper_once_accepts_submitted_terminal_status() -> None:
    redis = FakeRedis(xread_responses=[_status_response("received", "accepted", "submitted")])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
        ),
    )

    assert result.success is True
    assert result.statuses[-1] == "submitted"


def test_run_phase4_paper_once_fails_on_rejected_status() -> None:
    redis = FakeRedis(xread_responses=[_status_response("received", "rejected_stale")])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
        ),
    )

    assert result.success is False
    assert result.reason == "rejected_stale"


def test_run_phase4_paper_once_fails_on_timeout() -> None:
    redis = FakeRedis(xread_responses=[[], []])
    clock = FakeClock(values=[0.0, 0.1, 0.2, 1.2, 1.2])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=1.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
            monotonic_fn=clock.monotonic,
        ),
    )

    assert result.success is False
    assert result.reason == "timeout_waiting_terminal_status"


def test_run_phase4_paper_once_resumes_existing_message_without_republish() -> None:
    redis = FakeRedis(
        xrange_entries=[
            ("101-0", {b"message_id": MESSAGE_ID.encode("utf-8"), b"status": b"accepted"}),
            ("102-0", {b"message_id": MESSAGE_ID.encode("utf-8"), b"status": b"submitted"}),
        ],
    )

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_unexpected_publish_fn,
            resume_message_id=MESSAGE_ID,
        ),
    )

    assert result == Phase4PaperOnceResult(
        success=True,
        message_id=MESSAGE_ID,
        statuses=("accepted", "submitted"),
        reason=None,
    )


def test_run_phase4_paper_once_ignores_duplicate_status_events() -> None:
    redis = FakeRedis(
        xrange_entries=[
            ("101-0", {b"message_id": MESSAGE_ID.encode("utf-8"), b"status": b"accepted"}),
            ("102-0", {b"message_id": MESSAGE_ID.encode("utf-8"), b"status": b"accepted"}),
            ("103-0", {b"message_id": MESSAGE_ID.encode("utf-8"), b"status": b"submitted"}),
        ],
    )

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_unexpected_publish_fn,
            resume_message_id=MESSAGE_ID,
        ),
    )

    assert result.success is True
    assert result.statuses == ("accepted", "submitted")


def test_run_phase4_paper_once_fails_explicitly_on_terminal_before_accepted() -> None:
    redis = FakeRedis(xread_responses=[_status_response("received", "submitted", "accepted")])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
        ),
    )

    assert result.success is False
    assert result.reason == OUT_OF_ORDER_TERMINAL_BEFORE_ACCEPTED


def test_run_phase4_paper_once_fails_explicitly_on_filled_before_submitted() -> None:
    redis = FakeRedis(xread_responses=[_status_response("received", "accepted", "filled")])

    result = asyncio.run(
        run_phase4_paper_once(
            config=BridgeConfig(status_timeout_secs=5.0),
            redis_factory=lambda _url: redis,
            publish_fn=_fake_publish_fn,
        ),
    )

    assert result.success is False
    assert result.reason == OUT_OF_ORDER_FILLED_BEFORE_SUBMITTED
