# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/cpcv.py
# Combinatorial Purged Cross-Validation (CPCV) N=6, k=2 (15 trajetórias).
# Meta-modelo treinado com P_bma ortogonal (v10.7) como feature.
#
# CPCV vs K-Fold simples:
#   K-Fold: 1 sequência de teste. PBO calculado sobre 1 trajetória.
#   CPCV N=6, k=2: C(6,2)=15 combinações de folds de teste.
#   Gera 15 trajetórias de equity independentes → PBO estatisticamente robusto.
#
# PBO (Probability of Backtest Overfitting):
#   Fração das 15 trajetórias onde o modelo OOS perde para o modelo nulo.
#   PBO < 10% = critério de aprovação (Checklist item 9).
# Referência: Lopez de Prado & Bailey (2014), SSRN 2308659.
# =============================================================================
from __future__ import annotations

from itertools import combinations
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from lightgbm import LGBMClassifier
import structlog

from .uniqueness import compute_effective_n, compute_meta_sample_weights
from .pbma_purged import MODEL_PARAMS

log = structlog.get_logger(__name__)

META_FEATURES_V107 = [
    "p_bma_pkf",             # P_bma ortogonal via Purged K-Fold (NUNCA walk-forward)
    "score_ia_bull",         # score IA bullish (se IAs aprovadas — Fase 3)
    "score_ia_bear",         # score IA bearish
    "ias_net_score",         # score líquido das IAs
    "hmm_prob_bull",         # P(regime bull) via PCA+HMM
    "sigma_ewma",            # volatilidade EWMA no sinal
    "funding_rate_ma7d",     # funding rate atual
    "basis_3m",              # basis do mercado futuro
    "stablecoin_chg30",      # macro regime
    "unlock_pressure_rank",  # pressão de unlock on-chain
]


def _train_meta_model(
    X_train:  np.ndarray,
    y_train:  np.ndarray,
    weights:  np.ndarray,
    n_eff:    float,
) -> object:
    """
    Treina meta-modelo adequado ao N_eff disponível (Parte 8.3).
    Modelo selecionado ANTES de ver dados de validação do fold.
    """
    if n_eff < 60:
        model = LogisticRegression(**MODEL_PARAMS["logistic"])
        model.fit(X_train, y_train)          # Logística não aceita sample_weight aqui
    elif n_eff < 120:
        model = LGBMClassifier(
            **MODEL_PARAMS["lgbm_strict"],
        )
        model.fit(X_train, y_train, sample_weight=weights)
    else:
        model = LGBMClassifier(
            **MODEL_PARAMS["lgbm_standard"],
        )
        model.fit(X_train, y_train, sample_weight=weights)

    return model


