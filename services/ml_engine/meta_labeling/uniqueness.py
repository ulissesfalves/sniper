# =============================================================================
# DESTINO: services/ml_engine/meta_labeling/uniqueness.py
# Label Uniqueness dinâmica via t_touch real (v10.6).
# Concorrência real entre labels: dois trades se sobrepõem se seus
# horizontes temporais se cruzam. Unicidade = 1 / concorrência.
#
# POR QUE ISSO IMPORTA:
# Eventos sobrepostos no tempo não são independentes — o modelo que treina
# em dois trades sobrepostos está efetivamente vendo a mesma informação de
# mercado duas vezes. Labels com unicidade baixa recebem peso menor no treino.
# O N_eff (efetivo) é ≪ N_raw — saber o N_eff correto determina qual modelo
# usar (Logística vs LGBM vs LGBM Strict) — Parte 8.
# Referência: Lopez de Prado, AFML 2018, Cap. 4.
# =============================================================================
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Thresholds de N_eff para seleção de modelo (Parte 8.3)
N_EFF_LOGISTIC_ONLY  = 60    # < 60: apenas Logística
N_EFF_LGBM_STRICT    = 120   # 60-120: LGBM com parâmetros conservadores
# > 120: LGBM padrão


def compute_label_uniqueness(
    barrier_df: pd.DataFrame,
) -> pd.Series:
    """
    Unicidade dinâmica usando duração real do trade (t_touch).

    Para cada label i, conta quantos outros labels j têm janelas
    temporais sobrepostas [t0_j, t_touch_j]:
        overlap(i,j) = (t0_i <= t_touch_j) AND (t_touch_i >= t0_j)
        concurrency_i = Σ_j overlap(i,j)
        uniqueness_i  = 1 / concurrency_i

    v10.6 vs estático:
        Estático (errado): usa window=5 fixo para toda observação.
        Dinâmico (v10.6): usa t_touch real → trades curtos têm maior
        unicidade do que trades longos (correto: menor sobreposição).

    Args:
        barrier_df: DataFrame retornado por apply_triple_barrier.
                    DEVE ter coluna 't_touch'. Index = event_date.

    Returns:
        pd.Series de unicidade ∈ (0, 1]. Index = event_date.
    """
    if "t_touch" not in barrier_df.columns:
        raise ValueError("barrier_df precisa da coluna 't_touch'. "
                         "Usar apply_triple_barrier() primeiro.")

    t0     = pd.to_datetime(barrier_df.index).values.astype(np.int64)
    t1     = pd.to_datetime(barrier_df["t_touch"]).values.astype(np.int64)
    n      = len(t0)

    # Matriz de sobreposição (N × N) — evitar loop para N > 500
    if n <= 2000:
        # Vetorizado: broadcasting O(N²) em memória — OK até ~2k eventos
        overlap     = (t0[None, :] <= t1[:, None]) & (t1[None, :] >= t0[:, None])
        concurrency = overlap.sum(axis=1).astype(float)
    else:
        # Loop para N grande: O(N²) mas sem alocação de matriz NxN
        log.warning("uniqueness.large_n", n=n,
                    msg="N > 2000: usando loop — pode ser lento.")
        concurrency = np.ones(n)
        for i in range(n):
            concurrency[i] = float(np.sum(
                (t0 <= t1[i]) & (t1 >= t0[i])
            ))

    uniqueness = 1.0 / np.maximum(concurrency, 1.0)

    result = pd.Series(uniqueness, index=barrier_df.index, name="uniqueness")

    log.info("uniqueness.computed",
             n=n,
             mean_uniqueness=round(float(result.mean()), 4),
             min_uniqueness=round(float(result.min()), 4),
             max_concurrency=int(1.0 / result.min()) if result.min() > 0 else n)
    return result


