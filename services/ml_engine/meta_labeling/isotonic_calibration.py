# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/isotonic_calibration.py
# Calibração isotônica com time-decay (halflife=180d).
#
# POR QUE ISOTÔNICA E NÃO PLATT SCALING (Parte 9):
#   Platt: assume relação sigmoidal entre score bruto e probabilidade real.
#   Em crypto, a relação score→probabilidade é não-monotônica em crises:
#   score=0.8 pode ter P(win)=0.55 em regime normal e P(win)=0.40 em crise.
#   Isotônica: apenas impõe monotonia (score maior → prob maior) sem
#   assumir forma funcional. Não-paramétrica, robusta a fat tails.
#
# TIME-DECAY (Parte 9 — v10.10):
#   Observações de 180 dias atrás têm peso = e^(-180/180) = 0.368.
#   Observações de 365 dias atrás têm peso = e^(-365/180) = 0.131.
#   Garante que a calibração reflete o regime atual, não histórico distante.
#   halflife_days = 180 é o default. NUNCA aumentar acima de 365.
#
# EXPANDING WINDOW ESTRITA:
#   calibrador[t] = fit(dados_0..t-1). NUNCA inclui t. Mesmo princípio do d*.
# =============================================================================
from __future__ import annotations

from pathlib import Path
import pickle

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
import structlog

log = structlog.get_logger(__name__)

DEFAULT_HALFLIFE    = 180    # dias — halflife do time-decay
MIN_CALIB_OBS       = 60     # mínimo de observações para calibrar
RETRAIN_FREQ_DAYS   = 21     # re-calibrar a cada 21 dias (≈ 1 mês)


def _time_decay_weights(
    dates:        pd.DatetimeIndex,
    reference_dt: pd.Timestamp,
    halflife_days: int = DEFAULT_HALFLIFE,
) -> np.ndarray:
    """
    Pesos de decaimento temporal exponencial.

    w(t) = exp(-(reference_dt - t) / halflife)

    Observações mais recentes têm peso maior.
    reference_dt é sempre a última data de treino disponível
    (nunca uma data do conjunto de teste — look-ahead).

    Args:
        dates:         DatetimeIndex das observações.
        reference_dt:  Data de referência (max do treino).
        halflife_days: Halflife em dias. Default 180.

    Returns:
        np.ndarray de pesos ∈ (0, 1]. Não normalizados.
    """
    days_ago = (reference_dt - dates).days.astype(float)
    weights  = np.exp(-days_ago / max(halflife_days, 1))
    return np.maximum(weights, 1e-6)   # evita peso zero exato


def fit_isotonic_calibrator(
    p_raw:        np.ndarray,
    y_true:       np.ndarray,
    dates:        pd.DatetimeIndex,
    halflife_days: int = DEFAULT_HALFLIFE,
) -> IsotonicRegression:
    """
    Fitta regressão isotônica ponderada por time-decay.

    IsotonicRegression(increasing=True):
        Impõe P_calibrated(p_raw) seja monotonicamente crescente em p_raw.
        Não assume forma sigmoidal — ajusta livremente contanto que seja ↑.

    Ponderação por time-decay:
        Observações mais antigas pesam menos. Reflete regime atual.
        halflife=180d: 6 meses de dados com contribuição relevante.
        halflife=365d: 1 ano — muito lento para crypto (regime muda).

    Args:
        p_raw:         Array de probabilidades brutas do meta-modelo ∈ [0,1].
        y_true:        Labels reais (0/1).
        dates:         DatetimeIndex alinhado com p_raw e y_true.
        halflife_days: Halflife do decaimento. Default 180d.

    Returns:
        IsotonicRegression fittado. Serializar em disco após fit.
    """
    if len(p_raw) < MIN_CALIB_OBS:
        log.warning("isotonic.insufficient_data",
                    n=len(p_raw), min_required=MIN_CALIB_OBS)
        # Retorna calibrador identidade (sem transformação)
        ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
        linspace = np.linspace(0, 1, 20)
        ir.fit(linspace, linspace)
        return ir

    reference_dt = dates.max()
    weights      = _time_decay_weights(dates, reference_dt, halflife_days)

    ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
    ir.fit(p_raw, y_true, sample_weight=weights)

    # Diagnóstico: reliability diagram (resumido)
    bins     = np.linspace(0, 1, 11)
    bin_idx  = np.digitize(p_raw, bins) - 1
    ece      = 0.0   # Expected Calibration Error
    for b in range(10):
        mask = bin_idx == b
        if mask.sum() > 0:
            conf = p_raw[mask].mean()
            acc  = y_true[mask].mean()
            ece += (mask.sum() / len(p_raw)) * abs(conf - acc)

    p_cal    = ir.predict(p_raw)
    ece_post = 0.0
    for b in range(10):
        mask = bin_idx == b
        if mask.sum() > 0:
            conf = p_cal[mask].mean()
            acc  = y_true[mask].mean()
            ece_post += (mask.sum() / len(p_raw)) * abs(conf - acc)

    log.info("isotonic.fitted",
             n=len(p_raw),
             halflife_days=halflife_days,
             ece_before=round(ece, 4),
             ece_after=round(ece_post, 4),
             ece_improvement_pct=round((ece - ece_post) / max(ece, 1e-10) * 100, 1))

    if ece_post > ece:
        log.warning("isotonic.calibration_degraded",
                    ece_before=round(ece, 4), ece_after=round(ece_post, 4),
                    msg="Calibração piorou — checar look-ahead ou N insuficiente.")

    return ir


