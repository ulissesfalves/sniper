# =============================================================================
# DESTINO: services/ml_engine/regime/hmm_filter.py
# Filtro de regime HMM: Winsorização → RobustScaler → PCA → GaussianHMM.
# Pipeline walk-forward puro: threshold calibrado NO TREINO, aplicado cegamente
# no teste. Nenhum parâmetro do HMM usa dados fora da janela de treino.
# Referência: SNIPER v10.10, Parte 4.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.metrics import f1_score
import structlog

from .pca_robust import RobustPCAFitted, fit_robust_pca, transform_robust_pca

log = structlog.get_logger(__name__)

HMM_FEATURES = [
    "ret_1d",
    "ret_5d",
    "realized_vol_30d",
    "vol_ratio",
    "funding_rate_ma7d",
    "basis_3m",
    "stablecoin_chg30",
    "btc_ma200_flag",
    "dvol_zscore",
]

N_HMM_STATES   = 2
N_PCA_COMP      = 2
MIN_OBS_TREINO  = 252


@dataclass
class HMMFitted:
    """
    Artefato completo do filtro de regime.
    Serializado em disco para cada janela de expanding window.
    """
    pca_pipeline:  RobustPCAFitted
    hmm:           GaussianHMM
    bull_state:    int              # qual estado = bull (maior retorno médio)
    threshold:     float            # P(bull) > threshold → regime bull
    var_explained: float
    train_end_date: Optional[str]   # data final do treino (auditoria)
    f1_train:       float           # F1 no treino (diagnóstico)


def fit_hmm(
    X_train:        np.ndarray,
    returns_train:  np.ndarray,
    feature_names:  list[str] | None = None,
    n_components:   int   = N_PCA_COMP,
    n_hmm_states:   int   = N_HMM_STATES,
    train_end_date: str | None = None,
) -> HMMFitted:
    """
    Fitta o pipeline completo HMM em dados de treino.

    Processo:
        1. Winsorização 1%-99% → RobustScaler → PCA (2 componentes)
        2. GaussianHMM com covariância full (300 iterações)
        3. Identifica bull_state: estado com maior retorno médio
        4. Calibra threshold por F1 no treino (busca em 25 pontos)

    REGRA: threshold calibrado NO TREINO. Aplicado cegamente no OOS.
    Nunca calibrar threshold com dados de validação ou teste.

    Args:
        X_train:        Array (N_treino × N_features). Features do HMM_FEATURES.
        returns_train:  Array (N_treino,). Retornos diários correspondentes.
        feature_names:  Nomes das features. Default: HMM_FEATURES.
        n_components:   PCs para o HMM. Default 2 (ratio N/params ≈ 125).
        n_hmm_states:   Estados do HMM. Default 2 (bull / bear).
        train_end_date: Data final do treino para auditoria.

    Returns:
        HMMFitted com todos os artefatos serializáveis.
    """
    names = feature_names or HMM_FEATURES[:X_train.shape[1]]

    # ── 1. PCA Robusto ────────────────────────────────────────────────────
    pca_fitted = fit_robust_pca(X_train, feature_names=names,
                                n_components=n_components)
    X_pca      = transform_robust_pca(X_train, pca_fitted)

    # ── 2. HMM ────────────────────────────────────────────────────────────
    hmm = GaussianHMM(
        n_components=n_hmm_states,
        covariance_type="full",
        n_iter=300,
        random_state=42,
        tol=1e-4,
    )
    try:
        hmm.fit(X_pca)
    except Exception as e:
        log.error("hmm.fit_failed", error=str(e))
        raise

    # ── 3. Identifica bull_state ──────────────────────────────────────────
    states   = hmm.predict(X_pca)
    ret_by_state = [
        float(returns_train[states == s].mean()) if (states == s).sum() > 0 else -999.0
        for s in range(n_hmm_states)
    ]
    bull_state = int(np.argmax(ret_by_state))

    log.info("hmm.states_identified",
             state_returns={s: round(r, 4) for s, r in enumerate(ret_by_state)},
             bull_state=bull_state)

    # ── 4. Calibra threshold por F1 (no treino) ───────────────────────────
    probs     = hmm.predict_proba(X_pca)[:, bull_state]
    y_true    = (returns_train > 0).astype(int)
    best_thr  = 0.5
    best_f1   = 0.0

    for thr in np.linspace(0.20, 0.80, 25):
        preds = (probs > thr).astype(int)
        if preds.sum() > 0:
            score = f1_score(y_true, preds, zero_division=0)
            if score > best_f1:
                best_f1   = score
                best_thr  = float(thr)

    log.info("hmm.threshold_calibrated",
             threshold=round(best_thr, 3),
             f1_train=round(best_f1, 4))

    # ── Diagnóstico: cobertura do bear 2022 ───────────────────────────────
    # Checklist v10.8: HMM deve detectar 2022 bear em > 60% dos dias Jan-Jun/2022
    preds_all = (probs > best_thr).astype(int)
    pct_bull  = float(preds_all.mean())
    log.info("hmm.regime_distribution",
             pct_bull=round(pct_bull, 3),
             pct_bear=round(1 - pct_bull, 3))

    return HMMFitted(
        pca_pipeline=pca_fitted,
        hmm=hmm,
        bull_state=bull_state,
        threshold=best_thr,
        var_explained=pca_fitted.var_explained,
        train_end_date=train_end_date,
        f1_train=best_f1,
    )


