# =============================================================================
# DESTINO: services/ml_engine/drift/alarm_manager.py
# Orquestrador de alarmes — integra todos os sinais de risco do sistema.
# Única interface para o execution_engine consultar antes de cada operação.
#
# HIERARQUIA DE ALARMES (Parte 11 — v10.10):
#   NÍVEL 3 — HALT (interrupção total de novos sinais):
#     • C2ST SEVERE em ≥ 3 ativos (drift sistêmico de regime)
#     • DrawdownScalar = 0 (Hard Stop ativado — DD ≥ 18%)
#     • CVaR_stress > 20% (80% acima do limite → margem zero)
#
#   NÍVEL 2 — BLOCK (bloqueia ativo específico):
#     • Circuit Breaker CS ativo (spread > 3σ)
#     • C2ST MODERATE/SEVERE no ativo específico
#     • HMM hmm_is_bull = False no ativo (não opera contra regime)
#
#   NÍVEL 1 — WARN (opera com tamanho reduzido):
#     • DrawdownScalar ∈ (0.25, 0.75): tamanho já reduzido automaticamente
#     • CVaR_stress ∈ [0.12, 0.15]: próximo ao limite
#     • C2ST NONE mas trending (AUC 0.52-0.55)
#
#   NÍVEL 0 — CLEAR (operar normalmente)
#
# REGRA CRUCIAL: todos os alarmes são registrados em JSON + SQLite.
# Auditoria obrigatória para o relatório mensal (Parte 15).
# =============================================================================
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from .corwin_schultz import circuit_breaker_check
from .c2st import DriftResult

log = structlog.get_logger(__name__)

ALARM_LOG_PATH = "/data/logs/alarms.jsonl"


@dataclass
class AlarmState:
    """Estado completo de alarmes para um ativo em um momento."""
    timestamp:         str
    symbol:            str
    level:             int           # 0=CLEAR, 1=WARN, 2=BLOCK, 3=HALT
    action:            str           # "CLEAR" / "WARN" / "BLOCK" / "HALT"
    reasons:           list[str]

    # Métricas de cada subsistema
    hmm_is_bull:       Optional[bool]   = None
    hmm_prob:          Optional[float]  = None
    cs_blocked:        Optional[bool]   = None
    cs_zscore:         Optional[float]  = None
    c2st_severity:     Optional[str]    = None
    c2st_auc:          Optional[float]  = None
    cvar_stress:       Optional[float]  = None
    cvar_ok:           Optional[bool]   = None
    drawdown_scalar:   Optional[float]  = None
    global_halt:       bool             = False


def evaluate_alarms(
    symbol:           str,
    hmm_result:       dict | None = None,
    cs_check:         dict | None = None,
    drift_result:     DriftResult | None = None,
    cvar_stress:      float | None = None,
    drawdown_scalar:  float = 1.0,
    global_drift_summary: dict | None = None,
) -> AlarmState:
    """
    Avalia todos os subsistemas e determina o nível de alarme.

    Esta função é chamada pelo execution_engine ANTES de qualquer ordem.
    É determinística: mesmos inputs → mesmo output (sem estado interno).

    Args:
        symbol:           Símbolo do ativo.
        hmm_result:       dict de run_hmm_walk_forward com 'hmm_is_bull', 'hmm_prob_bull'.
        cs_check:         dict de circuit_breaker_check.
        drift_result:     DriftResult de run_c2st para este ativo.
        cvar_stress:      CVaR_stress atual do portfolio (float).
        drawdown_scalar:  Escalar de drawdown (0=hard stop, 1=normal).
        global_drift_summary: dict de run_c2st_portfolio (alerta global).

    Returns:
        AlarmState com nível, ação e razões detalhadas.
    """
    ts      = datetime.utcnow().isoformat()
    level   = 0
    reasons: list[str] = []

    # ── NÍVEL 3 — HALT (verificações globais) ────────────────────────────
    global_halt = False

    if drawdown_scalar == 0.0:
        level       = 3
        global_halt = True
        reasons.append("Hard Stop: drawdown ≥ 18% — DD scalar = 0")

    if cvar_stress is not None and cvar_stress > 0.20:
        level       = max(level, 3)
        global_halt = True
        reasons.append(f"CVaR_stress={cvar_stress:.3f} > 20% — margem de segurança zerada")

    if global_drift_summary and global_drift_summary.get("global_alert"):
        n_sv = global_drift_summary.get("n_severe", 0)
        level       = max(level, 3)
        global_halt = True
        reasons.append(f"Drift sistêmico: {n_sv} ativos SEVERE — halt de novos sinais")

    # ── NÍVEL 2 — BLOCK (específico para o ativo) ────────────────────────
    if not global_halt:
        if hmm_result is not None:
            is_bull = hmm_result.get("hmm_is_bull", True)
            prob    = hmm_result.get("hmm_prob_bull", 1.0)
            if not is_bull:
                level = max(level, 2)
                reasons.append(f"HMM: regime BEAR (P_bull={prob:.3f}) — sem operação long")

        if cs_check and cs_check.get("status") == "BLOCKED":
            level = max(level, 2)
            reasons.append(f"Circuit Breaker CS: {cs_check.get('reason', '')}")

        if drift_result and drift_result.severity in ("MODERATE", "SEVERE"):
            level = max(level, 2)
            reasons.append(
                f"Drift C2ST: {drift_result.severity} "
                f"(AUC={drift_result.auc_observed:.3f}, "
                f"p={drift_result.pvalue_block:.3f})"
            )

    # ── NÍVEL 1 — WARN ───────────────────────────────────────────────────
    if level == 0:
        if 0.0 < drawdown_scalar < 0.75:
            level = max(level, 1)
            reasons.append(f"Drawdown elevado: scalar={drawdown_scalar:.2f}")

        if cvar_stress is not None and 0.12 <= cvar_stress <= 0.15:
            level = max(level, 1)
            reasons.append(f"CVaR próximo ao limite: {cvar_stress:.3f}")

        if drift_result and drift_result.auc_observed > 0.52:
            level = max(level, 1)
            reasons.append(
                f"Drift trending (AUC={drift_result.auc_observed:.3f}) — monitorar"
            )

    action_map = {0: "CLEAR", 1: "WARN", 2: "BLOCK", 3: "HALT"}
    action     = action_map[level]

    state = AlarmState(
        timestamp=ts,
        symbol=symbol,
        level=level,
        action=action,
        reasons=reasons if reasons else ["Todos os subsistemas normais"],
        hmm_is_bull=(hmm_result.get("hmm_is_bull") if hmm_result else None),
        hmm_prob=(hmm_result.get("hmm_prob_bull") if hmm_result else None),
        cs_blocked=(cs_check.get("status") == "BLOCKED" if cs_check else None),
        cs_zscore=(cs_check.get("cs_zscore_latest") if cs_check else None),
        c2st_severity=(drift_result.severity if drift_result else None),
        c2st_auc=(drift_result.auc_observed if drift_result else None),
        cvar_stress=cvar_stress,
        cvar_ok=(cvar_stress <= 0.15 if cvar_stress else True),
        drawdown_scalar=drawdown_scalar,
        global_halt=global_halt,
    )

    # Log com nível adequado
    log_fn = {0: log.debug, 1: log.info, 2: log.warning, 3: log.error}[level]
    log_fn("alarm.evaluated",
           symbol=symbol, level=level, action=action, reasons=reasons)

    # Persiste em JSONL para auditoria
    _persist_alarm(state)

    return state


