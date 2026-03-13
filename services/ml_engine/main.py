# =============================================================================
# DESTINO: services/ml_engine/main.py
# SNIPER v10.10 â€” ML Engine Pipeline Completo (Fase 2)
#
# Orquestra: Load Data â†’ Features â†’ FracDiff â†’ HMM Walk-Forward â†’ CFI/VI
# Cada componente jÃ¡ estÃ¡ implementado como biblioteca. Este arquivo CONECTA tudo.
#
# SUBSTITUI o stub anterior que sÃ³ publicava heartbeat no Redis.
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
from features.onchain import UNLOCK_AUDIT_COLUMNS, UNLOCK_MODEL_FEATURE_COLUMNS

log = structlog.get_logger(__name__)

# â”€â”€â”€ CONFIG â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PARQUET_BASE     = config("PARQUET_BASE_PATH",    default="/data/parquet")
MODEL_PATH       = config("MODEL_ARTIFACTS_PATH", default="/data/models")
SQLITE_PATH      = config("SQLITE_PATH",          default="/data/sqlite/sniper.db")
REDIS_URL        = config("REDIS_URL",            default="redis://redis:6379/0")

FRACDIFF_TAU       = float(config("FRACDIFF_TAU",           default="1e-5"))
FRACDIFF_MIN_TRAIN = int(config("FRACDIFF_MIN_TRAIN_OBS",   default="252"))
HMM_N_COMPONENTS   = int(config("HMM_N_COMPONENTS",        default="2"))
HMM_MIN_VARIANCE   = float(config("HMM_MIN_VARIANCE",      default="0.80"))
HMM_MIN_TRAIN_OBS  = int(config("HMM_MIN_TRAIN_OBS",      default="126"))
HMM_RETRAIN_FREQ   = int(config("HMM_RETRAIN_FREQ_DAYS",   default="21"))
VI_THRESHOLD       = float(config("VI_THRESHOLD",           default="0.30"))
CS_SIGMA_THR       = float(config("CORWIN_SCHULTZ_SIGMA_THR", default="3.0"))
ISOTONIC_HALFLIFE  = int(config("ISOTONIC_HALFLIFE_DAYS",   default="180"))
UNLOCK_MODEL_FEATURE_SET = config("UNLOCK_MODEL_FEATURE_SET", default="baseline").strip().lower()
META_TARGET_MODE = config("META_TARGET_MODE", default="tp_only").strip().lower()
HMM_META_FEATURE_MODE = config("HMM_META_FEATURE_MODE", default="include").strip().lower()
HMM_HARD_GATE_MODE = config("HMM_HARD_GATE_MODE", default="off").strip().lower()
TB_EVENT_FILTER_MODE = config("TB_EVENT_FILTER_MODE", default="hmm_sigma").strip().lower()
MODEL_RUN_TAG = config("MODEL_RUN_TAG", default="").strip()

RETRY_INTERVAL     = 300  # 5 min entre ciclos
MIN_HISTORY_DAYS   = 365  # mÃ­nimo de dados para rodar pipeline
HMM_DEGRADABLE_INPUTS = {"funding_rate_ma7d", "basis_3m"}
UNLOCK_PROXY_FEATURE_COLUMNS = [
    "unlock_overhang_proxy_rank_full",
    "unlock_fragility_proxy_rank_fallback",
]

# Minimum assets para pipeline viÃ¡vel
MIN_ASSETS_PIPELINE = 10


def get_unlock_model_feature_columns(mode: str | None = None) -> list[str]:
    normalized = (mode or UNLOCK_MODEL_FEATURE_SET or "baseline").strip().lower()
    if normalized in {"baseline", "none", "off"}:
        return []
    if normalized == "proxies":
        return UNLOCK_PROXY_FEATURE_COLUMNS.copy()
    return list(UNLOCK_MODEL_FEATURE_COLUMNS)


def _neutral_fill_value(column: str) -> float:
    if column in UNLOCK_MODEL_FEATURE_COLUMNS:
        return 0.5
    if column in {"hmm_prob_bull", "btc_ma200_flag"}:
        return 0.5
    return 0.0


def _hmm_meta_feature_enabled(mode: str | None = None) -> bool:
    normalized = (mode or HMM_META_FEATURE_MODE or "include").strip().lower()
    return normalized not in {"exclude", "off", "gate_only", "false", "0"}


def _hmm_hard_gate_enabled(mode: str | None = None) -> bool:
    normalized = (mode or HMM_HARD_GATE_MODE or "off").strip().lower()
    return normalized in {"on", "true", "1", "hard_gate", "gate"}


def _tb_event_filter_mode(mode: str | None = None) -> str:
    normalized = (mode or TB_EVENT_FILTER_MODE or "hmm_sigma").strip().lower()
    if normalized in {"sigma_only", "sigma"}:
        return "sigma_only"
    return "hmm_sigma"


def _build_meta_target(barrier_df: pd.DataFrame, symbol: str) -> pd.Series:
    normalized = (META_TARGET_MODE or "tp_only").strip().lower()
    if normalized in {"pnl_positive", "profit_sign", "pnl_real_positive"}:
        if "pnl_real" in barrier_df.columns:
            pnl = pd.to_numeric(barrier_df["pnl_real"], errors="coerce")
            target = (pnl > 0).where(pnl.notna())
            log.info(
                "meta.target_mode",
                symbol=symbol,
                mode="pnl_positive",
                positive_rate=round(float(target.dropna().mean()), 4) if target.notna().any() else None,
            )
            return target.fillna(0).astype(int)
        log.warning("meta.target_mode_fallback", symbol=symbol, requested=normalized, fallback="tp_only")
    target = (barrier_df["label"] == 1).astype(int)
    log.info(
        "meta.target_mode",
        symbol=symbol,
        mode="tp_only",
        positive_rate=round(float(target.mean()), 4) if len(target) else None,
    )
    return target


def _compute_realized_trade_buckets(barrier_df: pd.DataFrame) -> tuple[float, float]:
    pnl_real = pd.to_numeric(barrier_df.get("pnl_real"), errors="coerce").dropna()
    positive = pnl_real[pnl_real > 0]
    negative = pnl_real[pnl_real < 0]
    avg_gain = float(positive.mean()) if not positive.empty else 0.02
    avg_loss = abs(float(negative.mean())) if not negative.empty else 0.02
    return max(avg_gain, 1e-6), max(avg_loss, 1e-6)


