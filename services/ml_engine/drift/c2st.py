# =============================================================================
# DESTINO: services/ml_engine/drift/c2st.py
# C2ST — Classifier Two-Sample Test com Block Bootstrap (v10.10).
#
# POR QUE C2ST E NÃO KS-TEST (Parte 11):
#   KS-test: univariado. Detecta drift em uma feature por vez.
#     Problema: em crypto, drift é frequentemente uma rotação no espaço
#     conjunto — nenhuma feature individual muda, mas a estrutura conjunta muda.
#     Exemplo: funding+volatilidade mudaram de regime após merge Ethereum.
#   C2ST: treina um classificador para distinguir "janela de treino" vs
#     "janela recente". AUC > 0.55 significa: os dados são distinguíveis
#     → distribuição mudou → requalificação obrigatória.
#
# BLOCK BOOTSTRAP (Parte 11 — v10.10):
#   Dados de série temporal têm autocorrelação. Bootstrap simples (i.i.d.)
#   gera intervalos de confiança otimistas — p-value muito pequeno.
#   Block Bootstrap (blocos de 5 dias) preserva a estrutura de dependência.
#   p-value correto → menos falsos alarmes, mais alarmes verdadeiros.
#   Referência: Lopez de Prado (2020) + Lähnemann et al. (2021).
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_score
import structlog

log = structlog.get_logger(__name__)

# Limites do C2ST (Parte 11 — Checklist item 10)
AUC_DRIFT_THRESHOLD       = 0.55    # AUC > 0.55: drift detectado
AUC_SEVERE_THRESHOLD      = 0.65    # AUC > 0.65: requalificação urgente
BLOCK_SIZE_DEFAULT        = 5       # tamanho do bloco (dias)
N_BOOTSTRAP_DEFAULT       = 500     # iterações bootstrap
TRAIN_WINDOW_DAYS         = 252     # janela de referência (1 ano)
TEST_WINDOW_DAYS          = 30      # janela recente (1 mês)


@dataclass
class DriftResult:
    """Resultado do teste C2ST para um ativo."""
    symbol:         str
    auc_observed:   float
    pvalue_block:   float        # p-value pelo Block Bootstrap
    drift_detected: bool         # True se AUC > threshold E pvalue < 0.05
    severity:       str          # "NONE" / "MODERATE" / "SEVERE"
    train_window:   tuple        # (start, end) da janela de referência
    test_window:    tuple        # (start, end) da janela recente
    n_train:        int
    n_test:         int
    feature_drift:  dict[str, float]  # drift por feature (AUC univariado)


def _block_bootstrap_pvalue(
    X_ref:     np.ndarray,
    X_recent:  np.ndarray,
    auc_obs:   float,
    block_size: int = BLOCK_SIZE_DEFAULT,
    n_bootstrap: int = N_BOOTSTRAP_DEFAULT,
    random_state: int = 42,
) -> float:
    """
    p-value via Block Bootstrap para o teste C2ST.

    Hipótese nula H0: X_ref e X_recent vêm da mesma distribuição.
    Sob H0: se combinarmos e re-amostramos em blocos, AUC esperado ≈ 0.50.

    Processo:
        1. Combina X_ref e X_recent em X_combined
        2. Repete N vezes:
           a. Re-amostra X_combined em blocos de block_size (com reposição)
           b. Divide em duas metades como se fossem "ref" e "recent"
           c. Treina classificador, calcula AUC_bootstrap
        3. p-value = fração das AUC_bootstrap ≥ auc_obs

    Block Bootstrap com blocos de 5 dias:
        Preserva autocorrelação de até 5 dias (típico em crypto diário).
        Blocos maiores = mais conservador. Blocos menores = anti-conservador.

    Returns:
        float: p-value ∈ [0, 1]. Rejeitar H0 se p < 0.05.
    """
    rng      = np.random.RandomState(random_state)
    n_ref    = len(X_ref)
    n_rec    = len(X_recent)
    X_comb   = np.vstack([X_ref, X_recent])
    n_comb   = len(X_comb)

    auc_null_dist: list[float] = []

    # Índices de início de bloco disponíveis
    block_starts = np.arange(0, n_comb - block_size + 1)

    for _ in range(n_bootstrap):
        # Amostra blocos com reposição
        n_blocks_needed = int(np.ceil(n_comb / block_size))
        sampled_starts  = rng.choice(block_starts, size=n_blocks_needed,
                                     replace=True)
        idx_boot        = np.concatenate([
            np.arange(s, min(s + block_size, n_comb))
            for s in sampled_starts
        ])[:n_comb]

        X_boot = X_comb[idx_boot]

        # Divide: primeira metade = "referência", segunda = "recente"
        X_boot_ref = X_boot[:n_ref]
        X_boot_rec = X_boot[n_ref:]

        y_boot = np.array([0] * len(X_boot_ref) + [1] * len(X_boot_rec))
        X_boot_all = np.vstack([X_boot_ref, X_boot_rec])

        try:
            clf = RandomForestClassifier(
                n_estimators=100, max_depth=3,
                random_state=rng.randint(0, 99999), n_jobs=1,
            )
            scores = cross_val_score(
                clf, X_boot_all, y_boot, cv=3,
                scoring="roc_auc", error_score=0.5,
            )
            auc_null_dist.append(float(scores.mean()))
        except Exception:  # noqa: BLE001
            auc_null_dist.append(0.5)

    # p-value: fração da null distribution ≥ auc_obs
    pvalue = float(np.mean(np.array(auc_null_dist) >= auc_obs))
    return pvalue


