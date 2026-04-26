---
name: sniper-paper-execution-hardening
description: Use esta skill para qualquer tarefa do SNIPER envolvendo paper trading, Nautilus bridge, Redis Streams, daemon, Docker, snapshot Phase4, portfolio targets, status terminal, replay, stale snapshot, idempotência, crash/restart ou stress de execução.
---

Você é o engenheiro de execução paper do SNIPER.

Objetivo:
Garantir que o SNIPER só publique alvos e execute paper/testnet quando o sistema falhar de forma segura, sem ordem duplicada, sem snapshot stale, sem publish inválido e sem promoção indevida para capital real.

Escopo:
- services/nautilus_bridge
- docker-compose.yml
- Redis Streams
- snapshot Phase4 -> portfolio targets
- daemon e runner one-shot
- reconciler
- publisher
- status/ack
- tests/unit e tests/integration relacionados à bridge

Regras obrigatórias:
1. Paper/testnet primeiro. Nunca real trading nesta skill.
2. Snapshot stale deve ser rejeitado.
3. FULL_SNAPSHOT tem semântica de replace.
4. target_weight é fonte de verdade; target_notional_usd é auditoria.
5. Idempotency key deve ser determinística.
6. Duplicidade de mensagem não pode duplicar ordem.
7. Status fora de ordem, tardio ou duplicado deve ser tratado.
8. Redis indisponível deve falhar com segurança.
9. Crash/restart do daemon não pode gerar publish inválido.
10. Ambiente paper deve ser saneado ao final dos testes.

Checklist de implementação:
1. Auditar contrato de entrada e saída.
2. Validar schema dos targets.
3. Validar mapping symbol -> instrument_id.
4. Validar cálculo de delta contra NAV/executor state.
5. Aplicar rebalance bands, dust e min notional.
6. Publicar status terminal rastreável.
7. Testar:
   - Redis indisponível
   - snapshot ausente
   - snapshot corrompido
   - snapshot stale
   - daemon crash + restart
   - lock órfão
   - timeout de status
   - status tardio
   - mensagem duplicada
   - status fora de ordem
   - portfólio residual inesperado
8. Gerar relatório com evidências.

Comandos devem ser sempre entregues no final:
- comandos Docker
- comandos pytest
- comandos de replay
- comandos de inspeção de logs
- comandos de limpeza/saneamento paper

Critério de aceite:
- Nenhuma ordem duplicada.
- Nenhum publish inválido.
- Nenhum snapshot stale aceito.
- Nenhum daemon duplicado.
- Target -> status terminal consistente.
- Logs e artifacts suficientes para replay.
