from __future__ import annotations

import asyncio
from collections import Counter
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import aiohttp
import numpy as np
import pandas as pd
import structlog
from decouple import config

from .unlock_support.historical import (
    ParsedDocument,
    build_normalized_event_rows,
    choose_best_capture,
    compute_reconstructed_insider_share,
    deterministic_parse,
    discover_official_urls,
    validate_reconstruction,
)
from .unlock_support.providers import (
    AssetDescriptor,
    CoinGeckoUnlockClient,
    DefiLlamaUnlockClient,
    MobulaClient,
    WaybackClient,
)
from .unlock_support.store import UnlockFeatureStore
from .unlock_support.utils import (
    ParsedDistributionItem,
    ParsedUnlockEvent,
    compute_insider_share,
    compute_unknown_bucket_ratio,
    compute_ups_raw,
    hash_payload,
    normalize_bucket_label,
    parse_date_like,
    percent_rank_average,
    safe_float,
    winsorize_cross_section,
)

log = structlog.get_logger(__name__)

MANDATORY_RECONSTRUCTION_ASSETS = {"LUNA", "LUNC", "LUNA2", "FTT", "CEL"}
DEFAULT_RECONSTRUCTION_ANCHORS = {"SOL", "ADA", "AVAX", "DOT", "ATOM", "LINK", "ARB", "OP", "APT", "MATIC", "POL"}


@dataclass(slots=True)
class LayerComputation:
    raw: float | None = None
    rank: float | None = None
    is_valid: bool = False
    confidence: float | None = None
    unknown_ratio: float = 0.0
    insider_share: float | None = None
    quality_flag: str = "missing"
    source_primary: str | None = None
    snapshot_ts: str | None = None
    circ: float | None = None
    total_supply: float | None = None
    distribution_items: list[ParsedDistributionItem] = field(default_factory=list)


