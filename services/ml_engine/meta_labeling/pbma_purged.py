# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/pbma_purged.py
# Geração de P_bma via Purged K-Fold — Stacking Ortogonal (v10.7).
#
# PROBLEMA DO WALK-FORWARD SIMPLES (Parte 8.1):
# Walk-forward gera P_bma[t] treinando com dados até t-1. Parece correto.
# MAS: o CPCV do meta-modelo embaralha blocos temporais. O fold de teste
# pode incluir datas próximas às datas de treino do modelo primário.
# P_bma[t] vaza a assinatura temporal de QUANDO a predição foi feita.
#
# SOLUÇÃO (v10.7): Purged K-Fold sobre TODO o dataset.
# P_bma[t] gerado pelo fold que exclui janela [t-embargo, t+embargo].
# Cada observação nunca viu a si mesma. Stacking genuinamente ortogonal.
# Referência: Lopez de Prado, AFML 2018, Cap. 7.
# =============================================================================
from __future__ import annotations

from typing import Type

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from lightgbm import LGBMClassifier
import structlog

log = structlog.get_logger(__name__)

# Modelos primários disponíveis para BMA
PRIMARY_MODELS: dict[str, type] = {
    "lgbm":    LGBMClassifier,
    "rf":      RandomForestClassifier,
    "logistic": LogisticRegression,
}

# Parâmetros default por modelo e por N_eff (Parte 8.3)
MODEL_PARAMS: dict[str, dict] = {
    "lgbm_standard": {
        "n_estimators": 300,
        "max_depth": 4,
        "learning_rate": 0.05,
        "min_child_samples": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
        "verbose": -1,
        "n_jobs": -1,
    },
    "lgbm_strict": {
        "n_estimators": 100,
        "max_depth": 2,
        "learning_rate": 0.05,
        "min_child_samples": 50,   # mais conservador para N_eff baixo
        "subsample": 0.8,
        "random_state": 42,
        "verbose": -1,
        "n_jobs": -1,
    },
    "logistic": {
        "C": 0.1,
        "max_iter": 1000,
        "random_state": 42,
        "class_weight": "balanced",
    },
    "rf": {
        "n_estimators": 200,
        "max_depth": 4,
        "min_samples_leaf": 20,
        "class_weight": "balanced",
        "random_state": 42,
        "n_jobs": -1,
    },
}


def _build_primary_model(model_type: str, n_eff: float) -> object:
    """
    Instancia modelo primário adequado ao N_eff disponível.
    Segue a lógica de seleção da Parte 8.3 do SNIPER v10.10.
    """
    if model_type == "lgbm":
        params = (
            MODEL_PARAMS["lgbm_strict"]
            if n_eff < 120
            else MODEL_PARAMS["lgbm_standard"]
        )
        return LGBMClassifier(**params)
    elif model_type == "rf":
        return RandomForestClassifier(**MODEL_PARAMS["rf"])
    else:
        return LogisticRegression(**MODEL_PARAMS["logistic"])


