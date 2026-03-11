# =============================================================================
# DESTINO: services/data_inserter/validators/anti_survivorship.py
# Garante que o universo histórico inclua ativos colapsados (Luna, FTT, CEL).
# Sem isso, o backtest é uma ilusão — regra inviolável do SNIPER v10.10.
#
# CORREÇÕES APLICADAS (v10.10-fix1):
#   BUG 1: price_change_percentage=30d removido (400 na API free/demo)
#   BUG 2: Header CoinGecko corrigido: CG- prefix → x-cg-demo-api-key
#   BUG 3: Filtro stablecoin: pass → continue (PYUSD etc passavam)
#   BUG 4: Rate limit: sleep(0.5) → sleep(2.5) (demo = ~30 req/min)
#   BUG 7: _fetch_coin_details trata 400 sem crash
# =============================================================================
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from collectors.token_unlocks import TokenUnlocksCollector

import aiohttp
import structlog

log = structlog.get_logger(__name__)

# Ativos colapsados que DEVEM estar no universo histórico (Parte 2 do doc)
MANDATORY_COLLAPSED: list[str] = [
    "LUNA",    # Terra Luna — colapso Mai/2022
    "LUNC",    # Luna Classic (mesmo ativo pós-colapso)
    "LUNA2",   # Terra 2.0
    "FTT",     # FTX Token — colapso Nov/2022
    "CEL",     # Celsius Network — colapso Jun/2022
]

# Exclusões fixas: não fazem parte do universo de trading
FIXED_EXCLUSIONS: set[str] = {
    "BTC", "ETH",       # regimes incompatíveis com a estratégia
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD",  # stablecoins
    "PYUSD", "USDD", "FRAX", "GUSD", "USDP",          # stablecoins adicionais
    "WBTC", "WETH", "STETH", "CBETH", "RETH",          # wrapped/LST
}

# Peso por categoria de unlock para o UPS score (Parte 2 do doc)
UPS_CATEGORY_WEIGHTS: dict[str, float] = {
    "Team/Founders": 1.5,
    "VC/Investors":  1.2,
    "Ecosystem":     0.8,
    "Airdrop/Public": 0.6,
}

# ── CoinGecko API key detection ──────────────────────────────────────────────
# CG- prefix = Demo key → header: x-cg-demo-api-key, URL: api.coingecko.com
# Sem CG- prefix = Pro key → header: x-cg-pro-api-key, URL: pro-api.coingecko.com
COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL  = "https://pro-api.coingecko.com/api/v3"


def _detect_coingecko_config(api_key: str) -> tuple[str, dict]:
    """
    Detecta tipo de API key CoinGecko e retorna (base_url, headers).
    - Sem key / placeholder → free (sem header)
    - Prefixo 'CG-' → Demo (x-cg-demo-api-key, URL free)
    - Outro → Pro (x-cg-pro-api-key, URL pro)
    """
    if not api_key or api_key == "your_coingecko_pro_key_here":
        return COINGECKO_FREE_URL, {}
    if api_key.startswith("CG-"):
        # Demo key: usa URL free com header demo
        return COINGECKO_FREE_URL, {"x-cg-demo-api-key": api_key}
    # Pro key
    return COINGECKO_PRO_URL, {"x-cg-pro-api-key": api_key}


@dataclass
class AssetRecord:
    symbol:         str
    coingecko_id:   str
    market_cap_usd: float
    volume_24h_usd: float
    age_months:     int
    ups_score:      float = 0.0
    ups_data_available: bool = False
    is_collapsed:   bool  = False


