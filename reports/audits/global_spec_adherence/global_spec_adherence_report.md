# SNIPER Global Spec Adherence Audit

Data da auditoria: 2026-04-26

Branch auditada: `codex/openclaw-sniper-handoff`

HEAD auditado: `0dbab35425548b37f5806a724c1858a0d5731bcd`

Veredito executivo: `GLOBAL_PARTIAL`

## Escopo

Esta auditoria compara o estado atual do repositório SNIPER contra a documentação e especificação disponíveis no próprio repositório. A rodada não implementou correções funcionais, não promoveu artifacts para `official` e não reabriu A3/A4.

Fontes priorizadas:

1. Estado atual do repositório.
2. Documentação em `docs/`, especialmente `SNIPER_v10_10_Especificacao_Definitiva.pdf`, `SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf`, `SNIPER_openclaw_handoff.md`, `SNIPER_regeneration_guide.md`, `SNIPER_memoria_especificacao_controle_fase4R_v3.md` e `unlock_pressure_rank_technical.md`.
3. Relatórios em `reports/gates/**`.
4. Manifests e artifacts em `data/models/**`, `data/models/research/**` e `data/parquet/**`.

Observação de evidência: `docs/SNIPER_v10.10_Technical_Architecture_presentation.pdf` teve extração textual praticamente vazia pela biblioteca disponível. A auditoria usou as demais fontes canônicas para requisitos técnicos; a apresentação permanece evidência documental inconclusiva até renderização/OCR dedicada.

## Veredito Global

`GLOBAL_PARTIAL`

O SNIPER tem uma base técnica substancial e coerente com a especificação em várias camadas: universo point-in-time/anti-survivorship, feature engineering com fracdiff, regime walk-forward, triple-barrier, market impact, meta-labeling, calibração, CPCV e bridge paper/Nautilus. Porém o projeto ainda não está aderente o suficiente para avançar para promoção operacional/paper como sistema official.

Bloqueios principais:

- Os artifacts oficiais atuais materializam `dsr_honest=0.0` com `n_trials_honest=5000`, abaixo do gate especificado.
- O diagnóstico oficial aponta Sharpe OOS de `0.3494` para a política fallback citada e subperíodos positivos abaixo do mínimo em um caminho oficial.
- A família cross-sectional está documentada e reportada como `ALIVE_BUT_NOT_PROMOTABLE`.
- A3 está encerrado como structural choke, e A3/A4 não devem ser reabertos sem evidência nova forte.
- Há divergência entre a memória Fase 4-R4, que documenta módulos como `phase4_config.py`, `phase4_data.py`, `phase4_dsr.py`, `phase4_backtest.py` e `phase4_calibration.py`, e o source rastreado, que contém `phase4_cpcv.py`, `phase4_gate_diagnostic.py` e `phase4_stage_a_experiment.py`.
- Existe controle CVaR estrutural em código, mas o artifact oficial atual não persiste auditoria empírica direta de CVaR de portfolio.

O próximo avanço deve ser um gate de reprodutibilidade e alinhamento source-doc-artifact, não uma promoção de modelo.

## Matriz Sintética Por Tema

### 1. Universo point-in-time e anti-survivorship

Status: `PARCIAL`

Evidência:

- `services/data_inserter/validators/anti_survivorship.py` implementa `AntiSurvivorshipValidator` e inclui ativos colapsados no universo point-in-time.
- `services/data_inserter/bootstrap_historical.py` marca colapsados como obrigatórios para evitar survivorship bias.
- `services/data_inserter/collectors/coingecko.py` usa ordenação por market cap histórico.

Risco: a estrutura está implementada, mas parte da reprodutibilidade depende de APIs externas e artifacts locais; a auditoria não reexecutou a coleta completa.

Recomendação: persistir manifest de universo por `as_of_date`, fonte, hash e cobertura; manter ativos mortos/collapsed como critério bloqueante de gate.

### 2. Feature engineering geral

Status: `PARCIAL`

Evidência:

- `services/ml_engine/fracdiff/weights.py` define `DEFAULT_TAU = 1e-5`.
- `services/ml_engine/fracdiff/transform.py` implementa fracdiff em log-space.
- `services/ml_engine/fracdiff/optimal_d.py` usa janela expanding e seleção de `d`.
- `services/ml_engine/main.py` materializa retornos, volatilidade, BTC regime, `sigma_intraday`, features de volume/derivativos/funding e exclusão de audit columns.

Risco: o arcabouço principal existe, mas a completude de todos os campos macro/derivativos esperados depende de disponibilidade efetiva nos parquet/artifacts atuais.

Recomendação: criar inventário de features official vs research com coluna, fonte, janela, defasagem, artifact, hash e status de cobertura.

### 3. `unlock_pressure_rank`

Status: `SATISFATÓRIO` para arquitetura; `PARCIAL` para cobertura observada operacional.

Evidência:

