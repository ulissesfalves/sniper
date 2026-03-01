# =============================================================================
# DESTINO: services/ml_engine/sizing/kelly_cvar.py
# Kelly Fracionário com Limite de CVaR e Cenário de Stress ρ=1.0 (v10.10).
#
# PIPELINE DE SIZING (Parte 10):
#   1. Kelly puro → f* = (μ - r) / σ²  (base matemática de maximização log-E[W])
#   2. Fração conservadora → f = κ × f*  (κ=0.25: quartil de Kelly)
#   3. Limite de CVaR → CVaR_95%(portfolio) ≤ 15% capital
#   4. Cenário de stress ρ=1.0 → correlação forçada (SNIPER v10.10 revisão)
#      Todos os ativos colapsam juntos no pior cenário → sizing mais conservador
#   5. Drawdown dinâmico → HWM tracking com redução automática
#
# CVaR vs VaR (Parte 10.2):
#   VaR_95% diz: "perco X ou menos em 95% dos dias."
#   CVaR_95% diz: "quando ultrapasso o VaR, perco Y em média."
#   Para fat tails (LUNA, FTX): VaR subestima gravemente o custo real.
#   CVaR captura a cauda — é o limite correto para gestão de risco crypto.
#
# ρ=1.0 STRESS (Parte 10.3 — v10.10):
#   Em flash crash, correlações entre altcoins → 1.0 (vide Nov/2022).
#   Usar correlação histórica real subestima o risco de crise.
#   Com ρ=1.0: σ_portfolio = Σ(w_i × σ_i) — máxima volatilidade possível.
#   Sizing calculado com ρ=1.0 ainda passa no CVaR_stress ≤ 15%.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Parâmetros SNIPER v10.10 — não alterar sem re-backtest completo
KELLY_FRACTION      = 0.25    # κ: quartil de Kelly — capital preservado
CVAR_LIMIT          = 0.15    # CVaR_95% máx: 15% do capital total
MAX_DRAWDOWN_LIMIT  = 0.18    # 18%: Hard Stop drawdown máximo (Parte 10.4)
CVAR_ALPHA          = 0.05    # α: cauda dos 5% piores cenários (CVaR_95%)
MIN_POSITION_USDT   = 50.0    # Posição mínima em USDT (liquidez)
MAX_POSITION_USDT   = 10_000  # Posição máxima absoluta por ativo


@dataclass
class SizingResult:
    """Resultado do sizing para um sinal."""
    symbol:          str
    position_usdt:   float         # Tamanho final da posição em USDT
    kelly_raw:       float         # Kelly puro (antes de qualquer limite)
    kelly_frac:      float         # Kelly × κ (antes de CVaR)
    cvar_95_stress:  float         # CVaR_95% com ρ=1.0 do portfolio final
    cvar_ok:         bool          # CVaR ≤ CVAR_LIMIT?
    drawdown_scalar: float         # Escalar dinâmico por drawdown (0-1)
    sigma_entry:     float         # Volatilidade no momento do sinal
    p_calibrated:    float         # P_calibrada que alimentou o sizing


def compute_kelly_fraction(
    mu:      float,
    sigma:   float,
    p_cal:   float,
    rf:      float = 0.0,
    kappa:   float = KELLY_FRACTION,
) -> float:
    """
    Kelly fracionário contínuo com ajuste de probabilidade calibrada.

    Kelly puro: f* = (μ - rf) / σ²
    Kelly fracionário: f = κ × f*  (κ=0.25 por padrão)

    Ajuste de μ por p_calibrada (Parte 10.1):
        μ_adjusted = p_cal × |μ_tp| - (1 - p_cal) × |μ_sl|
        Integra a probabilidade calibrada diretamente no sizing.
        Diferencia entre p_cal=0.90 (aposta grande) e p_cal=0.55 (aposta pequena).

    Domínio: f ∈ [0, 1.0]. Valores negativos → não operar.

    Args:
        mu:     Retorno esperado ajustado por p_cal (μ_adjusted acima).
        sigma:  Volatilidade EWMA no momento do sinal.
        p_cal:  Probabilidade calibrada (output do IsotonicCalibrator).
        rf:     Taxa livre de risco. Default 0 (crypto sem risk-free real).
        kappa:  Fração do Kelly. Default 0.25.

    Returns:
        float: fração do capital a alocar ∈ [0, 0.50].
    """
    sigma_sq = max(sigma ** 2, 1e-8)
    f_star   = max((mu - rf) / sigma_sq, 0.0)
    f_frac   = kappa * f_star

    # Cap conservador: nunca mais de 50% do capital em um ativo
    f_frac   = min(f_frac, 0.50)

    log.debug("kelly.computed",
              mu=round(mu, 4), sigma=round(sigma, 4), p_cal=round(p_cal, 4),
              f_star=round(f_star, 4), f_frac=round(f_frac, 4), kappa=kappa)

    return float(f_frac)


