# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/cpcv.py
# CPCV N=6, k=2 (15 trajetórias). Meta-modelo com VI feature selection.
#
# v10.10.5 MUDANÇAS:
#   1. Importa select_features_by_vi de pbma_purged. Aplica ao meta-df.
#   2. hmm_prob_bull REMOVIDO da lista de features do meta-modelo.
#      HMM é hard gate no primário — redundante aqui.
#   3. META_MODEL_PARAMS lobotomizados mantidos (depth=2, leaf=5%).
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
from features.onchain import UNLOCK_MODEL_FEATURE_COLUMNS

from .uniqueness import compute_effective_n, compute_meta_sample_weights
from .pbma_purged import (
    MODEL_PARAMS,
    load_vi_cluster_map,
    select_features_by_vi,
)

log = structlog.get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# META-MODEL PARAMS (v10.10.5) — Lobotomizados, separados do primário.
# ═══════════════════════════════════════════════════════════════════════════
META_MODEL_PARAMS: dict[str, dict] = {
    "lgbm_meta": {
        "n_estimators":     100,
        "max_depth":        2,
        "learning_rate":    0.05,
        "min_child_samples": 500,
        "subsample":        0.8,
        "colsample_bytree": 0.6,
        "is_unbalance":     True,
        "random_state":     42,
        "verbose":          -1,
        "n_jobs":           -1,
    },
    "lgbm_meta_strict": {
        "n_estimators":     50,
        "max_depth":        2,
        "learning_rate":    0.05,
        "min_child_samples": 500,
        "subsample":        0.7,
        "is_unbalance":     True,
        "random_state":     42,
        "verbose":          -1,
        "n_jobs":           -1,
    },
}

# Features do meta-modelo ANTES da filtragem VI.
# hmm_prob_bull REMOVIDO — já é hard gate no primário (v10.10.5).
META_FEATURES_V1105 = [
    "p_bma_pkf",             # P_bma ortogonal (OBRIGATÓRIA — nunca descartada)
    "score_ia_bull",         # score IA bullish
    "score_ia_bear",         # score IA bearish
    "ias_net_score",         # score líquido das IAs
    # "hmm_prob_bull",       # REMOVIDO v10.10.5 → hard gate, não feature
    "sigma_ewma",            # volatilidade EWMA no sinal
    "funding_rate_ma7d",     # funding rate atual
    "basis_3m",              # basis do mercado futuro
    "stablecoin_chg30",      # macro regime
    *UNLOCK_MODEL_FEATURE_COLUMNS,  # pressão de unlock on-chain em colunas ortogonais
]

# Compatibilidade retroativa: vários pontos do pipeline ainda importam
# META_FEATURES_V107. Mantemos alias para não quebrar integração.
META_FEATURES_V107 = META_FEATURES_V1105


def _select_meta_features(
    meta_df:     pd.DataFrame,
    feat_cols:   list[str],
    target_col:  str,
) -> list[str]:
    """
    Filtra features do meta-modelo via cluster_map da Fase 2.
    Mantém p_bma_pkf sempre presente e escolhe 1 feature por cluster usando
    maior correlação absoluta com o target.
    """
    cmap = load_vi_cluster_map()
    available = [f for f in feat_cols if f in meta_df.columns]
    if not available:
        return []

    selected = []
    if "p_bma_pkf" in available:
        selected.append("p_bma_pkf")

    if cmap is None:
        return list(dict.fromkeys(selected + [f for f in available if f != "p_bma_pkf"]))

    y = pd.to_numeric(meta_df[target_col], errors="coerce")
    used = set(selected)
    for _, feats in sorted(cmap.items(), key=lambda kv: str(kv[0])):
        feats_av = [f for f in feats if f in available and f not in used]
        if not feats_av:
            continue
        best, best_corr = None, -np.inf
        for feat in feats_av:
            x = pd.to_numeric(meta_df[feat], errors="coerce")
            mask = x.notna() & y.notna()
            if mask.sum() < 30 or float(x[mask].std()) < 1e-8:
                continue
            try:
                corr = float(abs(x[mask].corr(y[mask], method="spearman")))
            except Exception:
                corr = 0.0
            if np.isnan(corr):
                corr = 0.0
            if corr > best_corr:
                best, best_corr = feat, corr
        if best is not None:
            selected.append(best)
            used.add(best)

    if len(selected) >= 2:
        log.info("cpcv.vi_meta", n_before=len(available), n_after=len(selected), features=selected)
        return selected
    return list(dict.fromkeys(selected + [f for f in available if f != "p_bma_pkf"]))