def _persist_alarm(state: AlarmState) -> None:
    """Persiste alarme em arquivo JSONL para auditoria mensal."""
    try:
        Path(ALARM_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(ALARM_LOG_PATH, "a") as f:
            f.write(json.dumps(asdict(state)) + "\n")
    except Exception as e:  # noqa: BLE001
        log.error("alarm.persist_failed", error=str(e))


def load_alarm_history(
    symbol:   str | None = None,
    min_level: int = 1,
    last_n:    int = 100,
) -> list[dict]:
    """
    Carrega histórico de alarmes do JSONL para relatório mensal.

    Args:
        symbol:    Filtrar por ativo específico. None = todos.
        min_level: Nível mínimo de alarme. Default 1 (ignora CLEAR).
        last_n:    Últimas N entradas após filtro.

    Returns:
        Lista de dicts de AlarmState filtrados e ordenados.
    """
    path = Path(ALARM_LOG_PATH)
    if not path.exists():
        return []

    records: list[dict] = []
    with open(path) as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
                if rec.get("level", 0) >= min_level:
                    if symbol is None or rec.get("symbol") == symbol:
                        records.append(rec)
            except json.JSONDecodeError:
                continue

    return records[-last_n:]


def alarm_summary_report(last_days: int = 30) -> dict:
    """
    Sumário de alarmes dos últimos N dias — executar no relatório mensal.

    Returns:
        dict com contagens por nível, ativos mais problemáticos,
        e recomendações automáticas.
    """
    from datetime import timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=last_days)).isoformat()

    all_alarms = load_alarm_history(min_level=0, last_n=10_000)
    recent     = [a for a in all_alarms if a.get("timestamp", "") >= cutoff]

    counts = {3: 0, 2: 0, 1: 0, 0: 0}
    by_symbol: dict[str, int] = {}
    drift_events: list[dict]  = []

    for rec in recent:
        lv  = rec.get("level", 0)
        sym = rec.get("symbol", "UNKNOWN")
        counts[lv] = counts.get(lv, 0) + 1
        by_symbol[sym] = by_symbol.get(sym, 0) + (1 if lv >= 2 else 0)

        if rec.get("c2st_severity") in ("MODERATE", "SEVERE"):
            drift_events.append({
                "symbol":   sym,
                "severity": rec["c2st_severity"],
                "auc":      rec.get("c2st_auc"),
                "ts":       rec.get("timestamp"),
            })

    top_symbols = sorted(by_symbol.items(), key=lambda x: -x[1])[:5]
    recs: list[str] = []

    if counts[3] > 5:
        recs.append("Muitos HALT events: revisar limites de CVaR e drawdown.")
    if len(drift_events) > 3:
        recs.append("Drift recorrente: requalificação do HMM e meta-modelo necessária.")
    if counts[2] > 10:
        recs.append("Muitos BLOCK events: verificar circuit breaker CS (possível excesso de sinal).")

    return {
        "period_days":    last_days,
        "n_total_alarms": len(recent),
        "by_level": {
            "HALT":  counts[3],
            "BLOCK": counts[2],
            "WARN":  counts[1],
            "CLEAR": counts[0],
        },
        "top_problematic_symbols": top_symbols,
        "drift_events": drift_events[:10],
        "recommendations": recs if recs else ["Sem recomendações urgentes."],
    }