def compute_cvar_stress(
    position_fractions: dict[str, float],
    sigmas:             dict[str, float],
    pnl_history:        dict[str, np.ndarray] | None = None,
    alpha:              float = CVAR_ALPHA,
    force_rho_one:      bool  = True,
) -> tuple[float, float]:
    """
    CVaR_95% do portfolio com cenário de stress ρ=1.0 (v10.10).

    Dois CVaR calculados:
        CVaR_historical: usa correlações reais dos P&L históricos.
        CVaR_stress:     ρ=1.0 forçado — todos os ativos colapsam juntos.

    O LIMITE é aplicado sobre CVaR_stress (mais conservador).
    CVaR_historical fica como referência (diagnóstico).

    Fórmula com ρ=1.0:
        σ_portfolio_stress = Σ(w_i × σ_i)   ← soma linear (max diversificação negativa)
        VaR_stress = z_α × σ_portfolio_stress
        CVaR_stress = φ(z_α) / α × σ_portfolio_stress
        onde φ(z) = density normal padrão

    Com correlações reais (diagnóstico):
        σ_portfolio = √(w' × Σ × w)  onde Σ_ij = ρ_ij × σ_i × σ_j

    Args:
        position_fractions: {símbolo: fração do capital} — ex: {"SOL": 0.08}
        sigmas:             {símbolo: σ_ewma}
        pnl_history:        {símbolo: np.ndarray de P&L diários} (opcional)
        alpha:              Nível da cauda. Default 0.05 (CVaR_95%).
        force_rho_one:      Se True (default), aplica stress ρ=1.0.

    Returns:
        (cvar_stress, cvar_historical): ambos em fração do capital.
    """
    from scipy.stats import norm

    symbols = list(position_fractions.keys())
    weights = np.array([position_fractions.get(s, 0.0) for s in symbols])
    sigs    = np.array([sigmas.get(s, 0.02) for s in symbols])

    if weights.sum() <= 0:
        return 0.0, 0.0

    # ── CVaR com ρ=1.0 (stress) ───────────────────────────────────────────
    # σ_portfolio_stress = Σ(w_i × σ_i): soma linear = máx correlação
    sigma_p_stress = float(np.dot(weights, sigs))
    z_alpha        = float(norm.ppf(alpha))          # z_0.05 ≈ -1.645
    phi_z          = float(norm.pdf(z_alpha))         # densidade em z_α
    cvar_stress    = float(phi_z / alpha * sigma_p_stress)

    # ── CVaR com correlações reais (histórico) ────────────────────────────
    cvar_historical = cvar_stress  # fallback se sem histórico
    if pnl_history and all(s in pnl_history for s in symbols):
        # Truncar ao comprimento mínimo (eventos != datas alinhadas)
        min_len = min(len(pnl_history[s]) for s in symbols)
        if min_len >= 30:
            pnl_matrix = np.column_stack([
                pnl_history[s][:min_len] for s in symbols
            ])
            # Remove linhas com NaN
            pnl_matrix = pnl_matrix[~np.any(np.isnan(pnl_matrix), axis=1)]

            if len(pnl_matrix) >= 30:
                portfolio_pnl = pnl_matrix @ weights
                sorted_pnl    = np.sort(portfolio_pnl)
                n_tail        = max(1, int(len(sorted_pnl) * alpha))
                cvar_historical = float(-sorted_pnl[:n_tail].mean())

    log.debug("cvar_stress.computed",
              n_assets=len(symbols),
              sigma_portfolio_stress=round(sigma_p_stress, 4),
              cvar_stress=round(cvar_stress, 4),
              cvar_historical=round(cvar_historical, 4),
              rho_one=force_rho_one)

    return cvar_stress, cvar_historical


def compute_drawdown_scalar(
    capital_current: float,
    capital_hwm:     float,
    max_dd:          float = MAX_DRAWDOWN_LIMIT,
) -> float:
    """
    Escalar dinâmico de posição baseado no drawdown atual.

    Drawdown atual: DD = (HWM - capital_atual) / HWM

    Escalar (linear de 1→0 conforme DD→max_dd):
        scalar = max(0, 1 - DD / max_dd)

    Exemplos:
        DD = 0%   → scalar = 1.00 (tamanho cheio)
        DD = 9%   → scalar = 0.50 (metade do tamanho)
        DD = 18%  → scalar = 0.00 (Hard Stop — para de operar)

    Hard Stop em DD = 18% (max_dd): interrompe novas operações automaticamente.

    Args:
        capital_current: Capital atual em USDT.
        capital_hwm:     High-Water Mark histórico.
        max_dd:          Drawdown máximo antes do Hard Stop. Default 18%.

    Returns:
        float: escalar ∈ [0, 1].
    """
    if capital_hwm <= 0:
        return 1.0
    dd     = (capital_hwm - capital_current) / capital_hwm
    dd     = max(0.0, min(dd, 1.0))
    scalar = max(0.0, 1.0 - dd / max_dd)

    if scalar == 0.0:
        log.warning("drawdown.hard_stop_triggered",
                    dd_pct=round(dd * 100, 2),
                    max_dd_pct=round(max_dd * 100, 1),
                    msg="Hard Stop ativado. Nenhuma nova operação até recuperação.")
    elif dd > 0.09:
        log.warning("drawdown.elevated",
                    dd_pct=round(dd * 100, 2),
                    scalar=round(scalar, 3))
    return float(scalar)