def _fill_model_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.replace([np.inf, -np.inf], np.nan).ffill()
    for column in out.columns:
        if pd.api.types.is_numeric_dtype(out[column]):
            out[column] = pd.to_numeric(out[column], errors="coerce").fillna(_neutral_fill_value(column))
    return out


# â”€â”€â”€ DISCOVERY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def discover_symbols() -> list[str]:
    """
    Descobre sÃ­mbolos disponÃ­veis nos parquet diÃ¡rios.
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


def _read_parquet_df(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception:
        try:
            return pl.read_parquet(path).to_pandas()
        except Exception:
            try:
                return pd.read_pickle(path)
            except Exception:
                return pd.DataFrame()


def _write_parquet_df(df: pd.DataFrame, path: Path) -> None:
    try:
        pl.from_pandas(df).write_parquet(path, compression="zstd")
    except Exception:
        pass
    if path.exists() and path.stat().st_size > 0:
        return
    try:
        df.to_parquet(path, compression="zstd", index=False)
    except Exception:
        try:
            df.to_parquet(path, index=False)
        except Exception:
            df.to_pickle(path)


def load_ohlcv(symbol: str) -> pd.DataFrame | None:
    """Carrega OHLCV diÃ¡rio de um ativo. Retorna pd.DataFrame com DatetimeIndex."""
    path = Path(PARQUET_BASE) / "ohlcv_daily" / f"{symbol}.parquet"
    if not path.exists():
        return None
    try:
        df = _read_parquet_df(path)

        # Normaliza timestamp para DatetimeIndex
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()

        # Remove duplicatas
        df = df[~df.index.duplicated(keep="last")]

        # Garante colunas mÃ­nimas
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
        df = _read_parquet_df(path)
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



def _load_series_from_parquet(path: Path, candidates: list[str], out_name: str) -> pd.Series:
    """Carrega uma sÃ©rie temporal numÃ©rica de um parquet, alinhada por timestamp."""
    if not path.exists():
        return pd.Series(dtype=float, name=out_name)
    try:
        df = _read_parquet_df(path)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
        for col in candidates:
            if col in df.columns:
                ser = pd.to_numeric(df[col], errors="coerce")
                ser.name = out_name
                return ser
        return pd.Series(dtype=float, name=out_name)
    except Exception:
        return pd.Series(dtype=float, name=out_name)


def load_basis(symbol: str) -> pd.Series:
    """Carrega basis_3m do ativo. Sem fallback proxy."""
    path = Path(PARQUET_BASE) / "basis" / f"{symbol}.parquet"
    return _load_series_from_parquet(path, ["basis_3m", "basis"], "basis_3m")


def load_stablecoin_regime() -> pd.Series:
    """
    Carrega stablecoin_chg30 global do mercado.
    Busca em caminhos candidatos; se nÃ£o existir, retorna sÃ©rie vazia.
    """
    candidates = [
        Path(PARQUET_BASE) / "stablecoin" / "stablecoin_chg30.parquet",
        Path(PARQUET_BASE) / "stablecoin_chg30.parquet",
        Path(PARQUET_BASE) / "macro" / "stablecoin_chg30.parquet",
    ]
    for path in candidates:
        ser = _load_series_from_parquet(path, ["stablecoin_chg30"], "stablecoin_chg30")
        if len(ser) > 0:
            return ser
    return pd.Series(dtype=float, name="stablecoin_chg30")


def load_unlock_feature_frame(symbol: str) -> pd.DataFrame:
    """Carrega o pacote on-chain do ativo com colunas ortogonais e de auditoria."""
    candidates = [
        Path(PARQUET_BASE) / "unlocks" / f"{symbol}.parquet",
        Path(PARQUET_BASE) / "ups" / f"{symbol}.parquet",
        Path(PARQUET_BASE) / "onchain" / f"{symbol}.parquet",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            df = _read_parquet_df(path)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp").sort_index()
            elif "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], utc=True)
                df = df.set_index("date").sort_index()
            else:
                continue

            cols = [col for col in UNLOCK_MODEL_FEATURE_COLUMNS + UNLOCK_AUDIT_COLUMNS if col in df.columns]
            if cols:
                return df[cols]
        except Exception:
            continue
    return pd.DataFrame(columns=UNLOCK_MODEL_FEATURE_COLUMNS + UNLOCK_AUDIT_COLUMNS)


def load_klines_4h(symbol: str) -> pd.DataFrame | None:
    """Carrega klines 4h para Corwin-Schultz."""
    path = Path(PARQUET_BASE) / "ohlcv_4h" / f"{symbol}.parquet"
    if not path.exists():
        return None
    try:
        df = _read_parquet_df(path)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
        return df
    except Exception:
        return None


# â”€â”€â”€ FEATURE ENGINEERING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def compute_base_features(
    df: pd.DataFrame,
    symbol: str,
    btc_data: pd.DataFrame | None = None,
    funding_series: pd.Series | None = None,
    basis_series: pd.Series | None = None,
    stablecoin_series: pd.Series | None = None,
    unlock_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Computa as features base do SNIPER em conformidade com a especificaÃ§Ã£o.
    NÃ£o substitui features obrigatÃ³rias por proxies silenciosos.
    """
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df.get("volume", pd.Series(0.0, index=df.index))

    features = pd.DataFrame(index=df.index)

    def _align_optional(series: pd.Series | None, name: str) -> pd.Series:
        if series is None or len(series) == 0:
            return pd.Series(np.nan, index=df.index, name=name, dtype=float)
        ser = pd.to_numeric(series, errors="coerce")
        if not isinstance(ser.index, pd.DatetimeIndex):
            return pd.Series(np.nan, index=df.index, name=name, dtype=float)
        ser = ser[~ser.index.duplicated(keep="last")].sort_index()
        out = ser.reindex(df.index, method="ffill")
        out.name = name
        return out.astype(float)

    def _align_unlock_numeric_column(name: str) -> pd.Series:
        if unlock_frame is None or unlock_frame.empty or name not in unlock_frame.columns:
            return pd.Series(np.nan, index=df.index, name=name, dtype=float)
        ser = pd.to_numeric(unlock_frame[name], errors="coerce")
        if not isinstance(ser.index, pd.DatetimeIndex):
            return pd.Series(np.nan, index=df.index, name=name, dtype=float)
        ser = ser[~ser.index.duplicated(keep="last")].sort_index()
        out = ser.reindex(df.index, method="ffill")
        out.name = name
        return out.astype(float)

    def _align_unlock_audit_column(name: str) -> pd.Series:
        if unlock_frame is None or unlock_frame.empty or name not in unlock_frame.columns:
            return pd.Series(pd.NA, index=df.index, name=name, dtype="object")
        ser = unlock_frame[name]
        if not isinstance(ser.index, pd.DatetimeIndex):
            return pd.Series(pd.NA, index=df.index, name=name, dtype="object")
        ser = ser[~ser.index.duplicated(keep="last")].sort_index()
        out = ser.reindex(df.index, method="ffill")
        out.name = name
        return out

    # Retornos multi-horizonte
    features["ret_1d"]  = np.log(close / close.shift(1))
    features["ret_5d"]  = np.log(close / close.shift(5))
    features["ret_20d"] = np.log(close / close.shift(20))

    # Volatilidade realizada 30d (annualizada)
    features["realized_vol_30d"] = (
        features["ret_1d"].rolling(30, min_periods=10).std() * np.sqrt(365)
    )

    # Vol ratio: vol recente vs vol longa
    vol_5d  = features["ret_1d"].rolling(5, min_periods=3).std()
    vol_30d = features["ret_1d"].rolling(30, min_periods=10).std()
    features["vol_ratio"] = (vol_5d / vol_30d.clip(lower=1e-8)).clip(0, 5)

    # Features obrigatÃ³rias da especificaÃ§Ã£o
    features["funding_rate_ma7d"] = _align_optional(funding_series, "funding_rate_ma7d")
    features["basis_3m"] = _align_optional(basis_series, "basis_3m")
    features["stablecoin_chg30"] = _align_optional(stablecoin_series, "stablecoin_chg30")
    for col in UNLOCK_MODEL_FEATURE_COLUMNS:
        features[col] = _align_unlock_numeric_column(col).clip(0, 1)
    for col in UNLOCK_AUDIT_COLUMNS:
        features[col] = _align_unlock_audit_column(col)

    # BTC MA200 flag: usa BTC real quando disponÃ­vel
    ma200 = close.rolling(200, min_periods=100).mean()
    if btc_data is not None and "close" in btc_data.columns:
        btc_close = btc_data["close"]
        btc_ma200 = btc_close.rolling(200, min_periods=100).mean()
        btc_flag  = (btc_close > btc_ma200).astype(float)
        features["btc_ma200_flag"] = btc_flag.reindex(df.index, method="ffill").fillna(0.5)
    else:
        features["btc_ma200_flag"] = (close > ma200).astype(float)

    # DVOL zscore via Parkinson
    from features.volatility import compute_sigma_intraday_parkinson
    parkinson = compute_sigma_intraday_parkinson(high, low, ewm_span=20)
    parkinson_mean = parkinson.rolling(90, min_periods=30).mean()
    parkinson_std  = parkinson.rolling(90, min_periods=30).std().clip(lower=1e-8)
    features["dvol_zscore"] = ((parkinson - parkinson_mean) / parkinson_std).clip(-4, 4)

    log.info(
        "features.computed",
        symbol=symbol,
        n_rows=len(features),
        n_valid=int(features.dropna(how="all").shape[0]),
        features=list(features.columns),
        has_btc_ref=btc_data is not None,
        has_funding=features["funding_rate_ma7d"].notna().any(),
        has_basis=features["basis_3m"].notna().any(),
        has_stablecoin=features["stablecoin_chg30"].notna().any(),
        has_unlock=any(features[col].notna().any() for col in UNLOCK_MODEL_FEATURE_COLUMNS),
    )

    return features


