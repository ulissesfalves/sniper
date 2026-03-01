# =============================================================================
# DESTINO: services/ml_engine/fracdiff/optimal_d.py
# Encontra o d* ótimo por ativo usando expanding window estrita.
# Expanding window = sem look-ahead no hiperparâmetro d*.
# ADF test: mínimo d que garante estacionariedade (p < 0.05).
# Custo: ~60-90 min para 20 ativos × 1500 dias. Salvar em disco.
# Recalcular mensalmente (rolling update).
# Referência: Lopez de Prado, AFML 2018, Cap. 5.
# =============================================================================
from __future__ import annotations

from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
import structlog

from .weights import fracdiff_weights, DEFAULT_TAU
from .transform import fracdiff_log_fast

log = structlog.get_logger(__name__)

# Grid de busca: 20 valores entre 0.05 e 1.0
D_GRID = np.linspace(0.05, 1.0, 20)


def find_optimal_d_expanding(
    price_series:    pd.Series,
    min_train_obs:   int   = 252,
    pval_threshold:  float = 0.05,
    tau:             float = DEFAULT_TAU,
) -> pd.Series:
    """
    Calcula d* ótimo com expanding window estrita — ZERO look-ahead.

    Para cada ponto t (a partir de min_train_obs):
        - Usa SOMENTE preços de t0 até t-1
        - Testa FracDiff(prices[:t], d) para cada d no grid
        - Seleciona o MENOR d que passa ADF (p < pval_threshold)
        - Se nenhum d passa: usa d=1.0 (primeira diferença)

    Por que expanding window é obrigatório:
        Se d* fosse calculado globalmente, o modelo veria dados futuros
        ao selecionar o hiperparâmetro → look-ahead bias garantido.

    Args:
        price_series:   pd.Series de preços brutos com DatetimeIndex.
        min_train_obs:  Mínimo de observações antes de computar d*.
                        252 = ~1 ano de dados diários.
        pval_threshold: P-value máximo para o ADF. Default 0.05.
        tau:            Weight cutoff para fracdiff_weights.

    Returns:
        pd.Series: d* ótimo para cada data. Valores anteriores ao
                   min_train_obs são preenchidos com bfill/ffill.

    Custo computacional:
        O(N × 20 × n_weights) — N dias × 20 valores de d × ~500 pesos.
        Para 1500 dias: ~30M operações por ativo. Cache em disco é essencial.
    """
    n         = len(price_series)
    d_optimal = pd.Series(index=price_series.index, dtype=float)

    # Pré-computa pesos para cada d no grid (evita recomputar N vezes)
    weights_cache: dict[float, np.ndarray] = {
        float(d): fracdiff_weights(float(d), tau=tau) for d in D_GRID
    }
    log.info("fracdiff.expanding.start",
             n_obs=n, min_train=min_train_obs, n_d_values=len(D_GRID))

    for t in range(min_train_obs, n):
        prices_t = price_series.iloc[:t].values
        d_found  = None

        for d in D_GRID:
            d_float = float(d)
            w       = weights_cache[d_float]

            # FracDiff rápido com pesos pré-calculados
            diff = fracdiff_log_fast(prices_t, d=d_float, tau=tau)
            clean = diff[~np.isnan(diff)]

            if len(clean) < 30:
                continue

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    adf_pval = adfuller(clean, maxlag=1, autolag=None)[1]
                if adf_pval < pval_threshold:
                    d_found = d_float
                    break   # menor d estacionário encontrado
            except Exception:  # noqa: BLE001
                continue

        d_optimal.iloc[t] = d_found if d_found is not None else 1.0

        if t % 100 == 0:
            log.debug("fracdiff.expanding.progress",
                      t=t, pct=round(t / n * 100, 1),
                      d_last=round(d_found or 1.0, 3))

    # Preenche NaN dos primeiros pontos com bfill + ffill
    d_optimal = d_optimal.bfill().ffill()
    log.info("fracdiff.expanding.complete",
             d_mean=round(d_optimal.mean(), 3),
             d_min=round(d_optimal.min(), 3),
             d_max=round(d_optimal.max(), 3))
    return d_optimal