def compute_position_size(
    symbol:              str,
    p_calibrated:        float,
    sigma_ewma:          float,
    capital_total:       float,
    capital_hwm:         float,
    portfolio_positions: dict[str, float],
    portfolio_sigmas:    dict[str, float],
    pnl_history:         dict[str, np.ndarray] | None = None,
    k_tp:                float = 1.5,
    k_sl:                float = 1.5,
    kappa:               float = KELLY_FRACTION,
    cvar_limit:          float = CVAR_LIMIT,
    max_dd:              float = MAX_DRAWDOWN_LIMIT,
) -> SizingResult:
    """
    Pipeline completo de sizing — Parte 10 SNIPER v10.10.

    Passos:
        1. μ_adjusted = p_cal × (k_tp × σ) - (1-p_cal) × (k_sl × σ)
        2. Kelly fracionário: f = κ × f* = κ × μ_adjusted / σ²
        3. Posição candidata: Q_candidata = f × capital_total × dd_scalar
        4. Adiciona posição candidata ao portfolio
        5. Recalcula CVaR_stress (ρ=1.0) do portfolio
        6. Se CVaR_stress > cvar_limit: reduz proporcionalmente
        7. Aplica limites absolutos [MIN_POSITION_USDT, MAX_POSITION_USDT]

    Args:
        symbol:              Símbolo do ativo (ex: "SOLUSDT").
        p_calibrated:        P_calibrada ∈ [0, 1] — output da Isotônica.
        sigma_ewma:          σ EWMA no momento do sinal.
        capital_total:       Capital total disponível em USDT.
        capital_hwm:         High-Water Mark atual.
        portfolio_positions: {símbolo: fração atual} — posições abertas.
        portfolio_sigmas:    {símbolo: σ_ewma} — volatilidades das posições abertas.
        pnl_history:         {símbolo: np.ndarray} — histórico de P&L por ativo.
        k_tp, k_sl:          Multiplicadores das barreiras TP e SL.
        kappa:               Fração de Kelly. Default 0.25.
        cvar_limit:          Limite de CVaR_stress. Default 15%.
        max_dd:              Drawdown máximo (Hard Stop). Default 18%.

    Returns:
        SizingResult com posição final e métricas de diagnóstico.
    """
    # ── 1. Retorno esperado ajustado por p_calibrada ──────────────────────
    # Ganho esperado no TP: k_tp × σ com probabilidade p_cal
    # Perda esperada no SL: k_sl × σ com probabilidade (1 - p_cal)
    mu_tp     = k_tp * sigma_ewma    # retorno se TP hit
    mu_sl     = k_sl * sigma_ewma    # perda se SL hit
    mu_adj    = p_calibrated * mu_tp - (1.0 - p_calibrated) * mu_sl

    if mu_adj <= 0:
        log.info("sizing.negative_edge",
                 symbol=symbol, mu_adj=round(mu_adj, 5),
                 p_cal=round(p_calibrated, 4),
                 msg="Edge negativo — sem posição.")
        return SizingResult(
            symbol=symbol, position_usdt=0.0,
            kelly_raw=0.0, kelly_frac=0.0,
            cvar_95_stress=0.0, cvar_ok=True,
            drawdown_scalar=compute_drawdown_scalar(
                capital_total, capital_hwm, max_dd
            ),
            sigma_entry=sigma_ewma,
            p_calibrated=p_calibrated,
        )

    # ── 2. Kelly fracionário ──────────────────────────────────────────────
    kelly_raw  = float(max((mu_adj) / max(sigma_ewma ** 2, 1e-8), 0.0))
    kelly_frac = compute_kelly_fraction(
        mu=mu_adj, sigma=sigma_ewma,
        p_cal=p_calibrated, kappa=kappa,
    )

    # ── 3. Escalar de drawdown ────────────────────────────────────────────
    dd_scalar = compute_drawdown_scalar(capital_total, capital_hwm, max_dd)
    if dd_scalar == 0.0:
        return SizingResult(
            symbol=symbol, position_usdt=0.0,
            kelly_raw=kelly_raw, kelly_frac=kelly_frac,
            cvar_95_stress=0.0, cvar_ok=True,
            drawdown_scalar=0.0, sigma_entry=sigma_ewma,
            p_calibrated=p_calibrated,
        )

    q_candidate_usdt = kelly_frac * capital_total * dd_scalar

    # ── 4-5. CVaR com ρ=1.0 do portfolio + candidata ─────────────────────
    portfolio_trial = {**portfolio_positions,
                       symbol: q_candidate_usdt / capital_total}
    sigmas_trial    = {**portfolio_sigmas, symbol: sigma_ewma}

    cvar_stress, cvar_hist = compute_cvar_stress(
        portfolio_trial, sigmas_trial, pnl_history,
        alpha=CVAR_ALPHA, force_rho_one=True,
    )

    # ── 6. Reduz proporcionalmente se CVaR > limite ───────────────────────
    q_final_usdt = q_candidate_usdt
    if cvar_stress > cvar_limit:
        # Fator de redução: quanto preciso encolher a posição para CVaR ≤ limite?
        # CVaR é linear em σ_portfolio sob ρ=1.0 → redução linear é exata
        reduction    = cvar_limit / max(cvar_stress, 1e-10)
        q_final_usdt = q_candidate_usdt * reduction
        log.info("sizing.cvar_reduction",
                 symbol=symbol,
                 cvar_stress=round(cvar_stress, 4),
                 cvar_limit=cvar_limit,
                 reduction=round(reduction, 4),
                 q_before=round(q_candidate_usdt, 2),
                 q_after=round(q_final_usdt, 2))

        # Recalcula CVaR com posição reduzida (verificação)
        port_final   = {**portfolio_positions,
                        symbol: q_final_usdt / capital_total}
        cvar_stress, _ = compute_cvar_stress(
            port_final, sigmas_trial, pnl_history,
            alpha=CVAR_ALPHA, force_rho_one=True,
        )

    # ── 7. Limites absolutos ──────────────────────────────────────────────
    q_final_usdt = float(np.clip(q_final_usdt, MIN_POSITION_USDT, MAX_POSITION_USDT))
    cvar_ok      = cvar_stress <= cvar_limit

    log.info("sizing.complete",
             symbol=symbol,
             p_calibrated=round(p_calibrated, 4),
             mu_adj=round(mu_adj, 5),
             kelly_raw=round(kelly_raw, 4),
             kelly_frac=round(kelly_frac, 4),
             dd_scalar=round(dd_scalar, 3),
             position_usdt=round(q_final_usdt, 2),
             cvar_stress=round(cvar_stress, 4),
             cvar_ok=cvar_ok)

    return SizingResult(
        symbol=symbol,
        position_usdt=round(q_final_usdt, 2),
        kelly_raw=kelly_raw,
        kelly_frac=kelly_frac,
        cvar_95_stress=cvar_stress,
        cvar_ok=cvar_ok,
        drawdown_scalar=dd_scalar,
        sigma_entry=sigma_ewma,
        p_calibrated=p_calibrated,
    )


