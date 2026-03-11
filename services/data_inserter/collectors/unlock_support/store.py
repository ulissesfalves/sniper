from __future__ import annotations

import sqlite3
from contextlib import closing
from hashlib import sha1
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from .utils import canonical_json_dumps


class UnlockFeatureStore:
    def __init__(self, sqlite_path: str, parquet_base: str) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_base = Path(parquet_base)
        self.feature_dir = self.parquet_base / "unlocks"
        self.diagnostics_dir = self.parquet_base / "unlock_diagnostics"
        self.market_dir = self.parquet_base / "unlock_market"
        self.wayback_dir = self.parquet_base / "unlock_wayback"
        for directory in (
            self.feature_dir,
            self.diagnostics_dir,
            self.market_dir,
            self.wayback_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS raw_mobula_snapshots (
            snapshot_ts TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            http_status INTEGER,
            PRIMARY KEY (snapshot_ts, asset_id, payload_hash)
        );
        CREATE TABLE IF NOT EXISTS raw_coingecko_snapshots (
            snapshot_ts TEXT NOT NULL,
            cg_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            http_status INTEGER,
            PRIMARY KEY (snapshot_ts, cg_id, payload_hash)
        );
        CREATE TABLE IF NOT EXISTS raw_wayback_fetches (
            as_of_date TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            capture_ts TEXT NOT NULL,
            mime_type TEXT,
            payload_path TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            PRIMARY KEY (as_of_date, asset_id, source_url, capture_ts, payload_hash)
        );
        CREATE TABLE IF NOT EXISTS raw_defillama_unlocks (
            snapshot_ts TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            page_type TEXT NOT NULL,
            extracted_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            PRIMARY KEY (snapshot_ts, asset_id, page_type, payload_hash)
        );
        CREATE TABLE IF NOT EXISTS asset_source_registry (
            asset_id TEXT NOT NULL,
            url TEXT NOT NULL,
            url_type TEXT NOT NULL,
            domain TEXT NOT NULL,
            is_official INTEGER NOT NULL,
            source_priority INTEGER NOT NULL,
            active_flag INTEGER NOT NULL,
            first_seen_ts TEXT,
            last_seen_ts TEXT,
            PRIMARY KEY (asset_id, url)
        );
        CREATE TABLE IF NOT EXISTS unlock_events_normalized (
            asset_id TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            event_date TEXT NOT NULL,
            raw_label TEXT,
            bucket TEXT NOT NULL,
            tokens REAL NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT,
            confidence REAL,
            PRIMARY KEY (asset_id, as_of_date, event_date, bucket, source_type, source_url)
        );
        CREATE TABLE IF NOT EXISTS feature_unlock_daily (
            date TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            ups_raw_observed REAL,
            unlock_pressure_rank_observed REAL,
            ups_raw_reconstructed REAL,
            unlock_pressure_rank_reconstructed REAL,
            proxy_full_raw REAL,
            unlock_overhang_proxy_rank_full REAL,
            proxy_fallback_raw REAL,
            unlock_fragility_proxy_rank_fallback REAL,
            unlock_pressure_rank_selected_for_reporting REAL,
            unlock_feature_state TEXT,
            unknown_bucket_ratio REAL,
            quality_flag TEXT,
            reconstruction_confidence REAL,
            PRIMARY KEY (date, asset_id)
        );
        CREATE TABLE IF NOT EXISTS feature_unlock_diagnostics (
            date TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            circ REAL,
            total_supply REAL,
            insider_share_pti REAL,
            insider_share_mode TEXT,
            avg_30d_volume REAL,
            market_cap REAL,
            source_primary TEXT,
            snapshot_ts TEXT,
            supply_history_mode TEXT,
            PRIMARY KEY (date, asset_id)
        );
        CREATE TABLE IF NOT EXISTS feature_unlock_quality_daily (
            date TEXT NOT NULL PRIMARY KEY,
            n_assets INTEGER NOT NULL,
            observed_count INTEGER NOT NULL,
            reconstructed_count INTEGER NOT NULL,
            proxy_full_count INTEGER NOT NULL,
            proxy_fallback_count INTEGER NOT NULL,
            missing_count INTEGER NOT NULL,
            observed_coverage REAL NOT NULL,
            reconstructed_coverage REAL NOT NULL,
            proxy_full_coverage REAL NOT NULL,
            proxy_fallback_coverage REAL NOT NULL,
            missing_rate REAL NOT NULL,
            unknown_bucket_block_count INTEGER NOT NULL,
            unknown_bucket_block_rate REAL NOT NULL,
            raw_zero_count INTEGER NOT NULL,
            raw_zero_fraction REAL NOT NULL,
            massive_ties_fraction REAL NOT NULL,
            review_required_count INTEGER NOT NULL,
            promotion_blocked_count INTEGER NOT NULL,
            shadow_mode_flag INTEGER NOT NULL,
            snapshot_lag_days_avg REAL,
            snapshot_lag_days_max REAL,
            provider_failures_mobula INTEGER,
            provider_failures_coingecko INTEGER,
            provider_failures_defillama INTEGER,
            provider_failures_wayback INTEGER,
            rank_distribution_json TEXT
        );
        """
        with closing(self._connect()) as conn:
            conn.executescript(ddl)
            conn.commit()

    def persist_raw_json(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            clean = dict(row)
            for key in ("payload_json", "extracted_json"):
                if key in clean and not isinstance(clean[key], str):
                    clean[key] = canonical_json_dumps(clean[key])
            normalized_rows.append(clean)

        columns = sorted(normalized_rows[0].keys())
        placeholders = ", ".join(f":{column}" for column in columns)
        sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        with closing(self._connect()) as conn:
            conn.executemany(sql, normalized_rows)
            conn.commit()

    def upsert_asset_sources(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT INTO asset_source_registry (
            asset_id, url, url_type, domain, is_official, source_priority,
            active_flag, first_seen_ts, last_seen_ts
        ) VALUES (
            :asset_id, :url, :url_type, :domain, :is_official, :source_priority,
            :active_flag, :first_seen_ts, :last_seen_ts
        )
        ON CONFLICT(asset_id, url) DO UPDATE SET
            url_type = excluded.url_type,
            domain = excluded.domain,
            is_official = excluded.is_official,
            source_priority = excluded.source_priority,
            active_flag = excluded.active_flag,
            first_seen_ts = COALESCE(asset_source_registry.first_seen_ts, excluded.first_seen_ts),
            last_seen_ts = excluded.last_seen_ts
        """
        with closing(self._connect()) as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def replace_unlock_events(self, asset_id: str, as_of_date: str, rows: list[dict[str, Any]]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM unlock_events_normalized WHERE asset_id = ? AND as_of_date = ?",
                (asset_id, as_of_date),
            )
            if rows:
                sql = """
                INSERT INTO unlock_events_normalized (
                    asset_id, as_of_date, event_date, raw_label, bucket, tokens,
                    source_type, source_url, confidence
                ) VALUES (
                    :asset_id, :as_of_date, :event_date, :raw_label, :bucket, :tokens,
                    :source_type, :source_url, :confidence
                )
                """
                conn.executemany(sql, rows)
            conn.commit()

    def persist_feature_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT OR REPLACE INTO feature_unlock_daily (
            date, asset_id, ups_raw_observed, unlock_pressure_rank_observed,
            ups_raw_reconstructed, unlock_pressure_rank_reconstructed,
            proxy_full_raw, unlock_overhang_proxy_rank_full, proxy_fallback_raw,
            unlock_fragility_proxy_rank_fallback,
            unlock_pressure_rank_selected_for_reporting, unlock_feature_state,
            unknown_bucket_ratio, quality_flag, reconstruction_confidence
        ) VALUES (
            :date, :asset_id, :ups_raw_observed, :unlock_pressure_rank_observed,
            :ups_raw_reconstructed, :unlock_pressure_rank_reconstructed,
            :proxy_full_raw, :unlock_overhang_proxy_rank_full, :proxy_fallback_raw,
            :unlock_fragility_proxy_rank_fallback,
            :unlock_pressure_rank_selected_for_reporting, :unlock_feature_state,
            :unknown_bucket_ratio, :quality_flag, :reconstruction_confidence
        )
        """
        with closing(self._connect()) as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def persist_diagnostic_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
        INSERT OR REPLACE INTO feature_unlock_diagnostics (
            date, asset_id, circ, total_supply, insider_share_pti, insider_share_mode,
            avg_30d_volume, market_cap, source_primary, snapshot_ts, supply_history_mode
        ) VALUES (
            :date, :asset_id, :circ, :total_supply, :insider_share_pti, :insider_share_mode,
            :avg_30d_volume, :market_cap, :source_primary, :snapshot_ts, :supply_history_mode
        )
        """
        with closing(self._connect()) as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def persist_quality_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            clean = dict(row)
            if "rank_distribution_json" in clean and not isinstance(clean["rank_distribution_json"], str):
                clean["rank_distribution_json"] = canonical_json_dumps(clean["rank_distribution_json"])
            normalized_rows.append(clean)

        sql = """
        INSERT OR REPLACE INTO feature_unlock_quality_daily (
            date, n_assets, observed_count, reconstructed_count, proxy_full_count,
            proxy_fallback_count, missing_count, observed_coverage, reconstructed_coverage,
            proxy_full_coverage, proxy_fallback_coverage, missing_rate,
            unknown_bucket_block_count, unknown_bucket_block_rate, raw_zero_count,
            raw_zero_fraction, massive_ties_fraction, review_required_count,
            promotion_blocked_count, shadow_mode_flag, snapshot_lag_days_avg,
            snapshot_lag_days_max, provider_failures_mobula, provider_failures_coingecko,
            provider_failures_defillama, provider_failures_wayback, rank_distribution_json
        ) VALUES (
            :date, :n_assets, :observed_count, :reconstructed_count, :proxy_full_count,
            :proxy_fallback_count, :missing_count, :observed_coverage, :reconstructed_coverage,
            :proxy_full_coverage, :proxy_fallback_coverage, :missing_rate,
            :unknown_bucket_block_count, :unknown_bucket_block_rate, :raw_zero_count,
            :raw_zero_fraction, :massive_ties_fraction, :review_required_count,
            :promotion_blocked_count, :shadow_mode_flag, :snapshot_lag_days_avg,
            :snapshot_lag_days_max, :provider_failures_mobula, :provider_failures_coingecko,
            :provider_failures_defillama, :provider_failures_wayback, :rank_distribution_json
        )
        """
        with closing(self._connect()) as conn:
            conn.executemany(sql, normalized_rows)
            conn.commit()

    def load_table(self, table: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> pd.DataFrame:
        query = f"SELECT * FROM {table}"
        if where_sql:
            query = f"{query} WHERE {where_sql}"
        with closing(self._connect()) as conn:
            return pd.read_sql_query(query, conn, params=params)

    def write_wayback_payload(
        self,
        asset_id: str,
        capture_ts: str,
        source_url: str,
        content: bytes,
    ) -> str:
        safe_name = sha1_name(f"{asset_id}|{capture_ts}|{source_url}")
        path = self.wayback_dir / asset_id
        path.mkdir(parents=True, exist_ok=True)
        payload_path = path / f"{safe_name}.bin"
        payload_path.write_bytes(content)
        return str(payload_path)

    def export_feature_parquet(self, asset_ids: list[str] | None = None) -> list[Path]:
        rows: list[Path] = []
        filter_sql = ""
        params: tuple[Any, ...] = ()
        if asset_ids:
            placeholders = ", ".join("?" for _ in asset_ids)
            filter_sql = f"WHERE d.asset_id IN ({placeholders})"
            params = tuple(asset_ids)

        query = f"""
        SELECT
            d.date AS timestamp,
            d.asset_id AS symbol,
            d.ups_raw_observed,
            d.unlock_pressure_rank_observed,
            d.ups_raw_reconstructed,
            d.unlock_pressure_rank_reconstructed,
            d.proxy_full_raw,
            d.unlock_overhang_proxy_rank_full,
            d.proxy_fallback_raw,
            d.unlock_fragility_proxy_rank_fallback,
            d.unlock_pressure_rank_selected_for_reporting,
            d.unlock_feature_state,
            d.unknown_bucket_ratio,
            d.quality_flag,
            d.reconstruction_confidence,
            x.circ,
            x.total_supply,
            x.insider_share_pti,
            x.insider_share_mode,
            x.avg_30d_volume,
            x.market_cap,
            x.source_primary,
            x.snapshot_ts,
            x.supply_history_mode
        FROM feature_unlock_daily d
        LEFT JOIN feature_unlock_diagnostics x
            ON d.date = x.date AND d.asset_id = x.asset_id
        {filter_sql}
        ORDER BY d.asset_id, d.date
        """
        with closing(self._connect()) as conn:
            df = pd.read_sql_query(query, conn, params=params)
        if df.empty:
            return rows

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        for symbol, symbol_df in df.groupby("symbol", sort=True):
            out_path = self.feature_dir / f"{symbol}.parquet"
            self._write_parquet(symbol_df.reset_index(drop=True), out_path)
            rows.append(out_path)
        return rows

    def export_quality_parquet(self) -> Path | None:
        query = """
        SELECT *
        FROM feature_unlock_quality_daily
        ORDER BY date
        """
        with closing(self._connect()) as conn:
            df = pd.read_sql_query(query, conn)
        if df.empty:
            return None

        out_path = self.diagnostics_dir / "unlock_quality_daily.parquet"
        self._write_parquet(df.reset_index(drop=True), out_path)
        return out_path

    def _write_parquet(self, df: pd.DataFrame, out_path: Path) -> None:
        try:
            pl.from_pandas(df).write_parquet(out_path, compression="zstd")
        except Exception:
            pass
        if out_path.exists() and out_path.stat().st_size > 0:
            return
        try:
            df.to_parquet(out_path, compression="zstd", index=False)
        except Exception:
            try:
                df.to_parquet(out_path, index=False)
            except Exception:
                df.to_pickle(out_path)


def sha1_name(raw: str) -> str:
    return sha1(raw.encode("utf-8")).hexdigest()