# â”€â”€â”€ FRACDIFF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_fracdiff_for_symbol(df: pd.DataFrame, symbol: str) -> tuple[pd.Series, float]:
    """
    Roda FracDiff expanding window para um ativo.
    Retorna sÃ©rie diferenciada e d* Ã³timo final.
    """
    from fracdiff.optimal_d import find_optimal_d_expanding
    from fracdiff.transform import fracdiff_log_fast

    close = df["close"]

    if len(close) < FRACDIFF_MIN_TRAIN:
        log.warning("fracdiff.insufficient_data", symbol=symbol, n=len(close))
        # Fallback: usa d=0.5 como estimativa conservadora
        result = fracdiff_log_fast(close.values, d=0.5, tau=FRACDIFF_TAU)
        return pd.Series(result, index=close.index, name="close_fracdiff"), 0.5

    # Expanding window: calcula d* Ã³timo para cada janela
    # NOTA: isso Ã© computacionalmente caro (~30s/ativo). Usar cache.
    d_star_series = find_optimal_d_expanding(
        close, min_train_obs=FRACDIFF_MIN_TRAIN, tau=FRACDIFF_TAU
    )

    # Usa o Ãºltimo d* como d representativo para a sÃ©rie completa
    d_final = float(d_star_series.dropna().iloc[-1]) if not d_star_series.dropna().empty else 0.5

    result = fracdiff_log_fast(close.values, d=d_final, tau=FRACDIFF_TAU)

    log.info("fracdiff.done", symbol=symbol,
             d_star=round(d_final, 4), n_valid=int(np.sum(~np.isnan(result))))

    return pd.Series(result, index=close.index, name="close_fracdiff"), d_final