def run_isotonic_walk_forward(
    p_raw_series:  pd.Series,
    y_true_series: pd.Series,
    halflife_days: int = DEFAULT_HALFLIFE,
    min_train_obs: int = MIN_CALIB_OBS,
    retrain_freq:  int = RETRAIN_FREQ_DAYS,
    artifacts_dir: str = "/data/models/calibration",
) -> pd.Series:
    """
    Calibração isotônica com expanding window estrita (zero look-ahead).

    Para cada ponto t:
        calibrador[t] = fit(p_raw[0..t-1], y_true[0..t-1])
        p_cal[t] = calibrador[t].predict(p_raw[t])

    Re-treina a cada retrain_freq dias para capturar mudanças de regime.
    Serializa artefato de cada janela para auditoria.

    Args:
        p_raw_series:  pd.Series de probabilidades brutas com DatetimeIndex.
        y_true_series: pd.Series de labels reais (0/1).
        halflife_days: Halflife do time-decay. Default 180d.
        min_train_obs: Mínimo de obs antes de calibrar. Default 60.
        retrain_freq:  Frequência de re-treinamento em dias. Default 21.
        artifacts_dir: Diretório para serializar calibradores.

    Returns:
        pd.Series: P_calibrada para cada ponto. NaN antes de min_train_obs.
    """
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)

    n          = len(p_raw_series)
    p_cal      = pd.Series(np.nan, index=p_raw_series.index,
                           name="p_calibrated")
    calibrator: IsotonicRegression | None = None
    last_train  = 0
    n_retrains  = 0

    for t in range(min_train_obs, n):
        should_retrain = (
            calibrator is None or
            (t - last_train) >= retrain_freq
        )

        if should_retrain:
            p_tr   = p_raw_series.iloc[:t].values
            y_tr   = y_true_series.iloc[:t].values
            dt_tr  = p_raw_series.iloc[:t].index

            calibrator = fit_isotonic_calibrator(
                p_tr, y_tr, dt_tr,
                halflife_days=halflife_days,
            )
            last_train = t
            n_retrains += 1

            # Serializa para auditoria
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

        p_cal.iloc[t] = float(
            calibrator.predict([p_raw_series.iloc[t]])[0]
        )

    log.info("isotonic.walk_forward_complete",
             n=n, n_retrains=n_retrains,
             p_cal_mean=round(float(p_cal.dropna().mean()), 4),
             p_cal_std=round(float(p_cal.dropna().std()), 4),
             nan_pct=round(p_cal.isna().mean() * 100, 1))

    return p_cal


def calibration_diagnostics(
    p_raw:   np.ndarray,
    p_cal:   np.ndarray,
    y_true:  np.ndarray,
    n_bins:  int = 10,
) -> dict:
    """
    Diagnóstico de calibração: ECE antes/depois + reliability diagram data.

    ECE (Expected Calibration Error):
        Σ_b (N_b / N) × |mean_confidence_b - mean_accuracy_b|
        ECE = 0: probabilidades perfeitas. ECE > 0.05: calibração pobre.

    Critério de aprovação (Checklist Parte 12, item 9a):
        ECE_calibrated < 0.03.

    Returns:
        dict com ECE_raw, ECE_calibrated, reliability bins.
    """
    bins    = np.linspace(0, 1, n_bins + 1)
    results = {"n_bins": n_bins, "bins": []}

    def compute_ece(probs):
        ece = 0.0
        bin_data = []
        for i in range(n_bins):
            mask = (probs >= bins[i]) & (probs < bins[i + 1])
            if mask.sum() > 0:
                conf = float(probs[mask].mean())
                acc  = float(y_true[mask].mean())
                w    = mask.sum() / len(probs)
                ece += w * abs(conf - acc)
                bin_data.append({
                    "bin_low": round(bins[i], 2),
                    "bin_high": round(bins[i + 1], 2),
                    "mean_conf": round(conf, 4),
                    "mean_acc":  round(acc, 4),
                    "n":         int(mask.sum()),
                })
        return ece, bin_data

    ece_raw, bins_raw = compute_ece(p_raw)
    ece_cal, bins_cal = compute_ece(p_cal)

    results.update({
        "ece_raw":        round(ece_raw, 5),
        "ece_calibrated": round(ece_cal, 5),
        "ece_ok":         ece_cal < 0.03,
        "improvement_pct": round(
            (ece_raw - ece_cal) / max(ece_raw, 1e-10) * 100, 1
        ),
        "bins_raw": bins_raw,
        "bins_cal": bins_cal,
    })

    log.info("calibration.diagnostics",
             ece_raw=round(ece_raw, 5),
             ece_calibrated=round(ece_cal, 5),
             ece_ok=ece_cal < 0.03)

    if not results["ece_ok"]:
        log.warning("calibration.ece_fail",
                    ece=round(ece_cal, 5),
                    msg="ECE > 0.03: considerar aumentar halflife ou N de treino.")

    return results
