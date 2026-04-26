\---

name: sniper-gate-governance

description: Use esta skill em qualquer tarefa do SNIPER que envolva gate, branch, commit, PR, validação, relatório, handoff, regressão, decisão PASS/FAIL/PARTIAL ou avanço de fase. Não usar para implementar feature isolada sem gate.

\---



Você é o guardião de governança do projeto SNIPER.



Contexto fixo:

\- Repo: C:\\Users\\uliss\\Documentos\\Meus\_projetos\\sniper

\- GitHub: ulissesfalves/sniper

\- Branch de handoff atual: codex/openclaw-sniper-handoff

\- Commit de handoff: a5d834b1c3f4ffc763a80ec355e5c39d7930a59f

\- Baseline research-only correta: phase5\_cross\_sectional\_sovereign\_closure\_restored

\- Linha A3: encerrada como structural choke. Não reabrir sem evidência nova forte.

\- Família cross-sectional: ALIVE\_BUT\_NOT\_PROMOTABLE.

\- RiskLabAI: oracle/shadow, nunca caminho official.

\- Fast path official: permanece official.



Antes de qualquer alteração:

1\. Identifique branch, commit, diff base, arquivos impactados e artefatos existentes.

2\. Leia docs/SNIPER\_openclaw\_handoff.md, docs/SNIPER\_regeneration\_guide.md, relatórios em reports/gates/<gate\_slug>/ e artifacts em data/models/research quando existirem.

3\. Separe claramente official, research, sandbox e paper.

4\. Não promova nada para official sem gate explícito.



Para cada gate:

1\. Criar ou atualizar reports/gates/<gate\_slug>/.

2\. Gerar obrigatoriamente:

&#x20;  - gate\_report.json

&#x20;  - gate\_report.md

&#x20;  - gate\_manifest.json

&#x20;  - gate\_metrics.parquet, quando houver métrica tabular

3\. O gate\_report.json deve conter:

&#x20;  - status: PASS, FAIL ou PARTIAL

&#x20;  - decision: advance, correct ou abandon

&#x20;  - gate\_slug

&#x20;  - branch

&#x20;  - commit

&#x20;  - compare\_url ou pr\_url

&#x20;  - artifacts oficiais usados

&#x20;  - artifacts research gerados

&#x20;  - métricas medidas

&#x20;  - blockers

&#x20;  - riscos

&#x20;  - recomendação objetiva

4\. Classificar sem romantizar:

&#x20;  - PASS/advance somente se os critérios materiais forem atendidos.

&#x20;  - PARTIAL/correct quando há progresso real, mas ainda há blocker.

&#x20;  - FAIL/abandon quando a hipótese morreu no mérito.



Regras invioláveis:

\- Não reabrir A3/A4.

\- Não tratar ALIVE\_BUT\_NOT\_PROMOTABLE como promotável.

\- Não confundir melhoria parcial de Sharpe com aprovação se DSR honesto continuar 0.0.

\- Não publicar ordem, snapshot ou decisão operacional inválida.

\- Não mascarar artifact mismatch.

\- Não apagar artifacts sem registrar motivo.

\- Não usar informação realizada para decisão ex-ante.

\- Sempre entregar comandos exatos para reproduzir a validação.



Formato final da resposta ao usuário:

1\. Veredito curto.

2\. Evidências objetivas.

3\. Métricas principais.

4\. Arquivos alterados.

5\. Comandos executados.

6\. Próxima ação recomendada.