def compute_effective_n(
    barrier_df: pd.DataFrame,
    uniqueness:  pd.Series | None = None,
) -> tuple[float, pd.Series, str]:
    """
    Calcula N_eff e determina o modelo a usar (Parte 8.3).

    N_eff = Σ(uniqueness_i) — soma das unicidades.

    Modelo por N_eff:
        < 60:  LOGISTIC_ONLY  — RF/LGBM overfita com tão poucos eventos
        60-120: LGBM_STRICT   — LGBM com max_depth=2, min_child_samples=50
        > 120:  LGBM_STANDARD — LGBM padrão do SNIPER

    Returns:
        (N_eff, uniqueness_series, model_type_string)
    """
    if uniqueness is None:
        uniqueness = compute_label_uniqueness(barrier_df)

    n_eff = float(uniqueness.sum())

    if n_eff < N_EFF_LOGISTIC_ONLY:
        model_type = "LOGISTIC_ONLY"
    elif n_eff < N_EFF_LGBM_STRICT:
        model_type = "LGBM_STRICT"
    else:
        model_type = "LGBM_STANDARD"

    log.info("effective_n.computed",
             n_raw=len(barrier_df),
             n_eff=round(n_eff, 1),
             model_type=model_type)

    # Checklist Parte 12: N_eff deve ser documentado ANTES de treinar
    if n_eff < 120:
        log.warning("effective_n.low",
                    n_eff=round(n_eff, 1),
                    msg="N_eff baixo: considerar pooling cross-ativo.")

    return n_eff, uniqueness, model_type


def compute_meta_sample_weights(
    barrier_df:   pd.DataFrame,
    uniqueness:   pd.Series,
    halflife_days: int   = 180,
    sl_penalty:    float = 2.0,
) -> pd.Series:
    """
    Pesos de amostragem para o meta-modelo (v10.6).
    Combinação multiplicativa de três fatores:

    w_final = w_uniqueness × w_time_decay × w_sl_penalty

    w_uniqueness:  1 / concorrência (menos overlap → mais peso)
    w_time_decay:  exp(-dias_atras / halflife) — observações recentes
                   valem mais (mercado evolui)
    w_sl_penalty:  SL pesa sl_penalty × mais que TP/TS (v10.6: W_SL = 2 × W_TS)
                   Penaliza erros de risco mais gravemente.

    Normalização: pesos somam N (peso médio = 1.0).

    Args:
        barrier_df:    DataFrame do Triple-Barrier com coluna 'label'.
        uniqueness:    pd.Series de uniqueness (compute_label_uniqueness).
        halflife_days: Halflife do decaimento temporal em dias. Default 180.
        sl_penalty:    Multiplicador para labels -1 (SL). Default 2.0.
                       Definir ANTES de ver dados de validação (checklist 6c).

    Returns:
        pd.Series de pesos normalizados (média = 1.0).
    """
    df = barrier_df.copy()
    df.index = pd.to_datetime(df.index)

    # Componente 1: unicidade
    w_uniq = uniqueness.reindex(df.index).fillna(uniqueness.mean()).values

    # Componente 2: decaimento temporal exponencial
    days_ago = np.array((df.index.max() - df.index).days, dtype=float)
    w_time   = np.exp(-days_ago / halflife_days)

    # Componente 3: penalidade assimétrica de SL
    sl_map = {1: 1.0, 0: 1.0, -1: float(sl_penalty)}
    w_sl   = df["label"].map(sl_map).fillna(1.0).values

    combined = w_uniq * w_time * w_sl

    # Normaliza: média = 1.0
    normalized = pd.Series(
        combined / combined.sum() * len(combined),
        index=df.index,
        name="sample_weight",
    )

    log.info("sample_weights.computed",
             n=len(normalized),
             sl_penalty=sl_penalty,
             halflife_days=halflife_days,
             weight_sl_mean=round(float(
                 normalized[df["label"] == -1].mean()
             ), 4) if (df["label"] == -1).any() else 0.0,
             weight_tp_mean=round(float(
                 normalized[df["label"] == 1].mean()
             ), 4) if (df["label"] == 1).any() else 0.0)

    return normalized