def _train_meta_model(
    X_train:  np.ndarray,
    y_train:  np.ndarray,
    weights:  np.ndarray,
    n_eff:    float,
) -> object:
    """Meta-modelo lobotomizado (v10.10.5). depth=2, leaf=5%."""
    n_train = len(y_train)

    if n_eff < 60:
        model = LogisticRegression(**MODEL_PARAMS["logistic"])
        model.fit(X_train, y_train)
    elif n_eff < 120:
        params = META_MODEL_PARAMS["lgbm_meta_strict"].copy()
        params["min_child_samples"] = max(50, int(n_train * 0.05))
        model = LGBMClassifier(**params)
        model.fit(X_train, y_train, sample_weight=weights)
    else:
        params = META_MODEL_PARAMS["lgbm_meta"].copy()
        params["min_child_samples"] = max(50, int(n_train * 0.05))
        model = LGBMClassifier(**params)
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
    CPCV com VI feature selection para meta-modelo (v10.10.5).

    C(6,2) = 15 trajetórias OOS. Features filtradas por VI.
    hmm_prob_bull removido das features (hard gate no primário).
    """
    raw_feat_cols = feature_cols or META_FEATURES_V1105

    # ── VI Feature Selection para meta-modelo ────────────────────────────
    feat_cols = _select_meta_features(meta_df, raw_feat_cols, target_col)

    if "p_bma_pkf" not in meta_df.columns:
        raise ValueError("meta_df deve conter 'p_bma_pkf' (Purged K-Fold).")

    # Garante hmm_prob_bull NÃO está nas features
    feat_cols = [f for f in feat_cols if f != "hmm_prob_bull"]

    meta_df = meta_df.sort_values(
        "date" if "date" in meta_df.columns
        else meta_df.index.name or "index"
    ).reset_index(drop=True)
    n       = len(meta_df)
    embargo = max(1, int(n * embargo_pct))

    splits  = np.array_split(np.arange(n), n_splits)
    results: list[dict] = []

    log.info("cpcv.start", n=n, n_splits=n_splits,
             n_combos=len(list(combinations(range(n_splits), n_test_splits))),
             feature_cols=feat_cols)

    for combo in combinations(range(n_splits), n_test_splits):
        test_idx  = np.concatenate([splits[i] for i in combo])
        train_idx = np.array([j for j in range(n) if j not in set(test_idx)])
        train_idx = train_idx[
            ~((train_idx >= test_idx.min() - embargo) &
              (train_idx <= test_idx.max() + embargo))
        ]

        if len(train_idx) < 40 or len(test_idx) < 15:
            continue

        train_df = meta_df.iloc[train_idx]
        test_df  = meta_df.iloc[test_idx]

        if "t_touch" in train_df.columns:
            n_eff, uniqueness, _ = compute_effective_n(train_df)
            sw = compute_meta_sample_weights(
                train_df, uniqueness,
                halflife_days=halflife_days, sl_penalty=sl_penalty)
        else:
            n_eff = float(len(train_df))
            sw = pd.Series(1.0, index=train_df.index)

        X_tr = train_df[feat_cols].fillna(0).values
        y_tr = train_df[target_col].values
        w_tr = sw.values

        X_te = test_df[feat_cols].fillna(0).values
        y_te = test_df[target_col].values

        try:
            model   = _train_meta_model(X_tr, y_tr, w_tr, n_eff)
            p_oos   = model.predict_proba(X_te)[:, 1]
            if "hmm_prob_bull" in test_df.columns:
                hmm_te = test_df["hmm_prob_bull"].fillna(0.0).values
                p_oos = np.where(hmm_te < 0.50, 0.0, p_oos)
            auc_oos = roc_auc_score(y_te, p_oos)

            ret_oos = test_df.get(
                "pnl_real", pd.Series(0.0, index=test_df.index)).values
            long_mask = p_oos > 0.5
            if long_mask.sum() > 0:
                oos_ret = ret_oos[long_mask]
                sharpe_oos = (oos_ret.mean() /
                              (oos_ret.std() + 1e-10) * np.sqrt(252))
            else:
                sharpe_oos = 0.0

            results.append({
                "combo": combo, "n_train": len(train_idx),
                "n_test": len(test_idx), "n_eff": round(n_eff, 1),
                "auc_oos": round(auc_oos, 4),
                "sharpe_oos": round(sharpe_oos, 4),
                "beats_null": auc_oos > 0.50,
            })
        except Exception as e:
            log.error("cpcv.err", combo=combo, error=str(e))

    if not results:
        return {"status": "FAIL", "n_trajectories": 0}

    df_res   = pd.DataFrame(results)
    auc_mean = float(df_res["auc_oos"].mean())
    auc_std  = float(df_res["auc_oos"].std())
    pbo      = float((df_res["auc_oos"] < 0.50).mean())
    status   = "PASS" if pbo < 0.10 else "FAIL"

    log.info("cpcv.done", n_traj=len(results),
             auc_mean=round(auc_mean, 4), auc_std=round(auc_std, 4),
             pbo=round(pbo, 4), status=status, features=feat_cols)

    if status == "FAIL":
        log.warning("cpcv.pbo_fail", pbo=round(pbo, 3))

    return {
        "results":        df_res.to_dict("records"),
        "auc_mean":       round(auc_mean, 4),
        "auc_std":        round(auc_std, 4),
        "pbo":            round(pbo, 4),
        "status":         status,
        "n_trajectories": len(results),
        "auc_by_combo":   df_res[["combo", "auc_oos"]].values.tolist(),
        "features_used":  feat_cols,
    }