class TokenUnlocksCollector:
    def __init__(
        self,
        parquet_base: str | None = None,
        sqlite_path: str | None = None,
        runtime_history_mode: str | None = None,
    ) -> None:
        self.parquet_base = parquet_base or config("PARQUET_BASE_PATH", default="/data/parquet")
        self.sqlite_path = sqlite_path or config("SQLITE_PATH", default="/data/sqlite/sniper.db")
        self.store = UnlockFeatureStore(self.sqlite_path, self.parquet_base)
        self.cg_client = CoinGeckoUnlockClient(self.parquet_base)
        self.mobula_client = MobulaClient()
        self.defillama_client = DefiLlamaUnlockClient()
        self.wayback_client = WaybackClient()

        self.eps = float(config("UNLOCK_EPS", default="1e-9"))
        self.winsor_low = float(config("UNLOCK_WINSOR_LOW", default="0.01"))
        self.winsor_high = float(config("UNLOCK_WINSOR_HIGH", default="0.99"))
        self.unknown_threshold = float(config("UNLOCK_UNKNOWN_BUCKET_MAX", default="0.15"))
        self.reconstruction_promote_min = float(config("UNLOCK_RECONSTRUCTION_CONFIDENCE_MIN", default="0.85"))
        self.reconstruction_evidence_min = float(config("UNLOCK_RECONSTRUCTION_EVIDENCE_MIN", default="0.70"))
        self.observed_shadow_min_snapshots = int(config("UNLOCK_OBSERVED_SHADOW_MIN_SNAPSHOTS", default="30"))
        self.runtime_history_mode = (
            runtime_history_mode
            or config("UNLOCK_RUNTIME_HISTORY_MODE", default="latest_only")
        ).strip().lower()
        self.reconstruction_max_urls = max(
            int(config("UNLOCK_RECONSTRUCTION_MAX_URLS_PER_ASSET", default="4")),
            1,
        )
        anchors_raw = config(
            "UNLOCK_RECONSTRUCTION_ANCHORS",
            default=",".join(sorted(DEFAULT_RECONSTRUCTION_ANCHORS)),
        )
        self.reconstruction_targets = MANDATORY_RECONSTRUCTION_ASSETS | {
            item.strip().upper() for item in anchors_raw.split(",") if item.strip()
        }

    async def fetch_current_ups_scores(self, assets: list[Any]) -> dict[str, float | None]:
        if not assets:
            return {}
        descriptors = self.cg_client.build_descriptors(assets)
        async with aiohttp.ClientSession() as session:
            descriptors = await self._prepare_descriptors(session, descriptors)
            live_bundle = await self._collect_live_snapshots(session, descriptors)

        scores: dict[str, float | None] = {}
        today = date.today()
        gate_counts: Counter[str] = Counter()
        for descriptor in descriptors:
            market_ctx = live_bundle.get(descriptor.asset_id, {})
            observed = self._compute_observed_layer(
                descriptor=descriptor,
                as_of_date=today,
                mobula_payload=market_ctx.get("mobula_payload"),
                mobula_snapshot_ts=market_ctx.get("mobula_snapshot_ts"),
                market_row=market_ctx.get("market_row"),
            )
            scores[descriptor.asset_id] = observed.raw if observed.is_valid else None
            if not observed.is_valid:
                gate_counts[observed.quality_flag] += 1
        log.info(
            "unlock.current_ups_summary",
            n_assets=len(descriptors),
            available=sum(1 for value in scores.values() if value is not None),
            missing=sum(1 for value in scores.values() if value is None),
            gate_counts=dict(sorted(gate_counts.items())),
            mobula_http_ok=sum(
                1 for descriptor in descriptors if safe_float((live_bundle.get(descriptor.asset_id) or {}).get("mobula_status"), 0.0) == 200.0
            ),
            mobula_payload_validated=sum(
                1 for descriptor in descriptors if bool((live_bundle.get(descriptor.asset_id) or {}).get("mobula_payload"))
            ),
            coingecko_market_ok=sum(
                1 for descriptor in descriptors if bool((live_bundle.get(descriptor.asset_id) or {}).get("market_row_present"))
            ),
        )
        return scores

    async def fetch_and_store(self, universe: list[Any]) -> list[Any]:
        if not universe:
            return []

        descriptors = self.cg_client.build_descriptors(universe)
        async with aiohttp.ClientSession() as session:
            descriptors = await self._prepare_descriptors(session, descriptors)
            reconstruction_descriptors = [descriptor for descriptor in descriptors if descriptor.asset_id in self.reconstruction_targets]
            live_bundle = await self._collect_live_snapshots(session, descriptors)
            log.info(
                "unlock.phase_start",
                phase="coin_details",
                n_assets=len(reconstruction_descriptors),
                runtime_history_mode=self.runtime_history_mode,
            )
            details_map = await self._collect_coin_details(session, reconstruction_descriptors)
            log.info(
                "unlock.phase_complete",
                phase="coin_details",
                n_assets=len(reconstruction_descriptors),
                resolved=sum(1 for payload in details_map.values() if payload),
            )
            log.info("unlock.phase_start", phase="market_history", n_assets=len(descriptors))
            history_map: dict[str, pd.DataFrame] = {}
            history_empty = 0
            for index, descriptor in enumerate(descriptors, start=1):
                history = await self._build_market_history(
                    session,
                    descriptor,
                    (live_bundle.get(descriptor.asset_id) or {}).get("market_row"),
                )
                if history.empty:
                    history_empty += 1
                history_map[descriptor.asset_id] = history
                if index == len(descriptors) or index % 10 == 0:
                    log.info(
                        "unlock.market_history_progress",
                        processed=index,
                        total=len(descriptors),
                        empty=history_empty,
                    )
            log.info(
                "unlock.phase_complete",
                phase="market_history",
                n_assets=len(descriptors),
                empty=history_empty,
            )
            log.info("unlock.phase_start", phase="reconstructed_layers", n_assets=len(reconstruction_descriptors))
            reconstructed_map = await self._collect_reconstructed_layers(
                session=session,
                descriptors=descriptors,
                history_map=history_map,
                details_map=details_map,
            )
            log.info(
                "unlock.phase_complete",
                phase="reconstructed_layers",
                n_assets=len(reconstruction_descriptors),
                produced=sum(len(layer_map) for layer_map in reconstructed_map.values()),
            )

        feature_rows: list[dict[str, Any]] = []
        diagnostic_rows: list[dict[str, Any]] = []

        for descriptor in descriptors:
            asset_id = descriptor.asset_id
            history = self._select_runtime_feature_history(history_map.get(asset_id, pd.DataFrame()))
            if history.empty:
                log.warning("unlock.market_history_missing", asset_id=asset_id)
                continue
            observed_map = self._load_observed_layers(asset_id, history)
            reconstructed_layers = reconstructed_map.get(asset_id, {})
            for _, row in history.iterrows():
                as_of_date = row["date"]
                observed = observed_map.get(as_of_date, LayerComputation())
                reconstructed = reconstructed_layers.get(as_of_date, LayerComputation())
                insider_share = self._choose_insider_share(observed, reconstructed)
                total_supply_pti = self._choose_total_supply(row, observed, reconstructed)
                proxy_full = self._compute_proxy_full(row, total_supply_pti, insider_share)
                proxy_fallback = self._compute_proxy_fallback(row, insider_share)

                unknown_ratio = max(observed.unknown_ratio, reconstructed.unknown_ratio)
                quality_flag = self._choose_quality_flag(observed, reconstructed, proxy_full, proxy_fallback)
                feature_rows.append(
                    {
                        "date": as_of_date.isoformat(),
                        "asset_id": asset_id,
                        "ups_raw_observed": observed.raw,
                        "unlock_pressure_rank_observed": np.nan,
                        "ups_raw_reconstructed": reconstructed.raw,
                        "unlock_pressure_rank_reconstructed": np.nan,
                        "proxy_full_raw": proxy_full.raw,
                        "unlock_overhang_proxy_rank_full": np.nan,
                        "proxy_fallback_raw": proxy_fallback.raw,
                        "unlock_fragility_proxy_rank_fallback": np.nan,
                        "unlock_pressure_rank_selected_for_reporting": np.nan,
                        "unlock_feature_state": "MISSING",
                        "unknown_bucket_ratio": unknown_ratio,
                        "quality_flag": quality_flag,
                        "reconstruction_confidence": reconstructed.confidence,
                        "_obs_quality": observed.quality_flag,
                        "_rec_quality": reconstructed.quality_flag,
                    }
                )
                diagnostic_rows.append(
                    {
                        "date": as_of_date.isoformat(),
                        "asset_id": asset_id,
                        "circ": safe_float(row.get("circ_approx")),
                        "total_supply": total_supply_pti,
                        "insider_share_pti": insider_share,
                        "insider_share_mode": self._insider_share_mode(observed, reconstructed, insider_share),
                        "avg_30d_volume": safe_float(row.get("avg_30d_volume")),
                        "market_cap": safe_float(row.get("market_cap")),
                        "source_primary": observed.source_primary or reconstructed.source_primary or proxy_full.source_primary or proxy_fallback.source_primary,
                        "snapshot_ts": observed.snapshot_ts or reconstructed.snapshot_ts,
                        "supply_history_mode": self._supply_history_mode(row, observed, reconstructed, total_supply_pti),
                    }
                )

        ranked_rows = self._apply_ranking_and_state(feature_rows)
        quality_rows = self._build_quality_rows(
            ranked_rows=ranked_rows,
            diagnostic_rows=diagnostic_rows,
            descriptors=descriptors,
            live_bundle=live_bundle,
            reconstructed_map=reconstructed_map,
        )
        self.store.persist_feature_rows(ranked_rows)
        self.store.persist_diagnostic_rows(diagnostic_rows)
        self.store.persist_quality_rows(quality_rows)
        exported = self.store.export_feature_parquet([descriptor.asset_id for descriptor in descriptors])
        quality_path = self.store.export_quality_parquet()
        if quality_rows:
            log.info("unlock.quality_summary", **quality_rows[-1])
        log.info(
            "unlock.features_saved",
            n_assets=len(exported),
            sqlite_path=str(self.sqlite_path),
            quality_parquet=str(quality_path) if quality_path else None,
        )
        return exported

    async def _prepare_descriptors(
        self,
        session: aiohttp.ClientSession,
        descriptors: list[AssetDescriptor],
    ) -> list[AssetDescriptor]:
        unresolved = [descriptor for descriptor in descriptors if not descriptor.coingecko_id]
        if unresolved:
            mapping = await self._resolve_symbol_ids(session, [descriptor.symbol for descriptor in unresolved])
            descriptors = [
                AssetDescriptor(
                    asset_id=descriptor.asset_id,
                    symbol=descriptor.symbol,
                    coingecko_id=descriptor.coingecko_id or mapping.get(descriptor.symbol),
                    contract_address=descriptor.contract_address,
                    homepage=descriptor.homepage,
                    mobula_identifier=descriptor.mobula_identifier,
                )
                for descriptor in descriptors
            ]
        return descriptors

    async def _resolve_symbol_ids(
        self,
        session: aiohttp.ClientSession,
        symbols: list[str],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}
        if not symbols:
            return mapping
        try:
            async with session.get(
                f"{self.cg_client.base_url}/coins/list",
                headers=self.cg_client.headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("unlock.resolve_symbols_failed", error=str(exc))
            return mapping

        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in payload or []:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            buckets.setdefault(symbol, []).append(row)

        for symbol in symbols:
            matches = buckets.get(symbol.upper(), [])
            if not matches:
                continue
            matches.sort(key=lambda row: str(row.get("id") or ""))
            mapping[symbol.upper()] = str(matches[0].get("id"))
        return mapping

    async def _collect_coin_details(
        self,
        session: aiohttp.ClientSession,
        descriptors: list[AssetDescriptor],
    ) -> dict[str, dict[str, Any] | None]:
        details_map: dict[str, dict[str, Any] | None] = {}
        for descriptor in descriptors:
            details_map[descriptor.asset_id] = await self.cg_client.fetch_coin_details(session, descriptor.coingecko_id or "")
        return details_map

    async def _collect_live_snapshots(
        self,
        session: aiohttp.ClientSession,
        descriptors: list[AssetDescriptor],
    ) -> dict[str, dict[str, Any]]:
        snapshot_ts = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
        market_map = await self.cg_client.fetch_current_markets(session, descriptors)
        bundle: dict[str, dict[str, Any]] = {}

        cg_rows: list[dict[str, Any]] = []
        mobula_rows: list[dict[str, Any]] = []
        defillama_rows: list[dict[str, Any]] = []
        matched_schedule_present = 0
        matched_distribution_present = 0

        for descriptor in descriptors:
            market_row = market_map.get(descriptor.coingecko_id or "", {})
            normalized_market_row = self._normalize_market_row(market_row)
            if market_row:
                cg_rows.append(
                    {
                        "snapshot_ts": snapshot_ts,
                        "cg_id": descriptor.coingecko_id,
                        "payload_json": market_row,
                        "payload_hash": hash_payload(market_row),
                        "http_status": 200,
                    }
                )

            mobula_status, mobula_raw_payload, mobula_payload_matched = await self.mobula_client.fetch_metadata(session, descriptor)
            if mobula_payload_matched and mobula_raw_payload:
                schedule_payload, distribution_payload = self.mobula_client.extract_schedule_distribution(mobula_raw_payload)
                if self._payload_has_rows(schedule_payload):
                    matched_schedule_present += 1
                if self._payload_has_rows(distribution_payload):
                    matched_distribution_present += 1
            if mobula_raw_payload:
                mobula_rows.append(
                    {
                        "snapshot_ts": snapshot_ts,
                        "asset_id": descriptor.asset_id,
                        "payload_json": mobula_raw_payload,
                        "payload_hash": hash_payload(mobula_raw_payload),
                        "http_status": mobula_status,
                    }
                )

            defillama_status, defillama_payload = await self.defillama_client.fetch_asset_page(session, descriptor)
            if defillama_payload:
                defillama_rows.append(
                    {
                        "snapshot_ts": snapshot_ts,
                        "asset_id": descriptor.asset_id,
                        "page_type": "asset",
                        "extracted_json": defillama_payload,
                        "payload_hash": hash_payload(defillama_payload),
                    }
                )

            bundle[descriptor.asset_id] = {
                "snapshot_ts": snapshot_ts,
                "coingecko_status": 200 if market_row else 404,
                "market_row": normalized_market_row,
                "market_row_present": bool(market_row),
                "mobula_payload": mobula_raw_payload if mobula_payload_matched else None,
                "mobula_status": mobula_status,
                "mobula_snapshot_ts": snapshot_ts if mobula_payload_matched and mobula_raw_payload else None,
                "mobula_payload_matched": mobula_payload_matched,
                "defillama_payload": defillama_payload,
                "defillama_status": defillama_status,
            }

        self.store.persist_raw_json("raw_coingecko_snapshots", cg_rows)
        self.store.persist_raw_json("raw_mobula_snapshots", mobula_rows)
        self.store.persist_raw_json("raw_defillama_unlocks", defillama_rows)
        log.info(
            "unlock.live_snapshot_summary",
            n_assets=len(descriptors),
            coingecko_market_ok=sum(1 for row in bundle.values() if row.get("market_row_present")),
            mobula_http_ok=sum(1 for row in bundle.values() if safe_float(row.get("mobula_status"), 0.0) == 200.0),
            mobula_payload_matched=sum(1 for row in bundle.values() if row.get("mobula_payload_matched")),
            mobula_schedule_present=matched_schedule_present,
            mobula_distribution_present=matched_distribution_present,
            defillama_http_ok=sum(1 for row in bundle.values() if safe_float(row.get("defillama_status"), 0.0) == 200.0),
        )
        return bundle

    async def _build_market_history(
        self,
        session: aiohttp.ClientSession,
        descriptor: AssetDescriptor,
        live_row: dict[str, Any] | None,
    ) -> pd.DataFrame:
        history = await self.cg_client.ensure_market_history(session, descriptor)
        if history.is_empty():
            fallback = self._build_minimal_market_history(descriptor, live_row)
            if not fallback.empty:
                log.warning(
                    "unlock.market_history_minimal_fallback",
                    asset_id=descriptor.asset_id,
                    cg_id=descriptor.coingecko_id,
                    reason="compatible_history_unavailable",
                )
                return fallback
            return pd.DataFrame(
                columns=["date", "price_usd", "market_cap", "total_volume", "circ_approx", "total_supply", "avg_30d_volume"]
            )

        df = history.to_pandas()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if "total_supply" not in df.columns:
            df["total_supply"] = np.nan
        if live_row and not df.empty:
            current_day = date.today()
            live_total_supply = safe_float(live_row.get("total_supply"))
            df.loc[df["date"] == current_day, "total_supply"] = live_total_supply
            if current_day not in set(df["date"]):
                extra = {
                    "asset_id": descriptor.asset_id,
                    "date": current_day,
                    "price_usd": live_row.get("price_usd"),
                    "market_cap": live_row.get("market_cap"),
                    "total_volume": live_row.get("total_volume"),
                    "circ_approx": live_row.get("circulating_supply") or live_row.get("circ_approx"),
                    "total_supply": live_total_supply,
                }
                df = pd.concat([df, pd.DataFrame([extra])], ignore_index=True)
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        volume = pd.to_numeric(df["total_volume"], errors="coerce")
        df["avg_30d_volume"] = volume.rolling(window=30, min_periods=1).mean()
        return df

    def _build_minimal_market_history(
        self,
        descriptor: AssetDescriptor,
        live_row: dict[str, Any] | None,
    ) -> pd.DataFrame:
        if not live_row:
            return pd.DataFrame()
        current_day = date.today()
        frame = pd.DataFrame(
            [
                {
                    "asset_id": descriptor.asset_id,
                    "date": current_day,
                    "price_usd": safe_float(live_row.get("price_usd")),
                    "market_cap": safe_float(live_row.get("market_cap")),
                    "total_volume": safe_float(live_row.get("total_volume")),
                    "circ_approx": safe_float(live_row.get("circulating_supply") or live_row.get("circ_approx")),
                    "total_supply": safe_float(live_row.get("total_supply")),
                }
            ]
        )
        frame["avg_30d_volume"] = pd.to_numeric(frame["total_volume"], errors="coerce")
        return frame

    def _load_observed_layers(
        self,
        asset_id: str,
        history: pd.DataFrame,
    ) -> dict[date, LayerComputation]:
        snapshots = self.store.load_table("raw_mobula_snapshots", "asset_id = ?", (asset_id,))
        if snapshots.empty:
            return {}
        history_map = history.set_index("date").to_dict(orient="index")
        observed: dict[date, LayerComputation] = {}
        for row in snapshots.to_dict(orient="records"):
            snapshot_dt = datetime.fromisoformat(str(row["snapshot_ts"]).replace("Z", "+00:00")).date()
            payload = json.loads(row["payload_json"])
            market_row = history_map.get(snapshot_dt, {})
            observed[snapshot_dt] = self._compute_observed_layer(
                descriptor=AssetDescriptor(asset_id=asset_id, symbol=asset_id, coingecko_id=None),
                as_of_date=snapshot_dt,
                mobula_payload=payload,
                mobula_snapshot_ts=row["snapshot_ts"],
                market_row=market_row,
            )
        return observed

    def _select_runtime_feature_history(self, history: pd.DataFrame) -> pd.DataFrame:
        if history.empty or self.runtime_history_mode == "full":
            return history
        if "date" not in history.columns:
            return history
        latest_date = history["date"].max()
        return history.loc[history["date"] == latest_date].copy()

    async def _collect_reconstructed_layers(
        self,
        session: aiohttp.ClientSession,
        descriptors: list[AssetDescriptor],
        history_map: dict[str, pd.DataFrame],
        details_map: dict[str, dict[str, Any] | None],
    ) -> dict[str, dict[date, LayerComputation]]:
        reconstructed_map: dict[str, dict[date, LayerComputation]] = {}
        for descriptor in descriptors:
            if descriptor.asset_id not in self.reconstruction_targets:
                continue
            history = history_map.get(descriptor.asset_id, pd.DataFrame())
            if history.empty:
                continue
            reconstruction_history = self._latest_reconstruction_history(history)
            details = details_map.get(descriptor.asset_id)
            reconstructed_map[descriptor.asset_id] = await self._build_reconstructed_for_asset(
                session=session,
                descriptor=descriptor,
                details=details,
                history=reconstruction_history,
            )
        return reconstructed_map

    def _latest_reconstruction_history(self, history: pd.DataFrame) -> pd.DataFrame:
        if history.empty or "date" not in history.columns:
            return history
        latest_date = history["date"].max()
        return history.loc[history["date"] == latest_date].copy()

    async def _build_reconstructed_for_asset(
        self,
        session: aiohttp.ClientSession,
        descriptor: AssetDescriptor,
        details: dict[str, Any] | None,
        history: pd.DataFrame,
    ) -> dict[date, LayerComputation]:
        out: dict[date, LayerComputation] = {}
        if history.empty:
            return out

        seen_ts = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
        official_urls = discover_official_urls(details, descriptor, seen_ts)
        self.store.upsert_asset_sources(official_urls)
        candidate_urls = self._select_reconstruction_urls(official_urls)
        if candidate_urls:
            log.info(
                "unlock.reconstruction_urls_selected",
                asset_id=descriptor.asset_id,
                total_discovered=len(official_urls),
                selected=len(candidate_urls),
                selected_types=[row["url_type"] for row in candidate_urls],
            )

        all_captures = []
        capture_results = await asyncio.gather(
            *[
                self.wayback_client.query_captures(session, row["url"], history["date"].max())
                for row in candidate_urls
            ],
            return_exceptions=True,
        )
        for row, captures in zip(candidate_urls, capture_results, strict=False):
            if isinstance(captures, Exception):
                log.warning(
                    "unlock.wayback_query_failed",
                    asset_id=descriptor.asset_id,
                    url=row["url"],
                    error=str(captures),
                )
                continue
            all_captures.extend(captures)
        if not all_captures:
            return out

        capture_cache: dict[tuple[str, str], tuple[ParsedDocument, str, str, str, str, str]] = {}
        for as_of_date in history["date"]:
            best = choose_best_capture(all_captures, as_of_date)
            if best is None:
                out[as_of_date] = LayerComputation(quality_flag="no_capture")
                continue

            cache_key = (best.timestamp, best.original_url)
            if cache_key not in capture_cache:
                try:
                    mime_type, content = await self.wayback_client.fetch_capture(session, best)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "unlock.wayback_fetch_failed",
                        asset_id=descriptor.asset_id,
                        capture_ts=best.timestamp,
                        source_url=best.original_url,
                        error=str(exc),
                    )
                    out[as_of_date] = LayerComputation(quality_flag="no_capture")
                    continue
                payload_path = self.store.write_wayback_payload(descriptor.asset_id, best.timestamp, best.original_url, content)
                payload_hash = hash_payload(content)
                capture_cache[cache_key] = (
                    deterministic_parse(content, mime_type, best.original_url),
                    best.capture_dt.isoformat(),
                    best.original_url,
                    mime_type,
                    payload_path,
                    payload_hash,
                )

            parsed, capture_ts, source_url, mime_type, payload_path, payload_hash = capture_cache[cache_key]
            self.store.persist_raw_json(
                "raw_wayback_fetches",
                [
                    {
                        "as_of_date": as_of_date.isoformat(),
                        "asset_id": descriptor.asset_id,
                        "source_url": source_url,
                        "capture_ts": capture_ts,
                        "mime_type": mime_type,
                        "payload_path": payload_path,
                        "payload_hash": payload_hash,
                    }
                ],
            )
            circ_hint = safe_float(history.loc[history["date"] == as_of_date, "circ_approx"].squeeze())
            validation = validate_reconstruction(best, parsed, as_of_date, circ_hint)
            confidence = validation.confidence
            normalized_rows = build_normalized_event_rows(
                asset_id=descriptor.asset_id,
                as_of_date=as_of_date,
                parsed=parsed,
                source_type="wayback",
                source_url=source_url,
                confidence=confidence,
            )
            self.store.replace_unlock_events(descriptor.asset_id, as_of_date.isoformat(), normalized_rows)

            insider_share = compute_reconstructed_insider_share(parsed)
            raw = None
            quality_flag = validation.quality_flag
            if confidence < self.reconstruction_evidence_min:
                quality_flag = "confidence_rejected"
            elif confidence < self.reconstruction_promote_min:
                quality_flag = "evidence_only"
            elif circ_hint and circ_hint > 0 and validation.unknown_bucket_ratio <= self.unknown_threshold:
                raw = compute_ups_raw(parsed.events, as_of_date, circ_hint, eps=self.eps)

            out[as_of_date] = LayerComputation(
                raw=raw,
                is_valid=raw is not None,
                confidence=confidence,
                unknown_ratio=validation.unknown_bucket_ratio,
                insider_share=insider_share,
                quality_flag=quality_flag,
                source_primary="wayback",
                snapshot_ts=capture_ts,
                circ=circ_hint,
                total_supply=parsed.total_supply_hint,
                distribution_items=parsed.distribution_items,
            )
        return out

    def _select_reconstruction_urls(self, official_urls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not official_urls:
            return []
        url_type_priority = {
            "whitepaper": 0,
            "docs": 1,
            "tokenomics": 2,
            "vesting": 3,
            "unlocks": 4,
            "homepage": 5,
            "litepaper": 6,
            "blog": 7,
            "official_forum": 8,
        }
        ranked = sorted(
            official_urls,
            key=lambda row: (
                url_type_priority.get(str(row.get("url_type") or ""), 99),
                int(row.get("source_priority") or 999),
                str(row.get("url") or ""),
            ),
        )
        return ranked[: self.reconstruction_max_urls]

    def _compute_observed_layer(
        self,
        descriptor: AssetDescriptor,
        as_of_date: date,
        mobula_payload: dict[str, Any] | None,
        mobula_snapshot_ts: str | None,
        market_row: dict[str, Any] | None,
    ) -> LayerComputation:
        if not mobula_payload:
            return LayerComputation(quality_flag="missing_schedule")

        schedule_payload, distribution_payload = self.mobula_client.extract_schedule_distribution(mobula_payload)
        market_metrics = self.mobula_client.extract_market_metrics(mobula_payload)
        market_ctx = dict(market_row or {})
        circ = market_metrics.get("circulating_supply") or market_ctx.get("circulating_supply") or market_ctx.get("circ_approx")
        total_supply = market_metrics.get("total_supply") or market_ctx.get("total_supply")
        schedule_events = self._parse_schedule_rows(schedule_payload, total_supply_hint=total_supply)
        distribution_items = self._parse_distribution_rows(distribution_payload)
        insider_share = compute_insider_share(distribution_items)
        unknown_ratio = compute_unknown_bucket_ratio(schedule_events)

        quality_flag = "ok"
        raw = None
        if not schedule_events:
            quality_flag = "unparsed_schedule" if self._payload_has_rows(schedule_payload) else "missing_schedule"
        elif not circ or circ <= 0:
            quality_flag = "invalid_circ"
        elif unknown_ratio > self.unknown_threshold:
            quality_flag = "review_required"
        else:
            raw = compute_ups_raw(schedule_events, as_of_date, float(circ), eps=self.eps)

        return LayerComputation(
            raw=raw,
            is_valid=raw is not None,
            unknown_ratio=unknown_ratio,
            insider_share=insider_share,
            quality_flag=quality_flag,
            source_primary="mobula",
            snapshot_ts=mobula_snapshot_ts,
            circ=safe_float(circ),
            total_supply=safe_float(total_supply),
            distribution_items=distribution_items,
        )

    def _parse_schedule_rows(
        self,
        payload: Any,
        total_supply_hint: float | None = None,
    ) -> list[ParsedUnlockEvent]:
        if payload is None:
            return []
        rows: list[ParsedUnlockEvent] = []
        source_rows = payload if isinstance(payload, list) else [payload]
        for row in source_rows:
            if isinstance(row, dict) and isinstance(row.get("allocations"), list):
                event_date = parse_date_like(row.get("date") or row.get("unlockDate") or row.get("startDate") or row.get("eventDate"))
                for alloc in row["allocations"]:
                    rows.extend(self._event_from_row(alloc, event_date=event_date, total_supply_hint=total_supply_hint))
            elif isinstance(row, dict) and isinstance(row.get("allocation_details"), dict):
                event_date = parse_date_like(
                    row.get("unlock_date") or row.get("date") or row.get("unlockDate") or row.get("startDate") or row.get("eventDate")
                )
                for raw_label, token_value in row["allocation_details"].items():
                    rows.extend(
                        self._event_from_row(
                            {"label": raw_label, "tokens": token_value, "unlock_date": row.get("unlock_date")},
                            event_date=event_date,
                            total_supply_hint=total_supply_hint,
                        )
                    )
            else:
                rows.extend(self._event_from_row(row, event_date=None, total_supply_hint=total_supply_hint))
        return rows

    def _event_from_row(
        self,
        row: Any,
        event_date: date | None,
        total_supply_hint: float | None,
    ) -> list[ParsedUnlockEvent]:
        if not isinstance(row, dict):
            return []
        resolved_date = event_date or parse_date_like(
            row.get("unlock_date") or row.get("date") or row.get("unlockDate") or row.get("startDate") or row.get("eventDate")
        )
        raw_label = row.get("allocationName") or row.get("standardAllocationName") or row.get("label") or row.get("name") or row.get("bucket")
        token_value = safe_float(
            row.get("unlockAmount") or row.get("tokens_to_unlock") or row.get("tokens") or row.get("amount") or row.get("value")
        )
        if token_value is None:
            pct_value = safe_float(str(row.get("percentage") or row.get("percent") or "").replace("%", ""))
            if pct_value is not None and total_supply_hint and total_supply_hint > 0:
                pct_fraction = pct_value / 100.0 if pct_value > 1 else pct_value
                token_value = pct_fraction * total_supply_hint
        if resolved_date is None or raw_label is None or token_value is None or token_value < 0:
            return []
        return [
            ParsedUnlockEvent(
                event_date=resolved_date,
                raw_label=str(raw_label),
                bucket=normalize_bucket_label(str(raw_label)),
                tokens=float(token_value),
            )
        ]

    def _parse_distribution_rows(self, payload: Any) -> list[ParsedDistributionItem]:
        if payload is None:
            return []
        rows: list[ParsedDistributionItem] = []
        source_rows = payload if isinstance(payload, list) else [payload]
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            raw_label = row.get("label") or row.get("name") or row.get("allocationName") or row.get("standardAllocationName") or row.get("bucket")
            value = safe_float(row.get("share") or row.get("percentage") or row.get("percent") or row.get("tokens") or row.get("amount"))
            if raw_label is None or value is None:
                continue
            if value > 1.0 and value <= 100.0:
                value = value / 100.0
            rows.append(
                ParsedDistributionItem(
                    raw_label=str(raw_label),
                    bucket=normalize_bucket_label(str(raw_label)),
                    value=float(value),
                )
            )
        return rows

    @staticmethod
    def _payload_has_rows(payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, list):
            return len(payload) > 0
        if isinstance(payload, dict):
            return len(payload) > 0
        return True

    def _compute_proxy_full(self, market_row: pd.Series, total_supply: float | None, insider_share: float | None) -> LayerComputation:
        circ = safe_float(market_row.get("circ_approx"))
        market_cap = safe_float(market_row.get("market_cap"))
        avg_30d_volume = safe_float(market_row.get("avg_30d_volume"))
        if not circ or circ <= 0 or not market_cap or market_cap <= 0 or not avg_30d_volume or avg_30d_volume <= 0:
            return LayerComputation(quality_flag="proxy_full_inputs_missing", source_primary="proxy_full")
        if total_supply is None or total_supply <= 0:
            return LayerComputation(quality_flag="proxy_full_total_supply_missing", source_primary="proxy_full")
        insider = float(np.clip(insider_share if insider_share is not None else 0.0, 0.0, 1.0))
        vested_overhang = max(total_supply - circ, 0.0) / max(circ, self.eps)
        liq_penalty = math.sqrt(market_cap / max(avg_30d_volume, self.eps))
        raw = math.log1p(vested_overhang) * (1.0 + insider) * liq_penalty
        return LayerComputation(raw=raw, is_valid=True, insider_share=insider, source_primary="proxy_full")

    def _compute_proxy_fallback(self, market_row: pd.Series, insider_share: float | None) -> LayerComputation:
        market_cap = safe_float(market_row.get("market_cap"))
        avg_30d_volume = safe_float(market_row.get("avg_30d_volume"))
        if not market_cap or market_cap <= 0 or not avg_30d_volume or avg_30d_volume <= 0:
            return LayerComputation(quality_flag="proxy_fallback_inputs_missing", source_primary="proxy_fallback")
        insider = float(np.clip(insider_share if insider_share is not None else 0.0, 0.0, 1.0))
        raw = math.sqrt(market_cap / max(avg_30d_volume, self.eps)) * (1.0 + insider)
        return LayerComputation(raw=raw, is_valid=True, insider_share=insider, source_primary="proxy_fallback")

    def _choose_total_supply(
        self,
        market_row: pd.Series,
        observed: LayerComputation,
        reconstructed: LayerComputation,
    ) -> float | None:
        for candidate in (
            observed.total_supply,
            reconstructed.total_supply,
            safe_float(market_row.get("total_supply")),
        ):
            if candidate is not None and candidate > 0:
                return float(candidate)
        return None

    def _supply_history_mode(
        self,
        market_row: pd.Series,
        observed: LayerComputation,
        reconstructed: LayerComputation,
        total_supply: float | None,
    ) -> str:
        if observed.total_supply is not None and total_supply == observed.total_supply:
            return "observed_supply"
        if reconstructed.total_supply is not None and total_supply == reconstructed.total_supply:
            return "reconstructed_supply"
        if safe_float(market_row.get("total_supply")) is not None and total_supply is not None:
            return "market_supply"
        return "market_chart_circ_approx"

    def _apply_ranking_and_state(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        frame = pd.DataFrame(rows)
        for raw_col, rank_col in (
            ("ups_raw_observed", "unlock_pressure_rank_observed"),
            ("ups_raw_reconstructed", "unlock_pressure_rank_reconstructed"),
            ("proxy_full_raw", "unlock_overhang_proxy_rank_full"),
            ("proxy_fallback_raw", "unlock_fragility_proxy_rank_fallback"),
        ):
            frame[rank_col] = (
                frame.groupby("date", group_keys=False)[raw_col]
                .apply(lambda series: percent_rank_average(winsorize_cross_section(series, self.winsor_low, self.winsor_high)))
            )

        frame["unlock_pressure_rank_selected_for_reporting"] = np.nan
        frame["unlock_feature_state"] = "MISSING"

        for idx, row in frame.iterrows():
            if pd.notna(row["unlock_pressure_rank_observed"]) and row["_obs_quality"] == "ok":
                frame.at[idx, "unlock_pressure_rank_selected_for_reporting"] = row["unlock_pressure_rank_observed"]
                frame.at[idx, "unlock_feature_state"] = "OBSERVED"
            elif (
                pd.notna(row["unlock_pressure_rank_reconstructed"])
                and safe_float(row["reconstruction_confidence"], 0.0) >= self.reconstruction_promote_min
                and row["_rec_quality"] == "ok"
            ):
                frame.at[idx, "unlock_pressure_rank_selected_for_reporting"] = row["unlock_pressure_rank_reconstructed"]
                frame.at[idx, "unlock_feature_state"] = "RECONSTRUCTED"
            elif pd.notna(row["unlock_overhang_proxy_rank_full"]):
                frame.at[idx, "unlock_pressure_rank_selected_for_reporting"] = row["unlock_overhang_proxy_rank_full"]
                frame.at[idx, "unlock_feature_state"] = "PROXY_FULL"
            elif pd.notna(row["unlock_fragility_proxy_rank_fallback"]):
                frame.at[idx, "unlock_pressure_rank_selected_for_reporting"] = row["unlock_fragility_proxy_rank_fallback"]
                frame.at[idx, "unlock_feature_state"] = "PROXY_FALLBACK"

        frame = frame.drop(columns=["_obs_quality", "_rec_quality"], errors="ignore")
        frame = frame.replace({np.nan: None})
        return frame.to_dict(orient="records")

    def _choose_insider_share(self, observed: LayerComputation, reconstructed: LayerComputation) -> float | None:
        if observed.insider_share is not None:
            return observed.insider_share
        if reconstructed.insider_share is not None:
            return reconstructed.insider_share
        return 0.0

    def _insider_share_mode(
        self,
        observed: LayerComputation,
        reconstructed: LayerComputation,
        insider_share: float | None,
    ) -> str:
        if observed.insider_share is not None:
            return "observed"
        if reconstructed.insider_share is not None:
            return "reconstructed"
        if insider_share is None or abs(float(insider_share)) < self.eps:
            return "missing_zeroed"
        return "derived"

    def _choose_quality_flag(
        self,
        observed: LayerComputation,
        reconstructed: LayerComputation,
        proxy_full: LayerComputation,
        proxy_fallback: LayerComputation,
    ) -> str:
        for layer in (observed, reconstructed, proxy_full, proxy_fallback):
            if layer.quality_flag == "review_required":
                return "review_required"
        if observed.is_valid:
            return observed.quality_flag
        if reconstructed.is_valid:
            return reconstructed.quality_flag
        if proxy_full.is_valid:
            return "proxy_full"
        if proxy_fallback.is_valid:
            return "proxy_fallback"
        return observed.quality_flag or reconstructed.quality_flag or "missing"

    def _build_quality_rows(
        self,
        ranked_rows: list[dict[str, Any]],
        diagnostic_rows: list[dict[str, Any]],
        descriptors: list[AssetDescriptor],
        live_bundle: dict[str, dict[str, Any]],
        reconstructed_map: dict[str, dict[date, LayerComputation]],
    ) -> list[dict[str, Any]]:
        if not ranked_rows:
            return []

        frame = pd.DataFrame(ranked_rows)
        diagnostic_frame = pd.DataFrame(diagnostic_rows)
        if not diagnostic_frame.empty:
            frame = frame.merge(
                diagnostic_frame[["date", "asset_id", "snapshot_ts"]],
                on=["date", "asset_id"],
                how="left",
            )

        model_rank_cols = [
            "unlock_pressure_rank_observed",
            "unlock_pressure_rank_reconstructed",
            "unlock_overhang_proxy_rank_full",
            "unlock_fragility_proxy_rank_fallback",
        ]
        raw_cols = {
            "OBSERVED": "ups_raw_observed",
            "RECONSTRUCTED": "ups_raw_reconstructed",
            "PROXY_FULL": "proxy_full_raw",
            "PROXY_FALLBACK": "proxy_fallback_raw",
        }
        targeted_assets = {descriptor.asset_id for descriptor in descriptors if descriptor.asset_id in self.reconstruction_targets}
        observed_snapshot_days = int(frame.loc[pd.to_numeric(frame["ups_raw_observed"], errors="coerce").notna(), "date"].nunique())
        shadow_mode_flag = int(observed_snapshot_days < self.observed_shadow_min_snapshots)

        run_date: str | None = None
        for payload in live_bundle.values():
            snapshot_ts = payload.get("snapshot_ts")
            if snapshot_ts:
                run_date = datetime.fromisoformat(str(snapshot_ts).replace("Z", "+00:00")).date().isoformat()
                break

        provider_failures_live = {
            "provider_failures_mobula": sum(
                1 for descriptor in descriptors if safe_float(live_bundle.get(descriptor.asset_id, {}).get("mobula_status"), 0.0) != 200.0
            ),
            "provider_failures_coingecko": sum(
                1 for descriptor in descriptors if not live_bundle.get(descriptor.asset_id, {}).get("market_row_present", False)
            ),
            "provider_failures_defillama": None if not self.defillama_client.endpoint else sum(
                1
                for descriptor in descriptors
                if safe_float(live_bundle.get(descriptor.asset_id, {}).get("defillama_status"), 0.0) != 200.0
            ),
        }

        rows: list[dict[str, Any]] = []
        for group_date, group in frame.groupby("date", sort=True):
            n_assets = int(len(group))
            if n_assets == 0:
                continue
            missing_mask = group[model_rank_cols].isna().all(axis=1)
            selected_raw = group.apply(lambda row: self._selected_raw_value(row, raw_cols), axis=1)
            selected_raw = pd.to_numeric(selected_raw, errors="coerce")
            selected_raw_valid = selected_raw.dropna()

            group_date_obj = datetime.fromisoformat(str(group_date)).date()
            snapshot_lags = group["snapshot_ts"].apply(lambda value: self._snapshot_lag_days(group_date_obj, value)).dropna()
            tie_fractions = [
                self._largest_tie_fraction(group[column])
                for column in [*model_rank_cols, "unlock_pressure_rank_selected_for_reporting"]
            ]

            distribution = {
                "state_counts": {str(key): int(value) for key, value in group["unlock_feature_state"].value_counts(dropna=False).items()},
                "selected_for_reporting": self._rank_stats(group["unlock_pressure_rank_selected_for_reporting"]),
            }
            for column in model_rank_cols:
                distribution[column] = self._rank_stats(group[column])

            wayback_failures = None
            if targeted_assets:
                wayback_failures = 0
                for asset_id in targeted_assets:
                    layer = reconstructed_map.get(asset_id, {}).get(group_date_obj)
                    if layer is None or layer.quality_flag in {"no_capture", "lookahead_rejected", "unstructured_payload"}:
                        wayback_failures += 1

            quality_row = {
                "date": str(group_date),
                "n_assets": n_assets,
                "observed_count": int(pd.to_numeric(group["unlock_pressure_rank_observed"], errors="coerce").notna().sum()),
                "reconstructed_count": int(pd.to_numeric(group["unlock_pressure_rank_reconstructed"], errors="coerce").notna().sum()),
                "proxy_full_count": int(pd.to_numeric(group["unlock_overhang_proxy_rank_full"], errors="coerce").notna().sum()),
                "proxy_fallback_count": int(pd.to_numeric(group["unlock_fragility_proxy_rank_fallback"], errors="coerce").notna().sum()),
                "missing_count": int(missing_mask.sum()),
                "observed_coverage": float(pd.to_numeric(group["unlock_pressure_rank_observed"], errors="coerce").notna().mean()),
                "reconstructed_coverage": float(pd.to_numeric(group["unlock_pressure_rank_reconstructed"], errors="coerce").notna().mean()),
                "proxy_full_coverage": float(pd.to_numeric(group["unlock_overhang_proxy_rank_full"], errors="coerce").notna().mean()),
                "proxy_fallback_coverage": float(pd.to_numeric(group["unlock_fragility_proxy_rank_fallback"], errors="coerce").notna().mean()),
                "missing_rate": float(missing_mask.mean()),
                "unknown_bucket_block_count": int((pd.to_numeric(group["unknown_bucket_ratio"], errors="coerce") > self.unknown_threshold).sum()),
                "unknown_bucket_block_rate": float((pd.to_numeric(group["unknown_bucket_ratio"], errors="coerce") > self.unknown_threshold).mean()),
                "raw_zero_count": int((selected_raw_valid == 0.0).sum()),
                "raw_zero_fraction": float((selected_raw_valid == 0.0).mean()) if not selected_raw_valid.empty else 0.0,
                "massive_ties_fraction": float(max(tie_fractions) if tie_fractions else 0.0),
                "review_required_count": int((group["quality_flag"] == "review_required").sum()),
                "promotion_blocked_count": int(
                    (
                        pd.to_numeric(group["reconstruction_confidence"], errors="coerce").notna()
                        & (group["unlock_feature_state"] != "RECONSTRUCTED")
                        & pd.to_numeric(group["unlock_pressure_rank_observed"], errors="coerce").isna()
                    ).sum()
                ),
                "shadow_mode_flag": shadow_mode_flag,
                "snapshot_lag_days_avg": float(snapshot_lags.mean()) if not snapshot_lags.empty else None,
                "snapshot_lag_days_max": float(snapshot_lags.max()) if not snapshot_lags.empty else None,
                "provider_failures_mobula": provider_failures_live["provider_failures_mobula"] if group_date == run_date else None,
                "provider_failures_coingecko": provider_failures_live["provider_failures_coingecko"] if group_date == run_date else None,
                "provider_failures_defillama": provider_failures_live["provider_failures_defillama"] if group_date == run_date else None,
                "provider_failures_wayback": wayback_failures,
                "rank_distribution_json": distribution,
            }
            rows.append(quality_row)
        return rows

    def _selected_raw_value(self, row: pd.Series, raw_cols: dict[str, str]) -> float | None:
        raw_column = raw_cols.get(str(row.get("unlock_feature_state") or ""))
        if not raw_column:
            return None
        return safe_float(row.get(raw_column))

    def _snapshot_lag_days(self, as_of_date: date, snapshot_ts: Any) -> float | None:
        if not snapshot_ts:
            return None
        try:
            snapshot_date = datetime.fromisoformat(str(snapshot_ts).replace("Z", "+00:00")).date()
        except ValueError:
            return None
        return float((as_of_date - snapshot_date).days)

    def _largest_tie_fraction(self, values: pd.Series) -> float:
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        if numeric.empty:
            return 0.0
        counts = numeric.value_counts(dropna=True)
        if counts.empty:
            return 0.0
        return float(counts.max() / len(numeric))

    def _rank_stats(self, values: pd.Series) -> dict[str, float | int | None]:
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        if numeric.empty:
            return {
                "n": 0,
                "min": None,
                "p25": None,
                "p50": None,
                "p75": None,
                "max": None,
                "n_unique": 0,
            }
        return {
            "n": int(len(numeric)),
            "min": float(numeric.min()),
            "p25": float(numeric.quantile(0.25)),
            "p50": float(numeric.quantile(0.50)),
            "p75": float(numeric.quantile(0.75)),
            "max": float(numeric.max()),
            "n_unique": int(numeric.nunique(dropna=True)),
        }

    def _normalize_market_row(self, market_row: dict[str, Any]) -> dict[str, Any]:
        if not market_row:
            return {}
        price = safe_float(market_row.get("current_price"))
        market_cap = safe_float(market_row.get("market_cap"))
        circulating_supply = safe_float(market_row.get("circulating_supply"))
        if circulating_supply is None and price and price > 0 and market_cap and market_cap > 0:
            circulating_supply = market_cap / price
        return {
            "price_usd": price,
            "market_cap": market_cap,
            "total_volume": safe_float(market_row.get("total_volume")),
            "circulating_supply": circulating_supply,
            "total_supply": safe_float(market_row.get("total_supply") or market_row.get("max_supply")),
            "circ_approx": circulating_supply,
        }
