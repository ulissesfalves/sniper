# =============================================================================
# DESTINO: services/ml_engine/regime/hmm_filter.py
# Filtro de regime HMM: Winsorização → RobustScaler → PCA → GaussianHMM.
#
# v10.10.1 FIX: PCA agora usa variância-alvo (85%) em vez de n_components=2.
# Com 9 features, PCA(2) captura só 49% → HMM cego → hmm_prob_bull=0.9998.
# PCA(0.85) retém 4-5 PCs → HMM recebe estrutura suficiente para diferenciar
# bull/bear. Ratio N/params mantido ≥10 com N_train ≥ 500.
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

N_HMM_STATES       = 2
MIN_VARIANCE_TARGET = 0.85    # v10.10.1: variância-alvo para PCA
MIN_OBS_TREINO      = 252


@dataclass
class HMMFitted:
    """Artefato completo do filtro de regime."""
    pca_pipeline:   RobustPCAFitted
    hmm:            GaussianHMM
    bull_state:     int
    threshold:      float
    var_explained:  float
    n_pca_components: int     # v10.10.1: registra PCs reais usados
    train_end_date: Optional[str]
    f1_train:       float


def fit_hmm(
    X_train:         np.ndarray,
    returns_train:   np.ndarray,
    feature_names:   list[str] | None = None,
    min_variance:    float = MIN_VARIANCE_TARGET,
    n_hmm_states:    int   = N_HMM_STATES,
    train_end_date:  str | None = None,
    # Legacy parameter — ignored if min_variance is set
    n_components:    int | None = None,
) -> HMMFitted:
    """
    Fitta pipeline completo HMM em dados de treino.

    v10.10.1: PCA seleciona PCs por variância-alvo (85%), não fixo em 2.

    Pipeline:
        1. Winsorização 1%-99% → RobustScaler → PCA (variância ≥ 85%)
        2. GaussianHMM full cov (300 iter)
        3. bull_state = estado com maior retorno médio
        4. threshold calibrado por F1 no treino

    Checagem de estabilidade:
        N_train / (n_hmm_params) deve ser ≥ 10.
        n_hmm_params = n_states * (n_states - 1) + n_states * n_pcs + n_states * n_pcs * (n_pcs+1)/2
        5 PCs, 2 estados: ~37 params → N_train ≥ 370 (atendido com min_train=252+)
    """
    names = feature_names or HMM_FEATURES[:X_train.shape[1]]

    # ── 1. PCA Robusto (variância-alvo) ──────────────────────────────
    pca_fitted = fit_robust_pca(
        X_train,
        feature_names=names,
        min_variance=min_variance,
        n_components=None,  # Force variance-based selection
    )
    X_pca = transform_robust_pca(X_train, pca_fitted)
    n_pcs_used = pca_fitted.n_components

    # Checagem de estabilidade HMM
    # full cov params: n_states*(n_pcs + n_pcs*(n_pcs+1)/2) + n_states*(n_states-1)
    n_hmm_params = (n_hmm_states * (n_pcs_used + n_pcs_used*(n_pcs_used+1)//2)
                    + n_hmm_states * (n_hmm_states - 1))
    param_ratio = len(X_train) / max(n_hmm_params, 1)

    # Se ratio muito baixo, usar covariância diagonal para estabilidade
    if param_ratio < 10:
        cov_type = "diag"
        log.warning("hmm.using_diag_cov",
                    n_pcs=n_pcs_used, n_params=n_hmm_params,
                    N_train=len(X_train), ratio=round(param_ratio, 1))
    else:
        cov_type = "full"

    log.info("hmm.pca_result",
             n_pcs=n_pcs_used,
             var_explained=round(pca_fitted.var_explained, 3),
             n_hmm_params=n_hmm_params,
             param_ratio=round(param_ratio, 1),
             cov_type=cov_type)

    # ── 2. HMM ──────────────────────────────────────────────────────
    hmm = GaussianHMM(
        n_components=n_hmm_states,
        covariance_type=cov_type,
        n_iter=300,
        random_state=42,
        tol=1e-4,
    )
    try:
        hmm.fit(X_pca)
    except Exception as e:
        log.error("hmm.fit_failed", error=str(e), n_pcs=n_pcs_used)
        raise

    # ── 3. Identifica bull_state ─────────────────────────────────────
    states = hmm.predict(X_pca)
    ret_by_state = [
        float(returns_train[states == s].mean()) if (states == s).sum() > 0 else -999.0
        for s in range(n_hmm_states)
    ]
    bull_state = int(np.argmax(ret_by_state))

    # Diagnóstico: distribuição de estados
    state_counts = [(states == s).sum() for s in range(n_hmm_states)]
    log.info("hmm.states_identified",
             state_returns={s: round(r, 4) for s, r in enumerate(ret_by_state)},
             state_counts={s: int(c) for s, c in enumerate(state_counts)},
             bull_state=bull_state)

    # ── 4. Calibra threshold por F1 (no treino) ─────────────────────
    probs    = hmm.predict_proba(X_pca)[:, bull_state]
    y_true   = (returns_train > 0).astype(int)
    best_thr = 0.5
    best_f1  = 0.0

    for thr in np.linspace(0.20, 0.80, 25):
        preds = (probs > thr).astype(int)
        if preds.sum() > 0:
            score = f1_score(y_true, preds, zero_division=0)
            if score > best_f1:
                best_f1  = score
                best_thr = float(thr)

    # Diagnóstico: distribuição de probs
    pct_bull = float((probs > best_thr).mean())
    log.info("hmm.threshold_calibrated",
             threshold=round(best_thr, 3),
             f1_train=round(best_f1, 4),
             pct_bull=round(pct_bull, 3),
             prob_mean=round(float(probs.mean()), 4),
             prob_std=round(float(probs.std()), 4))

    return HMMFitted(
        pca_pipeline=pca_fitted,
        hmm=hmm,
        bull_state=bull_state,
        threshold=best_thr,
        var_explained=pca_fitted.var_explained,
        n_pca_components=n_pcs_used,
        train_end_date=train_end_date,
        f1_train=best_f1,
    )


def predict_regime(
    X_oos:  np.ndarray,
    fitted: HMMFitted,
) -> tuple[np.ndarray, np.ndarray]:
    """Prediz regime para dados OOS."""
    X_pca = transform_robust_pca(X_oos, fitted.pca_pipeline)
    probs = fitted.hmm.predict_proba(X_pca)[:, fitted.bull_state]
    return probs, probs > fitted.threshold


def run_hmm_walk_forward(
    feature_df:       pd.DataFrame,
    returns:          pd.Series,
    min_train:        int   = MIN_OBS_TREINO,
    retrain_freq:     int   = 63,
    artifacts_dir:    str   = "/data/models/hmm",
    min_variance:     float = MIN_VARIANCE_TARGET,
    # Legacy — kept for backwards compat, ignored if min_variance set
    n_pca_components: int | None = None,
) -> pd.DataFrame:
    """
    Walk-forward completo do HMM.

    v10.10.1: PCA variância-alvo. Se n_pca_components é passado (legado),
    será ignorado em favor de min_variance.
    """
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
    n           = len(feature_df)
    prob_series = np.full(n, np.nan)
    bull_series = np.zeros(n, dtype=bool)

    fitted_hmm: HMMFitted | None = None
    last_train_idx = 0

    for t in range(min_train, n):
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
                    min_variance=min_variance,
                    train_end_date=str(feature_df.index[t]),
                )
                last_train_idx = t

                artifact_path = Path(artifacts_dir) / f"hmm_t{t}.pkl"
                with open(artifact_path, "wb") as f:
                    pickle.dump(fitted_hmm, f)

                log.info("hmm.retrained",
                         t=t, date=str(feature_df.index[t]),
                         n_pcs=fitted_hmm.n_pca_components,
                         var_exp=round(fitted_hmm.var_explained, 3),
                         threshold=round(fitted_hmm.threshold, 3))
            except Exception as e:
                log.error("hmm.train_error", t=t, error=str(e))
                continue

        if fitted_hmm is None:
            continue

        X_t = feature_df.iloc[[t]].values
        if np.any(np.isnan(X_t)):
            continue

        try:
            probs, bulls       = predict_regime(X_t, fitted_hmm)
            prob_series[t]     = float(probs[0])
            bull_series[t]     = bool(bulls[0])
        except Exception as e:
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
    """Checklist v10.8 para HMM."""
    result: dict = {}

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
