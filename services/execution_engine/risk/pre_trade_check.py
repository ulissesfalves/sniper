# =============================================================================
# DESTINO: services/execution_engine/risk/pre_trade_check.py
# Verificação pré-trade: única porta de entrada antes de qualquer ordem.
# Integra AlarmManager + CVaR + DrawdownScalar + HMM.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


@dataclass
class PreTradeResult:
    approved:       bool
    reason:         str
    alarm_level:    int          # 0-3
    cvar_stress:    float
    drawdown_scalar: float
    hmm_is_bull:    Optional[bool]
    position_usdt:  float        # tamanho aprovado (pode ser reduzido)


async def run_pre_trade_check(
    symbol:          str,
    position_usdt:   float,
    capital_total:   float,
    capital_hwm:     float,
    portfolio_state: dict,       # posições abertas atuais
    hmm_state:       dict,       # {'hmm_is_bull': bool, 'hmm_prob_bull': float}
    cs_state:        dict,       # resultado circuit_breaker_check()
    drift_state:     dict,       # resultado run_c2st() para este ativo
    cvar_state:      dict,       # resultado portfolio_stress_report()
    global_drift:    dict,       # resultado run_c2st_portfolio()
) -> PreTradeResult:
    """
    Verificação pré-trade: bloqueia ordem se qualquer condição de risco ativa.

    Hierarquia de bloqueio (ordem de verificação):
        1. Hard Stop (drawdown ≥ 18%): bloqueia tudo
        2. HALT global (drift sistêmico ou CVaR > 20%): bloqueia tudo
        3. BLOCK por ativo (HMM bear, CS ativo, drift MODERATE+): bloqueia ativo
        4. CVaR > 15%: reduz posição proporcionalmente

    Returns:
        PreTradeResult com approved=True/False e razão detalhada.
        Se approved=True, position_usdt pode ser menor que o original (redução CVaR).
    """
    from decouple import config
    MAX_DD = float(config("MAX_DRAWDOWN_LIMIT", default="0.18"))

    # ── 1. Hard Stop ────────────────────────────────────────────────────
    dd = (capital_hwm - capital_total) / max(capital_hwm, 1e-10)
    dd_scalar = max(0.0, 1.0 - dd / MAX_DD)

    if dd_scalar == 0.0:
        return PreTradeResult(
            approved=False, alarm_level=3,
            reason=f"Hard Stop: drawdown atual {dd*100:.1f}% ≥ {MAX_DD*100:.0f}%",
            cvar_stress=cvar_state.get("cvar_stress_rho1", 0),
            drawdown_scalar=0.0, hmm_is_bull=None,
            position_usdt=0.0,
        )

    # ── 2. HALT global ──────────────────────────────────────────────────
    cvar_stress = float(cvar_state.get("cvar_stress_rho1", 0.0))
    if cvar_stress > 0.20:
        return PreTradeResult(
            approved=False, alarm_level=3,
            reason=f"CVaR_stress={cvar_stress:.3f} > 20% — margem de segurança zerada",
            cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
            hmm_is_bull=None, position_usdt=0.0,
        )

    if global_drift.get("global_alert"):
        n_sv = global_drift.get("n_severe", 0)
        return PreTradeResult(
            approved=False, alarm_level=3,
            reason=f"Drift sistêmico: {n_sv} ativos SEVERE",
            cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
            hmm_is_bull=None, position_usdt=0.0,
        )

    # ── 3. BLOCK por ativo ──────────────────────────────────────────────
    hmm_bull = hmm_state.get("hmm_is_bull", True)
    hmm_prob = hmm_state.get("hmm_prob_bull", 1.0)

    if not hmm_bull:
        return PreTradeResult(
            approved=False, alarm_level=2,
            reason=f"HMM regime BEAR (P_bull={hmm_prob:.3f})",
            cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
            hmm_is_bull=False, position_usdt=0.0,
        )

    if cs_state.get("status") == "BLOCKED":
        return PreTradeResult(
            approved=False, alarm_level=2,
            reason=f"Circuit Breaker CS: {cs_state.get('reason','')}",
            cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
            hmm_is_bull=True, position_usdt=0.0,
        )

    drift_severity = drift_state.get("severity", "NONE")
    if drift_severity == "SEVERE":
        return PreTradeResult(
            approved=False, alarm_level=2,
            reason=f"Drift C2ST SEVERE — requalificação obrigatória",
            cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
            hmm_is_bull=True, position_usdt=0.0,
        )

    # ── 4. Redução proporcional por CVaR ────────────────────────────────
    final_usdt = position_usdt * dd_scalar

    if cvar_stress > 0.15:
        reduction  = 0.15 / max(cvar_stress, 1e-10)
        final_usdt = final_usdt * reduction
        log.info("pre_trade.cvar_reduction",
                 symbol=symbol, cvar=round(cvar_stress, 4),
                 reduction=round(reduction, 3),
                 pos_before=round(position_usdt, 2),
                 pos_after=round(final_usdt, 2))

    # WARN: drift MODERATE ou dd elevado — aprova com log
    alarm_level = 0
    warn_reason = ""
    if drift_severity == "MODERATE":
        alarm_level = 1
        warn_reason = f"WARN: Drift C2ST MODERATE (AUC={drift_state.get('auc_observed', 0):.3f})"
    elif dd > 0.09:
        alarm_level = 1
        warn_reason = f"WARN: Drawdown={dd*100:.1f}% — scalar={dd_scalar:.2f}"

    if warn_reason:
        log.warning("pre_trade.warn", symbol=symbol, reason=warn_reason)

    log.info("pre_trade.approved",
             symbol=symbol,
             position_usdt=round(final_usdt, 2),
             dd_scalar=round(dd_scalar, 3),
             cvar_stress=round(cvar_stress, 4),
             hmm_bull=hmm_bull,
             alarm_level=alarm_level)

    return PreTradeResult(
        approved=True, alarm_level=alarm_level,
        reason=warn_reason or "OK",
        cvar_stress=cvar_stress, drawdown_scalar=dd_scalar,
        hmm_is_bull=hmm_bull,
        position_usdt=round(final_usdt, 2),
    )