def compute_feature_drift(
    X_ref:    np.ndarray,
    X_recent: np.ndarray,
    feature_names: list[str],
) -> dict[str, float]:
    """
    AUC univariado por feature — identifica QUAIS features driftaram.

    Útil para diagnóstico: após drift detectado, saber qual feature
    mudou de regime (ex: funding_rate colapsou após bear prolongado).

    Returns:
        {feature_name: auc_univariado} ordenado por AUC desc.
    """
    result: dict[str, float] = {}
    y      = np.array([0] * len(X_ref) + [1] * len(X_recent))

    for i, fname in enumerate(feature_names):
        x_ref_i = X_ref[:, i].reshape(-1, 1)
        x_rec_i = X_recent[:, i].reshape(-1, 1)
        X_i     = np.vstack([x_ref_i, x_rec_i])

        try:
            clf = RandomForestClassifier(
                n_estimators=50, max_depth=2,
                random_state=42, n_jobs=1,
            )
            scores = cross_val_score(
                clf, X_i, y, cv=3,
                scoring="roc_auc", error_score=0.5,
            )
            result[fname] = round(float(scores.mean()), 4)
        except Exception:  # noqa: BLE001
            result[fname] = 0.5

    return dict(sorted(result.items(), key=lambda x: -x[1]))


def run_c2st(
    feature_df:       pd.DataFrame,
    symbol:           str,
    train_window_days: int = TRAIN_WINDOW_DAYS,
    test_window_days:  int = TEST_WINDOW_DAYS,
    auc_threshold:     float = AUC_DRIFT_THRESHOLD,
    block_size:        int = BLOCK_SIZE_DEFAULT,
    n_bootstrap:       int = N_BOOTSTRAP_DEFAULT,
    artifacts_dir:     str = "/data/models/drift",
) -> DriftResult:
    """
    C2ST completo para um ativo.

    Janela de treino (referência): últimos train_window_days antes do test_window.
    Janela de teste (recente): últimos test_window_days do dataset.

    Classificador: RandomForest (n_estimators=300, max_depth=4).
        Simples o suficiente para não overfit com N~30 (janela recente).
        AUC calculado via cross-validation 5-fold na janela de treino+teste.

    Critérios de alarme (Parte 11, Checklist item 10):
        NONE:     AUC ≤ 0.55 ou pvalue ≥ 0.05
        MODERATE: AUC ∈ (0.55, 0.65) E pvalue < 0.05
        SEVERE:   AUC > 0.65 E pvalue < 0.05 → requalificação urgente

    Args:
        feature_df:        DataFrame de features com DatetimeIndex.
        symbol:            Símbolo do ativo (para logging e artefatos).
        train_window_days: Janela de referência. Default 252 (1 ano).
        test_window_days:  Janela recente. Default 30 (1 mês).
        auc_threshold:     AUC mínimo para alarme. Default 0.55.
        block_size:        Tamanho do bloco no Bootstrap. Default 5 dias.
        n_bootstrap:       Iterações bootstrap. Default 500.
        artifacts_dir:     Diretório para salvar resultados.

    Returns:
        DriftResult com veredicto, p-value e drift por feature.
    """
    Path(artifacts_dir).mkdir(parents=True, exist_ok=True)
    feature_df = feature_df.sort_index().ffill().bfill().fillna(0)
    n          = len(feature_df)

    if n < train_window_days + test_window_days:
        log.warning("c2st.insufficient_data",
                    symbol=symbol, n=n,
                    required=train_window_days + test_window_days)
        return DriftResult(
            symbol=symbol, auc_observed=0.5, pvalue_block=1.0,
            drift_detected=False, severity="NONE",
            train_window=(None, None), test_window=(None, None),
            n_train=0, n_test=0, feature_drift={},
        )

    # Divide janelas
    test_end   = feature_df.index[-1]
    test_start = feature_df.index[-test_window_days]
    ref_end    = feature_df.index[-(test_window_days + 1)]
    ref_start  = feature_df.index[-(train_window_days + test_window_days)]

    X_ref    = feature_df.loc[ref_start:ref_end].values
    X_recent = feature_df.loc[test_start:test_end].values
    fnames   = feature_df.columns.tolist()

    log.info("c2st.start",
             symbol=symbol, n_ref=len(X_ref), n_recent=len(X_recent),
             n_features=len(fnames))

    # ── C2ST: treina classificador ref(0) vs recent(1) ───────────────────
    y      = np.array([0] * len(X_ref) + [1] * len(X_recent))
    X_all  = np.vstack([X_ref, X_recent])

    clf = RandomForestClassifier(
        n_estimators=300, max_depth=4,
        class_weight="balanced",
        random_state=42, n_jobs=-1,
    )
    cv_scores  = cross_val_score(clf, X_all, y, cv=5,
                                 scoring="roc_auc", error_score=0.5)
    auc_obs    = float(cv_scores.mean())

    # ── Block Bootstrap p-value ───────────────────────────────────────────
    pvalue = _block_bootstrap_pvalue(
        X_ref, X_recent, auc_obs,
        block_size=block_size, n_bootstrap=n_bootstrap,
    )

    # ── Drift por feature (diagnóstico) ──────────────────────────────────
    feat_drift = compute_feature_drift(X_ref, X_recent, fnames)

    # ── Severity ──────────────────────────────────────────────────────────
    drift_detected = (auc_obs > auc_threshold) and (pvalue < 0.05)
    if not drift_detected:
        severity = "NONE"
    elif auc_obs < AUC_SEVERE_THRESHOLD:
        severity = "MODERATE"
    else:
        severity = "SEVERE"

    result = DriftResult(
        symbol=symbol,
        auc_observed=round(auc_obs, 4),
        pvalue_block=round(pvalue, 4),
        drift_detected=drift_detected,
        severity=severity,
        train_window=(str(ref_start), str(ref_end)),
        test_window=(str(test_start), str(test_end)),
        n_train=len(X_ref),
        n_test=len(X_recent),
        feature_drift=feat_drift,
    )

    # Persiste resultado
    import json
    out = Path(artifacts_dir) / f"c2st_{symbol}_{str(test_end)[:10]}.json"
    with open(out, "w") as f:
        json.dump({
            "symbol":        symbol,
            "auc_observed":  result.auc_observed,
            "pvalue_block":  result.pvalue_block,
            "drift_detected": result.drift_detected,
            "severity":      severity,
            "feature_drift": feat_drift,
        }, f, indent=2)

    log_fn = log.warning if drift_detected else log.info
    log_fn("c2st.result",
           symbol=symbol,
           auc=round(auc_obs, 4),
           pvalue=round(pvalue, 4),
           severity=severity,
           top_drifted=list(feat_drift.items())[:3])

    return result