class AntiSurvivorshipValidator:
    """
    Constrói o universo point-in-time com proteção total contra survivorship bias.

    Regras (Parte 2 — SNIPER v10.10):
    - Top 50 por market cap NAQUELE DIA histórico (não ranking atual)
    - Volume diário médio 30d > $20M USD
    - Idade mínima > 18 meses
    - Exclusões fixas: BTC, ETH, stablecoins, wrapped, LSTs
    - UPS score: excluir top 25% por pressão de unlock
    - OBRIGATÓRIO: incluir Luna, FTT, CEL e todos os colapsados
    """

    # Rate limit: demo ~30 req/min → 2.5s entre chamadas = ~24/min (seguro)
    RATE_LIMIT_DELAY = 2.5

    def __init__(self, cg_api_key: Optional[str] = None) -> None:
        from decouple import config
        self.api_key = cg_api_key or config("COINGECKO_API_KEY", default="")
        self.base_url, self.headers = _detect_coingecko_config(self.api_key)
        log.info("universe.cg_config",
                 base_url=self.base_url,
                 has_key=bool(self.api_key),
                 is_demo=self.api_key.startswith("CG-") if self.api_key else False)

    async def _fetch_top_n_by_marketcap(
        self, session: aiohttp.ClientSession, top_n: int = 50
    ) -> list[dict]:
        """Busca top N por market cap. Retorna lista raw da CoinGecko."""
        url = f"{self.base_url}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order":       "market_cap_desc",
            "per_page":    min(top_n * 2, 250),   # pega 2x para compensar exclusões
            "page":        1,
            "sparkline":   "false",
            # FIX BUG 1: price_change_percentage REMOVIDO — causa 400 na API free/demo
        }
        async with session.get(url, params=params, headers=self.headers,
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 429:
                log.warning("universe.rate_limited", action="waiting_60s")
                await asyncio.sleep(60)
                return await self._fetch_top_n_by_marketcap(session, top_n)
            if resp.status != 200:
                text = await resp.text()
                log.error("universe.marketcap_fetch_fail",
                          status=resp.status, body=text[:200])
                return []
            return await resp.json()

    async def _fetch_coin_details(
        self, session: aiohttp.ClientSession, coin_id: str
    ) -> Optional[dict]:
        """
        Busca data de lançamento para calcular idade do ativo.
        FIX BUG 7: retorna None em vez de crashar em 400/404.
        """
        url = f"{self.base_url}/coins/{coin_id}"
        params = {"localization": "false", "tickers": "false",
                  "market_data": "false", "community_data": "false"}
        try:
            async with session.get(url, params=params, headers=self.headers,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 429:
                    log.warning("universe.details_rate_limit", coin=coin_id)
                    await asyncio.sleep(60)
                    return await self._fetch_coin_details(session, coin_id)
                if resp.status != 200:
                    # FIX BUG 7: log + retorna None em vez de raise_for_status()
                    log.warning("universe.details_http_error",
                                coin=coin_id, status=resp.status)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("universe.details_exception", coin=coin_id, error=str(e))
            return None

    def _calculate_age_months(self, genesis_date_str: Optional[str]) -> int:
        """Calcula idade em meses a partir da data de gênese."""
        if not genesis_date_str:
            return 0
        try:
            genesis = datetime.strptime(genesis_date_str, "%Y-%m-%d")
            delta   = datetime.utcnow() - genesis
            return int(delta.days / 30)
        except ValueError:
            return 0

    def _is_stablecoin(self, symbol: str, name: str) -> bool:
        """Detecta stablecoins por symbol e name patterns."""
        sym = symbol.upper()
        nm  = name.upper()
        # Explicitamente excluídas
        if sym in FIXED_EXCLUSIONS:
            return True
        # Heurísticas para pegar stablecoins não listadas
        stable_patterns = {"USD", "EUR", "GBP", "JPY", "USDT", "USDC"}
        if any(pat in sym for pat in stable_patterns):
            return True
        if any(kw in nm for kw in {"STABLECOIN", "PEGGED", "BRIDGED"}):
            return True
        return False

    def compute_ups_score(
        self,
        unlock_schedule: dict[str, float],
    ) -> float:
        """
        UPS = Σ(pct_supply_unlock_30d[cat] × category_weight[cat])
        Fonte: Parte 2, seção 2.1 do documento SNIPER v10.10.
        """
        score = sum(
            pct * UPS_CATEGORY_WEIGHTS.get(cat, 0.5)
            for cat, pct in unlock_schedule.items()
        )
        return round(score, 6)

    def filter_by_ups(self, assets: list[AssetRecord]) -> list[AssetRecord]:
        """
        Remove ativos no top 25% de UPS score (maior pressão de unlock).
        Ativos colapsados são ISENTOS desta filtragem — mantidos por regra.
        Se não houver dado de UPS para um ativo, ele é mantido com warning explícito.
        """
        import numpy as np
        active = [a for a in assets if not a.is_collapsed]
        collapsed = [a for a in assets if a.is_collapsed]

        if not active:
            return collapsed

        active_with_ups = [a for a in active if a.ups_data_available]
        active_without_ups = [a for a in active if not a.ups_data_available]

        if not active_with_ups:
            log.warning("ups.filter_skipped", reason="nenhum ativo com UPS disponível")
            return active + collapsed

        scores = [a.ups_score for a in active_with_ups]
        threshold = float(np.percentile(scores, 75))
        filtered = [a for a in active_with_ups if a.ups_score <= threshold]

        removed = [a.symbol for a in active_with_ups if a.ups_score > threshold]
        if removed:
            log.info("ups.filtered_out", symbols=removed, threshold=round(threshold, 6))
        if active_without_ups:
            log.warning(
                "ups.data_missing",
                symbols=[a.symbol for a in active_without_ups],
                action="mantidos_no_universo",
            )

        return filtered + active_without_ups + collapsed

    async def build_universe_point_in_time(
        self,
        top_n:            int   = 50,
        min_volume_usd:   float = 20_000_000,
        min_age_months:   int   = 18,
    ) -> list[AssetRecord]:
        """
        Constrói universo completo com todas as regras aplicadas.
        Retorna lista de AssetRecord prontos para coleta de OHLCV.
        """
        assets: list[AssetRecord] = []

        async with aiohttp.ClientSession() as session:
            raw = await self._fetch_top_n_by_marketcap(session, top_n)
            log.info("universe.raw_fetched", count=len(raw))

            if not raw:
                log.error("universe.empty_marketcap",
                          msg="CoinGecko retornou 0 coins. Verificar API key e rate limits.")
                # Retorna apenas colapsados obrigatórios para não travar
                return self._build_collapsed_records()

            # ── Adiciona colapsados obrigatórios ─────────────────────────
            collapsed_records = self._build_collapsed_records()
            log.info("universe.mandatory_collapsed", symbols=MANDATORY_COLLAPSED)

            # ── Processa ativos do top N ──────────────────────────────────
            for coin in raw:
                symbol = coin.get("symbol", "").upper()
                name   = coin.get("name", "")

                # Exclusões fixas + stablecoins
                if symbol in FIXED_EXCLUSIONS:
                    continue
                # FIX BUG 3: continue em vez de pass para stablecoins detectadas
                if self._is_stablecoin(symbol, name):
                    log.debug("universe.stablecoin_excluded", symbol=symbol, name=name)
                    continue
                if "WRAPPED" in name.upper():
                    continue

                volume = coin.get("total_volume", 0) or 0
                mktcap = coin.get("market_cap", 0)   or 0

                if volume < min_volume_usd:
                    log.debug("universe.volume_fail", symbol=symbol,
                              volume=volume, min=min_volume_usd)
                    continue

                # Verifica idade via coin details
                age_months = await self._safe_fetch_age(session, coin["id"], symbol)

                if age_months < min_age_months:
                    log.debug("universe.age_fail", symbol=symbol, age=age_months)
                    continue

                assets.append(AssetRecord(
                    symbol=symbol,
                    coingecko_id=coin["id"],
                    market_cap_usd=mktcap,
                    volume_24h_usd=volume,
                    age_months=age_months,
                    ups_score=0.0,
                    ups_data_available=False,
                    is_collapsed=False,
                ))

                if len(assets) >= top_n:
                    break

            # ── Enriquecer UPS atual via camada observada de unlocks ───────
            try:
                unlocks = TokenUnlocksCollector()
                current_scores = await unlocks.fetch_current_ups_scores(assets)
                for asset in assets:
                    score = current_scores.get(asset.symbol)
                    if score is not None:
                        asset.ups_score = float(score)
                        asset.ups_data_available = True
                log.info(
                    "ups.current_scores_enriched",
                    available=sum(1 for a in assets if a.ups_data_available),
                    missing=sum(1 for a in assets if not a.ups_data_available),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("ups.enrichment_failed", error=str(exc), action="seguir_sem_bloquear")

            # ── Aplica UPS filter (ativos ativos apenas) ──────────────────
            final = self.filter_by_ups(assets) + collapsed_records

            # Remove duplicatas (caso colapsado já apareça no top N)
            seen: set[str] = set()
            unique: list[AssetRecord] = []
            for a in final:
                if a.symbol not in seen:
                    seen.add(a.symbol)
                    unique.append(a)

            log.info(
                "universe.final",
                total=len(unique),
                active=sum(1 for a in unique if not a.is_collapsed),
                collapsed=sum(1 for a in unique if a.is_collapsed),
            )
            self._validate_mandatory_present(unique)
            return unique

    async def _safe_fetch_age(
        self, session: aiohttp.ClientSession, coin_id: str, symbol: str
    ) -> int:
        """
        Busca idade com tratamento robusto de erros.
        FIX BUG 4: sleep(2.5) para respeitar rate limit demo (~30 req/min).
        FIX BUG 7: não crasheia em 400/404.
        """
        try:
            details = await self._fetch_coin_details(session, coin_id)
            if details is None:
                # Fallback: assume ativo antigo (conservador — não excluir sem evidência)
                log.warning("universe.age_fallback", symbol=symbol, coin_id=coin_id,
                            msg="Sem dados de genesis. Assumindo >18 meses.")
                await asyncio.sleep(self.RATE_LIMIT_DELAY)
                return 999  # conservador: inclui o ativo

            genesis = details.get("genesis_date") or \
                      (details.get("ico_data") or {}).get("ico_start_date")
            age_months = self._calculate_age_months(genesis)

            # FIX BUG 4: respeitar rate limit demo
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            return age_months if age_months > 0 else 999  # sem data = assumir antigo

        except Exception as e:
            log.warning("universe.age_exception", symbol=symbol, error=str(e))
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            return 999  # conservador

    def _build_collapsed_records(self) -> list[AssetRecord]:
        """Cria registros para ativos colapsados obrigatórios."""
        collapsed_ids: dict[str, str] = {
            "LUNA":  "terra-luna",
            "LUNC":  "terra-luna",
            "LUNA2": "terra-luna-2",
            "FTT":   "ftx-token",
            "CEL":   "celsius-degree-token",
        }
        return [
            AssetRecord(
                symbol=symbol, coingecko_id=cg_id,
                market_cap_usd=0, volume_24h_usd=0,
                age_months=999, ups_score=0.0, ups_data_available=False, is_collapsed=True,
            )
            for symbol, cg_id in collapsed_ids.items()
        ]

    def _validate_mandatory_present(self, assets: list[AssetRecord]) -> None:
        """
        HARD CHECK: aborta se qualquer ativo obrigatório estiver ausente.
        "Sem isso o backtest é ilusão." — SNIPER v10.10, Parte 2.
        """
        present = {a.symbol for a in assets}
        missing = [s for s in MANDATORY_COLLAPSED if s not in present]
        if missing:
            raise RuntimeError(
                f"VIOLAÇÃO ANTI-SURVIVORSHIP: ativos obrigatórios ausentes: {missing}. "
                "O backtest não pode prosseguir sem eles."
            )
        log.info("universe.mandatory_check_passed")
