from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd

CANONICAL_BUCKET_WEIGHTS: dict[str, float] = {
    "Team/Founders": 1.5,
    "VC/Investors": 1.2,
    "Ecosystem": 0.8,
    "Airdrop/Public": 0.6,
}
CANONICAL_BUCKETS: tuple[str, ...] = tuple(CANONICAL_BUCKET_WEIGHTS.keys())
OTHER_BUCKET = "Other"

BUCKET_ALIASES: dict[str, str] = {
    "team": "Team/Founders",
    "founder": "Team/Founders",
    "founders": "Team/Founders",
    "founder / team": "Team/Founders",
    "founders / team": "Team/Founders",
    "core contributors": "Team/Founders",
    "contributors": "Team/Founders",
    "contributor sale": "Team/Founders",
    "advisors": "Team/Founders",
    "advisor": "Team/Founders",
    "employee": "Team/Founders",
    "employees": "Team/Founders",
    "private investors": "VC/Investors",
    "public investors": "VC/Investors",
    "investor": "VC/Investors",
    "investors": "VC/Investors",
    "backers": "VC/Investors",
    "seed": "VC/Investors",
    "seed sale": "VC/Investors",
    "private sale": "VC/Investors",
    "public sale": "Airdrop/Public",
    "strategic sale": "VC/Investors",
    "strategic": "VC/Investors",
    "ido": "Airdrop/Public",
    "ieo": "Airdrop/Public",
    "ico": "Airdrop/Public",
    "launchpool": "Airdrop/Public",
    "launchpad": "Airdrop/Public",
    "community": "Airdrop/Public",
    "airdrop": "Airdrop/Public",
    "testnet incentive": "Airdrop/Public",
    "treasury": "Ecosystem",
    "foundation": "Ecosystem",
    "ecosystem": "Ecosystem",
    "rewards": "Ecosystem",
    "reward": "Ecosystem",
    "reserve": "Ecosystem",
    "reserved": "Ecosystem",
    "grants": "Ecosystem",
    "grant": "Ecosystem",
    "incentives": "Ecosystem",
    "liquidity mining": "Airdrop/Public",
}


@dataclass(slots=True)
class ParsedUnlockEvent:
    event_date: date
    raw_label: str
    bucket: str
    tokens: float


@dataclass(slots=True)
class ParsedDistributionItem:
    raw_label: str
    bucket: str
    value: float


def canonical_json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def hash_payload(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = canonical_json_dumps(payload).encode("utf-8")
    return sha256(raw).hexdigest()


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-", "nan", "NaN"):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def parse_date_like(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw[:25]).date()
    except ValueError:
        return None


def normalize_bucket_label(label: str | None) -> str:
    if not label:
        return OTHER_BUCKET
    raw = " ".join(str(label).strip().lower().replace("_", " ").replace("-", " ").split())
    if raw in BUCKET_ALIASES:
        return BUCKET_ALIASES[raw]
    upper = raw.upper()
    if any(key in upper for key in ("TEAM", "FOUNDER", "ADVISOR", "CONTRIBUTOR", "EMPLOYEE")):
        return "Team/Founders"
    if any(
        key in upper
        for key in (
            "INVESTOR",
            "BACKER",
            "PRIVATE",
            "SEED",
            "STRATEGIC",
            "SERIES",
            "ICO",
            "IEO",
            "LAUNCHPAD",
            "LAUNCHPOOL",
        )
    ):
        return "VC/Investors" if "PUBLIC" not in upper else "Airdrop/Public"
    if any(key in upper for key in ("FOUNDATION", "TREASURY", "ECOSYSTEM", "RESERVE", "GRANT", "INCENTIVE")):
        return "Ecosystem"
    if any(key in upper for key in ("AIRDROP", "COMMUNITY", "PUBLIC", "TESTNET", "REWARD", "LIQUIDITY MINING")):
        return "Airdrop/Public"
    return OTHER_BUCKET


def winsorize_cross_section(
    values: pd.Series,
    lower: float = 0.01,
    upper: float = 0.99,
) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return numeric.astype(float)
    lo = float(valid.quantile(lower))
    hi = float(valid.quantile(upper))
    return numeric.clip(lower=lo, upper=hi)


def percent_rank_average(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    valid = numeric.dropna()
    if valid.empty:
        return result
    n_obs = len(valid)
    if n_obs == 1:
        result.loc[valid.index] = 0.5
        return result
    ranks = valid.rank(method="average", ascending=True)
    result.loc[valid.index] = (ranks - 1.0) / float(n_obs - 1)
    return result


def compute_unknown_bucket_ratio(events: list[ParsedUnlockEvent | dict[str, Any]]) -> float:
    total = 0.0
    unknown = 0.0
    for event in events:
        bucket = event.bucket if isinstance(event, ParsedUnlockEvent) else str(event.get("bucket") or OTHER_BUCKET)
        tokens = event.tokens if isinstance(event, ParsedUnlockEvent) else safe_float(event.get("tokens"), 0.0) or 0.0
        total += max(tokens, 0.0)
        if bucket == OTHER_BUCKET:
            unknown += max(tokens, 0.0)
    if total <= 0:
        return 0.0
    return float(unknown / total)


def compute_ups_raw(
    events: list[ParsedUnlockEvent | dict[str, Any]],
    as_of_date: date,
    circ: float,
    eps: float = 1e-9,
) -> float | None:
    circ = max(float(circ), 0.0)
    if circ <= 0:
        return None
    raw_score = 0.0
    window_end = as_of_date + pd.Timedelta(days=30)
    window_end_date = window_end.date() if hasattr(window_end, "date") else window_end
    for event in events:
        event_date = event.event_date if isinstance(event, ParsedUnlockEvent) else parse_date_like(event.get("event_date"))
        bucket = event.bucket if isinstance(event, ParsedUnlockEvent) else str(event.get("bucket") or OTHER_BUCKET)
        tokens = event.tokens if isinstance(event, ParsedUnlockEvent) else safe_float(event.get("tokens"), 0.0) or 0.0
        if event_date is None or event_date <= as_of_date or event_date > window_end_date:
            continue
        weight = CANONICAL_BUCKET_WEIGHTS.get(bucket)
        if weight is None:
            continue
        raw_score += weight * (float(tokens) / max(circ, eps))
    return float(raw_score)


def compute_insider_share(
    distribution_items: list[ParsedDistributionItem | dict[str, Any]],
    eps: float = 1e-9,
) -> float | None:
    if not distribution_items:
        return None
    insider = 0.0
    total = 0.0
    for item in distribution_items:
        bucket = item.bucket if isinstance(item, ParsedDistributionItem) else str(item.get("bucket") or OTHER_BUCKET)
        value = item.value if isinstance(item, ParsedDistributionItem) else safe_float(item.get("value"), 0.0) or 0.0
        if value < 0:
            continue
        if bucket in CANONICAL_BUCKET_WEIGHTS:
            total += value
        if bucket in {"Team/Founders", "VC/Investors"}:
            insider += value
    if total <= eps:
        return None
    return float(np.clip(insider / total, 0.0, 1.0))


def recursive_find_first(payload: Any, candidate_keys: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in candidate_keys:
                return value
        for value in payload.values():
            found = recursive_find_first(value, candidate_keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = recursive_find_first(item, candidate_keys)
            if found is not None:
                return found
    return None


def recursive_collect_lists(payload: Any, candidate_keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in candidate_keys and isinstance(value, list):
                found.append(value)
            found.extend(recursive_collect_lists(value, candidate_keys))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(recursive_collect_lists(item, candidate_keys))
    return found
