# =============================================================================
# DESTINO: services/ml_engine/fracdiff/transform.py
# Diferenciação fracionária NO ESPAÇO LOGARÍTMICO.
# CRÍTICO v10.7: fracdiff deve receber log(prices), NUNCA preços brutos.
#
# Por que log-espaço é obrigatório:
# BTC em $3k (2019) vs $70k (2024): fator 23x.
# No espaço linear, coeficientes escalares são aplicados sobre magnitudes USD.
# Variância de 2024 domina completamente 2019 → série heterocedástica.
# No espaço log: log(1.05) ≈ 0.049 em qualquer época → invariante à escala.
# Referência: Lopez de Prado, AFML 2018, Cap. 5 + revisão SNIPER v10.7.
# =============================================================================
from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
import structlog

from .weights import fracdiff_weights, DEFAULT_TAU

log = structlog.get_logger(__name__)


def fracdiff_log(
    prices:    np.ndarray | pd.Series,
    d:         float,
    tau:       float = DEFAULT_TAU,
    _weights:  np.ndarray | None = None,
) -> np.ndarray:
    """
    Diferenciação fracionária no espaço logarítmico.

    O argumento `prices` recebe preços BRUTOS (não log).
    A conversão para log ocorre INTERNAMENTE aqui — nunca passar log externamente
    para evitar duplo-log acidental.

    Processo:
        1. log_p = log(prices)                  # espaço logarítmico
        2. result[t] = Σ w_k * log_p[t-k]       # convolução com pesos FracDiff
        3. NaN nos primeiros (len(weights)-1) pontos (sem histórico suficiente)

    Args:
        prices:   Array de preços brutos (positivos). pd.Series ou np.ndarray.
        d:        Ordem de diferenciação ∈ [0, 1].
        tau:      Weight cutoff. Default 1e-5.
        _weights: Pesos pré-calculados (otimização: evita recomputar em loop).

    Returns:
        np.ndarray: série diferenciada fracionariamente em log-espaço.
                    Primeiros (n_weights - 1) pontos são NaN.
    """
    if isinstance(prices, pd.Series):
        prices_arr = prices.values.astype(np.float64)
    else:
        prices_arr = np.asarray(prices, dtype=np.float64)

    # Validação
    if np.any(prices_arr <= 0):
        n_invalid = np.sum(prices_arr <= 0)
        warnings.warn(
            f"fracdiff_log: {n_invalid} preços ≤ 0 detectados. "
            "Substituindo por NaN antes do log.",
            stacklevel=2,
        )
        prices_arr = np.where(prices_arr > 0, prices_arr, np.nan)

    # Log-espaço: invariante à escala
    log_p = np.log(prices_arr)

    # Pesos (usa cache se fornecido)
    w = _weights if _weights is not None else fracdiff_weights(d, tau=tau)
    n_w = len(w)
    n   = len(log_p)

    if n < n_w:
        log.warning("fracdiff_log.insufficient_data",
                    n=n, n_weights=n_w, d=d)
        return np.full(n, np.nan)

    # Convolução: result[t] = dot(w, log_p[t-n_w+1 : t+1])
    result = np.full(n, np.nan, dtype=np.float64)
    for t in range(n_w - 1, n):
        window = log_p[t - n_w + 1: t + 1]
        if not np.any(np.isnan(window)):
            result[t] = np.dot(w, window)

    return result


def fracdiff_log_fast(
    prices: np.ndarray | pd.Series,
    d:      float,
    tau:    float = DEFAULT_TAU,
) -> np.ndarray:
    """
    Versão vetorizada com np.convolve (mais rápida para séries longas).
    Equivalente a fracdiff_log mas usa convolução numpy — ~10x mais rápido.
    Usada no pipeline de features para séries > 1000 pontos.
    """
    if isinstance(prices, pd.Series):
        prices_arr = prices.values.astype(np.float64)
    else:
        prices_arr = np.asarray(prices, dtype=np.float64)

    prices_arr = np.where(prices_arr > 0, prices_arr, np.nan)
    log_p = np.log(prices_arr)

    w   = fracdiff_weights(d, tau=tau)
    n_w = len(w)
    n   = len(log_p)

    if n < n_w:
        return np.full(n, np.nan)

    # np.convolve retorna n + n_w - 1 pontos; pegamos apenas os últimos n
    conv = np.convolve(log_p, w, mode="full")[:n]

    # Os primeiros n_w-1 resultados são inválidos (histórico incompleto)
    result = np.full(n, np.nan)
    result[n_w - 1:] = conv[n_w - 1:]

    # Zera onde havia NaN no input
    nan_mask = np.isnan(log_p)
    result[nan_mask] = np.nan

    return result