# â”€â”€â”€ HMM REGIME DETECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_hmm_for_symbol(
    features: pd.DataFrame,
    returns: pd.Series,
    symbol: str,
) -> pd.DataFrame:
    """Roda HMM walk-forward para um ativo."""
    from regime.hmm_filter import run_hmm_walk_forward, validate_hmm_diagnostics

    # â”€â”€ FILTER: usar apenas HMM_FEATURES da especificaÃ§Ã£o (Parte 4) â”€â”€
    # Nenhuma proxy substitui funding/basis/stablecoin silenciosamente.
    HMM_FEATURE_COLS = [
        "ret_1d", "ret_5d", "realized_vol_30d", "vol_ratio",
        "funding_rate_ma7d", "basis_3m", "stablecoin_chg30",
        "btc_ma200_flag", "dvol_zscore",
    ]
    missing_required = [c for c in HMM_FEATURE_COLS if c not in features.columns]
    if missing_required:
        log.error("hmm.required_columns_missing", symbol=symbol, missing=missing_required)
        return pd.DataFrame(
            {"hmm_prob_bull": np.nan, "hmm_is_bull": False},
            index=features.index
        )

    feat_hmm = features[HMM_FEATURE_COLS].copy()
    degradable_sparse = [
        col for col in HMM_FEATURE_COLS
        if col in HMM_DEGRADABLE_INPUTS and feat_hmm[col].notna().sum() < max(30, HMM_MIN_TRAIN_OBS)
    ]
    if degradable_sparse:
        log.warning(
            "hmm.degraded_sparse_inputs",
            symbol=symbol,
            dropped=degradable_sparse,
            coverage={col: int(feat_hmm[col].notna().sum()) for col in degradable_sparse},
        )
        feat_hmm = feat_hmm.drop(columns=degradable_sparse)

    empty_required = [c for c in feat_hmm.columns if not feat_hmm[c].notna().any()]
    if empty_required:
        log.error("hmm.required_inputs_missing", symbol=symbol, missing=empty_required)
        return pd.DataFrame(
            {"hmm_prob_bull": np.nan, "hmm_is_bull": False},
            index=features.index
        )

    feat_clean = feat_hmm.ffill()

    # Drop columns with zero variance after forward fill.
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

    valid_rows = feat_clean.dropna()
    if len(valid_rows) < max(126, HMM_MIN_TRAIN_OBS):
        log.warning("hmm.insufficient_features", symbol=symbol, n=len(valid_rows))
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
        min_train=max(126, HMM_MIN_TRAIN_OBS),
        retrain_freq=max(7, HMM_RETRAIN_FREQ),
        artifacts_dir=artifacts_dir,
        min_variance=HMM_MIN_VARIANCE,
    )

    # ValidaÃ§Ã£o diagnÃ³stica
    diag = validate_hmm_diagnostics(hmm_result, returns.reindex(hmm_result.index))
    log.info("hmm.validation", symbol=symbol, **diag)

    return hmm_result


# â”€â”€â”€ CORWIN-SCHULTZ CIRCUIT BREAKER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€â”€ VI/CFI CLUSTERING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def run_vi_clustering(all_features: dict[str, pd.DataFrame]) -> dict:
    """
    Computa matriz VI GLOBAL (pooled) + clusterizaÃ§Ã£o hierÃ¡rquica (Ward).

    v10.10.2 â€” 4 DEFESAS LP DE PRADO:
      DEFESA 1: Pooling cross-asset (N=500â†’15000, estabiliza discretizaÃ§Ã£o)
      DEFESA 2: _safe_discretize() trata binÃ¡rias sem KBinsDiscretizer
      DEFESA 3: std < 1e-3 â†’ VI=1.0 (feature isolada, sem informaÃ§Ã£o mÃºtua)
      DEFESA 4: scipy.cluster.hierarchy.linkage(ward) + fcluster â†’ cluster_map.json

    Salva artefatos:
      - /data/models/vi_matrix.csv    (matriz de distÃ¢ncias VI)
      - /data/models/cluster_map.json (mapeamento clusterâ†’features, reutilizÃ¡vel Fase 4)
    """
    from vi_cfi.vi import compute_vi_distance_matrix, cluster_features

    exclude_cols = {"symbol", "d_star", "close_fracdiff", *UNLOCK_AUDIT_COLUMNS}

    # â”€â”€ Seleciona features comuns a >= 50% dos ativos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    # â”€â”€ DEFESA 1: Pooling cross-asset â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    pool_rows = []
    symbols_pooled = []
    for sym, feat_df in all_features.items():
        avail = [c for c in common_feats if c in feat_df.columns]
        if len(avail) < len(common_feats) * 0.7:
            continue
        sub = feat_df[avail].replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub) >= 50:
            pool_rows.append(sub)
            symbols_pooled.append(sym)

    if not pool_rows:
        log.warning("vi.no_pooled_data")
        return {"status": "INSUFFICIENT_DATA"}

    pooled_df = pd.concat(pool_rows, axis=0, ignore_index=True)
    for c in common_feats:
        if c not in pooled_df.columns:
            pooled_df[c] = 0.0
    # â”€â”€ SANITIZAÃ‡ÃƒO: Inf das janelas de inicializaÃ§Ã£o FracDiff/log-returns â”€â”€
    pooled_df = pooled_df[common_feats].replace([np.inf, -np.inf], np.nan).dropna()

    log.info("vi.pooled_data",
             n_symbols=len(symbols_pooled),
             n_obs=len(pooled_df),
             n_features=len(common_feats),
             features=common_feats)

    if len(pooled_df) < 200:
        log.warning("vi.insufficient_pooled", n=len(pooled_df))
        return {"status": "INSUFFICIENT_DATA"}

    # â”€â”€ DEFESA 2+3: Matriz VI (binÃ¡rios bypass + variÃ¢ncia zero guard) â”€â”€â”€â”€
    vi_matrix = compute_vi_distance_matrix(pooled_df, n_bins=10)

    # â”€â”€ DEFESA 4: ClusterizaÃ§Ã£o hierÃ¡rquica (Ward + fcluster) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    save_dir = str(Path(MODEL_PATH))
    cl_result = cluster_features(
        vi_matrix,
        vi_threshold=VI_THRESHOLD,
        save_path=save_dir,
    )

    triu = vi_matrix.values[np.triu_indices_from(vi_matrix.values, k=1)]
    mean_vi = round(float(triu.mean()), 4) if len(triu) > 0 else 0.0

    log.info("vi.clustering_done",
             n_symbols_pooled=len(symbols_pooled),
             n_obs_pooled=len(pooled_df),
             n_features=len(common_feats),
             n_clusters=cl_result["n_clusters"],
             n_redundant_pairs=len(cl_result["redundant_pairs"]),
             mean_vi=mean_vi,
             threshold=VI_THRESHOLD,
             cluster_sizes={cid: len(fs) for cid, fs in cl_result["cluster_map"].items()},
             status=cl_result["status"])

    return {
        "vi_matrix":          vi_matrix,
        "cluster_map":        cl_result["cluster_map"],
        "feature_to_cluster": cl_result["feature_to_cluster"],
        "redundant_pairs":    cl_result["redundant_pairs"],
        "n_symbols_pooled":   len(symbols_pooled),
        "n_obs_pooled":       len(pooled_df),
        "n_features":         len(common_feats),
        "n_clusters":         cl_result["n_clusters"],
        "mean_vi":            mean_vi,
        "status":             cl_result["status"],
    }


# â”€â”€â”€ PHASE 3: TRIPLE-BARRIER + META-LABELING + ISOTONIC + SIZING â”€â”€â”€â”€â”€â”€â”€â”€â”€