def portfolio_stress_report(
    positions:    dict[str, float],
    sigmas:       dict[str, float],
    capital:      float,
    pnl_history:  dict[str, np.ndarray] | None = None,
) -> dict:
    """
    Relatório completo de stress do portfolio — executar diariamente.

    Compara CVaR_histórico vs CVaR_stress(ρ=1.0).
    A diferença mede o "risco oculto" assumindo diversificação real.
    Em crises: a diversificação desaparece e o CVaR_stress é o real.

    Returns:
        dict com CVaR_historical, CVaR_stress, razão, margem de segurança.
    """
    cvar_stress, cvar_hist = compute_cvar_stress(
        {s: v / capital for s, v in positions.items()},
        sigmas, pnl_history,
        alpha=CVAR_ALPHA, force_rho_one=True,
    )

    sigma_total = sum(
        (v / capital) * sigmas.get(s, 0.02)
        for s, v in positions.items()
    )

    return {
        "cvar_historical":        round(cvar_hist, 4),
        "cvar_stress_rho1":       round(cvar_stress, 4),
        "cvar_limit":             CVAR_LIMIT,
        "margin_of_safety":       round(CVAR_LIMIT - cvar_stress, 4),
        "cvar_ok":                cvar_stress <= CVAR_LIMIT,
        "hidden_risk_factor":     round(cvar_stress / max(cvar_hist, 1e-10), 2),
        "sigma_portfolio_stress": round(sigma_total, 4),
        "n_positions":            len(positions),
        "total_exposure_pct":     round(sum(positions.values()) / capital * 100, 2),
    }
