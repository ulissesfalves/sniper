from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.config import portfolio_state_key
from services.nautilus_bridge.config import revision_key
from services.nautilus_bridge.contract import StoredSignal


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass
class BridgeStateStore:
    redis: Any
    config: BridgeConfig

    def _key(self, portfolio_id: str, environment: str, suffix: str) -> str:
        return portfolio_state_key(portfolio_id, environment, suffix)

    async def get_stream_cursor(self) -> str:
        value = _decode(await self.redis.get(self.config.cursor_key()))
        return value or "$"

    async def set_stream_cursor(self, stream_id: str) -> None:
        await self.redis.set(self.config.cursor_key(), stream_id)

    async def claim_next_revision(self, portfolio_id: str, environment: str) -> int:
        return int(await self.redis.incr(revision_key(portfolio_id, environment)))

    async def get_last_revision_accepted(self, portfolio_id: str, environment: str) -> int | None:
        value = _decode(await self.redis.get(self._key(portfolio_id, environment, "last_revision_accepted")))
        return int(value) if value is not None else None

    async def get_last_revision_applied(self, portfolio_id: str, environment: str) -> int | None:
        value = _decode(await self.redis.get(self._key(portfolio_id, environment, "last_revision_applied")))
        return int(value) if value is not None else None

    async def set_last_accepted_target(self, signal: StoredSignal) -> None:
        await self.redis.set(
            self._key(signal.payload.portfolio_id, signal.payload.environment, "last_revision_accepted"),
            signal.payload.portfolio_revision,
        )
        await self.redis.set(
            self._key(signal.payload.portfolio_id, signal.payload.environment, "last_accepted_target"),
            json.dumps(signal.to_dict(), separators=(",", ":"), sort_keys=True),
        )

    async def set_last_applied_target(self, signal: StoredSignal) -> None:
        await self.redis.set(
            self._key(signal.payload.portfolio_id, signal.payload.environment, "last_revision_applied"),
            signal.payload.portfolio_revision,
        )
        await self.redis.set(
            self._key(signal.payload.portfolio_id, signal.payload.environment, "last_applied_target"),
            json.dumps(signal.to_dict(), separators=(",", ":"), sort_keys=True),
        )

    async def get_last_accepted_target(self, portfolio_id: str, environment: str) -> StoredSignal | None:
        value = _decode(await self.redis.get(self._key(portfolio_id, environment, "last_accepted_target")))
        if value is None:
            return None
        return StoredSignal.from_dict(json.loads(value))

    async def get_last_applied_target(self, portfolio_id: str, environment: str) -> StoredSignal | None:
        value = _decode(await self.redis.get(self._key(portfolio_id, environment, "last_applied_target")))
        if value is None:
            return None
        return StoredSignal.from_dict(json.loads(value))

    async def get_last_accepted_fingerprint(self, portfolio_id: str, environment: str) -> str | None:
        signal = await self.get_last_accepted_target(portfolio_id, environment)
        return signal.payload.signal_fingerprint if signal is not None else None

    async def set_deferred_target(self, signal: StoredSignal) -> None:
        await self.redis.set(
            self._key(signal.payload.portfolio_id, signal.payload.environment, "deferred_target"),
            json.dumps(signal.to_dict(), separators=(",", ":"), sort_keys=True),
        )

    async def get_deferred_target(self, portfolio_id: str, environment: str) -> StoredSignal | None:
        value = _decode(await self.redis.get(self._key(portfolio_id, environment, "deferred_target")))
        if value is None:
            return None
        return StoredSignal.from_dict(json.loads(value))

    async def clear_deferred_target(self, portfolio_id: str, environment: str) -> None:
        await self.redis.delete(self._key(portfolio_id, environment, "deferred_target"))
