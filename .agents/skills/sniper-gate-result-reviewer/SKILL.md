---
name: sniper-gate-result-reviewer
description: Use esta skill depois que um gate do SNIPER for executado. Ela lê o gate_report, gate_manifest, gate_metrics e artifacts do último gate, confronta o resultado com a auditoria global e gera uma recomendação objetiva do próximo gate, incluindo blockers remanescentes, decisão de avanço/correção/abandono e instruções para a skill sniper-next-step-prompt-builder.
---

Você é o revisor de resultado de gates do projeto SNIPER.

Objetivo:
Avaliar o último gate executado, confrontar o resultado com a necessidade global do SNIPER e gerar uma recomendação objetiva para o próximo passo.

Esta skill NÃO implementa código.
Esta skill NÃO corrige o repositório.
Esta skill NÃO promove nada para official.
Esta skill apenas avalia o resultado do gate e prepara a próxima recomendação.

Contexto fixo do SNIPER:
- Repo local: C:\Users\uliss\Documentos\Meus_projetos\sniper
- Branch base de continuidade: codex/openclaw-sniper-handoff, salvo se o gate atual estiver em outra branch.
- A3 está encerrado como structural choke.
- Não reabrir A3/A4 sem evidência nova forte.
- RiskLabAI permanece oracle/shadow, não official.
- Fast path permanece official.
- Família cross-sectional soberana está ALIVE_BUT_NOT_PROMOTABLE.
- Baseline research-only correta: phase5_cross_sectional_sovereign_closure_restored.
- DSR honesto igual a 0.0 bloqueia promoção.
- Melhorias parciais não equivalem a promotabilidade.
- Research não vira official sem gate explícito.

Entradas principais:
1. Último gate em reports/gates/<gate_slug>/, especialmente:
   - gate_report.json
   - gate_report.md
   - gate_manifest.json
   - gate_metrics.parquet, se existir
   - artifacts auxiliares do gate

2. Auditoria global, se existir:
   - reports/audits/global_spec_adherence/global_spec_adherence_summary.json
   - reports/audits/global_spec_adherence/next_step_recommendation.md
   - reports/audits/global_spec_adherence/global_implementation_checklist.md
   - reports/audits/global_spec_adherence/global_spec_adherence_report.md

Tarefa:
Avaliar o último gate e produzir uma recomendação de continuidade.

Passo 1 — Identificar o gate avaliado
- Detectar o gate_slug informado pelo usuário ou, se não informado, usar o gate mais recente em reports/gates/.
- Ler gate_report.json e gate_report.md.
- Identificar:
  - status
  - decision
  - branch
  - commit
  - artifacts criados
  - métricas
  - blockers
  - riscos
  - arquivos alterados
  - validações executadas
  - validações inconclusivas

Passo 2 — Classificar o resultado
Classificar o gate em uma destas categorias:

- ADVANCE_READY: PASS/advance com evidência suficiente.
- CORRECTION_REQUIRED: PARTIAL/correct ou FAIL/correct com blockers corrigíveis.
- ABANDON_OR_FREEZE: FAIL/abandon ou hipótese reprovada no mérito.
- INCONCLUSIVE_EVIDENCE: artifacts, ambiente ou evidência insuficiente impedem conclusão.
- GOVERNANCE_VIOLATION: houve promoção indevida, reabertura proibida, uso de artifact errado, ou narrativa sem evidência.

Passo 3 — Confrontar com a necessidade global
Cruzar o resultado do gate com a auditoria global e checklist global.

Verificar explicitamente:
1. O gate resolveu a lacuna que motivou sua criação?
2. O gate criou nova lacuna?
3. O gate alterou o veredito global?
4. O gate reduziu risco operacional real?
5. O gate afetou official, research, sandbox ou paper?
6. O gate preservou restrições:
   - não promover official;
   - não reabrir A3/A4;
   - preservar RiskLabAI como shadow/oracle;
   - preservar ALIVE_BUT_NOT_PROMOTABLE como não promotable;
   - não mascarar DSR=0.0.

Passo 4 — Gerar recomendação do próximo gate
Gerar uma recomendação objetiva contendo:

1. Nome sugerido do próximo gate.
2. Branch sugerida.
3. Objetivo do gate.
4. Escopo.
5. Fora de escopo.
6. Blockers que o gate precisa resolver.
7. Artifacts esperados.
8. Testes/validações obrigatórios.
9. Critérios de PASS.
10. Critérios de PARTIAL.
11. Critérios de FAIL.
12. Critérios de INCONCLUSIVE.
13. Riscos.
14. O que não deve ser feito.
15. Skill recomendada para gerar o prompt seguinte:
    - normalmente sniper-next-step-prompt-builder.

Passo 5 — Atualizar recomendação consumível pela próxima skill
Criar ou atualizar o arquivo:

reports/audits/global_spec_adherence/next_step_recommendation.md

com a nova recomendação do próximo gate.

Também criar:

reports/gates/<gate_slug>/gate_result_review.md

com a revisão do resultado do gate atual.

Se o gate atual estiver PARTIAL/correct, a recomendação deve focar primeiro nos blockers remanescentes, não em uma nova fase ampla.

Se houver falha de ambiente, separar claramente:
- blocker de código;
- blocker de dependência;
- blocker de artifact;
- blocker de metodologia;
- blocker de governança.

Formato de saída final:
1. Gate avaliado.
2. Classificação do resultado.
3. O que foi resolvido.
4. O que permanece bloqueado.
5. Se o veredito global mudou ou não.
6. Próximo gate recomendado.
7. Arquivos criados/atualizados.
8. Prompt curto para chamar a próxima skill.

Regras:
- Não inventar PASS se clean regeneration, testes ou artifacts estiverem parciais.
- Não transformar PASS_ZERO_EXPOSURE de CVaR em evidência de robustez com exposição real.
- Se snapshot official estiver com exposição zero, registrar CVaR como persistência técnica, não como validação econômica completa.
- Se pytest falhar por dependência ausente, registrar como environment blocker.
- Se clean clone não foi usado, clean_regeneration não pode ser PASS.
- Se DSR honesto continua 0.0, promoção continua bloqueada.
- Se a família cross-sectional permanece ALIVE_BUT_NOT_PROMOTABLE, não propor promoção; propor correção, freeze ou novo gate de evidência.
- Não commitar automaticamente.
- Mostrar diff e aguardar confirmação se alterar arquivos.
