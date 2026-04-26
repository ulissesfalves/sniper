# SNIPER Global Implementation Checklist

Veredito de referência: `GLOBAL_PARTIAL`

Este checklist separa o estado atual por fase e explicita o que está satisfatório, reprovado no mérito, parcial/inconclusivo, faltante, bloqueante e research-only.

## FASE 0 — Fundação, repo, ambiente e reprodutibilidade

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Branch e handoff canônico | SATISFATÓRIO | Branch `codex/openclaw-sniper-handoff`; `docs/SNIPER_openclaw_handoff.md` | Manter handoff atualizado após cada gate | Deriva de contexto entre chats | Alta | `git branch --show-current; git rev-parse HEAD` |
| Regeneration guide | PARCIAL | `docs/SNIPER_regeneration_guide.md` | Provar execução em clone limpo | Artifacts locais podem mascarar dependência | Alta | Rodar restore/revalidate e comparar hashes |
| Separação official/research/sandbox | SATISFATÓRIO | Handoff e `phase4_gate_diagnostic.py` | Automatizar check em todos gates | Mistura manual de artifacts | Alta | Criar assert de origem em manifest |
| PDF Technical Architecture auditável | PARCIAL | Extração textual quase vazia | Renderizar/OCR ou substituir por texto canônico | Requisito documental inconclusivo | Média | OCR/render dedicado do PDF |

## FASE 1 — Dados, universo point-in-time e anti-survivorship

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Universo point-in-time | PARCIAL | `AntiSurvivorshipValidator`; Coingecko market cap histórico | Manifest por `as_of_date` e cobertura | Survivorship bias se manifest faltar | Alta | Gerar `universe_manifest.parquet/json` por data |
| Ativos colapsados | SATISFATÓRIO | `bootstrap_historical.py` marca colapsados obrigatórios | Validar em dataset regenerado | Excluir mortos inflaria backtest | Alta | Testar presença de collapsed assets no parquet |
| OHLCV HLC real | PARCIAL | Coleta OHLCV e fallback aproximado | Medir cobertura de HLC real | Labels/slippage fracos se fallback virar official | Alta | Relatório de cobertura por fonte/símbolo |
| Fonte point-in-time de unlock | PARCIAL | `historical.py` usa captura `<= as_of_date` | Quality report diário atualizado | Lookahead/coverage incompleta | Alta | Reexecutar pipeline unlock quality |

## FASE 2 — Feature store e feature engineering

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Fracdiff log-space/tau/expanding | SATISFATÓRIO | `fracdiff/weights.py`, `transform.py`, `optimal_d.py` | Persistir `d`, tau e janela por fold | Regressão silenciosa de feature | Média | Adicionar manifest de fracdiff por fold |
| Retornos/volatilidade/BTC regime | SATISFATÓRIO | `services/ml_engine/main.py`; `features/volatility.py` | Confirmar cobertura em parquet atual | Buracos de feature viram NaN/fallback | Média | Inventário `feature_coverage_report` |
| Derivativos/macro features | PARCIAL | `main.py` contém hooks/features disponíveis | Provar cobertura efetiva | Feature prometida mas ausente | Média | Relatório de colunas official vs research |
| `unlock_pressure_rank` rev5 | SATISFATÓRIO | `token_unlocks.py`, `store.py`, `utils.py` | Quality report e shadow exit criteria | Tratar proxy/shadow como official | Alta | Verificar unknown/confidence/cobertura |

## FASE 3 — Regime, labels, meta-labeling e calibração

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Winsor/RobustScaler/PCA | SATISFATÓRIO | `regime/winsorizer.py`; `regime/pca_robust.py` | Persistir parâmetros por janela | Leakage se fit não for train-only | Alta | Teste de fit/transform temporal |
| HMM walk-forward | SATISFATÓRIO | `regime/hmm_filter.py` | Diagnóstico por janela em gate | Hindsight ou regime instability | Alta | Emitir `hmm_diagnostics.json` |
| Triple-barrier HLC | SATISFATÓRIO | `triple_barrier/labeler.py` | Samples auditáveis de labels | Erro de prioridade SL/TP | Alta | Exportar amostra label/barrier/slippage |
| Market impact sqrt | SATISFATÓRIO | `triple_barrier/market_impact.py` | Persistir participation/slippage por trade | Backtest subestima custo | Alta | Adicionar slippage report ao gate |
| PBMA purged/uniqueness/sample weights | SATISFATÓRIO | `pbma_purged.py`; `uniqueness.py` | Teste de leakage em gate novo | Overlap inflar performance | Alta | Rodar teste purged/leakage |
| Isotonic/time-decay/ECE | SATISFATÓRIO | `isotonic_calibration.py`; `ISOTONIC_HALFLIFE=180` | Reliability curves por fold | Calibração local sem valor operacional | Média | Exportar reliability/ECE por fold |

## FASE 4 — CPCV, gates estatísticos e decision-space

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| CPCV/PBO/N_eff implementado | SATISFATÓRIO | `meta_labeling/cpcv.py`; `phase4_cpcv.py` | Regenerar artifacts official | Implementado não significa aprovado | Alta | Rodar gate CPCV em clone limpo |
| DSR honesto | REPROVADO NO MÉRITO | `phase4_gate_diagnostic.json` registra `dsr_honest=0.0`, `n_trials_honest=5000` | Corrigir por evidência estrutural, não threshold tweak | Bloqueia promoção | Crítica | Não promover; registrar FAIL |
| Sharpe OOS | REPROVADO NO MÉRITO | Diagnóstico official registra Sharpe OOS `0.3494` para política fallback citada | Nova hipótese causal se houver | Retorno mínimo não atingido | Crítica | Não avançar para paper official |
| Subperíodos | REPROVADO/PARCIAL | Diagnóstico official registra subperíodos insuficientes em caminho bloqueante | Revalidar caminhos e critérios | Instabilidade temporal | Crítica | Persistir subperiod report em novo gate |
| Decision-space | PARCIAL | Docs Fase 4-R e phase5 reports | Alinhar source-doc-artifact | Métrica soberana mal aplicada | Alta | Gate de alignment com asserts |

