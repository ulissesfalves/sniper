---
name: sniper-global-spec-adherence-audit
description: Use esta skill para auditar globalmente o projeto SNIPER contra sua documentação e especificação. Deve ser usada quando o usuário pedir leitura da documentação, avaliação de aderência do código, veredito global, checklist de implementação, gaps, riscos ou próximo passo. Não deve implementar correções nesta rodada, exceto criação de relatórios de auditoria.
---

Você é o auditor técnico global do projeto SNIPER.

Objetivo:
Auditar a aderência entre a documentação/especificação do SNIPER e o código atual do repositório, sem implementar correções funcionais. A saída deve ser um diagnóstico honesto, reproduzível e acionável.

Repo esperado:
C:\Users\uliss\Documentos\Meus_projetos\sniper

Branch de partida recomendada:
codex/openclaw-sniper-handoff

Fontes de verdade, nesta ordem:
1. Estado atual do repositório.
2. Documentação canônica em docs/, especialmente:
   - SNIPER_v10_10_Especificacao_Definitiva.pdf
   - SNIPER_v10.10_Technical_Architecture_presentation.pdf
   - SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf
   - SNIPER_openclaw_handoff.md
   - SNIPER_regeneration_guide.md
   - documentos de checklist, memória, handoff e continuidade do SNIPER, se existirem.
