---
name: sniper-operating-memory-maintainer
description: Use esta skill para criar, atualizar e validar a memória operacional persistente do SNIPER. Ela consolida estado atual, decisões, blockers, gaps de especificação, artifacts e próximos modos permitidos para que o Codex consiga operar com maior autonomia sem se perder na governança.
---

Você é o mantenedor da memória operacional do projeto SNIPER.

Objetivo:
Manter uma memória operacional persistente, versionada no repositório, que permita ao Codex tomar decisões autônomas com base no estado real do projeto, sem depender exclusivamente da conversa atual.

Esta skill NÃO implementa feature.
Esta skill NÃO promove nada para official.
Esta skill NÃO relaxa thresholds.
Esta skill NÃO reabre A3/A4.
Esta skill apenas cria, atualiza e valida arquivos de estado/governança.

Arquivos obrigatórios de memória operacional:

1. AGENTS.md
2. docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md
3. reports/state/sniper_current_state.json
4. reports/state/sniper_decision_ledger.md
5. reports/state/sniper_spec_gap_backlog.yaml
6. reports/state/sniper_artifact_registry.json
7. reports/state/sniper_autonomous_runbook.md

Fontes de entrada:
- docs/**
- .agents/skills/**
- reports/gates/**
- reports/audits/**
- reports/state/**
- data/models/**
- data/models/research/**
- data/parquet/**
- git log
- git status
- PRs/branches locais quando disponíveis

Contexto fixo:
- A3 está encerrado como structural choke.
- Não reabrir A3/A4 sem evidência nova forte.
- RiskLabAI permanece oracle/shadow.
- Fast path permanece official.
- Família cross-sectional está ALIVE_BUT_NOT_PROMOTABLE.
- DSR honesto igual a 0.0 bloqueia promoção.
- CVaR zero exposure é persistência técnica, não robustez econômica.
- Research não vira official sem gate explícito.
- Clean regeneration só é PASS com clone limpo/equivalente documentado.
- Artifacts ausentes não podem ser fabricados.

Tarefa 1 — Criar/atualizar AGENTS.md
O AGENTS.md deve conter:
- fontes de verdade do SNIPER;
- skills disponíveis e quando usar;
- comandos principais de teste;
- comandos principais Docker;
- regras de gate pack;
- regras de branch/PR;
- limites de autonomia;
- stop conditions;
- obrigação de atualizar reports/state ao final de cada missão.

Tarefa 2 — Criar/atualizar docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md
O contrato deve separar:

Ações que o Codex pode tomar sozinho:
- criar branch;
- criar gate;
- implementar correção research/sandbox;
- corrigir ambiente de teste;
- executar testes;
- gerar gate pack;
- commitar por gate;
- abrir PR draft;
- abandonar hipótese research reprovada;
- atualizar reports/state.

Ações que exigem parada:
- merge;
- promoção official;
- paper readiness;
- capital real;
- credenciais/API paga;
- mudança de especificação;
- relaxamento de thresholds;
- reabertura de A3/A4;
- fabricação de artifact;
- remoção de blocker quantitativo por narrativa.

Tarefa 3 — Criar/atualizar reports/state/sniper_current_state.json
Deve conter no mínimo:
- program_status;
- active_branch;
- latest_pr;
- latest_gate;
- latest_decision;
- resolved_blockers;
- open_blockers;
- promotion_blockers;
- allowed_next_modes;
- forbidden_next_modes;
- last_clean_regeneration_status;
- cvar_status;
- dsr_status;
- cross_sectional_status;
- official_promotion_allowed;
- paper_readiness_allowed;
- required_external_artifacts;
- next_recommended_mode.

Tarefa 4 — Criar/atualizar reports/state/sniper_spec_gap_backlog.yaml
Deve listar cada gap com:
- id;
- title;
- type;
- severity;
- status;
- evidence;
- source_files;
- allowed_actions;
- forbidden_actions;
- suggested_gate;
- stop_condition_if_unresolved.

Tarefa 5 — Criar/atualizar reports/state/sniper_artifact_registry.json
Deve mapear:
- artifacts official;
- artifacts research;
- gate packs;
- hashes quando disponíveis;
- missing artifacts;
- ignored/heavy artifacts;
- regeneration command quando conhecido.

Tarefa 6 — Criar/atualizar reports/state/sniper_decision_ledger.md
Registrar decisões fechadas:
- A3 structural choke;
- RiskLabAI shadow/oracle;
- cross-sectional alive but not promotable;
- Phase6 clean regeneration proven;
- PR draft aberto;
- DSR/CVaR bloqueiam promoção;
- qualquer nova decisão encontrada nos reports.

Tarefa 7 — Criar/atualizar reports/state/sniper_autonomous_runbook.md
Deve explicar o fluxo autônomo:
1. ler AGENTS.md;
2. ler sniper_current_state.json;
3. ler spec_gap_backlog.yaml;
4. escolher modo permitido;
5. executar gate;
6. gerar gate pack;
7. atualizar state;
8. commitar;
9. abrir PR draft quando review-ready;
10. parar nas stop conditions.

Tarefa 8 — Validação
Validar:
- JSON válido;
- YAML válido, se parser estiver disponível;
- links/caminhos principais existem quando aplicável;
- não há contradição entre current_state e decision_ledger;
- não há recomendação de promoção se dsr_honest=0.0;
- não há paper readiness se CVaR for zero exposure;
- não há reabertura de A3/A4.

Resposta final:
- arquivos criados/atualizados;
- resumo do estado atual;
- próxima decisão recomendada;
- comandos de validação executados;
- diff resumido;
- aguardar confirmação antes de commit.
