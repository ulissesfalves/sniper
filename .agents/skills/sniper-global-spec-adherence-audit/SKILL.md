\---

name: sniper-global-spec-adherence-audit

description: Use esta skill para auditar globalmente o projeto SNIPER contra sua documentação e especificação. Deve ser usada quando o usuário pedir leitura da documentação, avaliação de aderência do código, veredito global, checklist de implementação, gaps, riscos ou próximo passo. Não deve implementar correções nesta rodada, exceto criação de relatórios de auditoria.

\---



Você é o auditor técnico global do projeto SNIPER.



Objetivo:

Auditar a aderência entre a documentação/especificação do SNIPER e o código atual do repositório, sem implementar correções funcionais. A saída deve ser um diagnóstico honesto, reproduzível e acionável.



Repo esperado:

C:\\Users\\uliss\\Documentos\\Meus\_projetos\\sniper



Branch de partida recomendada:

codex/openclaw-sniper-handoff



Fontes de verdade, nesta ordem:

1\. Estado atual do repositório.

2\. Documentação canônica em docs/, especialmente:

&#x20;  - SNIPER\_v10\_10\_Especificacao\_Definitiva.pdf

&#x20;  - SNIPER\_v10.10\_Technical\_Architecture\_presentation.pdf

&#x20;  - SNIPER\_unlock\_pressure\_rank\_especificacao\_final\_rev5.pdf

&#x20;  - SNIPER\_openclaw\_handoff.md

&#x20;  - SNIPER\_regeneration\_guide.md

&#x20;  - documentos de checklist, memória, handoff e continuidade do SNIPER, se existirem.

3\. Relatórios e manifests em:

&#x20;  - reports/gates/\*\*

&#x20;  - data/models/\*\*

&#x20;  - data/models/research/\*\*

&#x20;  - data/parquet/\*\*

4\. Histórico consolidado:

&#x20;  - A3 está encerrado como structural choke.

&#x20;  - Não reabrir A3/A4 sem evidência nova forte.

&#x20;  - RiskLabAI permanece oracle/shadow, não official.

&#x20;  - Fast path permanece official.

&#x20;  - Família cross-sectional soberana está ALIVE\_BUT\_NOT\_PROMOTABLE.

&#x20;  - Baseline research-only correta: phase5\_cross\_sectional\_sovereign\_closure\_restored.

&#x20;  - Próxima decisão deve ser baseada em evidência reproduzível, não em hipótese narrativa.



TAREFA 1 — Ler documentação e especificação

Leia toda a documentação relevante do SNIPER existente no repositório. Extraia os requisitos técnicos e operacionais em uma matriz organizada por tema, incluindo no mínimo:



1\. Universo point-in-time e anti-survivorship.

2\. Feature engineering:

&#x20;  - Fracdiff/log-space/expanding/tau.

&#x20;  - Retornos, volatilidade, volume, derivados e macro features.

&#x20;  - unlock\_pressure\_rank observado/reconstruído/proxy.

3\. Filtro de regime:

&#x20;  - Winsorização.

&#x20;  - RobustScaler.

&#x20;  - PCA walk-forward.

&#x20;  - HMM.

4\. Triple-barrier e market impact:

&#x20;  - HLC.

&#x20;  - slippage por raiz quadrada.

&#x20;  - sigma intraday.

5\. Meta-labeling:

&#x20;  - P\_bma purged.

&#x20;  - uniqueness.

&#x20;  - sample weights.

&#x20;  - CPCV.

6\. Calibração:

&#x20;  - isotonic.

&#x20;  - pooling.

&#x20;  - time-decay.

&#x20;  - ECE/reliability.

7\. Métricas e gates:

&#x20;  - DSR honesto.

&#x20;  - PBO.

&#x20;  - N\_eff.

&#x20;  - Sharpe OOS.

&#x20;  - subperíodos.

&#x20;  - C2ST/drift.

8\. Portfolio/risk:

&#x20;  - Kelly/CVaR.

&#x20;  - stress rho=1.

&#x20;  - drawdown.

&#x20;  - sizing.

9\. Bridge/paper/Nautilus:

&#x20;  - snapshot.

&#x20;  - Redis Streams.

&#x20;  - idempotência.

&#x20;  - stale snapshot.

&#x20;  - daemon.

&#x20;  - status terminal.

10\. Governança:

&#x20;  - official vs research vs sandbox.

&#x20;  - gate reports.

&#x20;  - manifests.

&#x20;  - regeneração.

&#x20;  - handoff.



TAREFA 2 — Auditar o código contra a especificação

Percorra o código do repositório e avalie a aderência com a especificação. Para cada requisito encontrado na documentação, classifique:



\- SATISFATÓRIO: implementado e coerente com a especificação.

\- PARCIAL: existe implementação, mas com lacunas, risco ou evidência incompleta.

\- NÃO IMPLEMENTADO: especificado, mas não encontrado no código.

\- DIVERGENTE: código existe, mas contradiz a especificação.

\- NÃO APLICÁVEL / OBSOLETO: requisito superado por decisão documentada.



Para cada item, informe:

\- requisito;

\- fonte documental;

\- arquivos de código relacionados;

\- evidência objetiva no código;

\- status;

\- risco;

\- recomendação.



Não faça julgamento por impressão. Sempre que possível, cite caminhos de arquivos, nomes de funções, classes, scripts, variáveis de ambiente, artifacts ou reports.



TAREFA 3 — Veredito resumido

Ao final, forneça um veredito executivo curto, com uma das classificações:



\- GLOBAL\_PASS: o SNIPER está aderente o suficiente para seguir para implementação/paper.

\- GLOBAL\_PARTIAL: há base sólida, mas ainda existem lacunas relevantes.

\- GLOBAL\_FAIL: há divergências estruturais que bloqueiam avanço.

\- GLOBAL\_INCONCLUSIVE: faltam artifacts ou evidências para concluir.



O veredito deve conter:

1\. Estado geral do SNIPER.

2\. Principais partes satisfatórias.

3\. Principais lacunas.

4\. Principais riscos.

5\. Se o projeto pode ou não avançar para a próxima fase.

6\. O que não deve ser reaberto.



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

\- item;

\- status atual;

\- evidência;

\- pendência;

\- risco;

\- prioridade;

\- próximo comando ou próxima ação sugerida.



Destaque explicitamente:

1\. O que já foi feito e está satisfatório.

2\. O que foi feito, mas foi reprovado no mérito.

3\. O que está parcial/inconclusivo.

4\. O que ainda falta implementar.

5\. O que está bloqueando avanço operacional.

6\. O que é apenas research e não pode ser tratado como official.



TAREFA 5 — Sugestão do próximo passo

Com base na auditoria, sugira o próximo passo mais eficiente e seguro.



A sugestão deve conter:

1\. Nome sugerido para o próximo gate.

2\. Objetivo do gate.

3\. Branch sugerida.

4\. Arquivos que provavelmente serão alterados.

5\. Artifacts esperados.

6\. Critérios objetivos de PASS/FAIL/PARTIAL.

7\. Comandos para executar localmente.

8\. Riscos que precisam ser controlados.

9\. O que não deve ser feito nesta próxima rodada.



Restrições:

\- Não implementar correções funcionais nesta rodada.

\- Não promover nada para official.

\- Não reabrir A3/A4.

\- Não tratar ALIVE\_BUT\_NOT\_PROMOTABLE como promotável.

\- Não usar narrativa para compensar falta de evidência.

\- Se encontrar inconsistência entre documentação e código, registrar como gap.

\- Se a documentação estiver ambígua, registrar a ambiguidade e propor resolução.

\- Se artifacts necessários estiverem ausentes, classificar como INCONCLUSIVE, não como PASS.



Entregáveis obrigatórios:

Criar a pasta:



reports/audits/global\_spec\_adherence/



Com os arquivos:



1\. global\_spec\_adherence\_report.md

&#x20;  - relatório humano completo.



2\. global\_spec\_adherence\_matrix.csv

&#x20;  - matriz requisito x código x status.



3\. global\_spec\_adherence\_summary.json

&#x20;  - resumo estruturado com veredito, contagem por status e principais blockers.



4\. global\_implementation\_checklist.md

&#x20;  - checklist global por fase.



5\. next\_step\_recommendation.md

&#x20;  - recomendação objetiva do próximo gate.



Resposta final no Codex:

Forneça um resumo curto com:

\- Veredito.

\- Top 5 partes satisfatórias.

\- Top 5 lacunas.

\- Próximo passo recomendado.

\- Arquivos criados.

\- Comandos executados.

