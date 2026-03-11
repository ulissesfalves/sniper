from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
import pandas as pd
import polars as pl
import structlog
from decouple import config

from .utils import hash_payload, recursive_find_first, safe_float

log = structlog.get_logger(__name__)

COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3"
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_FETCH_URL = "https://web.archive.org/web/{timestamp}id_/{url}"


def _detect_coingecko_config(api_key: str) -> tuple[str, dict[str, str], str]:
    if not api_key or api_key == "your_coingecko_pro_key_here":
        return COINGECKO_FREE_URL, {}, "free"
    if api_key.startswith("CG-"):
        return COINGECKO_FREE_URL, {"x-cg-demo-api-key": api_key}, "demo"
    return COINGECKO_PRO_URL, {"x-cg-pro-api-key": api_key}, "pro"


@dataclass(slots=True)
class AssetDescriptor:
    asset_id: str
    symbol: str
    coingecko_id: str | None
    contract_address: str | None = None
    homepage: str | None = None
    mobula_identifier: str | None = None


@dataclass(slots=True)
class WaybackCapture:
    timestamp: str
    original_url: str
    mime_type: str
    status_code: str

    @property
    def capture_dt(self) -> datetime:
        return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


class CoinGeckoUnlockClient:
    RATE_LIMIT_DELAY = 1.25
    PUBLIC_MARKET_HISTORY_DAYS = 90
    RANGE_FALLBACK_DAYS = 90

    def __init__(self, parquet_base: str) -> None:
        api_key = config("COINGECKO_API_KEY", default="")
        self.base_url, self.headers, self.api_mode = _detect_coingecko_config(api_key)
        self.market_dir = Path(parquet_base) / "unlock_market"
        self.market_dir.mkdir(parents=True, exist_ok=True)

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        async with session.get(
            url,
            params=params,
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status == 429:
                await asyncio.sleep(45)
                return await self._get_json(session, url, params=params, allow_404=allow_404)
            if allow_404 and response.status == 404:
                return None
            response.raise_for_status()
            return await response.json()

    async def fetch_current_markets(
        self,
        session: aiohttp.ClientSession,
        descriptors: list[AssetDescriptor],
    ) -> dict[str, dict[str, Any]]:
        ids = [asset.coingecko_id for asset in descriptors if asset.coingecko_id]
        if not ids:
            return {}
        results: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            payload = await self._get_json(
                session,
                f"{self.base_url}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(chunk),
                    "order": "market_cap_desc",
                    "per_page": len(chunk),
                    "page": 1,
                    "sparkline": "false",
                },
            )
            for row in payload or []:
                row["payload_hash"] = hash_payload(row)
                results[str(row.get("id"))] = row
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return results

    async def fetch_coin_details(
        self,
        session: aiohttp.ClientSession,
        coingecko_id: str,
    ) -> dict[str, Any] | None:
        if not coingecko_id:
            return None
        payload = await self._get_json(
            session,
            f"{self.base_url}/coins/{coingecko_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            allow_404=True,
        )
        await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return payload

    async def ensure_market_history(
        self,
        session: aiohttp.ClientSession,
        asset: AssetDescriptor,
    ) -> pl.DataFrame:
        path = self.market_dir / f"{asset.asset_id}.parquet"
        existing = self._load_market_history_cache(path)
        if not existing.is_empty():
            latest = existing.select(pl.col("date").max()).item()
            if latest is not None and latest >= date.today():
                return existing.sort("date")

        if not asset.coingecko_id:
            return existing

        payload = await self._fetch_market_history_payload(session, asset)
        if payload is None:
            return existing

        history = self._parse_market_chart(payload, asset.asset_id)
        merged = self._merge_market_history(existing, history)
        if not merged.is_empty():
            self._write_market_history_cache(path, merged)
        return merged

    def _load_market_history_cache(self, path: Path) -> pl.DataFrame:
        if not path.exists():
            return pl.DataFrame()
        try:
            cached = pl.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("unlock.market_history_cache_read_failed", path=str(path), error=str(exc))
            return pl.DataFrame()
        return cached.sort("date")

    def _merge_market_history(self, existing: pl.DataFrame, incoming: pl.DataFrame) -> pl.DataFrame:
        if existing.is_empty():
            return incoming.sort("date") if not incoming.is_empty() else pl.DataFrame()
        if incoming.is_empty():
            return existing.sort("date")

        combined = pd.concat(
            [existing.to_pandas(), incoming.to_pandas()],
            ignore_index=True,
            sort=False,
        )
        if combined.empty:
            return pl.DataFrame()
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce").dt.date
        combined = combined.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
        return pl.from_pandas(combined).sort("date")

    def _write_market_history_cache(self, path: Path, frame: pl.DataFrame) -> None:
        tmp_path = path.with_suffix(".parquet.tmp")
        frame.write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(path)

    async def _fetch_market_history_payload(
        self,
        session: aiohttp.ClientSession,
        asset: AssetDescriptor,
    ) -> dict[str, Any] | None:
        requests: list[tuple[str, str, dict[str, Any]]] = []
        if self.api_mode == "pro":
            requests.append(
                (
                    "market_chart_days_max",
                    f"{self.base_url}/coins/{asset.coingecko_id}/market_chart",
                    {"vs_currency": "usd", "days": "max", "interval": "daily"},
                )
            )
        else:
            requests.append(
                (
                    "market_chart_days_365",
                    f"{self.base_url}/coins/{asset.coingecko_id}/market_chart",
                    {"vs_currency": "usd", "days": str(self.PUBLIC_MARKET_HISTORY_DAYS), "interval": "daily"},
                )
            )

        end_dt = datetime.now(tz=UTC).replace(microsecond=0)
        start_dt = end_dt - timedelta(days=self.RANGE_FALLBACK_DAYS)
        requests.append(
            (
                "market_chart_range_90d",
                f"{self.base_url}/coins/{asset.coingecko_id}/market_chart/range",
                {
                    "vs_currency": "usd",
                    "from": int(start_dt.timestamp()),
                    "to": int(end_dt.timestamp()),
                },
            )
        )

        for endpoint_label, url, params in requests:
            try:
                payload = await self._get_json(session, url, params=params, allow_404=True)
            except aiohttp.ClientResponseError as exc:
                log.warning(
                    "unlock.market_history_request_failed",
                    asset_id=asset.asset_id,
                    cg_id=asset.coingecko_id,
                    endpoint=endpoint_label,
                    api_mode=self.api_mode,
                    status=exc.status,
                    error=str(exc),
                )
                if exc.status not in {401, 403, 404, 429}:
                    return None
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "unlock.market_history_request_exception",
                    asset_id=asset.asset_id,
                    cg_id=asset.coingecko_id,
                    endpoint=endpoint_label,
                    api_mode=self.api_mode,
                    error=str(exc),
                )
                continue

            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            if payload:
                if endpoint_label != requests[0][0]:
                    log.warning(
                        "unlock.market_history_fallback_used",
                        asset_id=asset.asset_id,
                        cg_id=asset.coingecko_id,
                        endpoint=endpoint_label,
                        api_mode=self.api_mode,
                    )
                return payload
        return None

    def _parse_market_chart(self, payload: dict[str, Any], asset_id: str) -> pl.DataFrame:
        prices = payload.get("prices") or []
        market_caps = payload.get("market_caps") or []
        total_volumes = payload.get("total_volumes") or []
        by_date: dict[date, dict[str, float | str | None]] = {}

        def _attach(rows: list[list[Any]], key: str) -> None:
            for stamp_ms, value in rows:
                day = datetime.fromtimestamp(float(stamp_ms) / 1000.0, tz=UTC).date()
                by_date.setdefault(day, {"asset_id": asset_id, "date": day})[key] = safe_float(value)

        _attach(prices, "price_usd")
        _attach(market_caps, "market_cap")
        _attach(total_volumes, "total_volume")
        if not by_date:
            return pl.DataFrame()

        records: list[dict[str, Any]] = []
        for day in sorted(by_date):
            row = dict(by_date[day])
            price = safe_float(row.get("price_usd"))
            market_cap = safe_float(row.get("market_cap"))
            circ_approx = None
            if price and price > 0 and market_cap and market_cap > 0:
                circ_approx = market_cap / price
            row["circ_approx"] = circ_approx
            records.append(row)

        return (
            pl.DataFrame(records)
            .with_columns(
                pl.col("date").cast(pl.Date),
                pl.col("asset_id").cast(pl.Utf8),
                pl.col("price_usd").cast(pl.Float64),
                pl.col("market_cap").cast(pl.Float64),
                pl.col("total_volume").cast(pl.Float64),
                pl.col("circ_approx").cast(pl.Float64),
            )
            .sort("date")
        )

    def build_descriptors(self, universe: list[Any]) -> list[AssetDescriptor]:
        descriptors: list[AssetDescriptor] = []
        for item in universe:
            if isinstance(item, AssetDescriptor):
                descriptors.append(item)
                continue
            symbol = getattr(item, "symbol", str(item)).upper()
            descriptors.append(
                AssetDescriptor(
                    asset_id=symbol,
                    symbol=symbol,
                    coingecko_id=getattr(item, "coingecko_id", None),
                )
            )
        return descriptors