- `docs/SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf` e `docs/unlock_pressure_rank_technical.md` especificam observado, reconstruído e proxy, sem scraping frágil de long tail.
- `services/data_inserter/collectors/token_unlocks.py` implementa `unlock_pressure_rank_observed`, `unlock_pressure_rank_reconstructed`, `unlock_overhang_proxy_rank_full`, `unlock_fragility_proxy_rank_fallback`, `unlock_feature_state` e `selected_for_reporting`.
- `services/data_inserter/collectors/unlock_support/historical.py` impede lookahead ao escolher captura `<= as_of_date`.
- `services/data_inserter/collectors/unlock_support/utils.py` calcula UPS 30 dias e percent rank cross-sectional.
- `services/ml_engine/main.py` exclui campos de auditoria do conjunto de treinamento.

Risco: a especificação prevê shadow mode enquanto a cobertura observada não atingir maturidade; isso impede tratar a feature observada como plenamente official.

Recomendação: manter governança de quatro colunas, publicar relatório diário de cobertura/qualidade e bloquear promoção se unknown bucket ou confidence violarem a rev5.

### 4. Filtro de regime

Status: `SATISFATÓRIO`

Evidência:

- `services/ml_engine/regime/winsorizer.py` aplica winsorização fitada em treino.
- `services/ml_engine/regime/pca_robust.py` compõe winsorização, `RobustScaler` e PCA.
- `services/ml_engine/regime/hmm_filter.py` executa HMM em modo walk-forward, com retreino e sem hindsight.

Risco: a implementação está coerente; o risco remanescente é provar cobertura e estabilidade dos artifacts atuais após regeneração limpa.

Recomendação: manter diagnóstico HMM por fold/regime e hash de modelo por janela.

### 5. Triple-barrier e market impact

Status: `SATISFATÓRIO`

Evidência:

- `services/ml_engine/triple_barrier/labeler.py` usa HLC, toca SL por low e TP por high, e aplica slippage no stop.
- `services/ml_engine/triple_barrier/market_impact.py` implementa slippage pela lei da raiz quadrada com `sigma_intraday * sqrt(order_size / volume)`.
- `services/ml_engine/features/volatility.py` calcula `sigma_intraday`.
- `services/execution_engine/binance/executor.py` contém lógica compatível de slippage e `sigma_intraday` no executor.

Risco: o labeler está coerente; o risco operacional é garantir que `sigma_intraday` e volume usados no backtest sejam point-in-time e persistidos.

Recomendação: incluir amostras de labels com HLC, barreiras, slippage e volume no próximo manifest de gate.

### 6. Meta-labeling, CPCV e calibração

Status: `SATISFATÓRIO` em implementação; `PARCIAL` no resultado official atual.

Evidência:

- `services/ml_engine/meta_labeling/pbma_purged.py` implementa `PurgedKFold`, purge/embargo e geração de `p_bma`.
- `services/ml_engine/meta_labeling/uniqueness.py` implementa uniqueness, `N_eff` e sample weights.
- `services/ml_engine/meta_labeling/cpcv.py` implementa CPCV, PBO e sample weights.
- `services/ml_engine/meta_labeling/isotonic_calibration.py` implementa isotonic, walk-forward, pooling/time-decay e ECE.
- `data/models/phase4/phase4_gate_diagnostic.json` mostra CPCV/PBO/ECE/N_eff presentes, mas também bloqueios official por DSR/Sharpe/subperíodos.

Risco: a camada estatística existe, mas os gates de decisão atual não passam no mérito.

Recomendação: separar claramente "implementado" de "aprovado"; nenhum artifact com `dsr_honest=0.0` pode virar official.

### 7. Métricas e gates

Status: `PARCIAL`

Evidência:

- `services/ml_engine/phase4_cpcv.py` contém thresholds para PBO, Sharpe OOS, subperíodos e DSR honesto.
- `services/ml_engine/phase4_gate_diagnostic.py` classifica `DSR honesto`, `DSR invalidação global`, `Sharpe OOS` e `Subperiodos` como hard blockers.
- `data/models/phase4/phase4_gate_diagnostic.json` registra `dsr_honest=0.0`, `n_trials_honest=5000`, Sharpe OOS `0.3494` e subperíodos positivos insuficientes em caminho official.
- `reports/gates/phase5_cross_sectional_recent_regime_policy_falsification/gate_report.json` classifica a família como `ALIVE_BUT_NOT_PROMOTABLE`.

Risco: promover o sistema ignorando DSR/Sharpe/subperíodos violaria diretamente a especificação.

Recomendação: próximo gate deve provar reprodutibilidade e alinhamento antes de qualquer tentativa quantitativa nova.

### 8. Portfolio/risk

Status: `PARCIAL`

Evidência:

