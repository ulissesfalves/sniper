---
name: sniper-next-step-prompt-builder
description: Use esta skill depois da skill sniper-global-spec-adherence-audit. Ela lê reports/audits/global_spec_adherence/next_step_recommendation.md e arquivos auxiliares da auditoria global para gerar um prompt pronto para copiar e colar no Codex, orientando a implementação do próximo gate do SNIPER com governança, restrições, artifacts esperados e critérios de PASS/FAIL/PARTIAL.
---

Você é o construtor de prompts de continuidade operacional do SNIPER.

Objetivo:
Ler a recomendação de próximo passo gerada pela auditoria global do SNIPER e transformá-la em um prompt completo, claro, restritivo e pronto para copiar e colar no Codex para executar o próximo gate.

Esta skill NÃO implementa código.
Esta skill NÃO corrige o repositório.
Esta skill NÃO promove nada para official.
Esta skill apenas gera um prompt operacional para a próxima rodada.

Arquivos de entrada principais:
1. reports/audits/global_spec_adherence/next_step_recommendation.md

Arquivos auxiliares, se existirem:
2. reports/audits/global_spec_adherence/global_spec_adherence_summary.json
3. reports/audits/global_spec_adherence/global_implementation_checklist.md
4. reports/audits/global_spec_adherence/global_spec_adherence_report.md
5. reports/audits/global_spec_adherence/global_spec_adherence_matrix.csv

Contexto fixo do SNIPER:
- Repo local: C:\Users\uliss\Documentos\Meus_projetos\sniper
- Branch base recomendada: codex/openclaw-sniper-handoff, salvo se o next_step_recommendation.md indicar outra base.
- A3 está encerrado como structural choke.
- Não reabrir A3/A4 sem evidência nova forte.
- RiskLabAI permanece oracle/shadow, não official.
- Fast path permanece official.
- Família cross-sectional soberana está ALIVE_BUT_NOT_PROMOTABLE.
- Baseline research-only correta: phase5_cross_sectional_sovereign_closure_restored.
- Não promover research para official sem gate explícito.
- Não tratar melhoria parcial como aprovação se DSR honesto continuar 0.0.
- Não usar narrativa para compensar ausência de artifact.
- Se artifacts necessários estiverem ausentes, classificar como INCONCLUSIVE.

Tarefa:
Gerar um prompt completo para o próximo ciclo do Codex com base no conteúdo de next_step_recommendation.md.

O prompt gerado deve conter obrigatoriamente:

1. Skill recomendada para ativar no início do prompt:
   - Sempre incluir $sniper-gate-governance.
   - Incluir $sniper-quant-research-implementation se o próximo passo envolver modelo, feature, CPCV, DSR, PBO, ECE, CVaR, calibration, ranking ou research quantitativo.
   - Incluir $sniper-paper-execution-hardening se o próximo passo envolver bridge, Nautilus, Redis Streams, daemon, paper, snapshot, idempotência ou execução.
   - Se a tarefa for apenas auditoria/freeze/handoff, usar somente $sniper-gate-governance.

2. Cabeçalho do prompt:
   - Repo local.
   - Branch base.
   - Nome do próximo gate.
   - Branch sugerida.
   - Objetivo da rodada.
   - Escopo.
   - Fora de escopo.

3. Fontes de verdade:
   - Estado atual do repositório.
   - Documentação canônica em docs/.
   - Relatórios da auditoria global.
   - reports/gates/**.
   - data/models/**, data/models/research/** e data/parquet/** quando aplicável.

4. Resumo da auditoria global:
   - Veredito global.
   - Top blockers.
   - Lacunas que motivam o próximo gate.
   - O que já está satisfatório e não deve ser refeito sem necessidade.

5. Instruções de execução:
   - Criar ou trocar para a branch sugerida.
   - Ler documentação e artifacts relevantes.
   - Fazer plano curto antes de alterar código.
   - Implementar apenas o necessário para o gate.
   - Gerar artifacts esperados.
   - Gerar relatório de gate.
   - Rodar testes e validações.
   - Não promover nada para official.

6. Entregáveis esperados:
   - reports/gates/<gate_slug>/gate_report.json
   - reports/gates/<gate_slug>/gate_report.md
   - reports/gates/<gate_slug>/gate_manifest.json
   - reports/gates/<gate_slug>/gate_metrics.parquet, se houver métrica tabular
   - artifacts adicionais indicados pelo next_step_recommendation.md
   - qualquer relatório específico do gate

7. Critérios objetivos:
   - PASS
   - PARTIAL
   - FAIL
   - INCONCLUSIVE, quando artifacts ou evidências forem insuficientes

8. Comandos esperados:
   - git status
   - git branch --show-current
   - git checkout -b <branch_sugerida> ou git checkout <branch_existente>
   - comandos de teste aplicáveis
   - comandos de validação dos artifacts
   - comandos Docker, se o gate tocar execução/paper
   - comandos de inspeção dos relatórios

9. Restrições:
   - Não reabrir A3/A4.
   - Não promover para official.
   - Não mascarar DSR=0.0.
   - Não tratar ALIVE_BUT_NOT_PROMOTABLE como promotable.
   - Não apagar artifacts sem registrar motivo.
   - Não usar informação realizada para decisão ex-ante.
   - Não criar gate PASS sem evidência reproduzível.
   - Não commitar reports pesados sem instrução explícita.

10. Resposta final esperada do Codex:
   - Veredito do gate.
   - Métricas principais.
   - Arquivos alterados.
   - Artifacts criados.
   - Comandos executados.
   - Próximo passo recomendado.
   - Blocos de risco/remediação.

Formato de saída desta skill:
1. Primeiro, informe quais arquivos de auditoria foram lidos.
2. Depois, gere o prompt em bloco único, pronto para copiar e colar.
3. No final, salve também o prompt gerado em:
   reports/audits/global_spec_adherence/next_step_codex_prompt.md

Regras de qualidade:
- O prompt deve ser prescritivo, não genérico.
- O prompt deve carregar o gate_slug e branch recomendados a partir do next_step_recommendation.md.
- O prompt deve transformar lacunas em tarefas verificáveis.
- O prompt deve exigir evidência, artifacts e critérios de aceite.
- O prompt não deve pedir implementação ampla e aberta.
- Se next_step_recommendation.md estiver ausente, não inventar: reportar erro e pedir para rodar sniper-global-spec-adherence-audit primeiro.
- Se houver conflito entre summary.json e next_step_recommendation.md, priorizar next_step_recommendation.md e registrar o conflito.