def run_cpcv(
    meta_df:         pd.DataFrame,
    feature_cols:    list[str] | None = None,
    target_col:      str   = "y_meta",
    n_splits:        int   = 6,
    n_test_splits:   int   = 2,
    embargo_pct:     float = 0.01,
    sl_penalty:      float = 2.0,
    halflife_days:   int   = 180,
) -> dict:
    """
    CPCV completo para o meta-modelo SNIPER v10.10.

    Gera C(n_splits, n_test_splits) = C(6,2) = 15 trajetórias OOS.
    Cada trajetória usa uma combinação diferente de folds como teste.

    REGRA: meta_df DEVE conter 'p_bma_pkf' (Purged K-Fold).
    NUNCA usar 'p_bma_wf' (walk-forward) — vazamento de segunda ordem.

    Args:
        meta_df:       DataFrame com features + target + barrier info.
                       DEVE ter coluna 'p_bma_pkf' e 't_touch'.
        feature_cols:  Features do meta-modelo. Default META_FEATURES_V107.
        target_col:    Nome da coluna target. Default 'y_meta'.
        n_splits:      Número de folds. Default 6.
        n_test_splits: Folds usados como teste em cada combinação. Default 2.
        embargo_pct:   Fração de embargo entre treino e teste.
        sl_penalty:    Penalidade de SL nos sample_weights (v10.6).
        halflife_days: Halflife do time-decay nos sample_weights (v10.10).

    Returns:
        dict com:
            results:        lista de dicts por trajetória (auc, sharpe_oos, etc.)
            auc_mean:       AUC médio das 15 trajetórias
            auc_std:        desvio padrão do AUC
            pbo:            Probability of Backtest Overfitting
            status:         PASS (PBO < 10%) / FAIL
            n_trajectories: número de trajetórias geradas
    """
    feat_cols = feature_cols or META_FEATURES_V107

    # Valida colunas obrigatórias
    missing = [c for c in feat_cols + [target_col] if c not in meta_df.columns]
    if missing:
        log.warning("cpcv.missing_columns", missing=missing,
                    available=meta_df.columns.tolist())
        feat_cols = [c for c in feat_cols if c in meta_df.columns]

    if "p_bma_pkf" not in meta_df.columns:
        raise ValueError(
            "meta_df deve conter 'p_bma_pkf' (Purged K-Fold). "
            "Não usar 'p_bma_wf' — viola ortogonalidade do stacking."
        )

    meta_df = meta_df.sort_values("date" if "date" in meta_df.columns
                                  else meta_df.index.name or "index"
                                  ).reset_index(drop=True)
    n       = len(meta_df)
    embargo = max(1, int(n * embargo_pct))

    # Divide em n_splits blocos contíguos
    splits  = np.array_split(np.arange(n), n_splits)
    results: list[dict] = []

    log.info("cpcv.start",
             n=n, n_splits=n_splits, n_test_splits=n_test_splits,
             n_trajectories=len(list(combinations(range(n_splits), n_test_splits))),
             feature_cols=feat_cols)

    for combo in combinations(range(n_splits), n_test_splits):
        # Índices de teste: união dos folds selecionados
        test_idx  = np.concatenate([splits[i] for i in combo])
        train_idx = np.array([j for j in range(n) if j not in set(test_idx)])

        # Embargo ao redor das fronteiras de teste
        train_idx = train_idx[
            ~(
                (train_idx >= test_idx.min() - embargo) &
                (train_idx <= test_idx.max() + embargo)
            )
        ]

        if len(train_idx) < 40 or len(test_idx) < 15:
            log.debug("cpcv.fold_skip",
                      combo=combo, n_train=len(train_idx), n_test=len(test_idx))
            continue

        train_df = meta_df.iloc[train_idx]
        test_df  = meta_df.iloc[test_idx]

        # N_eff dinâmico (v10.6) — calculado ANTES de treinar
        if "t_touch" in train_df.columns:
            n_eff, uniqueness, _ = compute_effective_n(train_df)
            sw = compute_meta_sample_weights(
                train_df, uniqueness,
                halflife_days=halflife_days,
                sl_penalty=sl_penalty,
            )
        else:
            n_eff    = float(len(train_df))
            uniqueness = pd.Series(1.0, index=train_df.index)
            sw       = pd.Series(1.0, index=train_df.index)

        X_tr = train_df[feat_cols].fillna(0).values
        y_tr = train_df[target_col].values
        w_tr = sw.values

        X_te = test_df[feat_cols].fillna(0).values
        y_te = test_df[target_col].values

        try:
            model   = _train_meta_model(X_tr, y_tr, w_tr, n_eff)
            p_oos   = model.predict_proba(X_te)[:, 1]
            auc_oos = roc_auc_score(y_te, p_oos)

            # Sharpe OOS simples (retorno médio / desvio da predição)
            ret_oos   = test_df.get("pnl_real",
                                    pd.Series(0.0, index=test_df.index)).values
            long_mask = p_oos > 0.5
            if long_mask.sum() > 0:
                oos_returns = ret_oos[long_mask]
                sharpe_oos  = (oos_returns.mean() /
                               (oos_returns.std() + 1e-10) * np.sqrt(252))
            else:
                sharpe_oos = 0.0

            results.append({
                "combo":      combo,
                "n_train":    len(train_idx),
                "n_test":     len(test_idx),
                "n_eff":      round(n_eff, 1),
                "auc_oos":    round(auc_oos, 4),
                "sharpe_oos": round(sharpe_oos, 4),
                "beats_null": auc_oos > 0.50,
            })

            log.debug("cpcv.trajectory",
                      combo=combo,
                      auc_oos=round(auc_oos, 4),
                      n_eff=round(n_eff, 1))

        except Exception as e:  # noqa: BLE001
            log.error("cpcv.trajectory_error", combo=combo, error=str(e))

    if not results:
        log.error("cpcv.no_results")
        return {"status": "FAIL", "n_trajectories": 0}

    df_res = pd.DataFrame(results)

    auc_mean = float(df_res["auc_oos"].mean())
    auc_std  = float(df_res["auc_oos"].std())
    pbo      = float((df_res["auc_oos"] < 0.50).mean())
    status   = "PASS" if pbo < 0.10 else "FAIL"

    log.info("cpcv.complete",
             n_trajectories=len(results),
             auc_mean=round(auc_mean, 4),
             auc_std=round(auc_std, 4),
             pbo=round(pbo, 4),
             status=status,
             msg="PBO < 10% = PASS (checklist item 9)")

    if status == "FAIL":
        log.warning("cpcv.pbo_fail",
                    pbo=round(pbo, 3),
                    msg="PBO ≥ 10%: usar P_bma > 0.65 direto (Parte 15).")

    return {
        "results":        df_res.to_dict("records"),
        "auc_mean":       round(auc_mean, 4),
        "auc_std":        round(auc_std, 4),
        "pbo":            round(pbo, 4),
        "status":         status,
        "n_trajectories": len(results),
        "auc_by_combo":   df_res[["combo", "auc_oos"]].values.tolist(),
    }
