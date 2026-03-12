from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.metrics import f1_score
from sklearn.preprocessing import StandardScaler
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

N_HMM_STATES = 2
MIN_VARIANCE_TARGET = 0.85
MIN_OBS_TREINO = 126
CONTEXT_WINDOW = 42


@dataclass
class HMMFitted:
    pca_pipeline: RobustPCAFitted
    hmm: GaussianHMM
    bull_state: int
    threshold: float
    var_explained: float
    n_pca_components: int
    train_end_date: Optional[str]
    f1_train: float
    hmm_scaler_mean_: np.ndarray | None = None
    hmm_scaler_scale_: np.ndarray | None = None


def build_regime_target(
    returns: np.ndarray | pd.Series,
) -> pd.Series:
    if isinstance(returns, pd.Series):
        series = pd.to_numeric(returns, errors="coerce").astype(float)
    else:
        series = pd.Series(np.asarray(returns, dtype=float))
    return (series > 0).where(series.notna())


def fit_hmm(
    X_train: np.ndarray,
    returns_train: np.ndarray,
    feature_names: list[str] | None = None,
    min_variance: float = MIN_VARIANCE_TARGET,
    n_hmm_states: int = N_HMM_STATES,
    train_end_date: str | None = None,
    n_components: int | None = None,
) -> HMMFitted:
    names = feature_names or HMM_FEATURES[: X_train.shape[1]]

    pca_fitted = fit_robust_pca(
        X_train,
        feature_names=names,
        min_variance=min_variance,
        n_components=None,
    )
    X_pca = transform_robust_pca(X_train, pca_fitted)
    n_pcs_used = pca_fitted.n_components

    # Standardiza os scores do PCA antes do HMM.
    hmm_scaler = StandardScaler(with_mean=True, with_std=True)
    X_hmm = hmm_scaler.fit_transform(X_pca)

    n_hmm_params = (
        n_hmm_states * (n_pcs_used + n_pcs_used * (n_pcs_used + 1) // 2)
        + n_hmm_states * (n_hmm_states - 1)
    )
    param_ratio = len(X_train) / max(n_hmm_params, 1)
    cov_type = "diag" if param_ratio < 10 else "full"
    if cov_type == "diag":
        log.warning(
            "hmm.using_diag_cov",
            n_pcs=n_pcs_used,
            n_params=n_hmm_params,
            N_train=len(X_train),
            ratio=round(param_ratio, 1),
        )

    log.info(
        "hmm.pca_result",
        n_pcs=n_pcs_used,
        var_explained=round(pca_fitted.var_explained, 3),
        n_hmm_params=n_hmm_params,
        param_ratio=round(param_ratio, 1),
        cov_type=cov_type,
    )

    hmm = GaussianHMM(
        n_components=n_hmm_states,
        covariance_type=cov_type,
        n_iter=500,
        random_state=42,
        tol=1e-4,
    )
    hmm.fit(X_hmm)

    states = hmm.predict(X_hmm)

    # v10.10: bull state e threshold sÃ£o calibrados contra retorno histÃ³rico observado.
    train_returns = np.asarray(returns_train, dtype=float)
    state_scores: list[float] = []
    state_counts: list[int] = []
    for s in range(n_hmm_states):
        mask_s = states == s
        state_counts.append(int(mask_s.sum()))
        if mask_s.sum() < 5:
            state_scores.append(-999.0)
            continue
        vals = train_returns[mask_s]
        vals = vals[~np.isnan(vals)]
        state_scores.append(float(vals.mean()) if len(vals) > 0 else -999.0)
    bull_state = int(np.argmax(state_scores))
    separation = float(np.nanmax(state_scores) - np.nanmin(state_scores)) if len(state_scores) > 1 else 0.0
    if (not np.isfinite(separation)) or separation < 1e-3:
        log.warning(
            "hmm.failed_to_separate_states",
            state_scores={s: round(r, 6) for s, r in enumerate(state_scores)},
            state_counts={s: int(c) for s, c in enumerate(state_counts)},
        )

    log.info(
        "hmm.states_identified",
        state_returns_train={s: round(r, 6) for s, r in enumerate(state_scores)},
        state_counts={s: int(c) for s, c in enumerate(state_counts)},
        bull_state=bull_state,
        separation=round(separation, 6),
    )

    probs = hmm.predict_proba(X_hmm)[:, bull_state]
    y_true = build_regime_target(returns_train).to_numpy()
    best_thr = 0.5
    best_f1 = 0.0
    for thr in np.linspace(0.20, 0.80, 25):
        valid = ~np.isnan(y_true)
        if valid.sum() < 30:
            break
        preds = (probs[valid] > thr).astype(int)
        if preds.sum() == 0:
            continue
        score = f1_score(y_true[valid].astype(int), preds, zero_division=0)
        if score > best_f1:
            best_f1 = float(score)
            best_thr = float(thr)

    pct_bull = float((probs > best_thr).mean())
    log.info(
        "hmm.threshold_calibrated",
        threshold=round(best_thr, 3),
        f1_train=round(best_f1, 4),
        pct_bull=round(pct_bull, 3),
        prob_mean=round(float(probs.mean()), 4),
        prob_std=round(float(probs.std()), 4),
    )

    return HMMFitted(
        pca_pipeline=pca_fitted,
        hmm=hmm,
        bull_state=bull_state,
        threshold=best_thr,
        var_explained=pca_fitted.var_explained,
        n_pca_components=n_pcs_used,
        train_end_date=train_end_date,
        f1_train=best_f1,
        hmm_scaler_mean_=getattr(hmm_scaler, "mean_", None),
        hmm_scaler_scale_=getattr(hmm_scaler, "scale_", None),
    )


def predict_regime(X_seq: np.ndarray, fitted: HMMFitted) -> tuple[np.ndarray, np.ndarray]:
    X_pca = transform_robust_pca(X_seq, fitted.pca_pipeline)
    if fitted.hmm_scaler_mean_ is not None and fitted.hmm_scaler_scale_ is not None:
        mean = np.asarray(fitted.hmm_scaler_mean_, dtype=float)
        scale = np.asarray(fitted.hmm_scaler_scale_, dtype=float)
        scale = np.where(scale < 1e-8, 1.0, scale)
        X_pca = (X_pca - mean) / scale
    probs = fitted.hmm.predict_proba(X_pca)[:, fitted.bull_state]
    return probs, probs > fitted.threshold


def run_hmm_walk_forward(
    feature_df: pd.DataFrame,
    returns: pd.Series,
    min_train: int = MIN_OBS_TREINO,
    retrain_freq: int = 21,
    artifacts_dir: str = "/data/models/hmm",
    min_variance: float = MIN_VARIANCE_TARGET,
    n_pca_components: int | None = None,
) -> pd.DataFrame:
    artifact_root = Path(artifacts_dir)
    artifact_root.mkdir(parents=True, exist_ok=True)
    for old_artifact in artifact_root.glob("hmm_t*.pkl"):
        old_artifact.unlink(missing_ok=True)

    n = len(feature_df)
    prob_series = np.full(n, np.nan)
    bull_series = np.zeros(n, dtype=bool)

    fitted_hmm: HMMFitted | None = None
    last_train_idx = 0

    for t in range(min_train, n):
        should_retrain = fitted_hmm is None or (t - last_train_idx) >= retrain_freq
        if should_retrain:
            X_tr = feature_df.iloc[:t].dropna().values
            ret_tr = returns.iloc[:t].reindex(feature_df.iloc[:t].dropna().index).values
            if len(X_tr) < min_train:
                continue
            try:
                fitted_hmm = fit_hmm(
                    X_tr,
                    ret_tr,
                    feature_names=feature_df.columns.tolist(),
                    min_variance=min_variance,
                    train_end_date=str(feature_df.index[t]),
                )
                last_train_idx = t
                with open(artifact_root / f"hmm_t{t}.pkl", "wb") as f:
                    pickle.dump(fitted_hmm, f)
                log.info(
                    "hmm.retrained",
                    t=t,
                    date=str(feature_df.index[t]),
                    n_pcs=fitted_hmm.n_pca_components,
                    var_exp=round(fitted_hmm.var_explained, 3),
                    threshold=round(fitted_hmm.threshold, 3),
                )
            except Exception as e:
                log.error("hmm.train_error", t=t, error=str(e))
                continue

        if fitted_hmm is None:
            continue

        start_ctx = max(0, t - CONTEXT_WINDOW + 1)
        X_ctx = feature_df.iloc[start_ctx : t + 1].values
        if np.any(np.isnan(X_ctx)):
            continue
        try:
            probs, bulls = predict_regime(X_ctx, fitted_hmm)
            prob_series[t] = float(probs[-1])
            bull_series[t] = bool(bulls[-1])
        except Exception as e:
            log.warning("hmm.predict_error", t=t, error=str(e))

    result = pd.DataFrame({"hmm_prob_bull": prob_series, "hmm_is_bull": bull_series}, index=feature_df.index)
    log.info(
        "hmm.walk_forward_complete",
        n_bull=int(bull_series.sum()),
        n_bear=int((~bull_series).sum()),
        pct_bull=round(bull_series.mean(), 3),
        prob_std=round(float(pd.Series(prob_series).dropna().std()), 4) if np.isfinite(pd.Series(prob_series).dropna().std()) else None,
    )
    return result


def validate_hmm_diagnostics(hmm_result: pd.DataFrame, returns: pd.Series, min_f1: float = 0.45) -> dict:
    result: dict = {}

    mask_2022h1 = (hmm_result.index >= "2022-01-01") & (hmm_result.index <= "2022-06-30")
    if mask_2022h1.sum() > 0:
        bear_2022 = (~hmm_result.loc[mask_2022h1, "hmm_is_bull"]).mean()
        result["bear_2022_h1_pct"] = round(float(bear_2022), 3)
        result["bear_2022_ok"] = bear_2022 >= 0.60
    else:
        result["bear_2022_ok"] = None
        result["bear_2022_h1_pct"] = None

    valid_mask = ~np.isnan(hmm_result["hmm_prob_bull"])
    y_true = build_regime_target(returns.reindex(hmm_result.index))
    y_pred = hmm_result["hmm_is_bull"].fillna(False).astype(int)

    f1_mask = valid_mask & y_true.notna()
    if f1_mask.sum() > 30:
        oos_f1 = f1_score(y_true[f1_mask].astype(int), y_pred[f1_mask], zero_division=0)
        result["f1_oos"] = round(float(oos_f1), 4)
        result["f1_oos_ok"] = oos_f1 >= min_f1
    else:
        result["f1_oos_ok"] = None

    result["status"] = "PASS" if all(v for v in [result.get("bear_2022_ok"), result.get("f1_oos_ok")] if v is not None) else "FAIL"
    log.info("hmm.validation", **{k: v for k, v in result.items() if not isinstance(v, pd.DataFrame)})
    return result