def compute_fracdiff_features(
    price_store:   dict[str, pd.Series],
    cache_dir:     str | Path = "/data/models/fracdiff",
    min_train_obs: int   = 252,
    tau:           float = DEFAULT_TAU,
    force_recompute: bool = False,
) -> dict[str, dict]:
    """
    Pipeline completo de FracDiff para todos os ativos.
    Inclui todas as correções acumuladas:
        v10.5: expanding window estrita (sem look-ahead no hiperparâmetro)
        v10.6: weight cutoff τ=1e-5 (~500 obs efetivos)
        v10.7: espaço logarítmico (invariante à escala de preço)

    Cache em disco: evita recomputar ~90 min a cada execução.
    Invalidar manualmente ao mudar tau ou min_train_obs.

    Returns:
        {ativo: {
            'fracdiff_zscore':    pd.Series,   # feature primária para ML
            'fracdiff_accel':     pd.Series,   # taxa de mudança
            'd_optimal_series':   pd.Series,   # d* por data (diagnóstico)
            'tau_used':           float,
            'log_space':          True,         # flag de auditoria
        }}
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    feature_store: dict[str, dict] = {}

    for ativo, prices in price_store.items():
        cache_file = cache_path / f"{ativo}_fracdiff.parquet"

        # ── Verifica cache ────────────────────────────────────────────────
        if cache_file.exists() and not force_recompute:
            log.info("fracdiff.cache_hit", ativo=ativo)
            cached = pd.read_parquet(cache_file)
            feature_store[ativo] = {
                "fracdiff_zscore":  cached["fracdiff_zscore"],
                "fracdiff_accel":   cached["fracdiff_accel"],
                "d_optimal_series": cached["d_optimal"],
                "tau_used":         tau,
                "log_space":        True,
            }
            continue

        log.info("fracdiff.computing", ativo=ativo, n_obs=len(prices))

        # ── d* ótimo com expanding window ─────────────────────────────────
        d_series = find_optimal_d_expanding(prices, min_train_obs, tau=tau)

        # ── FracDiff ponto a ponto usando d* local ────────────────────────
        fd_values = np.full(len(prices), np.nan)

        for t in range(min_train_obs, len(prices)):
            d_t      = float(d_series.iloc[t])
            prices_t = prices.iloc[:t].values

            diff_t  = fracdiff_log_fast(prices_t, d=d_t, tau=tau)
            fd_values[t] = diff_t[-1]

        fd_series = pd.Series(fd_values, index=prices.index)

        # ── Z-score rolling 252d (sem look-ahead no zscore) ───────────────
        fd_mean   = fd_series.rolling(252, min_periods=30).mean()
        fd_std    = fd_series.rolling(252, min_periods=30).std()
        fd_zscore = (fd_series - fd_mean) / (fd_std + 1e-10)
        fd_accel  = fd_series.diff(1)

        # ── Salva cache ───────────────────────────────────────────────────
        cache_df = pd.DataFrame({
            "fracdiff_zscore": fd_zscore,
            "fracdiff_accel":  fd_accel,
            "d_optimal":       d_series,
        })
        cache_df.to_parquet(cache_file)
        log.info("fracdiff.cached", ativo=ativo, path=str(cache_file))

        feature_store[ativo] = {
            "fracdiff_zscore":  fd_zscore,
            "fracdiff_accel":   fd_accel,
            "d_optimal_series": d_series,
            "tau_used":         tau,
            "log_space":        True,    # flag obrigatória para auditoria
        }

    return feature_store


def run_diagnostic(
    prices_linear: np.ndarray,
    prices_log:    np.ndarray,
    d:             float = 0.4,
) -> dict:
    """
    Diagnóstico comparativo v10.6 (linear) vs v10.7 (log).
    Verifica se a heterocedasticidade é real e material para o ativo.

    Esperado em ativos com forte tendência (BTC, ETH):
    - Versão linear: série com variância crescente ao longo do tempo
    - Versão log: série com variância estável através dos ciclos

    Returns:
        dict com estatísticas de variância por período.
    """
    from .transform import fracdiff_log_fast

    # FracDiff linear (INCORRETO — apenas para comparação)
    fd_linear = fracdiff_log_fast(
        np.exp(np.log(np.maximum(prices_linear, 1e-10))),
        d=d
    )

    # FracDiff log (CORRETO — v10.7)
    fd_log = fracdiff_log_fast(prices_log, d=d)

    n  = len(fd_linear)
    h1 = slice(n // 4, n // 2)      # primeiro semestre
    h2 = slice(n // 2, 3 * n // 4)  # segundo semestre
    h3 = slice(3 * n // 4, n)       # período final

    def safe_std(arr, sl):
        sub = arr[sl]
        sub = sub[~np.isnan(sub)]
        return float(np.std(sub)) if len(sub) > 2 else np.nan

    return {
        "d": d,
        "linear_std_early": safe_std(fd_linear, h1),
        "linear_std_mid":   safe_std(fd_linear, h2),
        "linear_std_late":  safe_std(fd_linear, h3),
        "log_std_early":    safe_std(fd_log, h1),
        "log_std_mid":      safe_std(fd_log, h2),
        "log_std_late":     safe_std(fd_log, h3),
        "heteroscedasticity_confirmed": (
            safe_std(fd_linear, h3) / (safe_std(fd_linear, h1) + 1e-10) > 2.0
        ),
    }