- `services/ml_engine/sizing/kelly_cvar.py` implementa Kelly fracionado, CVaR stress `rho=1.0`, drawdown scalar e sizing.
- `services/execution_engine/risk/pre_trade_check.py` aplica hard stops, CVaR, drawdown e checks de regime/drift.
- `services/ml_engine/phase4_gate_diagnostic.py` registra que o controle CVaR estrutural existe, mas que não há artifact oficial persistido para auditoria empírica direta do CVaR atual.

Risco: sem artifact empírico persistido, CVaR não pode ser marcado como PASS operacional.

Recomendação: próximo gate deve persistir `portfolio_cvar`, stress `rho=1`, drawdown, gross/net exposure e decisões de corte por data/símbolo.

### 9. Bridge paper/Nautilus

Status: `SATISFATÓRIO` em implementação; bloqueado operacionalmente pelos gates upstream.

Evidência:

- `services/nautilus_bridge/config.py` define Redis Streams, lock, heartbeat, daemon e freshness de snapshot.
- `services/nautilus_bridge/contract.py` valida contrato de target, `FULL_SNAPSHOT` e pesos.
- `services/nautilus_bridge/acceptance.py` cobre stale snapshot, duplicate, incomplete, revision conflict e superseded.
- `services/nautilus_bridge/phase4_publisher.py` publica snapshot com envelope e Redis `XADD`.
- `services/nautilus_bridge/consumer.py`, `reconciler.py` e `run_phase4_paper_daemon.py` implementam consumo, reconciliação, idempotência, lock, crash/restart e status terminal.
- `tests/unit/test_nautilus_bridge_*.py` cobre os principais contratos.

Risco: a bridge está pronta como mecanismo, mas não deve ser alimentada por snapshot não-promotable.

Recomendação: manter paper bridge em modo testável/shadow até haver artifact official que passe DSR, Sharpe, subperíodos, CVaR e drift.

### 10. Governança, reports e regeneração

Status: `PARCIAL`

Evidência:

- `docs/SNIPER_openclaw_handoff.md` consolida: A3 encerrado, A3/A4 não reabrir, RiskLabAI oracle/shadow, fast path official, cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`, baseline research-only `phase5_cross_sectional_sovereign_closure_restored`.
- `docs/SNIPER_regeneration_guide.md` documenta comandos de restauração e validação do baseline soberano.
- `reports/gates/**/gate_report.json` contém reports com `PASS`, `PARTIAL` e `FAIL` por família.
- `reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_report.json` registra `SOVEREIGN_BASELINE_RESTORED_AND_VALID` e `EXACT_RESTORE`.

Risco: muitos artifacts em `data/models/research/**` são outputs locais e precisam de prova de regeneração limpa; a documentação Fase 4-R4 também diverge do source rastreado.

Recomendação: executar gate de alinhamento source-doc-artifact antes de qualquer implementação quantitativa nova.

## Divergências e Lacunas Principais

1. `phase4_config.py`, `phase4_data.py`, `phase4_dsr.py`, `phase4_backtest.py` e `phase4_calibration.py` são descritos em `docs/SNIPER_memoria_especificacao_controle_fase4R_v3.md`, mas não existem como source rastreado em `services/ml_engine/`.
2. `docs/SNIPER_v10.10_Technical_Architecture_presentation.pdf` não forneceu texto auditável com a ferramenta disponível.
3. O official atual não passa gates de robustez: `dsr_honest=0.0`, Sharpe/subperíodos insuficientes em diagnóstico official e família cross-sectional não promotable.
4. CVaR está implementado em código, mas falta artifact empírico official persistido para auditoria direta.
5. A cobertura observada de unlock ainda exige shadow/qualidade; a feature não deve ser tratada como fully official sem relatório de maturidade.

## O Que Não Deve Ser Reaberto

- A3 como linha promotable: está encerrado como structural choke.
- A4 sem evidência causal nova forte.
- RiskLabAI como official: permanece oracle/shadow.
- Família cross-sectional como promotable: status consolidado é `ALIVE_BUT_NOT_PROMOTABLE`.
- Baselines antigos divergentes: a baseline research-only correta é `phase5_cross_sectional_sovereign_closure_restored`.

## Próximo Passo Recomendado

Gate sugerido: `phase6_global_reproducibility_source_alignment_gate`

Objetivo: provar, em uma rodada reproduzível, que documentação, source rastreado e artifacts de gate estão alinhados; fechar lacunas inconclusivas; persistir evidência mínima de CVaR; e manter bloqueios de promoção enquanto DSR/Sharpe/subperíodos não passarem.

Classificação esperada se nada quantitativo mudar: `PARTIAL`, com avanço apenas se o gate produzir alinhamento e evidência reprodutível, não se tentar promover modelo.

Não fazer na próxima rodada:

- Não promover artifacts para official.
- Não reabrir A3/A4.
- Não transformar `ALIVE_BUT_NOT_PROMOTABLE` em promotable.
- Não usar RiskLabAI como caminho official.
- Não compensar falta de artifact com narrativa.

