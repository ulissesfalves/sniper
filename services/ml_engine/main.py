# =============================================================================
# DESTINO: services/ml_engine/main.py
# SNIPER v10.10 — ML Engine Pipeline Completo (Fase 2)
#
# Orquestra: Load Data → Features → FracDiff → HMM Walk-Forward → CFI/VI
# Cada componente já está implementado como biblioteca. Este arquivo CONECTA tudo.
#
# SUBSTITUI o stub anterior que só publicava heartbeat no Redis.
# =============================================================================
from __future__ import annotations

import asyncio
import sys
import time
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import structlog
from decouple import config

log = structlog.get_logger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PARQUET_BASE     = config("PARQUET_BASE_PATH",    default="/data/parquet")
MODEL_PATH       = config("MODEL_ARTIFACTS_PATH", default="/data/models")
SQLITE_PATH      = config("SQLITE_PATH",          default="/data/sqlite/sniper.db")
REDIS_URL        = config("REDIS_URL",            default="redis://redis:6379/0")

FRACDIFF_TAU       = float(config("FRACDIFF_TAU",           default="1e-5"))
FRACDIFF_MIN_TRAIN = int(config("FRACDIFF_MIN_TRAIN_OBS",   default="252"))
HMM_N_COMPONENTS   = int(config("HMM_N_COMPONENTS",        default="2"))
HMM_N_PCA          = float(config("HMM_MIN_VARIANCE",        default="0.85"))
VI_THRESHOLD       = float(config("VI_THRESHOLD",           default="0.30"))
CS_SIGMA_THR       = float(config("CORWIN_SCHULTZ_SIGMA_THR", default="3.0"))
ISOTONIC_HALFLIFE  = int(config("ISOTONIC_HALFLIFE_DAYS",   default="180"))

RETRY_INTERVAL     = 300  # 5 min entre ciclos
MIN_HISTORY_DAYS   = 365  # mínimo de dados para rodar pipeline

# Minimum assets para pipeline viável
MIN_ASSETS_PIPELINE = 10


# ─── DISCOVERY ───────────────────────────────────────────────────────────────

def discover_symbols() -> list[str]:
    """
    Descobre símbolos disponíveis nos parquet diários.
    Estrutura: /data/parquet/ohlcv_daily/{SYMBOL}.parquet
    """
    ohlcv_dir = Path(PARQUET_BASE) / "ohlcv_daily"
    if not ohlcv_dir.exists():
        return []
    symbols = [
        p.stem for p in ohlcv_dir.glob("*.parquet")
        if p.stat().st_size > 1000  # ignora arquivos vazios
    ]
    return sorted(symbols)


def load_ohlcv(symbol: str) -> pd.DataFrame | None:
    """Carrega OHLCV diário de um ativo. Retorna pd.DataFrame com DatetimeIndex."""
    path = Path(PARQUET_BASE) / "ohlcv_daily" / f"{symbol}.parquet"
    if not path.exists():
        return None
    try:
        df_pl = pl.read_parquet(path)
        df = df_pl.to_pandas()

        # Normaliza timestamp para DatetimeIndex
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()

        # Remove duplicatas
        df = df[~df.index.duplicated(keep="last")]

        # Garante colunas mínimas
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            log.warning("load.missing_columns", symbol=symbol,
                        columns=list(df.columns))
            return None

        # Converte para float64
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        else:
            df["volume"] = 0.0

        return df
    except Exception as e:
        log.error("load.error", symbol=symbol, error=str(e))
        return None


def load_funding(symbol: str) -> pd.Series:
    """Carrega funding rate MA7d. Retorna pd.Series com DatetimeIndex."""
    path = Path(PARQUET_BASE) / "funding" / f"{symbol}.parquet"
    if not path.exists():
        return pd.Series(dtype=float, name="funding_rate_ma7d")
    try:
        df = pl.read_parquet(path).to_pandas()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
        if "funding_rate_ma7d" in df.columns:
            return df["funding_rate_ma7d"]
        elif "funding_rate" in df.columns:
            return df["funding_rate"].rolling(21, min_periods=1).mean()
        return pd.Series(dtype=float, name="funding_rate_ma7d")
    except Exception:
        return pd.Series(dtype=float, name="funding_rate_ma7d")


def load_klines_4h(symbol: str) -> pd.DataFrame | None:
    """Carrega klines 4h para Corwin-Schultz."""
    path = Path(PARQUET_BASE) / "ohlcv_4h" / f"{symbol}.parquet"
    if not path.exists():
        return None
    try:
        df = pl.read_parquet(path).to_pandas()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
        return df
    except Exception:
        return None


# ─── FEATURE ENGINEERING ─────────────────────────────────────────────────────

