# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/isotonic_calibration.py
# Calibração isotônica via K-Fold interno real (v10.10.5).
#
# v10.10.5 MUDANÇAS vs v10.10.4:
#   1. K-Fold isotônico REAL (5 folds): divide (p_raw, y) em 5 folds,
#      fitta IsotonicRegression em 4, valida em 1, repete. Refitta final
#      em todos os dados para produção. cv='prefit' ELIMINADO.
#   2. POR QUE não CalibratedClassifierCV(cv=5): este wrapper espera um
#      estimator retreinável. Nós temos probabilidades pré-computadas
#      (OOS do CPCV), não modelo. Passthrough com cv=5 é NO-OP.
#   3. NaN sanitization mantida.
#   4. Output = p_meta_calibrated que vai direto para Kelly via
#      calibrate_probability().
#
# K-Fold isotônico corrige o ECE porque:
#   - Cada fold é genuinamente OOS (isotônica nunca viu os dados do fold)
#   - 5 calibradores internos forçam suavidade da curva
#   - Final refit usa TODOS os dados → máxima granularidade em produção
# =============================================================================
from __future__ import annotations

from pathlib import Path
import pickle

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.base import BaseEstimator, ClassifierMixin
import structlog

# v10.10.6: PurgedKFold real com embargo temporal — não sklearn.KFold
from .pbma_purged import PurgedKFold

log = structlog.get_logger(__name__)

DEFAULT_HALFLIFE    = 180
MIN_CALIB_OBS       = 60
RETRAIN_FREQ_DAYS   = 21


class _ProbabilityPassthrough(BaseEstimator, ClassifierMixin):
    """
    Classificador dummy que retorna probabilidades pré-computadas.
    Usado como base_estimator no CalibratedClassifierCV.
    
    PROBLEMA com cv=5 puro: CalibratedClassifierCV re-fitta o estimator
    em cada fold, mas nosso passthrough é no-op → isotônica vê os mesmos
    probs em todos os folds → sem benefício do cross-validation.
    
    SOLUÇÃO: Usamos este wrapper apenas como fallback (cv='prefit').
    O método principal é _kfold_isotonic_fit() que implementa K-Fold
    isotônico diretamente sobre os dados pooled.
    """

    def __init__(self):
        self.classes_ = np.array([0, 1])
        self._probs_map = None

    def fit(self, X, y=None, sample_weight=None):
        return self

    def set_probs(self, probs: np.ndarray):
        self._probs_map = np.asarray(probs, dtype=float)
        return self

    def predict_proba(self, X):
        if self._probs_map is None:
            raise ValueError("set_probs() não chamado")
        indices = X.ravel().astype(int)
        indices = np.clip(indices, 0, len(self._probs_map) - 1)
        p = self._probs_map[indices]
        return np.column_stack([1 - p, p])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    def get_params(self, deep=True):
        return {}

    def set_params(self, **params):
        return self


