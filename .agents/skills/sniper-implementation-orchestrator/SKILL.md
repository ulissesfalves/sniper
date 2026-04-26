---
name: sniper-implementation-orchestrator
description: Use esta skill como central de decisão do SNIPER. Ela avalia o estado atual do projeto, o último gate, os relatórios de auditoria e a próxima recomendação, decide qual skill deve ser usada e gera diretamente um prompt pronto para copiar e colar no Codex para o próximo gate ou etapa. Não implementa código.
---

Você é o orquestrador de implementação robusta do projeto SNIPER.

Objetivo:
Avaliar o estado atual do SNIPER e gerar diretamente o prompt mais adequado para o próximo gate/etapa, escolhendo quais skills devem ser ativadas no prompt.

Esta skill NÃO implementa código.
Esta skill NÃO altera código.
Esta skill NÃO promove nada para official.
Esta skill NÃO substitui as skills especializadas.
Esta skill apenas decide o próximo movimento e gera um prompt operacional.

Contexto fixo do SNIPER:
- Repo local: C:\Users\uliss\Documentos\Meus_projetos\sniper
- Branch de continuidade principal: codex/openclaw-sniper-handoff
- A3 está encerrado como structural choke.
- Não reabrir A3/A4 sem evidência nova forte.
- RiskLabAI permanece oracle/shadow, não official.
- Fast path permanece official.
- Família cross-sectional soberana está ALIVE_BUT_NOT_PROMOTABLE.
- Baseline research-only correta: phase5_cross_sectional_sovereign_closure_restored.
- DSR honesto igual a 0.0 bloqueia promoção.
- Research não vira official sem gate explícito.
- Melhorias parciais não equivalem a promotabilidade.

Skills disponíveis e responsabilidades:
1. sniper-global-spec-adherence-audit
   - Auditoria global contra especificação.
   - Usar quando não houver auditoria global recente ou quando o projeto tiver acumulado vários gates sem reavaliação global.

2. sniper-gate-result-reviewer
   - Revisão do último gate executado.
   - Usar depois de qualquer gate PASS/PARTIAL/FAIL/INCONCLUSIVE.

3. sniper-next-step-prompt-builder
   - Geração de prompt a partir de next_step_recommendation.md.
   - Usar quando já houver uma recomendação consolidada.

4. sniper-gate-governance
   - Sempre incluir em prompts de implementação/validação de gate.

5. sniper-quant-research-implementation
   - Incluir em prompts que envolvam features, modelos, CPCV, DSR, PBO, ECE, CVaR, calibration, ranking, HMM, fracdiff, unlock_pressure_rank ou pesquisa quantitativa.

6. sniper-paper-execution-hardening
   - Incluir em prompts que envolvam Nautilus, Redis Streams, daemon, paper trading, snapshot, stale snapshot, idempotência, Docker, crash/restart ou execução contínua.

Arquivos que esta skill deve inspecionar:
1. reports/audits/global_spec_adherence/global_spec_adherence_summary.json
2. reports/audits/global_spec_adherence/next_step_recommendation.md
3. reports/audits/global_spec_adherence/global_implementation_checklist.md
4. reports/audits/global_spec_adherence/global_spec_adherence_report.md
5. reports/gates/**/gate_report.json
6. reports/gates/**/gate_report.md
7. reports/gates/**/gate_manifest.json
8. reports/gates/**/gate_metrics.parquet, se existir
9. docs/SNIPER_openclaw_handoff.md, se existir
10. docs/SNIPER_regeneration_guide.md, se existir

Processo de decisão:

Passo 1 — Identificar o estado dos relatórios
- Verificar se existe auditoria global em reports/audits/global_spec_adherence/.
- Verificar se existe next_step_recommendation.md.
- Identificar o gate mais recente em reports/gates/.
- Ler gate_report.json do gate mais recente, se existir.
- Identificar status e decision do gate mais recente.

Passo 2 — Classificar o estado atual
Classifique o estado em uma destas categorias:

A. NEED_GLOBAL_AUDIT
Quando:
- não existe auditoria global;
- ou os relatórios globais estão ausentes/inválidos;
- ou houve muitos gates desde a última auditoria global.

Ação:
Gerar prompt para rodar:
$sniper-global-spec-adherence-audit

B. NEED_GATE_REVIEW
Quando:
- existe gate recente executado;
- e o gate ainda não tem gate_result_review.md;
- ou status/decision do gate recente é PARTIAL, FAIL ou INCONCLUSIVE;
- ou o usuário pediu avaliação do último resultado.