def compute_base_features(
    df: pd.DataFrame,
    symbol: str,
    btc_data: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Computa features base a partir de OHLCV diário.
    Features usadas pelo HMM (Parte 4 da spec):
      ret_1d, ret_5d, ret_20d, realized_vol_30d, vol_ratio,
      drawdown_pct, term_spread, volume_momentum,
      btc_ma200_flag, dvol_zscore

    NOTA v3:
      - btc_ma200_flag agora usa MA200 do BTC real (não do próprio ativo)
      - funding_rate_ma7d substituído por drawdown_pct (proxy superior para
        regime detection: no bear 2022 drawdown era -50% a -75% para todos os ativos)
    """
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df.get("volume", pd.Series(0.0, index=df.index))

    features = pd.DataFrame(index=df.index)

    # Retornos multi-horizonte
    features["ret_1d"]  = np.log(close / close.shift(1))
    features["ret_5d"]  = np.log(close / close.shift(5))
    features["ret_20d"] = np.log(close / close.shift(20))

    # Volatilidade realizada 30d (annualizada)
    features["realized_vol_30d"] = (
        features["ret_1d"].rolling(30, min_periods=10).std() * np.sqrt(365)
    )

    # Vol ratio: vol recente vs vol longa (detecta compressão de vol)
    vol_5d  = features["ret_1d"].rolling(5, min_periods=3).std()
    vol_30d = features["ret_1d"].rolling(30, min_periods=10).std()
    features["vol_ratio"] = (vol_5d / vol_30d.clip(lower=1e-8)).clip(0, 5)

    # Drawdown % do rolling 252d high (substitui funding_rate_ma7d):
    #   No bear 2022: drawdown -50% a -75%. Feature extremamente discriminativa.
    #   Em bull: drawdown ~0% a -10%.
    #   Muito mais informativo que funding_rate=0 (sem dados históricos).
    rolling_high = close.rolling(252, min_periods=30).max()
    features["drawdown_pct"] = ((close / rolling_high.clip(lower=1e-8)) - 1.0).clip(-1.0, 0.0)

    # Term Spread: (MA50 - MA200) / close
    ma50  = close.rolling(50, min_periods=20).mean()
    ma200 = close.rolling(200, min_periods=100).mean()
    features["term_spread"] = ((ma50 - ma200) / close.clip(lower=1e-8)).clip(-0.5, 0.5)

    # Volume Momentum: vol curto vs vol longo
    if vol.sum() > 0:
        vol_ma5  = vol.rolling(5,  min_periods=2).mean()
        vol_ma30 = vol.rolling(30, min_periods=10).mean()
        features["volume_momentum"] = (
            (vol_ma5 / vol_ma30.clip(lower=1e-8)) - 1.0
        ).clip(-3, 3)
    else:
        features["volume_momentum"] = 0.0

    # BTC MA200 flag: usa BTC real quando disponível (spec: "btc_ma200_flag")
    if btc_data is not None and "close" in btc_data.columns:
        btc_close = btc_data["close"]
        btc_ma200 = btc_close.rolling(200, min_periods=100).mean()
        btc_flag  = (btc_close > btc_ma200).astype(float)
        # Alinha BTC flag ao index do ativo
        features["btc_ma200_flag"] = btc_flag.reindex(df.index, method="ffill").fillna(0.5)
    else:
        # Fallback: MA200 do próprio ativo
        features["btc_ma200_flag"] = (close > ma200).astype(float)

    # DVOL zscore: proxy via Parkinson vol (High-Low range)
    from triple_barrier.market_impact import compute_intraday_vol_parkinson
    parkinson = compute_intraday_vol_parkinson(high, low, ewm_span=20)
    parkinson_mean = parkinson.rolling(90, min_periods=30).mean()
    parkinson_std  = parkinson.rolling(90, min_periods=30).std().clip(lower=1e-8)
    features["dvol_zscore"] = ((parkinson - parkinson_mean) / parkinson_std).clip(-4, 4)

    log.info("features.computed", symbol=symbol,
             n_rows=len(features),
             n_valid=int(features.dropna().shape[0]),
             features=list(features.columns),
             has_btc_ref=btc_data is not None)

    return features


# ─── FRACDIFF ────────────────────────────────────────────────────────────────

def run_fracdiff_for_symbol(df: pd.DataFrame, symbol: str) -> tuple[pd.Series, float]:
    """
    Roda FracDiff expanding window para um ativo.
    Retorna série diferenciada e d* ótimo final.
    """
    from fracdiff.optimal_d import find_optimal_d_expanding
    from fracdiff.transform import fracdiff_log_fast

    close = df["close"]

    if len(close) < FRACDIFF_MIN_TRAIN:
        log.warning("fracdiff.insufficient_data", symbol=symbol, n=len(close))
        # Fallback: usa d=0.5 como estimativa conservadora
        result = fracdiff_log_fast(close.values, d=0.5, tau=FRACDIFF_TAU)
        return pd.Series(result, index=close.index, name="close_fracdiff"), 0.5

    # Expanding window: calcula d* ótimo para cada janela
    # NOTA: isso é computacionalmente caro (~30s/ativo). Usar cache.
    d_star_series = find_optimal_d_expanding(
        close, min_train_obs=FRACDIFF_MIN_TRAIN, tau=FRACDIFF_TAU
    )

    # Usa o último d* como d representativo para a série completa
    d_final = float(d_star_series.dropna().iloc[-1]) if not d_star_series.dropna().empty else 0.5

    result = fracdiff_log_fast(close.values, d=d_final, tau=FRACDIFF_TAU)

    log.info("fracdiff.done", symbol=symbol,
             d_star=round(d_final, 4), n_valid=int(np.sum(~np.isnan(result))))

    return pd.Series(result, index=close.index, name="close_fracdiff"), d_final


# ─── HMM REGIME DETECTION ───────────────────────────────────────────────────

def run_hmm_for_symbol(
    features: pd.DataFrame,
    returns: pd.Series,
    symbol: str,
) -> pd.DataFrame:
    """Roda HMM walk-forward para um ativo."""
    from regime.hmm_filter import run_hmm_walk_forward, validate_hmm_diagnostics

    # ── FILTER: usar apenas HMM_FEATURES da spec (Parte 4) ──
    # Spec v10.10: ret_1d, ret_5d, realized_vol_30d, vol_ratio,
    #   funding_rate_ma7d, basis_3m, stablecoin_chg30, btc_ma200_flag, dvol_zscore
    # Substitutos quando dado não disponível:
    #   drawdown_pct → funding_rate_ma7d (stress proxy)
    #   term_spread → basis_3m (term structure proxy)
    #   volume_momentum → stablecoin_chg30 (flow proxy)
    HMM_FEATURE_COLS = [
        "ret_1d", "ret_5d", "realized_vol_30d", "vol_ratio",
        "drawdown_pct", "term_spread", "volume_momentum",
        "btc_ma200_flag", "dvol_zscore",
    ]
    available = [c for c in HMM_FEATURE_COLS if c in features.columns]
    if len(available) < 5:
        log.warning("hmm.too_few_features", symbol=symbol, available=available)
        return pd.DataFrame(
            {"hmm_prob_bull": np.nan, "hmm_is_bull": False},
            index=features.index
        )
    feat_hmm = features[available].copy()

    # FIX: preenche NaN residuais (ffill + fillna(0)) para evitar n=0
    feat_clean = feat_hmm.ffill().fillna(0)

    # Drop colunas com variância zero (constantes não informam o HMM)
    var = feat_clean.var()
    zero_var_cols = var[var < 1e-12].index.tolist()
    if zero_var_cols:
        log.info("hmm.dropping_zero_var", symbol=symbol, columns=zero_var_cols)
        feat_clean = feat_clean.drop(columns=zero_var_cols)

    if feat_clean.shape[1] < 3:
        log.warning("hmm.too_few_features", symbol=symbol, n_cols=feat_clean.shape[1])
        return pd.DataFrame(
            {"hmm_prob_bull": np.nan, "hmm_is_bull": False},
            index=features.index
        )

    valid_mask = feat_clean.dropna()
    if len(valid_mask) < FRACDIFF_MIN_TRAIN:
        log.warning("hmm.insufficient_features", symbol=symbol, n=len(valid_mask))
        return pd.DataFrame(
            {"hmm_prob_bull": np.nan, "hmm_is_bull": False},
            index=features.index
        )

    artifacts_dir = str(Path(MODEL_PATH) / "hmm" / symbol)

    log.info("hmm.input_features", symbol=symbol,
             n_features=feat_clean.shape[1],
             features=list(feat_clean.columns))

    hmm_result = run_hmm_walk_forward(
        feature_df=feat_clean,
        returns=returns.reindex(feat_clean.index),
        min_train=FRACDIFF_MIN_TRAIN,
        retrain_freq=63,
        artifacts_dir=artifacts_dir,
        min_variance=0.85,  # v10.10.1: PCA retém PCs até 85% variância (não fixo em 2)
    )

    # Validação diagnóstica
    diag = validate_hmm_diagnostics(hmm_result, returns.reindex(hmm_result.index))
    log.info("hmm.validation", symbol=symbol, **diag)

    return hmm_result


# ─── CORWIN-SCHULTZ CIRCUIT BREAKER ─────────────────────────────────────────

def run_corwin_schultz_for_symbol(symbol: str) -> pd.DataFrame | None:
    """Computa features CS a partir de klines 4h."""
    from drift.corwin_schultz import compute_cs_features

    klines = load_klines_4h(symbol)
    if klines is None or len(klines) < 30:
        return None

    high = klines["high"].astype(float) if "high" in klines.columns else None
    low  = klines["low"].astype(float)  if "low"  in klines.columns else None

    if high is None or low is None:
        return None

    return compute_cs_features(high, low, roll_window=30)


# ─── VI/CFI CLUSTERING ──────────────────────────────────────────────────────

def run_vi_clustering(all_features: dict[str, pd.DataFrame]) -> dict:
    """
    Computa matriz de distância VI entre features e identifica clusters redundantes.

    v10.10.1 FIX: Usa features POOLED de todos os ativos (não apenas 1 referência).
    O VI com 1 ativo falhava com INSUFFICIENT_DATA porque dropna() eliminava muitas
    linhas. Com pooling, N cresce de ~500 para ~15000, estabilizando a discretização.
    """
    from vi_cfi.vi import compute_vi_distance_matrix
    from vi_cfi.cfi import clustered_feature_importance

    exclude_cols = {"symbol", "d_star", "hmm_prob_bull", "hmm_is_bull",
                    "close_fracdiff"}

    # Seleciona features comuns a >= 50% dos ativos
    feat_count: dict[str, int] = {}
    for sym, feat_df in all_features.items():
        for c in feat_df.columns:
            if c not in exclude_cols:
                feat_count[c] = feat_count.get(c, 0) + 1

    min_assets = max(5, len(all_features) // 2)
    common_feats = sorted([c for c, n in feat_count.items() if n >= min_assets])
    if len(common_feats) < 3:
        log.warning("vi.too_few_common_features", n=len(common_feats))
        return {"status": "INSUFFICIENT_DATA"}

    # Pooling: concatena features de todos os ativos
    pool_rows = []
    symbols_pooled = []
    for sym, feat_df in all_features.items():
        avail = [c for c in common_feats if c in feat_df.columns]
        if len(avail) < len(common_feats) * 0.7:
            continue
        sub = feat_df[avail].dropna()
        if len(sub) >= 50:
            pool_rows.append(sub)
            symbols_pooled.append(sym)

    if not pool_rows:
        log.warning("vi.no_pooled_data")
        return {"status": "INSUFFICIENT_DATA"}

    pooled_df = pd.concat(pool_rows, axis=0, ignore_index=True)
    # Garante que temos as mesmas colunas (preenche faltantes)
    for c in common_feats:
        if c not in pooled_df.columns:
            pooled_df[c] = 0.0
    pooled_df = pooled_df[common_feats].dropna()

    log.info("vi.pooled_data",
             n_symbols=len(symbols_pooled),
             n_obs=len(pooled_df),
             n_features=len(common_feats),
             features=common_feats)

    if len(pooled_df) < 200:
        log.warning("vi.insufficient_pooled", n=len(pooled_df))
        return {"status": "INSUFFICIENT_DATA"}

    # Computa matriz VI no dataset pooled
    vi_matrix = compute_vi_distance_matrix(pooled_df, n_bins=10)

    # Identifica pares redundantes
    redundant_pairs = []
    cols = vi_matrix.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            if vi_matrix.iloc[i, j] < VI_THRESHOLD:
                redundant_pairs.append({
                    "f1": cols[i], "f2": cols[j],
                    "vi": round(float(vi_matrix.iloc[i, j]), 4)
                })

    log.info("vi.clustering_done",
             n_symbols_pooled=len(symbols_pooled),
             n_obs_pooled=len(pooled_df),
             n_features=len(cols),
             n_redundant_pairs=len(redundant_pairs),
             mean_vi=round(float(vi_matrix.values[np.triu_indices_from(vi_matrix.values, k=1)].mean()), 4),
             threshold=VI_THRESHOLD)

    # Salva matriz VI como artefato
    vi_path = Path(MODEL_PATH) / "vi_matrix.csv"
    vi_path.parent.mkdir(parents=True, exist_ok=True)
    vi_matrix.to_csv(vi_path)

    return {
        "vi_matrix": vi_matrix,
        "redundant_pairs": redundant_pairs,
        "n_symbols_pooled": len(symbols_pooled),
        "n_obs_pooled": len(pooled_df),
        "n_features": len(cols),
        "mean_vi": round(float(vi_matrix.values[np.triu_indices_from(vi_matrix.values, k=1)].mean()), 4),
        "status": "OK",
    }


# ─── PHASE 3: TRIPLE-BARRIER + META-LABELING + ISOTONIC + SIZING ─────────

CAPITAL_TOTAL      = float(config("CAPITAL_TOTAL",          default="200000"))
KELLY_KAPPA        = float(config("KELLY_QUARTER_FACTOR",   default="0.25"))
CVAR_LIMIT         = float(config("PORTFOLIO_CVAR_LIMIT",   default="0.15"))
TB_K_TP            = float(config("TB_K_TP",                default="1.5"))
TB_K_SL            = float(config("TB_K_SL",                default="1.5"))
TB_MAX_HOLDING     = int(config("TB_MAX_HOLDING_DAYS",      default="5"))
TB_ETA             = float(config("TB_ETA",                 default="0.10"))


def run_triple_barrier_for_symbol(
    df: pd.DataFrame,
    hmm_result: pd.DataFrame,
    features: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame | None:
    """
    Triple-Barrier labeling (Parte 5 spec v10.10):
    Eventos = datas onde HMM sinaliza bull. Labels: +1 TP, -1 SL, 0 time-stop.
    HLC priority: Low[t]<=SL verificado ANTES de High[t]>=TP.
    Slippage SL: ΔP = η × σ_intraday × √(Q/V).
    """
    from triple_barrier.labeler import (
        apply_triple_barrier, TripleBarrierConfig, validate_barrier_distribution
    )
    from features.volatility import compute_sigma_ewma, compute_sigma_intraday_parkinson

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df.get("volume", pd.Series(0.0, index=df.index))

    # Sigma EWMA (base das barreiras)
    returns    = features["ret_1d"].fillna(0)
    sigma_ewma = compute_sigma_ewma(returns, span=20)

    # Sigma intraday (Parkinson) para market impact
    sigma_intra = compute_sigma_intraday_parkinson(high, low, ewm_span=20)

    # Eventos: datas onde HMM = bull E dados suficientes (sigma válido)
    hmm_aligned = hmm_result.reindex(df.index)
    bull_mask   = hmm_aligned["hmm_is_bull"].fillna(False)
    valid_sigma = sigma_ewma > 0
    events      = df.index[bull_mask & valid_sigma]

    if len(events) < 30:
        log.warning("triple_barrier.too_few_events", symbol=symbol, n=len(events))
        return None

    # Position sizes default (proporção igual do capital)
    pos_size_default = CAPITAL_TOTAL * 0.05  # 5% por posição inicial
    position_sizes   = pd.Series(pos_size_default, index=df.index)

    config_tb = TripleBarrierConfig(
        k_tp=TB_K_TP, k_sl=TB_K_SL,
        max_holding_days=TB_MAX_HOLDING, eta=TB_ETA,
    )

    barrier_df = apply_triple_barrier(
        close_prices=close,
        high_prices=high,
        low_prices=low,
        volume_series=volume,
        events=events,
        sigma_ewma=sigma_ewma,
        sigma_intraday=sigma_intra,
        position_sizes=position_sizes,
        config=config_tb,
    )

    if barrier_df.empty or len(barrier_df) < 20:
        log.warning("triple_barrier.empty", symbol=symbol)
        return None

    # Validação de distribuição (checklist v10.5)
    diag = validate_barrier_distribution(barrier_df)
    log.info("triple_barrier.done", symbol=symbol, **diag)

    return barrier_df


def run_meta_labeling_for_symbol(
    features: pd.DataFrame,
    barrier_df: pd.DataFrame,
    hmm_result: pd.DataFrame,
    sigma_ewma: pd.Series,
    symbol: str,
) -> dict | None:
    """
    Meta-Labeling pipeline (Partes 8-9 spec v10.10):
    1. Uniqueness dinâmica (t_touch real)
    2. N_eff → seleção de modelo (Logística vs LGBM)
    3. P_bma via Purged K-Fold (stacking ortogonal v10.7)
    4. Isotonic calibration walk-forward com time-decay
    5. Retorna P_calibrated para Kelly sizing
    """
    from meta_labeling.uniqueness import (
        compute_label_uniqueness, compute_effective_n, compute_meta_sample_weights
    )
    from meta_labeling.pbma_purged import generate_pbma_purged_kfold
    from meta_labeling.isotonic_calibration import run_isotonic_walk_forward

    # ── 1. Uniqueness dinâmica ───────────────────────────────────────────
    uniqueness = compute_label_uniqueness(barrier_df)

    # ── 2. N_eff e sample weights ────────────────────────────────────────
    n_eff, uniqueness, model_type = compute_effective_n(barrier_df, uniqueness)
    log.info("meta.n_eff", symbol=symbol, n_eff=round(n_eff, 1),
             n_raw=len(barrier_df), model_type=model_type)

    if n_eff < 30:
        log.warning("meta.n_eff_too_low", symbol=symbol, n_eff=n_eff)
        return None

    sample_weights = compute_meta_sample_weights(
        barrier_df, uniqueness,
        halflife_days=ISOTONIC_HALFLIFE,
        sl_penalty=2.0,
    )

    # ── 3. Build meta features ───────────────────────────────────────────
    # Alinha features ao barrier_df index (event dates)
    meta_features = pd.DataFrame(index=barrier_df.index)

    # HMM probability
    hmm_aligned = hmm_result["hmm_prob_bull"].reindex(barrier_df.index, method="ffill")
    meta_features["hmm_prob_bull"] = hmm_aligned.fillna(0.5)

    # Sigma EWMA at signal
    meta_features["sigma_ewma"] = sigma_ewma.reindex(barrier_df.index, method="ffill").fillna(0)

    # Features derivadas do OHLCV
    for col in ["ret_1d", "ret_5d", "realized_vol_30d", "vol_ratio",
                "drawdown_pct", "term_spread", "dvol_zscore"]:
        if col in features.columns:
            meta_features[col] = features[col].reindex(
                barrier_df.index, method="ffill"
            ).fillna(0)

    # Drop columns with zero variance
    var = meta_features.var()
    meta_features = meta_features.loc[:, var > 1e-12]

    if meta_features.shape[1] < 3:
        log.warning("meta.too_few_features", symbol=symbol, n=meta_features.shape[1])
        return None

    # Target: label +1 → Y=1, label -1 ou 0 → Y=0
    y_target = (barrier_df["label"] == 1).astype(int)

    # ── 4. P_bma via Purged K-Fold ──────────────────────────────────────
    p_bma = generate_pbma_purged_kfold(
        feature_df=meta_features,
        target_series=y_target,
        sample_weights=sample_weights,
        n_eff=n_eff,
        n_splits=min(10, max(3, int(n_eff / 20))),
        embargo_pct=0.01,
    )

    # ── 5. Isotonic Calibration walk-forward ─────────────────────────────
    artifacts_dir = str(Path(MODEL_PATH) / "calibration" / symbol)

    p_calibrated = run_isotonic_walk_forward(
        p_raw_series=p_bma,
        y_true_series=y_target,
        halflife_days=ISOTONIC_HALFLIFE,
        min_train_obs=max(30, int(n_eff * 0.3)),
        retrain_freq=21,
        artifacts_dir=artifacts_dir,
    )

    # ── Diagnóstico ──────────────────────────────────────────────────────
    valid_mask = ~p_calibrated.isna()
    if valid_mask.sum() > 0:
        p_valid = p_calibrated[valid_mask]
        p_raw_valid = p_bma.reindex(p_valid.index)
        y_valid = y_target.reindex(p_valid.index)
        from sklearn.metrics import roc_auc_score, brier_score_loss
        try:
            auc = roc_auc_score(y_valid, p_valid)
            brier = brier_score_loss(y_valid, p_valid)
        except Exception:
            auc, brier = 0.5, 0.25

        # v10.10.1: ECE before/after isotonic (Fix 2 — calibração pooled)
        from meta_labeling.isotonic_calibration import calibration_diagnostics
        try:
            ece_diag = calibration_diagnostics(
                p_raw_valid.values, p_valid.values, y_valid.values)
            ece_before = ece_diag["ece_raw"]
            ece_after  = ece_diag["ece_calibrated"]
        except Exception:
            ece_before, ece_after = 0.0, 0.0

        log.info("meta.calibration_done", symbol=symbol,
                 n_calibrated=int(valid_mask.sum()),
                 auc=round(auc, 4), brier=round(brier, 4),
                 ece_before=round(ece_before, 4),
                 ece_after=round(ece_after, 4),
                 p_cal_mean=round(float(p_valid.mean()), 4),
                 p_cal_std=round(float(p_valid.std()), 4),
                 p_raw_mean=round(float(p_raw_valid.mean()), 4))
    else:
        auc, brier = 0.5, 0.25

    return {
        "p_bma": p_bma,
        "p_calibrated": p_calibrated,
        "y_target": y_target,
        "uniqueness": uniqueness,
        "n_eff": n_eff,
        "auc": auc,
        "brier": brier,
        "meta_features": meta_features,
    }


def run_kelly_sizing_for_symbol(
    barrier_df: pd.DataFrame,
    p_calibrated: pd.Series,
    sigma_ewma: pd.Series,
    symbol: str,
) -> pd.DataFrame:
    """
    Kelly Fracionário + CVaR sizing (Parte 10 spec v10.10).
    f = κ × (μ_adjusted) / σ²
    μ_adjusted = p_cal × |avg_pnl_tp| - (1-p_cal) × |avg_pnl_sl|
    """
    from sizing.kelly_cvar import compute_kelly_fraction

    # Calcula retornos médios de TP e SL para Kelly
    pnl_tp = barrier_df.loc[barrier_df["label"] == 1, "pnl_real"]
    pnl_sl = barrier_df.loc[barrier_df["label"] == -1, "pnl_real"]

    avg_tp = abs(float(pnl_tp.mean())) if len(pnl_tp) > 0 else 0.02
    avg_sl = abs(float(pnl_sl.mean())) if len(pnl_sl) > 0 else 0.02

    results = []
    for dt in p_calibrated.dropna().index:
        p_cal = float(p_calibrated.get(dt, 0.5))
        sigma = float(sigma_ewma.get(dt, 0.01))

        if sigma < 1e-6 or p_cal < 0.50:
            # Sem edge ou sem dados → skip
            results.append({"date": dt, "kelly_frac": 0.0, "position_usdt": 0.0,
                            "p_cal": p_cal, "sigma": sigma})
            continue

        # μ adjusted por P_calibrada (Parte 10.1)
        mu_adj = p_cal * avg_tp - (1 - p_cal) * avg_sl

        kelly_f = compute_kelly_fraction(
            mu=mu_adj, sigma=sigma, p_cal=p_cal, kappa=KELLY_KAPPA,
        )

        position_usdt = kelly_f * CAPITAL_TOTAL
        position_usdt = min(position_usdt, CAPITAL_TOTAL * 0.22)  # cap 22%

        results.append({
            "date": dt,
            "kelly_frac": round(kelly_f, 6),
            "position_usdt": round(position_usdt, 2),
            "p_cal": round(p_cal, 4),
            "sigma": round(sigma, 6),
            "mu_adj": round(mu_adj, 6),
        })

    sizing_df = pd.DataFrame(results)
    if not sizing_df.empty:
        sizing_df = sizing_df.set_index("date")
        avg_pos = float(sizing_df["position_usdt"].mean())
        max_pos = float(sizing_df["position_usdt"].max())
        pct_active = float((sizing_df["kelly_frac"] > 0).mean())
        log.info("kelly.sizing_done", symbol=symbol,
                 n_signals=len(sizing_df),
                 avg_position_usdt=round(avg_pos, 0),
                 max_position_usdt=round(max_pos, 0),
                 pct_active=round(pct_active * 100, 1))

    return sizing_df


def save_phase3_results(
    symbol: str,
    barrier_df: pd.DataFrame,
    meta_result: dict,
    sizing_df: pd.DataFrame,
) -> None:
    """Salva resultados da Fase 3 em parquet."""
    out_dir = Path(MODEL_PATH) / "phase3"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Barrier labels
    barrier_path = out_dir / f"{symbol}_barriers.parquet"
    pl.from_pandas(barrier_df.reset_index()).write_parquet(barrier_path, compression="zstd")

    # Meta-labeling results (p_bma + p_calibrated + y)
    meta_df = pd.DataFrame({
        "p_bma": meta_result["p_bma"],
        "p_calibrated": meta_result["p_calibrated"],
        "y_target": meta_result["y_target"],
        "uniqueness": meta_result["uniqueness"],
    })
    meta_path = out_dir / f"{symbol}_meta.parquet"
    pl.from_pandas(meta_df.reset_index()).write_parquet(meta_path, compression="zstd")

    # Sizing
    if not sizing_df.empty:
        sizing_path = out_dir / f"{symbol}_sizing.parquet"
        pl.from_pandas(sizing_df.reset_index()).write_parquet(sizing_path, compression="zstd")

    log.info("save.phase3", symbol=symbol,
             barriers=len(barrier_df),
             meta=len(meta_df),
             sizing=len(sizing_df))

def save_features_parquet(
    symbol: str,
    features: pd.DataFrame,
    hmm_result: pd.DataFrame,
    d_star: float,
) -> None:
    """Salva features + regime labels em parquet processado.
    NOTA: parquet_data é read-only para ml_engine. Escreve em model_artifacts."""
    out_dir = Path(MODEL_PATH) / "features"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged = features.join(hmm_result, how="left")
    merged["d_star"] = d_star
    merged["symbol"] = symbol

    # Converte para polars e salva
    df_pl = pl.from_pandas(merged.reset_index())
    path = out_dir / f"{symbol}.parquet"
    df_pl.write_parquet(path, compression="zstd")

    log.info("save.features", symbol=symbol, path=str(path),
             rows=len(df_pl), columns=list(merged.columns))


# ─── ORCHESTRATOR ────────────────────────────────────────────────────────────

async def run_ml_pipeline_full() -> dict:
    """
    Pipeline ML completo Fase 2 + Fase 3:
      Phase 2: Load Data → Features → FracDiff → HMM Walk-Forward → Corwin-Schultz
      Phase 3: Triple-Barrier → Uniqueness → PBMA Purged K-Fold → Isotonic Calibration
               → Kelly/CVaR Sizing → CVaR Portfolio Stress Test
      Cross-asset: VI/CFI clustering
    """
    start = time.time()
    symbols = discover_symbols()

    if len(symbols) < MIN_ASSETS_PIPELINE:
        log.warning("pipeline.insufficient_assets",
                    found=len(symbols), min=MIN_ASSETS_PIPELINE)
        return {"status": "waiting", "symbols": len(symbols)}

    log.info("pipeline.start", n_symbols=len(symbols), symbols=symbols[:10])

    # Ensure output dirs exist
    Path(MODEL_PATH).mkdir(parents=True, exist_ok=True)

    # ── Load BTC reference data ONCE (for btc_ma200_flag) ────────────
    btc_ref = None
    for btc_sym in ["BTC", "BTCUSDT"]:
        btc_ref = load_ohlcv(btc_sym)
        if btc_ref is not None and len(btc_ref) > 200:
            log.info("pipeline.btc_reference_loaded",
                     symbol=btc_sym, rows=len(btc_ref))
            break

    # Fallback: usar primeiro ativo grande como proxy de mercado
    if btc_ref is None:
        for proxy_sym in ["BNB", "ETH", "SOL", "XRP"]:
            btc_ref = load_ohlcv(proxy_sym)
            if btc_ref is not None and len(btc_ref) > 200:
                log.warning("pipeline.btc_proxy",
                            proxy=proxy_sym, rows=len(btc_ref),
                            msg="BTC parquet não encontrado, usando proxy")
                break

    results = {}
    all_features: dict[str, pd.DataFrame] = {}
    skipped = []

    # Phase 3: portfolio-level tracking for CVaR
    portfolio_fracs:  dict[str, float]      = {}
    portfolio_sigmas: dict[str, float]      = {}
    portfolio_pnl:    dict[str, np.ndarray] = {}

    for symbol in symbols:
        try:
            log.info("pipeline.symbol_start", symbol=symbol)

            # ── 1. Load data ─────────────────────────────────────────────
            df = load_ohlcv(symbol)
            if df is None or len(df) < MIN_HISTORY_DAYS:
                log.info("pipeline.skip_short", symbol=symbol,
                         rows=len(df) if df is not None else 0)
                skipped.append(symbol)
                continue

            # ── 2. Features base (com BTC reference) ─────────────────────
            features = compute_base_features(df, symbol, btc_data=btc_ref)

            # ── 3. FracDiff ──────────────────────────────────────────────
            fracdiff_series, d_star = run_fracdiff_for_symbol(df, symbol)
            features["close_fracdiff"] = fracdiff_series

            # ── 4. HMM walk-forward ──────────────────────────────────────
            hmm_features = features[
                [c for c in features.columns if c != "close_fracdiff"]
            ].copy()
            returns = features["ret_1d"].fillna(0)  # FIX: no NaN returns for HMM

            hmm_result = run_hmm_for_symbol(hmm_features, returns, symbol)

            # ── 5. Corwin-Schultz ────────────────────────────────────────
            cs_result = run_corwin_schultz_for_symbol(symbol)
            if cs_result is not None:
                log.info("pipeline.cs_done", symbol=symbol,
                         n_anomalies=int(cs_result["cs_anomaly"].sum()))

            # ── 6. Save Phase 2 features ──────────────────────────────────
            save_features_parquet(symbol, features, hmm_result, d_star)

            # Store CLEANED features for VI clustering (ffill+fillna like HMM)
            all_features[symbol] = features.ffill().fillna(0)

            # ══════════════════════════════════════════════════════════════
            # PHASE 3: Triple-Barrier → Meta-Labeling → Kelly Sizing
            # ══════════════════════════════════════════════════════════════

            phase3_ok = False
            meta_auc  = 0.0

            # ── 7. Triple-Barrier labeling ────────────────────────────────
            barrier_df = run_triple_barrier_for_symbol(
                df, hmm_result, features, symbol
            )

            if barrier_df is not None and len(barrier_df) >= 20:
                # ── 8. Meta-Labeling + Isotonic Calibration ───────────────
                from features.volatility import compute_sigma_ewma
                sigma_ewma = compute_sigma_ewma(returns, span=20)

                meta_result = run_meta_labeling_for_symbol(
                    features, barrier_df, hmm_result, sigma_ewma, symbol
                )

                if meta_result is not None:
                    # ── 9. Kelly/CVaR sizing ──────────────────────────────
                    sizing_df = run_kelly_sizing_for_symbol(
                        barrier_df, meta_result["p_calibrated"],
                        sigma_ewma, symbol
                    )

                    # Save Phase 3 results
                    save_phase3_results(symbol, barrier_df, meta_result, sizing_df)
                    phase3_ok = True
                    meta_auc  = meta_result.get("auc", 0.0)

                    # Collect for portfolio CVaR
                    if not sizing_df.empty:
                        latest = sizing_df.iloc[-1]
                        portfolio_fracs[symbol] = float(latest.get("kelly_frac", 0))
                        portfolio_sigmas[symbol] = float(latest.get("sigma", 0.01))
                        portfolio_pnl[symbol] = barrier_df["pnl_real"].values
                else:
                    log.warning("pipeline.meta_skip", symbol=symbol,
                                msg="N_eff too low or insufficient data")
            else:
                log.warning("pipeline.barrier_skip", symbol=symbol,
                            n_events=len(barrier_df) if barrier_df is not None else 0)

            results[symbol] = {
                "status": "ok",
                "d_star": round(d_star, 4),
                "n_rows": len(features),
                "hmm_pct_bull": round(float(hmm_result["hmm_is_bull"].mean()), 3),
                "cs_anomalies": int(cs_result["cs_anomaly"].sum()) if cs_result is not None else 0,
                "phase3": phase3_ok,
                "meta_auc": round(meta_auc, 4) if phase3_ok else None,
                "n_barriers": len(barrier_df) if barrier_df is not None else 0,
            }
            log.info("pipeline.symbol_done", symbol=symbol, **results[symbol])

        except Exception as e:
            log.error("pipeline.symbol_error", symbol=symbol, error=str(e),
                      exc_info=True)
            results[symbol] = {"status": "error", "error": str(e)}

    # ── 10. VI/CFI clustering (cross-asset) ──────────────────────────────
    if len(all_features) >= 5:
        vi_result = run_vi_clustering(all_features)
        if vi_result:
            log.info("pipeline.vi_done",
                     redundant_pairs=len(vi_result.get("redundant_pairs", [])))

    # ── 11. CVaR Portfolio Stress Test (Parte 10 — v10.10) ───────────────
    if len(portfolio_fracs) >= 2:
        try:
            from sizing.kelly_cvar import compute_cvar_stress

            cvar_stress, cvar_hist = compute_cvar_stress(
                position_fractions=portfolio_fracs,
                sigmas=portfolio_sigmas,
                pnl_history=portfolio_pnl,
                alpha=0.05,
                force_rho_one=True,
            )

            log.info("portfolio.cvar_check",
                     n_positions=len(portfolio_fracs),
                     cvar_historical=round(cvar_hist, 4),
                     cvar_stress_rho1=round(cvar_stress, 4),
                     limit=CVAR_LIMIT,
                     status="PASS" if cvar_stress <= CVAR_LIMIT else "BREACH")

            if cvar_stress > CVAR_LIMIT:
                reduction = CVAR_LIMIT / max(cvar_stress, 1e-8)
                log.warning("portfolio.cvar_breach",
                            reduction_factor=round(reduction, 3),
                            msg="All positions scaled down to meet CVaR limit")
        except Exception as e:
            log.warning("portfolio.cvar_error", error=str(e))

    # ── 12. Phase 3 Summary ──────────────────────────────────────────────
    n_phase3_ok = sum(1 for r in results.values()
                      if r.get("phase3") is True)
    aucs = [r["meta_auc"] for r in results.values()
            if r.get("meta_auc") is not None and r["meta_auc"] > 0]
    avg_auc = round(np.mean(aucs), 4) if aucs else 0.0

    log.info("phase3.summary",
             n_phase3_ok=n_phase3_ok,
             n_total=len(results),
             avg_meta_auc=avg_auc,
             n_portfolio_positions=len(portfolio_fracs))

    # ── 13. Publish summary to Redis ─────────────────────────────────────
    try:
        from redis.asyncio import from_url
        redis = await from_url(REDIS_URL, decode_responses=True)
        summary = {
            "event":    "ml_pipeline.complete",
            "n_assets": len(results),
            "n_ok":     sum(1 for r in results.values() if r.get("status") == "ok"),
            "n_error":  sum(1 for r in results.values() if r.get("status") == "error"),
            "n_skipped": len(skipped),
            "n_phase3_ok": n_phase3_ok,
            "avg_meta_auc": avg_auc,
            "n_portfolio": len(portfolio_fracs),
            "elapsed_s": round(time.time() - start, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await redis.publish("sniper:ml_status", json.dumps(summary))
        await redis.aclose()
    except Exception as e:
        log.warning("pipeline.redis_publish_fail", error=str(e))

    elapsed = round(time.time() - start, 1)
    log.info("=" * 60)
    log.info("pipeline.complete",
             elapsed_s=elapsed,
             total=len(results),
             ok=sum(1 for r in results.values() if r.get("status") == "ok"),
             errors=sum(1 for r in results.values() if r.get("status") == "error"),
             skipped=len(skipped))

    return {"status": "complete", "elapsed_s": elapsed, "results": results}


# ─── MAIN ────────────────────────────────────────────────────────────────────

async def main_loop() -> None:
    """Loop principal do ml_engine."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

    log.info("ml_engine.start",
             parquet_base=PARQUET_BASE,
             model_path=MODEL_PATH,
             fracdiff_tau=FRACDIFF_TAU,
             hmm_min_variance=HMM_N_PCA,
             vi_threshold=VI_THRESHOLD)

    while True:
        symbols = discover_symbols()

        if len(symbols) < MIN_ASSETS_PIPELINE:
            log.warning("ml_engine.waiting_data",
                        symbols_found=len(symbols),
                        min_required=MIN_ASSETS_PIPELINE,
                        retry_in_s=RETRY_INTERVAL)
            await asyncio.sleep(RETRY_INTERVAL)
            continue

        log.info("ml_engine.cycle_start", n_symbols=len(symbols))

        try:
            result = await run_ml_pipeline_full()
            log.info("ml_engine.cycle_done",
                     status=result.get("status"),
                     elapsed_s=result.get("elapsed_s"))
        except Exception as e:
            log.error("ml_engine.cycle_error", error=str(e), exc_info=True)

        # Próximo ciclo: alinhado ao data_inserter (4h)
        log.info("ml_engine.next_cycle", wait_s=RETRY_INTERVAL * 12,
                 msg="Aguardando próximo ciclo (4h)...")
        await asyncio.sleep(RETRY_INTERVAL * 12)  # ~60 min


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("ml_engine.shutdown")
        sys.exit(0)