class MobulaClient:
    RATE_LIMIT_DELAY = 0.45

    def __init__(self) -> None:
        self.base_url = config("MOBULA_API_BASE_URL", default="https://api.mobula.io/api/1").rstrip("/")
        self.metadata_paths = [
            config("MOBULA_METADATA_PATH", default="/metadata"),
            config("MOBULA_MULTI_METADATA_PATH", default="/multi-metadata"),
        ]
        api_key = config("MOBULA_API_KEY", default="")
        self.headers: dict[str, str] = {}
        if api_key:
            self.headers["Authorization"] = api_key
            self.headers["x-api-key"] = api_key

    async def _fetch_candidate(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, Any],
    ) -> tuple[int, Any]:
        async with session.get(
            url,
            params=params,
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            status = response.status
            if status == 429:
                await asyncio.sleep(30)
                return await self._fetch_candidate(session, url, params)
            if status in {400, 404}:
                return status, None
            if status >= 500:
                return status, None
            payload = await response.json(content_type=None)
            return status, payload

    async def fetch_metadata(
        self,
        session: aiohttp.ClientSession,
        asset: AssetDescriptor,
    ) -> tuple[int, dict[str, Any] | None, bool]:
        lookup_values = [
            asset.mobula_identifier,
            asset.contract_address,
            asset.coingecko_id,
            asset.symbol,
        ]
        params_candidates: list[dict[str, Any]] = []
        for value in lookup_values:
            if not value:
                continue
            params_candidates.extend(
                [
                    {"asset": value},
                    {"symbol": value},
                    {"id": value},
                ]
            )

        best_status = 404
        best_payload: dict[str, Any] | None = None
        is_match = False
        for path in self.metadata_paths:
            full_url = f"{self.base_url}{path}"
            for params in params_candidates:
                status, payload = await self._fetch_candidate(session, full_url, params)
                best_status = status
                if payload:
                    if best_payload is None:
                        best_payload = payload
                    if self._payload_matches_asset(payload, asset):
                        best_payload = payload
                        is_match = True
                        break
                    payload_symbol = str(self._payload_data(payload).get("symbol") or "").upper()
                    log.warning(
                        "unlock.mobula_payload_mismatch",
                        asset_id=asset.asset_id,
                        requested_symbol=asset.symbol,
                        returned_symbol=payload_symbol or None,
                        params=params,
                    )
            if is_match:
                break
        await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return best_status, best_payload, is_match

    @staticmethod
    def _payload_data(payload: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("data")
        if isinstance(nested, dict):
            return nested
        return payload

    @classmethod
    def _payload_matches_asset(cls, payload: dict[str, Any] | None, asset: AssetDescriptor) -> bool:
        data = cls._payload_data(payload)
        if not data:
            return False

        symbol = str(data.get("symbol") or "").upper()
        if asset.symbol and symbol and symbol == asset.symbol.upper():
            return True

        contracts = {str(item).lower() for item in (data.get("contracts") or []) if item}
        if asset.contract_address and asset.contract_address.lower() in contracts:
            return True

        return False

    @staticmethod
    def extract_schedule_distribution(payload: dict[str, Any] | None) -> tuple[Any, Any]:
        if not payload:
            return None, None
        data = MobulaClient._payload_data(payload)
        schedule = recursive_find_first(data, {"release_schedule", "releaseschedule", "unlocks", "releasecalendar"})
        distribution = recursive_find_first(data, {"distribution", "allocations", "allocation"})
        return schedule, distribution

    @staticmethod
    def extract_market_metrics(payload: dict[str, Any] | None) -> dict[str, float | None]:
        if not payload:
            return {
                "price_usd": None,
                "circulating_supply": None,
                "total_supply": None,
                "market_cap": None,
                "volume": None,
            }
        data = MobulaClient._payload_data(payload)
        return {
            "price_usd": safe_float(recursive_find_first(data, {"price", "priceusd", "current_price"})),
            "circulating_supply": safe_float(
                recursive_find_first(data, {"circulating_supply", "circulatingsupply", "circulating"})
            ),
            "total_supply": safe_float(recursive_find_first(data, {"total_supply", "totalsupply", "max_supply"})),
            "market_cap": safe_float(recursive_find_first(data, {"market_cap", "marketcap"})),
            "volume": safe_float(recursive_find_first(data, {"volume", "total_volume", "volume24h"})),
        }


class DefiLlamaUnlockClient:
    def __init__(self) -> None:
        self.endpoint = config("DEFILLAMA_UNLOCKS_ENDPOINT", default="").strip()

    async def fetch_asset_page(
        self,
        session: aiohttp.ClientSession,
        asset: AssetDescriptor,
    ) -> tuple[int, dict[str, Any] | None]:
        if not self.endpoint:
            return 404, None
        async with session.get(
            self.endpoint,
            params={"symbol": asset.symbol, "asset_id": asset.asset_id},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status >= 400:
                return response.status, None
            text = await response.text()
            payload = {
                "symbol": asset.symbol,
                "rows": list(csv.DictReader(StringIO(text))) if "," in text else text,
            }
            return response.status, payload


class WaybackClient:
    RATE_LIMIT_DELAY = 0.20
    JSON_TIMEOUT_SECONDS = 25
    FETCH_TIMEOUT_SECONDS = 45

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, Any],
        attempt: int = 0,
    ) -> Any:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=self.JSON_TIMEOUT_SECONDS)) as response:
            if response.status == 429:
                await asyncio.sleep(10)
                return await self._get_json(session, url, params, attempt=attempt + 1)
            if response.status in {502, 503, 504}:
                if attempt >= 2:
                    return None
                await asyncio.sleep(5 * (attempt + 1))
                return await self._get_json(session, url, params, attempt=attempt + 1)
            if response.status in {400, 404}:
                return None
            response.raise_for_status()
            return await response.json(content_type=None)

    async def query_captures(
        self,
        session: aiohttp.ClientSession,
        url: str,
        latest_before: date,
    ) -> list[WaybackCapture]:
        payload = await self._get_json(
            session,
            WAYBACK_CDX_URL,
            params={
                "url": url,
                "output": "json",
                "fl": "timestamp,original,mimetype,statuscode",
                "filter": "statuscode:200",
                "to": latest_before.strftime("%Y%m%d235959"),
                "collapse": "digest",
            },
        )
        await asyncio.sleep(self.RATE_LIMIT_DELAY)
        if not payload or len(payload) <= 1:
            return []
        captures: list[WaybackCapture] = []
        for row in payload[1:]:
            if len(row) < 4:
                continue
            captures.append(
                WaybackCapture(
                    timestamp=str(row[0]),
                    original_url=str(row[1]),
                    mime_type=str(row[2]),
                    status_code=str(row[3]),
                )
            )
        captures.sort(key=lambda item: item.timestamp)
        return captures

    async def fetch_capture(
        self,
        session: aiohttp.ClientSession,
        capture: WaybackCapture,
    ) -> tuple[str, bytes]:
        async with session.get(
            WAYBACK_FETCH_URL.format(timestamp=capture.timestamp, url=capture.original_url),
            timeout=aiohttp.ClientTimeout(total=self.FETCH_TIMEOUT_SECONDS),
        ) as response:
            response.raise_for_status()
            mime_type = response.headers.get("Content-Type", capture.mime_type or "")
            content = await response.read()
        await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return mime_type, content