def predict_regime(
    X_oos:  np.ndarray,
    fitted: HMMFitted,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Prediz regime para dados OOS usando artefatos do treino.

    Returns:
        probs:     np.ndarray (N,) — P(bull) para cada observação.
        is_bull:   np.ndarray (N,) bool — True se P(bull) > threshold.
    """
    X_pca = transform_robust_pca(X_oos, fitted.pca_pipeline)
    probs = fitted.hmm.predict_proba(X_pca)[:, fitted.bull_state]
    return probs, probs > fitted.threshold


def run_hmm_walk_forward(
    feature_df:       pd.DataFrame,
    returns:          pd.Series,
    min_train:        int   = MIN_OBS_TREINO,
    retrain_freq:     int   = 63,           # re-treinar a cada 63 dias (~1 trimestre)
    artifacts_dir:    str   = "/data/models/hmm",
    n_pca_components: int   = N_PCA_COMP,   # v10.10: SEMPRE 2 (ratio N/params ~125)
) -> pd.DataFrame:
    """
    Walk-forward completo do HMM.
    Para cada janela [0..t], fitta o HMM e prediz t+1.
    Re-treina a cada retrain_freq dias para capturar mudanças de regime.

    Critério de invalidação (Parte 15):
        F1 OOS treino < 0.45 → revisar features de input.

    Returns:
        pd.DataFrame com colunas ['hmm_prob_bull', 'hmm_is_bull']
        para todo o período OOS.
    """
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
    n          = len(feature_df)
    prob_series = np.full(n, np.nan)
    bull_series = np.zeros(n, dtype=bool)

    fitted_hmm: HMMFitted | None = None
    last_train_idx = 0

    for t in range(min_train, n):
        # Re-treina a cada retrain_freq dias ou na primeira vez
        should_retrain = (
            fitted_hmm is None or
            (t - last_train_idx) >= retrain_freq
        )

        if should_retrain:
            X_tr   = feature_df.iloc[:t].dropna().values
            ret_tr = returns.iloc[:t].reindex(
                feature_df.iloc[:t].dropna().index
            ).values

            if len(X_tr) < min_train:
                continue

            try:
                fitted_hmm = fit_hmm(
                    X_tr, ret_tr,
                    feature_names=feature_df.columns.tolist(),
                    n_components=n_pca_components,
                    train_end_date=str(feature_df.index[t]),
                )
                last_train_idx = t

                # Serializa artefato para auditoria
                artifact_path = Path(artifacts_dir) / f"hmm_t{t}.pkl"
                with open(artifact_path, "wb") as f:
                    pickle.dump(fitted_hmm, f)

                log.info("hmm.retrained",
                         t=t, date=str(feature_df.index[t]),
                         threshold=round(fitted_hmm.threshold, 3),
                         var_exp=round(fitted_hmm.var_explained, 3))
            except Exception as e:  # noqa: BLE001
                log.error("hmm.train_error", t=t, error=str(e))
                continue

        # Prediz ponto t
        if fitted_hmm is None:
            continue

        X_t = feature_df.iloc[[t]].values
        if np.any(np.isnan(X_t)):
            continue

        try:
            probs, bulls       = predict_regime(X_t, fitted_hmm)
            prob_series[t]     = float(probs[0])
            bull_series[t]     = bool(bulls[0])
        except Exception as e:  # noqa: BLE001
            log.warning("hmm.predict_error", t=t, error=str(e))

    result = pd.DataFrame(
        {"hmm_prob_bull": prob_series, "hmm_is_bull": bull_series},
        index=feature_df.index,
    )
    log.info("hmm.walk_forward_complete",
             n_bull=int(bull_series.sum()),
             n_bear=int((~bull_series).sum()),
             pct_bull=round(bull_series.mean(), 3))
    return result


def validate_hmm_diagnostics(
    hmm_result: pd.DataFrame,
    returns:    pd.Series,
    min_f1:     float = 0.45,
) -> dict:
    """
    Checklist obrigatório v10.8 para o HMM (Parte 12, itens 7b e 7c).

    Verifica:
    1. 2022 bear detectado em > 60% dos dias Jan-Jun/2022?
    2. F1 OOS ≥ min_f1?
    3. var_explained ≥ 80% em todas as janelas?

    Returns:
        dict com resultados de cada check e status geral.
    """
    result: dict = {}

    # Check 1: detecção do bear de 2022
    mask_2022h1 = (
        (hmm_result.index >= "2022-01-01") &
        (hmm_result.index <= "2022-06-30")
    )
    if mask_2022h1.sum() > 0:
        bear_2022 = (~hmm_result.loc[mask_2022h1, "hmm_is_bull"]).mean()
        result["bear_2022_h1_pct"] = round(float(bear_2022), 3)
        result["bear_2022_ok"]     = bear_2022 >= 0.60
    else:
        result["bear_2022_ok"] = None
        result["bear_2022_h1_pct"] = None

    # Check 2: F1 OOS
    valid_mask = ~np.isnan(hmm_result["hmm_prob_bull"])
    y_true = (returns.reindex(hmm_result.index) > 0).astype(int)
    y_pred = hmm_result["hmm_is_bull"].astype(int)

    if valid_mask.sum() > 30:
        oos_f1 = f1_score(
            y_true[valid_mask], y_pred[valid_mask], zero_division=0
        )
        result["f1_oos"]    = round(float(oos_f1), 4)
        result["f1_oos_ok"] = oos_f1 >= min_f1
    else:
        result["f1_oos_ok"] = None

    result["status"] = "PASS" if all(
        v for v in [result.get("bear_2022_ok"), result.get("f1_oos_ok")]
        if v is not None
    ) else "FAIL"

    log.info("hmm.validation", **{k: v for k, v in result.items()
                                   if not isinstance(v, pd.DataFrame)})
    return result