3. Relatórios e manifests em:
   - reports/gates/**
   - data/models/**
   - data/models/research/**
   - data/parquet/**
4. Histórico consolidado:
   - A3 está encerrado como structural choke.
   - Não reabrir A3/A4 sem evidência nova forte.
   - RiskLabAI permanece oracle/shadow, não official.
   - Fast path permanece official.
   - Família cross-sectional soberana está ALIVE_BUT_NOT_PROMOTABLE.
   - Baseline research-only correta: phase5_cross_sectional_sovereign_closure_restored.
   - Próxima decisão deve ser baseada em evidência reproduzível, não em hipótese narrativa.

TAREFA 1 — Ler documentação e especificação
Leia toda a documentação relevante do SNIPER existente no repositório. Extraia os requisitos técnicos e operacionais em uma matriz organizada por tema, incluindo no mínimo:

1. Universo point-in-time e anti-survivorship.
2. Feature engineering:
   - Fracdiff/log-space/expanding/tau.
   - Retornos, volatilidade, volume, derivados e macro features.
   - unlock_pressure_rank observado/reconstruído/proxy.
3. Filtro de regime:
   - Winsorização.
   - RobustScaler.
   - PCA walk-forward.
   - HMM.
4. Triple-barrier e market impact:
   - HLC.
   - slippage por raiz quadrada.
   - sigma intraday.
5. Meta-labeling:
   - P_bma purged.
   - uniqueness.
   - sample weights.
   - CPCV.
6. Calibração:
   - isotonic.
   - pooling.
   - time-decay.
   - ECE/reliability.
7. Métricas e gates:
   - DSR honesto.
   - PBO.
   - N_eff.
   - Sharpe OOS.
   - subperíodos.
   - C2ST/drift.
8. Portfolio/risk:
   - Kelly/CVaR.
   - stress rho=1.
   - drawdown.
   - sizing.
9. Bridge/paper/Nautilus:
   - snapshot.
   - Redis Streams.
   - idempotência.
   - stale snapshot.
   - daemon.
   - status terminal.
10. Governança:
   - official vs research vs sandbox.
   - gate reports.
   - manifests.
   - regeneração.
   - handoff.

TAREFA 2 — Auditar o código contra a especificação
Percorra o código do repositório e avalie a aderência com a especificação. Para cada requisito encontrado na documentação, classifique:

- SATISFATÓRIO: implementado e coerente com a especificação.
- PARCIAL: existe implementação, mas com lacunas, risco ou evidência incompleta.
- NÃO IMPLEMENTADO: especificado, mas não encontrado no código.
- DIVERGENTE: código existe, mas contradiz a especificação.
- NÃO APLICÁVEL / OBSOLETO: requisito superado por decisão documentada.

Para cada item, informe:
- requisito;
- fonte documental;
- arquivos de código relacionados;
- evidência objetiva no código;
- status;
- risco;
- recomendação.

Não faça julgamento por impressão. Sempre que possível, cite caminhos de arquivos, nomes de funções, classes, scripts, variáveis de ambiente, artifacts ou reports.

TAREFA 3 — Veredito resumido
Ao final, forneça um veredito executivo curto, com uma das classificações:

- GLOBAL_PASS: o SNIPER está aderente o suficiente para seguir para implementação/paper.
- GLOBAL_PARTIAL: há base sólida, mas ainda existem lacunas relevantes.
- GLOBAL_FAIL: há divergências estruturais que bloqueiam avanço.
- GLOBAL_INCONCLUSIVE: faltam artifacts ou evidências para concluir.

O veredito deve conter:
1. Estado geral do SNIPER.
2. Principais partes satisfatórias.
3. Principais lacunas.
4. Principais riscos.
5. Se o projeto pode ou não avançar para a próxima fase.
6. O que não deve ser reaberto.

TAREFA 4 — Checklist global de implementação do SNIPER
Crie um checklist global, estruturado por fases, contendo tudo que é necessário para implementar o SNIPER até o fim.

Separar no mínimo:

FASE 0 — Fundação, repo, ambiente e reprodutibilidade.
FASE 1 — Dados, universo point-in-time e anti-survivorship.
FASE 2 — Feature store e feature engineering.
FASE 3 — Regime, labels, meta-labeling e calibração.
FASE 4 — CPCV, gates estatísticos e decision-space.
FASE 5 — Hardening quantitativo.
FASE 6 — Bridge paper/Nautilus e execução contínua.
FASE 7 — Paper/testnet prolongado.
FASE 8 — Readiness para capital real, se e somente se todos os gates passarem.

Para cada fase, listar:
- item;
- status atual;
- evidência;
- pendência;
- risco;
- prioridade;
- próximo comando ou próxima ação sugerida.

Destaque explicitamente:
1. O que já foi feito e está satisfatório.
2. O que foi feito, mas foi reprovado no mérito.
3. O que está parcial/inconclusivo.
4. O que ainda falta implementar.
5. O que está bloqueando avanço operacional.
6. O que é apenas research e não pode ser tratado como official.

TAREFA 5 — Sugestão do próximo passo
Com base na auditoria, sugira o próximo passo mais eficiente e seguro.

A sugestão deve conter:
1. Nome sugerido para o próximo gate.
2. Objetivo do gate.
3. Branch sugerida.
4. Arquivos que provavelmente serão alterados.
5. Artifacts esperados.
6. Critérios objetivos de PASS/FAIL/PARTIAL.
7. Comandos para executar localmente.
8. Riscos que precisam ser controlados.
9. O que não deve ser feito nesta próxima rodada.

Restrições:
- Não implementar correções funcionais nesta rodada.
- Não promover nada para official.
- Não reabrir A3/A4.
- Não tratar ALIVE_BUT_NOT_PROMOTABLE como promotável.
- Não usar narrativa para compensar falta de evidência.
- Se encontrar inconsistência entre documentação e código, registrar como gap.
- Se a documentação estiver ambígua, registrar a ambiguidade e propor resolução.
- Se artifacts necessários estiverem ausentes, classificar como INCONCLUSIVE, não como PASS.

Entregáveis obrigatórios:
Criar a pasta:

reports/audits/global_spec_adherence/

Com os arquivos:

1. global_spec_adherence_report.md
2. global_spec_adherence_matrix.csv
3. global_spec_adherence_summary.json
4. global_implementation_checklist.md
5. next_step_recommendation.md

Resposta final no Codex:
Forneça um resumo curto com:
- Veredito.
- Top 5 partes satisfatórias.
- Top 5 lacunas.
- Próximo passo recomendado.
- Arquivos criados.
- Comandos executados.
