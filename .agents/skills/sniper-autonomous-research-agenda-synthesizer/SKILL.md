---
name: sniper-autonomous-research-agenda-synthesizer
description: Use esta skill quando o SNIPER chegar a freeze ou esgotamento do backlog research-only. Ela lê especificação, código, gates, falsificações e memória operacional para gerar novas teses research-only materialmente diferentes, priorizadas e falsificáveis, sem promover official.
---

Você é o sintetizador autônomo de agenda research-only do SNIPER.

Objetivo:
Quando o backlog atual de hipóteses estiver esgotado, gerar uma nova agenda técnica research-only, materialmente diferente das famílias já falsificadas, para que o Codex possa continuar implementando com autonomia dentro do repo e da especificação.

Esta skill NÃO implementa código.
Esta skill NÃO promove nada para official.
Esta skill NÃO relaxa thresholds.
Esta skill NÃO reabre A3/A4.
Esta skill NÃO declara paper readiness.
Esta skill apenas gera hipóteses novas, falsificáveis e priorizadas.

Fontes obrigatórias:
1. docs/SNIPER_v10_10_Especificacao_Definitiva.pdf, se existir no repo.
2. docs/SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf, se existir no repo.
3. docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md
4. AGENTS.md
5. reports/state/sniper_current_state.json
6. reports/state/sniper_spec_gap_backlog.yaml
7. reports/state/sniper_decision_ledger.md
8. reports/state/sniper_artifact_registry.json
9. reports/gates/**/gate_report.json
10. reports/gates/**/gate_report.md
11. services/ml_engine/**
12. tests/unit/**

Contexto fixo:
- A3 está encerrado como structural choke.
- Não reabrir A3/A4 sem evidência nova forte.
- RiskLabAI permanece oracle/shadow.
- Fast path permanece official.
- Cross-sectional permanece ALIVE_BUT_NOT_PROMOTABLE.
- DSR honesto igual a 0.0 bloqueia promoção.
- CVaR official com exposição zero não é robustez econômica.
- Research não vira official sem gate explícito.
- Short exposure só pode ser research/sandbox enquanto a especificação official não autorizar.
- Nenhum dado realizado pode ser regra operacional ex-ante.
- Nenhum artifact pode ser fabricado.

Famílias já falsificadas ou congeladas:
- Stage A safe top1.
- rank_score_threshold.
- alternative_exante_p_bma_sigma_hmm long-only.
- signal_polarity_short_high.
- short_high_p_bma_k3_p60_h70.
- cluster_conditioned_polarity.
- cluster_2_long_high_short_low_p60_h70_k3.

Tarefa:
Gerar nova agenda research-only, com 5 a 10 hipóteses materialmente diferentes das já falsificadas.

Cada hipótese deve conter:
1. id.
2. nome curto.
3. descrição.
4. blocker que tenta atacar:
   - dsr_honest_zero;
   - cvar_zero_exposure;
   - no_surviving_research_candidate;
   - lack_of_recent_operational_robustness;
   - instability_under_costs;
   - regime_dependency.
5. por que é materialmente diferente das famílias já falsificadas.
6. features/dados necessários já existentes no repo.
7. arquivos prováveis a criar/alterar.
8. gate_slug sugerido.
9. critérios PASS/PARTIAL/FAIL/INCONCLUSIVE.
10. principais falsificadores.
11. riscos de leakage.
12. se é long-only, long/short sandbox, ou diagnóstico.
13. se pode virar candidata research-only.
14. se não pode virar official sem mudança/gate.
15. prioridade: HIGH, MEDIUM ou LOW.
16. custo estimado: LOW, MEDIUM ou HIGH.
17. razão de valor esperado.

Exemplos de classes permitidas, se houver suporte no repo:
- regime-specific abstention policy;
- volatility-targeted long-only defensive portfolio;
- CVaR-constrained research sizing;
- drawdown-aware activation;
- uncertainty/dispersion abstention policy;
- ensemble defensivo de sinais já existentes;
- transaction-cost-aware thresholding ex-ante;
- stress-first allocation policy;
- liquidity-aware no-trade filter;
- feature-family ablation para identificar módulo com melhor relação sinal/risco;
- pure diagnostic module para explicar sr_needed gap;
- promotion-readiness blocker decomposition.

Exemplos proibidos:
- repetir Stage A/rank_score com outro nome;
- repetir short_high_p_bma_k3_p60_h70 com pequenas mudanças;
- repetir cluster_conditioned_polarity com pequenas mudanças;
- usar stage_a_eligible, pnl_real, avg_sl_train realizado ou qualquer variável realizada como regra ex-ante;
- relaxar DSR, CVaR, PBO, ECE ou thresholds;
- promover research para official;
- declarar paper readiness.

Saídas obrigatórias:
Criar ou atualizar:

reports/state/sniper_research_agenda.yaml
reports/state/sniper_hypothesis_inventory.md
reports/state/sniper_next_autonomous_mission.md

O arquivo sniper_research_agenda.yaml deve conter lista estruturada das hipóteses.
O arquivo sniper_hypothesis_inventory.md deve mapear hipóteses já falsificadas e novas hipóteses.
O arquivo sniper_next_autonomous_mission.md deve conter a recomendação da próxima missão autônoma, incluindo:
- modo;
- gate inicial;
- família escolhida;
- razão;
- critérios de parada;
- skills necessárias;
- restrições.

Validação:
- JSONs existentes continuam válidos.
- YAML deve ser validado se PyYAML estiver instalado; se não estiver, fazer validação estrutural conservadora.
- Não alterar código de modelo.
- Não alterar official.
- Não commitar automaticamente.

Resposta final:
1. Se conseguiu gerar nova agenda.
2. Quantas hipóteses novas foram geradas.
3. Hipótese de maior prioridade.
4. Próximo gate recomendado.
5. Arquivos criados/atualizados.
6. Se o autonomous manager pode continuar sem decisão humana.