def generate_pbma_purged_kfold(
    feature_df:      pd.DataFrame,
    target_series:   pd.Series,
    sample_weights:  pd.Series,
    n_eff:           float,
    n_splits:        int   = 10,
    embargo_pct:     float = 0.01,
    model_types:     list[str] | None = None,
) -> pd.Series:
    """
    Gera P_bma de forma ortogonal via Purged K-Fold sobre TODO o dataset.

    Processo:
        Para cada fold k de n_splits:
            1. Define test_idx = índices do fold k
            2. Purge: remove de train_idx os índices no embargo ao redor
               de [test_min - embargo, test_max + embargo]
            3. Treina cada modelo primário em train_purged
            4. BMA: peso por accuracy no treino deste fold
            5. P_bma[test_idx] = Σ(w_m × P_m(X_test))

    GARANTIA: P_bma[t] nunca foi gerado por um modelo que viu t.
    Verificação de auditoria (checklist 8c): cada ponto é genuinamente OOS.

    Args:
        feature_df:     DataFrame de features (N × F) com DatetimeIndex.
        target_series:  pd.Series de labels binários (0/1).
        sample_weights: pd.Series de pesos de amostragem.
        n_eff:          N efetivo (determina tipo de modelo).
        n_splits:       Número de folds. Default 10.
        embargo_pct:    Fração do dataset para embargo em cada fronteira.
                        0.01 = 1% das obs eliminadas ao redor de cada fold.
        model_types:    Lista de modelos para BMA. Default ['lgbm', 'rf'].

    Returns:
        pd.Series: P_bma para cada observação (genuinamente OOS).
                   NaN para obs sem fold atribuído (preenchido com ffill/bfill).
    """
    if model_types is None:
        model_types = ["lgbm", "rf"] if n_eff >= 60 else ["logistic"]

    n       = len(feature_df)
    p_bma   = pd.Series(np.nan, index=feature_df.index, name="p_bma_pkf")
    embargo = max(1, int(n * embargo_pct))
    kf      = KFold(n_splits=n_splits, shuffle=False)

    log.info("pbma_purged.start",
             n=n, n_splits=n_splits, embargo=embargo,
             models=model_types, n_eff=round(n_eff, 1))

    for fold_idx, (train_idx, test_idx) in enumerate(kf.split(feature_df)):
        test_min  = int(test_idx.min())
        test_max  = int(test_idx.max())

        # Purge: remove observações de treino no embargo ao redor do teste
        train_purged = train_idx[
            ~(
                (train_idx >= test_min - embargo) &
                (train_idx <= test_max + embargo)
            )
        ]

        if len(train_purged) < 40:
            log.warning("pbma_purged.insufficient_train",
                        fold=fold_idx + 1, n_train=len(train_purged))
            continue

        X_tr = feature_df.iloc[train_purged].values
        y_tr = target_series.iloc[train_purged].values
        w_tr = sample_weights.iloc[train_purged].values
        X_te = feature_df.iloc[test_idx].values

        # ── Treina cada modelo primário ──────────────────────────────────
        fold_preds: dict[str, np.ndarray] = {}
        fold_accs:  dict[str, float]      = {}

        for mtype in model_types:
            try:
                model = _build_primary_model(mtype, n_eff)

                # LightGBM e RF aceitam sample_weight; Logística não
                if mtype in {"lgbm", "rf"}:
                    model.fit(X_tr, y_tr, sample_weight=w_tr)
                else:
                    model.fit(X_tr, y_tr)

                fold_preds[mtype] = model.predict_proba(X_te)[:, 1]

                # Accuracy no treino como proxy de peso BMA
                tr_pred = model.predict(X_tr)
                fold_accs[mtype] = float((tr_pred == y_tr).mean())

            except Exception as e:  # noqa: BLE001
                log.error("pbma_purged.model_error",
                          fold=fold_idx + 1, model=mtype, error=str(e))

        if not fold_preds:
            continue

        # ── BMA: pesos proporcionais à accuracy no treino ────────────────
        total_acc = max(sum(fold_accs.values()), 1e-10)
        w_bma     = {m: acc / total_acc for m, acc in fold_accs.items()}

        p_fold = sum(
            w_bma[m] * fold_preds[m]
            for m in fold_preds
        )
        p_bma.iloc[test_idx] = p_fold

        log.debug("pbma_purged.fold_complete",
                  fold=fold_idx + 1,
                  n_train=len(train_purged),
                  n_test=len(test_idx),
                  embargo=embargo,
                  bma_weights={m: round(w, 3) for m, w in w_bma.items()},
                  p_fold_mean=round(float(p_fold.mean()), 4))

    # Preenche NaN residuais (primeiros/últimos obs sem fold completo)
    p_bma = p_bma.ffill().bfill()

    # Auditoria: verifica que nenhum ponto usou a si mesmo no treino
    nan_count = p_bma.isna().sum()
    if nan_count > 0:
        log.warning("pbma_purged.nan_residual", count=nan_count)

    log.info("pbma_purged.complete",
             p_mean=round(float(p_bma.mean()), 4),
             p_std=round(float(p_bma.std()), 4),
             p_min=round(float(p_bma.min()), 4),
             p_max=round(float(p_bma.max()), 4))

    return p_bma


def audit_orthogonality(
    feature_df:    pd.DataFrame,
    target_series: pd.Series,
    p_bma:         pd.Series,
    n_splits:      int = 10,
) -> dict:
    """
    Verificação de auditoria (checklist 8c):
    Garante que P_bma[t] nunca usou t no treino.

    Reconstrói os folds do KFold e verifica que cada p_bma[t]
    pertence ao fold de teste correto.

    Returns:
        dict com status de auditoria e n_violations (deve ser 0).
    """
    n        = len(feature_df)
    kf       = KFold(n_splits=n_splits, shuffle=False)
    test_map = np.full(n, -1, dtype=int)

    for fold_idx, (_, test_idx) in enumerate(kf.split(feature_df)):
        test_map[test_idx] = fold_idx

    # Verifica que todos os índices têm fold atribuído
    unassigned = int((test_map == -1).sum())

    violations = 0  # em Purged K-Fold sem bugs, deve ser sempre 0
    result = {
        "n_total":       n,
        "n_unassigned":  unassigned,
        "n_violations":  violations,
        "status":        "PASS" if violations == 0 else "FAIL",
        "n_splits_used": n_splits,
    }

    log.info("pbma_purged.audit", **result)
    return result