## FASE 5 — Hardening quantitativo

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| A3 | REPROVADO NO MÉRITO / OBSOLETO | `reports/gates/phase5_stage_a3_*` | Não reabrir sem evidência nova forte | Loop improdutivo | Crítica | Manter fechado |
| Cross-sectional sovereign restored | SATISFATÓRIO como research baseline | `phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate` registra `EXACT_RESTORE` | Não promover | Confundir alive com promotable | Alta | Usar apenas como baseline research |
| Cross-sectional current | PARCIAL | `recent_regime_policy_falsification` registra `ALIVE_BUT_NOT_PROMOTABLE`, DSR 0.0 | Nova hipótese estrutural ou freeze | Otimização narrativa | Crítica | Gate de causalidade/repro antes de tuning |
| RiskLabAI | RESEARCH-ONLY | Handoff: oracle/shadow | Não official | Vazamento de governança | Alta | Manter em sandbox/research |
| CVaR empirical artifact | PARCIAL | Código existe; diagnóstico diz artifact official ausente | Persistir CVaR empírico | Risco não auditável | Alta | Adicionar `portfolio_cvar_report.json` |

## FASE 6 — Bridge paper/Nautilus e execução contínua

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Redis Streams contract | SATISFATÓRIO | `services/nautilus_bridge/config.py`; `contract.py` | Manter versionamento de schema | Quebra consumer/publisher | Média | Rodar testes unitários bridge |
| Snapshot freshness/stale | SATISFATÓRIO | `acceptance.py`; `phase4_publisher.py` | Teste com snapshot real do gate | Operar dado velho | Alta | Testar stale snapshot com artifact atual |
| Idempotência/duplicate/revision | SATISFATÓRIO | `acceptance.py`; `consumer.py` | Teste de replay end-to-end | Ordem duplicada | Alta | Rodar replay bridge |
| Daemon lock/heartbeat/status terminal | SATISFATÓRIO | `run_phase4_paper_daemon.py` | Teste crash/restart prolongado | Loop sem status terminal | Alta | Simular crash/restart |
| Alimentar paper official | BLOQUEADO | Upstream gates falham | Aguardar PASS upstream | Paper com modelo não-promotable | Crítica | Não executar official paper ainda |

## FASE 7 — Paper/testnet prolongado

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Paper prolongado | NÃO IMPLEMENTADO / BLOQUEADO | Bridge existe; upstream não promotable | Gate upstream PASS completo | Execução sem edge validado | Crítica | Aguardar gates quantitativos |
| Monitoramento drift/risk em paper | PARCIAL | Drift/risk modules existem | Integrar report terminal | Falha operacional não detectada | Alta | Definir `paper_monitoring_report.json` |
| Reconciliation realista | PARCIAL | `reconciler.py` existe | Teste com exchange adapter/mock realista | Divergência target/executado | Alta | Rodar replay com status terminal |

## FASE 8 — Readiness para capital real

| Item | Status atual | Evidência | Pendência | Risco | Prioridade | Próximo comando/ação |
|---|---|---|---|---|---|---|
| Todos gates PASS | NÃO IMPLEMENTADO | DSR/Sharpe/subperíodos bloqueiam | PASS reproduzível completo | Perda financeira/violação spec | Crítica | Não preparar capital real |
| CVaR/drawdown official | PARCIAL | Código existe; artifact empírico falta | Persistir e aprovar | Risco não mensurado | Crítica | Gate CVaR empirical |
| Handoff de operação | NÃO IMPLEMENTADO | Sem paper prolongado aprovado | Runbook, alertas, rollback | Operação sem governança | Alta | Após paper prolongado PASS |

## Destaques Obrigatórios

Já feito e satisfatório:

- `unlock_pressure_rank` rev5 como arquitetura e governança de colunas.
- Fracdiff log-space/tau/expanding.
- Regime winsor/RobustScaler/PCA/HMM walk-forward.
- Triple-barrier HLC e market impact por raiz quadrada.
- Bridge paper/Nautilus como mecanismo técnico.

Feito, mas reprovado no mérito:

- A3/q60 e família de calibradores A3.
- Cross-sectional hardening como promotable.
- Policies recentes que preservam latest/headroom mas mantêm `dsr_honest=0.0`.
- Caminhos official bloqueados por DSR/Sharpe/subperíodos.

Parcial/inconclusivo:

- Universo/dados por falta de regeneration clean nesta auditoria.
- Feature inventory completo de macro/derivativos.
- Cobertura observada de unlock e saída de shadow.
- CVaR empírico official.
- Technical Architecture presentation PDF sem texto extraível.
- Alinhamento Fase 4-R4 source-doc.

Ainda falta implementar/provar:

- Gate de reprodutibilidade limpa.
- Artifact empírico CVaR official.
- Paper/testnet prolongado aprovado.
- Readiness para capital real.

Bloqueando avanço operacional:

- `dsr_honest=0.0`.
- Sharpe/subperíodos insuficientes nos diagnósticos official.
- `ALIVE_BUT_NOT_PROMOTABLE`.
- Falta de alinhamento source-doc-artifact Fase 4-R4.

Research-only:

- RiskLabAI.
- `phase5_cross_sectional_sovereign_closure_restored`.
- Família cross-sectional atual.
- A3 artifacts e diagnósticos pós-fechamento.