def build_official_url_candidates(details: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not details:
        return []

    links = details.get("links") or {}
    candidates: dict[str, dict[str, Any]] = {}
    base_urls = [url for url in (links.get("homepage") or []) if url]
    for base_url in base_urls:
        parsed = urlparse(base_url)
        domain = parsed.netloc.lower()
        if not domain:
            continue
        candidates[base_url] = {
            "url": base_url,
            "url_type": "homepage",
            "domain": domain,
            "is_official": 1,
        }
        for suffix, url_type in (
            ("/tokenomics", "tokenomics"),
            ("/vesting", "vesting"),
            ("/unlocks", "unlocks"),
            ("/docs/tokenomics", "docs"),
            ("/whitepaper", "whitepaper"),
            ("/litepaper", "litepaper"),
            ("/blog", "blog"),
        ):
            guess = urljoin(base_url.rstrip("/") + "/", suffix.lstrip("/"))
            candidates[guess] = {
                "url": guess,
                "url_type": url_type,
                "domain": domain,
                "is_official": 1,
            }

    whitepaper_url = links.get("whitepaper")
    if whitepaper_url:
        parsed = urlparse(whitepaper_url)
        candidates[whitepaper_url] = {
            "url": whitepaper_url,
            "url_type": "whitepaper",
            "domain": parsed.netloc.lower(),
            "is_official": 1,
        }

    docs_urls = links.get("official_forum_url") or []
    for url in docs_urls:
        if not url:
            continue
        parsed = urlparse(url)
        candidates[url] = {
            "url": url,
            "url_type": "official_forum",
            "domain": parsed.netloc.lower(),
            "is_official": 1,
        }
    return list(candidates.values())