Ação:
Gerar prompt para rodar:
$sniper-gate-result-reviewer

C. NEED_NEXT_STEP_PROMPT
Quando:
- existe next_step_recommendation.md atualizado;
- e o usuário precisa do prompt do próximo gate;
- e não há gate pendente de revisão.

Ação:
Gerar prompt para rodar:
$sniper-next-step-prompt-builder

D. READY_TO_EXECUTE_NEXT_GATE
Quando:
- next_step_recommendation.md está claro;
- o próximo gate tem nome, branch, objetivo e critérios;
- e o usuário quer seguir com implementação/validação.

Ação:
Gerar diretamente o prompt completo do próximo gate, incluindo skills no cabeçalho.

E. FREEZE_OR_ABORT_REQUIRED
Quando:
- o último gate matou a hipótese no mérito;
- ou houve violação de governança;
- ou DSR=0.0 continua e a recomendação tenta promover;
- ou ALIVE_BUT_NOT_PROMOTABLE foi tratado como promotable;
- ou há artifact mismatch não resolvido.

Ação:
Gerar prompt de freeze/abandon/correção de governança, não implementação ampla.

Passo 3 — Escolher skills para o prompt do próximo gate
Sempre incluir:
$sniper-gate-governance

Incluir também:
- $sniper-quant-research-implementation se o próximo gate tocar:
  feature, modelo, CPCV, DSR, PBO, ECE, CVaR, calibration, ranking, HMM, fracdiff, unlock_pressure_rank, Stage A, Phase 4/5 quantitativo.

- $sniper-paper-execution-hardening se o próximo gate tocar:
  Nautilus, Redis Streams, daemon, paper, Docker, snapshot, stale snapshot, idempotência, status terminal, crash/restart, reconciliação.

- $sniper-global-spec-adherence-audit apenas se for auditoria global.

- $sniper-gate-result-reviewer apenas se a tarefa for revisar gate anterior.

- $sniper-next-step-prompt-builder apenas se a tarefa for gerar prompt a partir de next_step_recommendation.md.

Passo 4 — Gerar prompt final
O prompt final deve ser pronto para copiar e colar no Codex.

O prompt deve conter:
1. Skills a ativar no topo.
2. Repo local.
3. Branch base.
4. Gate slug.
5. Branch sugerida.
6. Objetivo.
7. Escopo.
8. Fora de escopo.
9. Fontes de verdade.
10. Contexto do último gate e auditoria.
11. Tarefas objetivas.
12. Entregáveis esperados.
13. Critérios de PASS/PARTIAL/FAIL/INCONCLUSIVE.
14. Restrições de governança.
15. Comandos esperados.
16. Resposta final esperada do Codex.

Passo 5 — Salvar saída
Criar ou atualizar:

reports/audits/global_spec_adherence/orchestrator_recommendation.md

com:
- estado classificado;
- skill recomendada;
- justificativa;
- prompt gerado;
- arquivos lidos;
- próximos riscos.

Também criar ou atualizar:

reports/audits/global_spec_adherence/orchestrator_next_prompt.md

contendo apenas o prompt final em bloco único, pronto para copiar e colar.

Regras de governança:
- Não misturar responsabilidades das skills.
- Não gerar prompt de implementação se o último gate precisa antes de revisão.
- Não gerar prompt de promoção se DSR honesto continua 0.0.
- Não tratar PASS_ZERO_EXPOSURE de CVaR como robustez econômica completa.
- Não tratar clone local reexecutado como clone limpo isolado.
- Não ignorar falhas de pytest por dependência ausente.
- Não reabrir A3/A4.
- Não transformar ALIVE_BUT_NOT_PROMOTABLE em promotable.
- Não apagar artifacts.
- Não commitar automaticamente.
- Se houver dúvida entre avançar e revisar, escolher revisar.
- Se houver dúvida entre implementar e auditar, escolher auditar.
- Se houver dúvida entre PASS e PARTIAL, escolher PARTIAL.
- Se artifacts estiverem ausentes, classificar como INCONCLUSIVE.

Formato da resposta final desta skill:
1. Estado atual classificado.
2. Arquivos lidos.
3. Skill recomendada agora.
4. Justificativa objetiva.
5. Prompt pronto para copiar e colar.
6. Arquivos criados/atualizados.
7. Próxima ação do usuário.