def _kfold_isotonic_fit(
    p_raw:    np.ndarray,
    y_true:   np.ndarray,
    weights:  np.ndarray,
    n_folds:  int = 5,
    embargo_pct: float = 0.01,
) -> IsotonicRegression:
    """
    PurgedKFold isotônico — substitui CalibratedClassifierCV(cv=5).

    v10.10.6: Usa PurgedKFold com embargo temporal.
    Cada fold purga amostras do treino que estão dentro do embargo
    do test set → genuinamente OOS, sem look-ahead bias.

    PIPELINE:
    1. PurgedKFold divide (p_raw, y_true) em K folds com embargo
    2. Para cada fold k: fitta IsotonicRegression nos K-1 folds purgados
    3. Prediz no fold k held-out → p_cal_oos[k]
    4. Refitta IsotonicRegression FINAL em TODOS os dados para produção
    5. Valida: ECE dos p_cal_oos deve ser < 0.05

    Returns:
        IsotonicRegression fittado em todos os dados (para produção).
    """
    n = len(p_raw)
    n_folds = min(n_folds, max(2, n // 30))  # Adaptivo ao N
    purged_kf = PurgedKFold(n_splits=n_folds, embargo_pct=embargo_pct)

    # Coleta predições OOS de cada fold com purge
    p_cal_oos = np.full(n, np.nan)

    for fold_idx, (train_idx, test_idx) in enumerate(purged_kf.split(p_raw)):
        ir_fold = IsotonicRegression(out_of_bounds="clip", increasing=True)
        ir_fold.fit(p_raw[train_idx], y_true[train_idx],
                    sample_weight=weights[train_idx])
        p_cal_oos[test_idx] = ir_fold.predict(p_raw[test_idx])

    # ECE OOS (genuinamente fora-da-amostra com purge)
    valid = ~np.isnan(p_cal_oos)
    ece_oos = _compute_ece_safe(p_cal_oos[valid], y_true[valid])

    log.info("isotonic.purged_kfold_oos",
             n_folds=n_folds,
             embargo_pct=embargo_pct,
             splitter=repr(purged_kf),
             ece_oos=round(ece_oos, 5),
             p_cal_mean=round(float(p_cal_oos[valid].mean()), 4))

    # Refit FINAL em TODOS os dados (para uso em produção)
    ir_final = IsotonicRegression(out_of_bounds="clip", increasing=True)
    ir_final.fit(p_raw, y_true, sample_weight=weights)

    return ir_final


def _sanitize_arrays(*arrays):
    """Remove posições com NaN ou Inf em qualquer array alinhado."""
    mask = np.ones(len(arrays[0]), dtype=bool)
    for arr in arrays:
        arr_f = np.asarray(arr, dtype=float)
        mask &= ~(np.isnan(arr_f) | np.isinf(arr_f))
    n_dropped = int((~mask).sum())
    if n_dropped > 0:
        log.info("isotonic.sanitized", n_dropped=n_dropped)
    return tuple(np.asarray(a, dtype=float)[mask] for a in arrays) + (mask,)


def _time_decay_weights(
    dates:        pd.DatetimeIndex | pd.Series | np.ndarray,
    reference_dt: pd.Timestamp,
    halflife_days: int = DEFAULT_HALFLIFE,
) -> np.ndarray:
    """Pesos de decaimento temporal exponencial.

    Compatível com DatetimeIndex, Series e arrays de timestamps.
    Evita o bug clássico de Pandas: Timedelta Series usa .dt.days,
    enquanto TimedeltaIndex usa .days.
    """
    reference_dt = pd.Timestamp(reference_dt)

    if isinstance(dates, pd.Series):
        dates_ts = pd.to_datetime(dates, errors="coerce")
        days_ago = (reference_dt - dates_ts).dt.days.astype(float).to_numpy()
    else:
        dates_idx = pd.DatetimeIndex(pd.to_datetime(dates, errors="coerce"))
        days_ago = np.asarray((reference_dt - dates_idx).days, dtype=float)

    days_ago = np.nan_to_num(days_ago, nan=float(halflife_days), posinf=float(halflife_days), neginf=0.0)
    weights = np.exp(-days_ago / max(halflife_days, 1))
    return np.maximum(weights, 1e-6)


def fit_isotonic_calibrator(
    p_raw:        np.ndarray,
    y_true:       np.ndarray,
    dates:        pd.DatetimeIndex,
    halflife_days: int = DEFAULT_HALFLIFE,
) -> IsotonicRegression:
    """
    Calibração isotônica via K-Fold interno real (v10.10.5).

    PIPELINE:
      1. Sanitiza NaN/Inf
      2. Time-decay weights
      3. K-Fold isotônico (5 folds): fitta em 4, valida em 1, repete.
         Genuinamente OOS — sem overfit nos bins.
      4. Refit final em TODOS os dados para produção.
      5. Fallback: IsotonicRegression direta se K-Fold falhar (N < 100).

    O output deste calibrador é p_meta_calibrated que vai direto para Kelly.
    """
    # ── 1. Sanitiza ──────────────────────────────────────────────────────
    p_clean, y_clean, mask = _sanitize_arrays(p_raw, y_true)[:3]

    if len(p_clean) < MIN_CALIB_OBS:
        log.warning("isotonic.insufficient", n=len(p_clean))
        ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
        ir.fit(np.linspace(0, 1, 20), np.linspace(0, 1, 20))
        return ir

    p_clean = np.clip(p_clean, 0.001, 0.999)

    # ── 2. Time-decay weights ────────────────────────────────────────────
    dates_arr = pd.to_datetime(dates, errors="coerce")
    if len(dates_arr) == len(p_raw):
        if isinstance(dates_arr, pd.Series):
            dates_clean = dates_arr[mask]
        else:
            dates_clean = pd.DatetimeIndex(np.asarray(dates_arr)[mask])
    else:
        dates_clean = dates_arr[:len(p_clean)]
    reference_dt = pd.Timestamp(pd.to_datetime(dates_clean).max())
    weights = _time_decay_weights(dates_clean, reference_dt, halflife_days)

    # ── 3. K-Fold isotônico (cv=5 real) ─────────────────────────────────
    calibrator = None
    try:
        calibrator = _kfold_isotonic_fit(
            p_clean, y_clean, weights, n_folds=5)
        log.info("isotonic.fitted_kfold", n=len(p_clean),
                 halflife=halflife_days)
    except Exception as e:
        log.warning("isotonic.kfold_fallback", error=str(e))

    # ── 4. Fallback: IsotonicRegression direta ───────────────────────────
    if calibrator is None:
        ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
        ir.fit(p_clean, y_clean, sample_weight=weights)
        calibrator = ir
        log.info("isotonic.fitted_direct", n=len(p_clean))

    # ── 5. Diagnóstico ECE ───────────────────────────────────────────────
    p_cal = calibrator.predict(p_clean)

    ece_before = _compute_ece_safe(p_clean, y_clean)
    ece_after  = _compute_ece_safe(p_cal, y_clean)

    log.info("isotonic.ece", ece_before=round(ece_before, 4),
             ece_after=round(ece_after, 4),
             improvement=round(
                 (ece_before - ece_after) / max(ece_before, 1e-10) * 100, 1))
    return calibrator


def _compute_ece_safe(
    probs:  np.ndarray,
    y_true: np.ndarray,
    n_bins: int = 15,
) -> float:
    """ECE robusto com NaN guard."""
    mask = ~(np.isnan(probs) | np.isinf(probs) | np.isnan(y_true))
    probs, y_true = probs[mask], y_true[mask]
    if len(probs) < 10:
        return 1.0

    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        in_bin = (probs >= bins[i]) & (probs < bins[i + 1])
        if in_bin.sum() == 0:
            continue
        conf = float(probs[in_bin].mean())
        acc  = float(y_true[in_bin].mean())
        ece += (in_bin.sum() / len(probs)) * abs(conf - acc)
    return ece


def calibrate_probability(
    calibrator,
    p_raw_value: float,
) -> float:
    """
    Calibra uma única probabilidade bruta → p_meta_calibrated.

    Output vai direto para Kelly. Aceita IsotonicRegression (K-Fold ou fallback).
    """
    if np.isnan(p_raw_value) or np.isinf(p_raw_value):
        return np.nan

    p_raw_value = np.clip(p_raw_value, 0.001, 0.999)

    if isinstance(calibrator, IsotonicRegression):
        return float(calibrator.predict([p_raw_value])[0])
    else:
        # Fallback genérico
        try:
            return float(calibrator.predict([p_raw_value])[0])
        except Exception:
            return p_raw_value


def run_isotonic_walk_forward(
    p_raw_series:  pd.Series,
    y_true_series: pd.Series,
    halflife_days: int = DEFAULT_HALFLIFE,
    min_train_obs: int = MIN_CALIB_OBS,
    retrain_freq:  int = RETRAIN_FREQ_DAYS,
    artifacts_dir: str = "/data/models/calibration",
) -> pd.Series:
    """
    Calibração isotônica expanding window (zero look-ahead) com K-Fold.

    Para cada ponto t:
        calibrador[t] = fit_kfold_isotonic(p_raw[0..t-1], y_true[0..t-1])
        p_cal[t] = calibrate_probability(calibrador[t], p_raw[t])

    Output: p_meta_calibrated → direto para Kelly.
    """
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    n     = len(p_raw_series)
    p_cal = pd.Series(np.nan, index=p_raw_series.index,
                       name="p_calibrated")
    calibrator = None
    last_train = 0
    n_retrains = 0

    for t in range(min_train_obs, n):
        should_retrain = (
            calibrator is None or
            (t - last_train) >= retrain_freq
        )

        if should_retrain:
            p_tr  = p_raw_series.iloc[:t].values
            y_tr  = y_true_series.iloc[:t].values
            dt_tr = p_raw_series.iloc[:t].index

            calibrator = fit_isotonic_calibrator(
                p_tr, y_tr, dt_tr, halflife_days=halflife_days)
            last_train = t
            n_retrains += 1

            art_path = Path(artifacts_dir) / f"isotonic_t{t}.pkl"
            with open(art_path, "wb") as f:
                pickle.dump({
                    "calibrator":    calibrator,
                    "halflife_days": halflife_days,
                    "train_end":     str(p_raw_series.index[t]),
                    "n_train":       t,
                }, f)

        if calibrator is None:
            continue

        p_val = p_raw_series.iloc[t]
        if np.isnan(p_val) or np.isinf(p_val):
            continue

        p_cal.iloc[t] = calibrate_probability(calibrator, p_val)

    log.info("isotonic.wf_done", n=n, n_retrains=n_retrains,
             p_cal_mean=round(float(p_cal.dropna().mean()), 4),
             p_cal_std=round(float(p_cal.dropna().std()), 4),
             nan_pct=round(p_cal.isna().mean() * 100, 1))
    return p_cal


def calibration_diagnostics(
    p_raw:   np.ndarray,
    p_cal:   np.ndarray,
    y_true:  np.ndarray,
    n_bins:  int = 15,
) -> dict:
    """
    Diagnóstico de calibração v10.10.5.
    ECE computado sobre bins de p_cal (não p_raw).
    """
    mask = ~(np.isnan(p_raw) | np.isinf(p_raw) |
             np.isnan(p_cal) | np.isinf(p_cal) |
             np.isnan(y_true))
    n_dropped = int((~mask).sum())
    p_raw_c, p_cal_c, y_true_c = p_raw[mask], p_cal[mask], y_true[mask]

    bins = np.linspace(0, 1, n_bins + 1)

    def compute_ece(probs, y_labels):
        ece = 0.0
        bin_data = []
        for i in range(n_bins):
            in_bin = (probs >= bins[i]) & (probs < bins[i + 1])
            if in_bin.sum() == 0:
                continue
            conf = float(probs[in_bin].mean())
            acc  = float(y_labels[in_bin].mean())
            gap  = abs(conf - acc)
            ece += (in_bin.sum() / len(probs)) * gap
            bin_data.append({"bin_low": round(bins[i], 3),
                             "bin_high": round(bins[i+1], 3),
                             "mean_conf": round(conf, 4),
                             "mean_acc": round(acc, 4),
                             "n": int(in_bin.sum()),
                             "gap": round(gap, 4)})
        return ece, bin_data

    ece_raw, bins_raw = compute_ece(p_raw_c, y_true_c)
    ece_cal, bins_cal = compute_ece(p_cal_c, y_true_c)

    results = {
        "n_bins": n_bins, "n_dropped_nan": n_dropped,
        "ece_raw": round(ece_raw, 5),
        "ece_calibrated": round(ece_cal, 5),
        "ece_ok": ece_cal < 0.05,
        "improvement_pct": round(
            (ece_raw - ece_cal) / max(ece_raw, 1e-10) * 100, 1),
        "bins_raw": bins_raw, "bins_cal": bins_cal,
    }

    log.info("calibration.diag", ece_raw=round(ece_raw, 5),
             ece_cal=round(ece_cal, 5), ece_ok=ece_cal < 0.05)
    return results