CAPITAL_TOTAL      = float(config("CAPITAL_TOTAL",          default="200000"))
KELLY_KAPPA        = float(config("KELLY_QUARTER_FACTOR",   default="0.25"))
CVAR_LIMIT         = float(config("PORTFOLIO_CVAR_LIMIT",   default="0.15"))
TB_K_TP            = float(config("TB_K_TP",                default="1.5"))
TB_K_SL            = float(config("TB_K_SL",                default="1.5"))
TB_MAX_HOLDING     = int(config("TB_MAX_HOLDING_DAYS",      default="5"))
TB_ETA             = float(config("TB_ETA",                 default="0.10"))
TB_REFERENCE_POSITION_FRAC = float(config("TB_REFERENCE_POSITION_FRAC", default="0.05"))


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
    Slippage SL: Î”P = Î· Ã— Ïƒ_intraday Ã— âˆš(Q/V).
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

    # Eventos: datas onde HMM = bull E dados suficientes (sigma vÃ¡lido)
    hmm_aligned = hmm_result.reindex(df.index)
    valid_sigma = sigma_ewma > 0
    if _tb_event_filter_mode() == "sigma_only":
        events = df.index[valid_sigma]
    else:
        bull_mask = hmm_aligned["hmm_prob_bull"].fillna(0.0) >= 0.50
        events = df.index[bull_mask & valid_sigma]

    if len(events) < 30:
        log.warning("triple_barrier.too_few_events", symbol=symbol, n=len(events))
        return None

    # Position sizes default (proporÃ§Ã£o igual do capital)
    pos_size_default = CAPITAL_TOTAL * 0.05  # 5% por posiÃ§Ã£o inicial
    pos_size_default = CAPITAL_TOTAL * TB_REFERENCE_POSITION_FRAC
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

    # ValidaÃ§Ã£o de distribuiÃ§Ã£o (checklist v10.5)
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
    1. Uniqueness dinÃ¢mica (t_touch real)
    2. N_eff â†’ seleÃ§Ã£o de modelo (LogÃ­stica vs LGBM)
    3. P_bma via Purged K-Fold (stacking ortogonal v10.7)
    4. Isotonic calibration walk-forward com time-decay
    5. Retorna P_calibrated para Kelly sizing
    """
    from meta_labeling.uniqueness import (
        compute_label_uniqueness, compute_effective_n, compute_meta_sample_weights
    )
    from meta_labeling.pbma_purged import generate_pbma_purged_kfold
    from meta_labeling.isotonic_calibration import run_isotonic_walk_forward

    # â”€â”€ 1. Uniqueness dinÃ¢mica â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    uniqueness = compute_label_uniqueness(barrier_df)

    # â”€â”€ 2. N_eff e sample weights â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    # â”€â”€ 3. Build meta features â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Alinha features ao barrier_df index (event dates)
    meta_features = pd.DataFrame(index=barrier_df.index)

    unlock_meta_cols = get_unlock_model_feature_columns()
    log.info(
        "meta.unlock_feature_set",
        symbol=symbol,
        unlock_model_feature_set=UNLOCK_MODEL_FEATURE_SET,
        unlock_model_columns=unlock_meta_cols,
        hmm_meta_feature_enabled=_hmm_meta_feature_enabled(),
        hmm_hard_gate_enabled=_hmm_hard_gate_enabled(),
        meta_target_mode=META_TARGET_MODE,
    )

    # HMM probability
    hmm_aligned = hmm_result["hmm_prob_bull"].reindex(barrier_df.index, method="ffill")
    if _hmm_meta_feature_enabled():
        meta_features["hmm_prob_bull"] = hmm_aligned.fillna(0.5)

    # Sigma EWMA at signal
    meta_features["sigma_ewma"] = sigma_ewma.reindex(barrier_df.index, method="ffill").fillna(0)

    # Features da especificaÃ§Ã£o + FracDiff
    for col in [
        "ret_1d",
        "ret_5d",
        "ret_20d",
        "realized_vol_30d",
        "vol_ratio",
        "funding_rate_ma7d",
        "basis_3m",
        "stablecoin_chg30",
        *unlock_meta_cols,
        "dvol_zscore",
        "btc_ma200_flag",
        "close_fracdiff",
    ]:
        if col in features.columns:
            aligned = features[col].reindex(barrier_df.index, method="ffill")
            # close_fracdiff Ã© obrigatÃ³rio: preserva NaN iniciais para trim por linhas,
            # evitando que zeros artificiais matem o sinal da feature.
            if col == "close_fracdiff":
                meta_features[col] = aligned
            elif col in UNLOCK_MODEL_FEATURE_COLUMNS or col == "btc_ma200_flag":
                meta_features[col] = aligned
            else:
                meta_features[col] = aligned.fillna(0)

    # Target: label +1 â†’ Y=1, label -1 ou 0 â†’ Y=0
    y_target = _build_meta_target(barrier_df, symbol)

    # HMM hard gate alinhado aos eventos
    hmm_for_gate = None
    if _hmm_hard_gate_enabled() and "hmm_prob_bull" in hmm_result.columns:
        hmm_for_gate = hmm_result["hmm_prob_bull"].reindex(
            barrier_df.index, method="ffill"
        ).fillna(0.0)

    # Preserva TODO o histÃ³rico por ativo.
    # O warm-up do FracDiff NÃƒO deve mutilar o dataset inteiro da Fase 3/4.
    # Em vez de truncar o ativo inteiro, mantemos a coluna e imputamos apenas
    # os NaNs iniciais de forma conservadora apÃ³s o alinhamento temporal.
    if "close_fracdiff" in meta_features.columns:
        frac_aligned = pd.to_numeric(meta_features["close_fracdiff"], errors="coerce")
        first_valid = frac_aligned.first_valid_index()
        valid_ratio = float(frac_aligned.notna().mean()) if len(frac_aligned) else 0.0
        log.info("meta.fracdiff_coverage", symbol=symbol,
                 valid_ratio=round(valid_ratio, 3),
                 first_valid=str(first_valid) if first_valid is not None else None)
        meta_features["close_fracdiff"] = frac_aligned.ffill().fillna(0.0)

    # SanitizaÃ§Ã£o final apÃ³s trim por linhas
    meta_features = _fill_model_frame(meta_features)

    # Drop columns with zero variance
    var = meta_features.var()
    meta_features = meta_features.loc[:, var > 1e-12]

    if meta_features.shape[1] < 3:
        log.warning("meta.too_few_features", symbol=symbol, n=meta_features.shape[1])
        return None

    # â”€â”€ 4. P_bma via PurgedKFold â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    p_bma = generate_pbma_purged_kfold(
        feature_df=meta_features,
        target_series=y_target,
        sample_weights=sample_weights,
        n_eff=n_eff,
        n_splits=min(10, max(3, int(n_eff / 20))),
        embargo_pct=0.01,
        hmm_series=hmm_for_gate,
        drop_hmm_feature=not _hmm_meta_feature_enabled(),
    )

    # â”€â”€ 5. Isotonic Calibration walk-forward â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    artifacts_dir = str(Path(MODEL_PATH) / "calibration" / symbol)

    p_calibrated = run_isotonic_walk_forward(
        p_raw_series=p_bma,
        y_true_series=y_target,
        halflife_days=ISOTONIC_HALFLIFE,
        min_train_obs=max(30, int(n_eff * 0.3)),
        retrain_freq=21,
        artifacts_dir=artifacts_dir,
    )

    # Reaplica HMM hard gate APÃ“S calibraÃ§Ã£o isotÃ´nica.
    if hmm_for_gate is not None:
        bear_mask = hmm_for_gate.reindex(p_calibrated.index).fillna(0.0) < 0.50
        p_calibrated = p_calibrated.copy()
        p_calibrated.loc[bear_mask] = 0.0

    # â”€â”€ DiagnÃ³stico â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        # v10.10.1: ECE before/after isotonic (Fix 2 â€” calibraÃ§Ã£o pooled)
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
    Kelly FracionÃ¡rio + CVaR sizing (Parte 10 spec v10.10).
    f = Îº Ã— (Î¼_adjusted) / ÏƒÂ²
    Î¼_adjusted = p_cal Ã— |avg_pnl_tp| - (1-p_cal) Ã— |avg_pnl_sl|
    """
    from sizing.kelly_cvar import compute_kelly_fraction

    # Calcula retornos mÃ©dios de TP e SL para Kelly
    avg_tp, avg_sl = _compute_realized_trade_buckets(barrier_df)

    results = []
    for dt in p_calibrated.dropna().index:
        p_cal = float(p_calibrated.get(dt, 0.5))
        sigma = float(sigma_ewma.get(dt, 0.01))

        if sigma < 1e-6 or p_cal < 0.50:
            # Sem edge ou sem dados â†’ skip
            results.append({"date": dt, "kelly_frac": 0.0, "position_usdt": 0.0,
                            "p_cal": p_cal, "sigma": sigma})
            continue

        # Î¼ adjusted por P_calibrada (Parte 10.1)
        mu_adj = p_cal * avg_tp - (1 - p_cal) * avg_sl

        kelly_f = compute_kelly_fraction(
            mu=mu_adj, sigma=sigma, p_cal=p_cal, kappa=KELLY_KAPPA,
        )

        position_usdt = kelly_f * CAPITAL_TOTAL
        position_usdt = min(position_usdt, CAPITAL_TOTAL * 0.08)  # cap 8%

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
    _write_parquet_df(barrier_df.reset_index(), barrier_path)

    # Meta-labeling results (p_bma + p_calibrated + y)
    meta_df = pd.DataFrame({
        "p_bma": meta_result["p_bma"],
        "p_calibrated": meta_result["p_calibrated"],
        "y_target": meta_result["y_target"],
        "uniqueness": meta_result["uniqueness"],
    })
    meta_path = out_dir / f"{symbol}_meta.parquet"
    _write_parquet_df(meta_df.reset_index(), meta_path)

    # Sizing
    if not sizing_df.empty:
        sizing_path = out_dir / f"{symbol}_sizing.parquet"
        _write_parquet_df(sizing_df.reset_index(), sizing_path)

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
    NOTA: parquet_data Ã© read-only para ml_engine. Escreve em model_artifacts."""
    out_dir = Path(MODEL_PATH) / "features"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged = features.join(hmm_result, how="left")
    merged["d_star"] = d_star
    merged["symbol"] = symbol

    path = out_dir / f"{symbol}.parquet"
    _write_parquet_df(merged.reset_index(), path)

    log.info("save.features", symbol=symbol, path=str(path),
             rows=len(merged), columns=list(merged.columns))


def _load_saved_feature_artifact(symbol: str) -> pd.DataFrame:
    path = Path(MODEL_PATH) / "features" / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = _read_parquet_df(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()
    return df


def _load_saved_barrier_artifact(symbol: str) -> pd.DataFrame:
    path = Path(MODEL_PATH) / "phase3" / f"{symbol}_barriers.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = _read_parquet_df(path)
    for column in ["event_date", "date", "timestamp", "index"]:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], utc=True)
            df = df.set_index(column).sort_index()
            break
    return df


async def rerun_phase3_from_saved_features(symbols: list[str] | None = None) -> dict:
    from features.volatility import compute_sigma_ewma

    feature_dir = Path(MODEL_PATH) / "features"
    if symbols is None:
        symbols = sorted(path.stem for path in feature_dir.glob("*.parquet"))

    ok = 0
    skipped = 0
    errors: dict[str, str] = {}

    for symbol in symbols:
        try:
            features = _load_saved_feature_artifact(symbol)
            barrier_df = _load_saved_barrier_artifact(symbol)
            if features.empty or barrier_df.empty:
                skipped += 1
                continue
            if "hmm_prob_bull" not in features.columns:
                errors[symbol] = "missing_hmm_prob_bull"
                continue

            hmm_result = features[["hmm_prob_bull"]].copy()
            if "hmm_is_bull" in features.columns:
                hmm_result["hmm_is_bull"] = features["hmm_is_bull"].fillna(False).astype(bool)
            else:
                hmm_result["hmm_is_bull"] = features["hmm_prob_bull"].fillna(0.0) >= 0.50

            returns = pd.to_numeric(features["ret_1d"], errors="coerce").fillna(0.0)
            sigma_ewma = compute_sigma_ewma(returns, span=20).reindex(features.index, fill_value=0.0)
            meta_result = run_meta_labeling_for_symbol(features, barrier_df, hmm_result, sigma_ewma, symbol)
            if meta_result is None:
                errors[symbol] = "meta_result_none"
                continue
            sizing_df = run_kelly_sizing_for_symbol(
                barrier_df,
                meta_result["p_calibrated"],
                sigma_ewma,
                symbol,
            )
            save_phase3_results(symbol, barrier_df, meta_result, sizing_df)
            ok += 1
        except Exception as exc:
            errors[symbol] = str(exc)

    result = {
        "status": "ok" if not errors else "partial",
        "ok": ok,
        "skipped": skipped,
        "errors": errors,
        "unlock_model_feature_set": UNLOCK_MODEL_FEATURE_SET,
        "unlock_model_columns": get_unlock_model_feature_columns(),
        "meta_target_mode": META_TARGET_MODE,
        "hmm_meta_feature_mode": HMM_META_FEATURE_MODE,
        "hmm_hard_gate_mode": HMM_HARD_GATE_MODE,
        "tb_event_filter_mode": _tb_event_filter_mode(),
        "model_run_tag": MODEL_RUN_TAG,
    }
    log.info("phase3.rerun_complete", **result)
    return result


# â”€â”€â”€ ORCHESTRATOR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def run_ml_pipeline_full() -> dict:
    """
    Pipeline ML completo Fase 2 + Fase 3:
      Phase 2: Load Data â†’ Features â†’ FracDiff â†’ HMM Walk-Forward â†’ Corwin-Schultz
      Phase 3: Triple-Barrier â†’ Uniqueness â†’ PBMA Purged K-Fold â†’ Isotonic Calibration
               â†’ Kelly/CVaR Sizing â†’ CVaR Portfolio Stress Test
      Cross-asset: VI/CFI clustering
    """
    start = time.time()
    symbols = discover_symbols()

    if len(symbols) < MIN_ASSETS_PIPELINE:
        log.warning("pipeline.insufficient_assets",
                    found=len(symbols), min=MIN_ASSETS_PIPELINE)
        return {"status": "waiting", "symbols": len(symbols)}

    log.info(
        "pipeline.start",
        n_symbols=len(symbols),
        symbols=symbols[:10],
        unlock_model_feature_set=UNLOCK_MODEL_FEATURE_SET,
        unlock_model_columns=get_unlock_model_feature_columns(),
        meta_target_mode=META_TARGET_MODE,
        hmm_meta_feature_mode=HMM_META_FEATURE_MODE,
        hmm_hard_gate_mode=HMM_HARD_GATE_MODE,
        tb_event_filter_mode=_tb_event_filter_mode(),
        model_run_tag=MODEL_RUN_TAG,
    )

    # Ensure output dirs exist
    Path(MODEL_PATH).mkdir(parents=True, exist_ok=True)
    # v10.10.6: Pre-create Phase 3 directory so diagnostic never sees "NOT FOUND"
    (Path(MODEL_PATH) / "phase3").mkdir(parents=True, exist_ok=True)
    (Path(MODEL_PATH) / "calibration").mkdir(parents=True, exist_ok=True)

    # â”€â”€ Load BTC reference data ONCE (for btc_ma200_flag) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                            msg="BTC parquet nÃ£o encontrado, usando proxy")
                break

    results = {}
    all_features: dict[str, pd.DataFrame] = {}
    phase2_cache: dict[str, dict] = {}
    skipped = []

    # Phase 3: portfolio-level tracking for CVaR
    portfolio_fracs:  dict[str, float]      = {}
    portfolio_sigmas: dict[str, float]      = {}
    portfolio_pnl:    dict[str, np.ndarray] = {}

    from features.volatility import compute_sigma_ewma

    for symbol in symbols:
        try:
            log.info("pipeline.symbol_start", symbol=symbol)

            df = load_ohlcv(symbol)
            if df is None or len(df) < MIN_HISTORY_DAYS:
                log.info("pipeline.skip_short", symbol=symbol,
                         rows=len(df) if df is not None else 0)
                skipped.append(symbol)
                continue

            funding_series = load_funding(symbol)
            basis_series = load_basis(symbol)
            stablecoin_series = load_stablecoin_regime()
            unlock_frame = load_unlock_feature_frame(symbol)
            features = compute_base_features(
                df,
                symbol,
                btc_data=btc_ref,
                funding_series=funding_series,
                basis_series=basis_series,
                stablecoin_series=stablecoin_series,
                unlock_frame=unlock_frame,
            )

            fracdiff_series, d_star = run_fracdiff_for_symbol(df, symbol)
            features["close_fracdiff"] = fracdiff_series

            hmm_features = features[[c for c in features.columns if c != "close_fracdiff"]].copy()
            returns = features["ret_1d"].fillna(0)
            hmm_result = run_hmm_for_symbol(hmm_features, returns, symbol)

            cs_result = run_corwin_schultz_for_symbol(symbol)
            if cs_result is not None:
                log.info("pipeline.cs_done", symbol=symbol,
                         n_anomalies=int(cs_result["cs_anomaly"].sum()))

            save_features_parquet(symbol, features, hmm_result, d_star)
            vi_feature_frame = features.drop(columns=UNLOCK_AUDIT_COLUMNS, errors="ignore")
            all_features[symbol] = _fill_model_frame(vi_feature_frame)

            features = features.reindex(df.index)
            hmm_result = hmm_result.reindex(df.index)
            hmm_result["hmm_prob_bull"] = hmm_result["hmm_prob_bull"].fillna(0.0)
            hmm_result["hmm_is_bull"] = hmm_result["hmm_is_bull"].fillna(False)

            sigma_ewma = compute_sigma_ewma(returns.reindex(df.index, fill_value=0.0), span=20)
            sigma_ewma = sigma_ewma.reindex(df.index, fill_value=0.0)

            log.info("pipeline.phase2_ready", symbol=symbol,
                     len_df=len(df), len_features=len(features), len_hmm=len(hmm_result),
                     len_sigma=len(sigma_ewma),
                     hmm_bull_pct=round(float(hmm_result["hmm_is_bull"].mean()), 3),
                     sigma_valid_pct=round(float((sigma_ewma > 0).mean()), 3))

            phase2_cache[symbol] = {
                "df": df,
                "features": features,
                "hmm_result": hmm_result,
                "sigma_ewma": sigma_ewma,
                "d_star": d_star,
                "cs_result": cs_result,
            }

        except Exception as e:
            log.error("pipeline.phase2_error", symbol=symbol,
                      error=str(e), exc_info=True)
            results[symbol] = {"status": "error", "error": str(e), "phase": 2}
            continue

    vi_result = {}
    if len(all_features) >= 5:
        vi_result = run_vi_clustering(all_features)
        if vi_result.get("status") in ("OK", "PASS"):
            log.info("pipeline.vi_done",
                     n_clusters=vi_result.get("n_clusters"),
                     redundant_pairs=len(vi_result.get("redundant_pairs", [])),
                     cluster_sizes={cid: len(fs) for cid, fs in
                                    vi_result.get("cluster_map", {}).items()})
        else:
            log.warning("pipeline.vi_failed", status=vi_result.get("status"))
    else:
        log.warning("pipeline.vi_skipped", n_assets=len(all_features),
                    msg="Not enough phase2 assets for VI clustering")

    for symbol in symbols:
        if symbol not in phase2_cache:
            continue

        cached = phase2_cache[symbol]
        df = cached["df"]
        features = cached["features"]
        hmm_result = cached["hmm_result"]
        sigma_ewma = cached["sigma_ewma"]
        d_star = cached["d_star"]
        cs_result = cached["cs_result"]

        phase3_ok = False
        meta_auc = 0.0
        barrier_df = None

        try:
            barrier_df = run_triple_barrier_for_symbol(df, hmm_result, features, symbol)
        except Exception as e:
            log.error(f"FATAL ERROR in Phase 3 Triple-Barrier for {symbol}: {str(e)}",
                      exc_info=True)
            raise

        if barrier_df is not None and len(barrier_df) >= 20:
            try:
                meta_result = run_meta_labeling_for_symbol(
                    features, barrier_df, hmm_result, sigma_ewma, symbol
                )
            except Exception as e:
                log.error(f"FATAL ERROR in Phase 3 Meta-Labeling for {symbol}: {str(e)}",
                          exc_info=True)
                raise

            if meta_result is not None:
                try:
                    sizing_df = run_kelly_sizing_for_symbol(
                        barrier_df, meta_result["p_calibrated"], sigma_ewma, symbol
                    )

                    save_phase3_results(symbol, barrier_df, meta_result, sizing_df)
                    phase3_ok = True
                    meta_auc = meta_result.get("auc", 0.0)

                    log.info("phase3.SAVED_OK", symbol=symbol,
                             n_barriers=len(barrier_df),
                             auc=round(meta_auc, 4),
                             n_sizing=len(sizing_df))

                    if not sizing_df.empty:
                        latest = sizing_df.iloc[-1]
                        portfolio_fracs[symbol] = float(latest.get("kelly_frac", 0))
                        portfolio_sigmas[symbol] = float(latest.get("sigma", 0.01))
                        portfolio_pnl[symbol] = barrier_df["pnl_real"].values
                except Exception as e:
                    log.error(f"FATAL ERROR in Phase 3 Kelly/Save for {symbol}: {str(e)}",
                              exc_info=True)
                    raise
            else:
                log.warning("phase3.meta_returned_none", symbol=symbol,
                            msg="N_eff too low or insufficient data - skipping, NOT fatal")
        else:
            n_ev = len(barrier_df) if barrier_df is not None else 0
            log.warning("phase3.insufficient_barrier_events", symbol=symbol,
                        n_events=n_ev, min_required=20,
                        msg="Not enough HMM-bull events for this symbol - skipping")

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

    phase4_status = None
    n_phase3_ready = sum(1 for r in results.values() if r.get("phase3") is True)
    if n_phase3_ready > 0:
        try:
            from phase4_cpcv import main as run_phase4_cpcv

            run_phase4_cpcv()

            phase4_report_path = Path(MODEL_PATH) / "phase4_report_v4.json"
            snapshot_path = Path(MODEL_PATH) / "phase4" / "phase4_execution_snapshot.parquet"
            aggregated_path = Path(MODEL_PATH) / "phase4" / "phase4_aggregated_predictions.parquet"
            phase4_report = {}

            if phase4_report_path.exists():
                with open(phase4_report_path, "r", encoding="utf-8") as fin:
                    phase4_report = json.load(fin)
                phase4_status = phase4_report.get("cpcv", {}).get("status")

            if snapshot_path.exists():
                snapshot_df = pl.read_parquet(snapshot_path).to_pandas()
                if not snapshot_df.empty:
                    active_mask = pd.to_numeric(snapshot_df.get("position_usdt", 0.0), errors="coerce").fillna(0.0) > 0
                    active_snapshot = snapshot_df.loc[active_mask].copy()
                    portfolio_fracs = {
                        str(row["symbol"]): float(row.get("kelly_frac", 0.0))
                        for _, row in active_snapshot.iterrows()
                    }
                    portfolio_sigmas = {
                        str(row["symbol"]): float(row.get("sigma_ewma", 0.01))
                        for _, row in active_snapshot.iterrows()
                    }
                    if aggregated_path.exists():
                        agg_df = pl.read_parquet(aggregated_path).to_pandas()
                        portfolio_pnl = {
                            str(symbol): pd.to_numeric(sym_df["pnl_real"], errors="coerce").fillna(0.0).values
                            for symbol, sym_df in agg_df.groupby("symbol")
                        }
                    log.info("phase4.snapshot_loaded",
                             status=phase4_status,
                             n_symbols=len(snapshot_df),
                             n_active=len(active_snapshot))
            else:
                log.warning("phase4.snapshot_missing", path=str(snapshot_path))
        except Exception as e:
            log.warning("phase4.run_error", error=str(e), exc_info=True)

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

    # â”€â”€ 12. Phase 3 Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    n_phase3_ok = sum(1 for r in results.values()
                      if r.get("phase3") is True)
    aucs = [r["meta_auc"] for r in results.values()
            if r.get("meta_auc") is not None and r["meta_auc"] > 0]
    avg_auc = round(np.mean(aucs), 4) if aucs else 0.0

    log.info("phase3.summary",
             n_phase3_ok=n_phase3_ok,
             n_total=len(results),
             avg_meta_auc=avg_auc,
             n_portfolio_positions=len(portfolio_fracs),
             phase4_status=phase4_status)

    # â”€â”€ 13. Publish summary to Redis â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            "phase4_status": phase4_status,
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

    return {"status": "complete", "elapsed_s": elapsed, "results": results, "phase4_status": phase4_status}


# â”€â”€â”€ MAIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

        # PrÃ³ximo ciclo: alinhado ao data_inserter (4h)
        log.info("ml_engine.next_cycle", wait_s=RETRY_INTERVAL * 12,
                 msg="Aguardando prÃ³ximo ciclo (4h)...")
        await asyncio.sleep(RETRY_INTERVAL * 12)  # ~60 min


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("ml_engine.shutdown")
        sys.exit(0)


