from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from services.nautilus_bridge.contract import SignalPayload
from services.nautilus_bridge.status import STATUS_ACCEPTED
from services.nautilus_bridge.status import STATUS_REJECTED_DUPLICATE
from services.nautilus_bridge.status import STATUS_REJECTED_INCOMPLETE_SNAPSHOT
from services.nautilus_bridge.status import STATUS_REJECTED_OUT_OF_ORDER
from services.nautilus_bridge.status import STATUS_REJECTED_REVISION_CONFLICT
from services.nautilus_bridge.status import STATUS_REJECTED_STALE
from services.nautilus_bridge.status import STATUS_REJECTED_SUPERSEDED
from services.nautilus_bridge.status import should_commit_cursor_for_status


@dataclass(frozen=True)
class AcceptanceContext:
    managed_instruments: frozenset[str]
    last_revision_accepted: int | None
    last_accepted_fingerprint: str | None
    now: datetime
    max_signal_age: timedelta


@dataclass(frozen=True)
class AcceptanceDecision:
    status: str
    reason: str | None = None
    should_store_accepted: bool = False

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED

    @property
    def should_commit_cursor(self) -> bool:
        return should_commit_cursor_for_status(self.status)


@dataclass(frozen=True)
class DeferredDecision:
    can_apply: bool
    status: str | None = None
    reason: str | None = None
    should_clear_deferred: bool = False


def evaluate_acceptance(payload: SignalPayload, context: AcceptanceContext) -> AcceptanceDecision:
    now_utc = context.now.astimezone(UTC)
    if payload.expires_at is not None and payload.expires_at < now_utc:
        return AcceptanceDecision(
            status=STATUS_REJECTED_STALE,
            reason="Signal expired before processing",
        )
    if payload.published_at + context.max_signal_age < now_utc:
        return AcceptanceDecision(
            status=STATUS_REJECTED_STALE,
            reason="Signal published_at exceeded max_signal_age",
        )
    target_instruments = {target.instrument_id for target in payload.targets}
    if target_instruments != set(context.managed_instruments):
        return AcceptanceDecision(
            status=STATUS_REJECTED_INCOMPLETE_SNAPSHOT,
            reason="FULL_SNAPSHOT does not match the managed instrument set",
        )
    if context.last_revision_accepted is None or payload.portfolio_revision > context.last_revision_accepted:
        return AcceptanceDecision(status=STATUS_ACCEPTED, should_store_accepted=True)
    if payload.portfolio_revision < context.last_revision_accepted:
        return AcceptanceDecision(
            status=STATUS_REJECTED_OUT_OF_ORDER,
            reason="portfolio_revision is lower than last_revision_accepted",
        )
    if payload.signal_fingerprint == context.last_accepted_fingerprint:
        return AcceptanceDecision(
            status=STATUS_REJECTED_DUPLICATE,
            reason="portfolio_revision matches an already accepted semantic signal",
        )
    return AcceptanceDecision(
        status=STATUS_REJECTED_REVISION_CONFLICT,
        reason="portfolio_revision matches but fingerprint differs",
    )


def evaluate_deferred_signal(
    *,
    deferred_revision: int,
    last_revision_accepted: int | None,
) -> DeferredDecision:
    if last_revision_accepted is None:
        return DeferredDecision(can_apply=False)
    if deferred_revision != last_revision_accepted:
        return DeferredDecision(
            can_apply=False,
            status=STATUS_REJECTED_SUPERSEDED,
            reason="Deferred revision is older than the latest accepted revision",
            should_clear_deferred=True,
        )
    return DeferredDecision(can_apply=True)