def run_c2st_portfolio(
    feature_store:    dict[str, pd.DataFrame],
    min_severe_alert: int   = 3,
    artifacts_dir:    str   = "/data/models/drift",
    **kwargs,
) -> dict:
    """
    C2ST para todos os ativos do portfolio.

    Alerta de portfolio quando ≥ min_severe_alert ativos têm drift SEVERE.
    Sinal de regime shift sistêmico — interrompe novos sinais em TODOS os ativos.

    Args:
        feature_store:    {símbolo: DataFrame de features}.
        min_severe_alert: Mínimo de ativos em SEVERE para alerta global.
        **kwargs:         Argumentos repassados para run_c2st por ativo.

    Returns:
        dict com resultados por ativo + status global do portfolio.
    """
    results: dict[str, DriftResult] = {}

    for symbol, feat_df in feature_store.items():
        try:
            r = run_c2st(feat_df, symbol,
                         artifacts_dir=artifacts_dir, **kwargs)
            results[symbol] = r
        except Exception as e:  # noqa: BLE001
            log.error("c2st.portfolio_error", symbol=symbol, error=str(e))

    n_severe   = sum(1 for r in results.values() if r.severity == "SEVERE")
    n_moderate = sum(1 for r in results.values() if r.severity == "MODERATE")
    n_none     = sum(1 for r in results.values() if r.severity == "NONE")

    global_alert = n_severe >= min_severe_alert

    summary = {
        "n_assets":    len(results),
        "n_severe":    n_severe,
        "n_moderate":  n_moderate,
        "n_none":      n_none,
        "global_alert": global_alert,
        "action": (
            "HALT_ALL_SIGNALS — drift sistêmico detectado. "
            "Requalificação obrigatória de todos os modelos."
            if global_alert else
            "OK — operar normalmente. Monitorar ativos MODERATE."
        ),
        "by_symbol": {
            sym: {
                "auc":      r.auc_observed,
                "pvalue":   r.pvalue_block,
                "severity": r.severity,
                "top_drift": list(r.feature_drift.items())[:2],
            }
            for sym, r in results.items()
        },
    }

    log_fn = log.warning if global_alert else log.info
    log_fn("c2st.portfolio_summary",
           n_severe=n_severe, n_moderate=n_moderate,
           global_alert=global_alert, action=summary["action"])

    return summary
