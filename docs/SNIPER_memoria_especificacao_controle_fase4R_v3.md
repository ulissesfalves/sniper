# SNIPER — Memória Mestra de Especificação e Controle de Implementação (Fase 4-R)

## 1. Finalidade
Este documento consolida a história técnica, a especificação operacional, o plano de remediação, o progresso intermediário e o estado final reportado da **Fase 4-R** do SNIPER. O objetivo é servir como memória de controle do que foi identificado, decidido, implementado e validado, sem misturar hipótese antiga com estado final.

Esta versão **v3** reavalia e atualiza a memória anterior à luz dos novos arquivos anexados, especialmente os snapshots adicionais de `walkthrough.md.resolved.*` e a própria memória consolidada anterior anexada pelo usuário.

## Nota de alinhamento source-doc — Phase 6

Auditoria em 2026-04-26 para o gate `phase6_global_reproducibility_source_alignment_gate`: a seção 7 registra a arquitetura final reportada da Fase 4-R4, mas o source rastreado atual da branch de continuidade não contém os módulos `phase4_config.py`, `phase4_data.py`, `phase4_dsr.py`, `phase4_backtest.py` e `phase4_calibration.py`. O source rastreado atual em `services/ml_engine/` contém `phase4_cpcv.py`, `phase4_gate_diagnostic.py` e `phase4_stage_a_experiment.py`.

Para fins de regeneração e auditoria nesta branch, tratar o source rastreado atual como fonte operacional de verdade. A seção 7 permanece como memória histórica/reportada da intenção de refatoração, não como prova de que esses módulos existem no checkout atual. Essa nota não promove artifacts, não reabre A3/A4 e não altera critérios quantitativos.

---

## 2. Corpus integral considerado nesta versão
Esta versão foi consolidada após leitura do conjunto completo de arquivos disponíveis nesta conversa, incluindo arquivos principais, metadados, snapshots históricos e a memória consolidada anterior.

### 2.1 Arquivos-base
- `implementation_plan.md`
- `task.md`
- `walkthrough.md`

### 2.2 Metadados
- `implementation_plan.md.metadata.json`
- `task.md.metadata.json`
- `walkthrough.md.metadata.json`

### 2.3 Snapshots `.resolved`
- `implementation_plan.md.resolved`
- `implementation_plan.md.resolved.0`
- `implementation_plan.md.resolved.1`
- `task.md.resolved`
- `task.md.resolved.0`
- `task.md.resolved.1`
- `task.md.resolved.2`
- `task.md.resolved.3`
- `task.md.resolved.4`
- `task.md.resolved.5`
- `task.md.resolved.6`
- `walkthrough.md.resolved`
- `walkthrough.md.resolved.0`
- `walkthrough.md.resolved.1`
- `walkthrough.md.resolved.2`
- `walkthrough.md.resolved.3`

### 2.4 Documento consolidado anterior
- `21fa5649-4665-49ae-a557-48c983f10940.md`

---

## 3. Regra de consolidação adotada
Para manter coerência lógica:

1. **Snapshots históricos** foram tratados como trilha de evolução do trabalho, não como estado final.
2. Quando havia conflito entre um snapshot antigo e um registro posterior de conclusão, prevaleceu o **estado final mais recente**.
3. Quando uma hipótese crítica apareceu em um audit inicial, mas depois foi marcada como corrigida, auditada ou encerrada, a memória registra **os dois momentos**:
   - o problema foi levantado;
   - o fechamento posterior reportou sua remediação, reclassificação ou absorção por refactoring.
4. Quando o material recebido não trouxe log bruto, diff, hash ou artefato primário, o documento registra o item como **“reportado”** e não como evidência reproduzida nesta conversa.
5. O documento anterior anexado pelo usuário foi tratado como **uma consolidação intermediária útil**, mas não como fonte soberana acima dos snapshots históricos; quando os novos arquivos trouxeram mais granularidade, a presente versão absorveu esses detalhes.

---

## 4. O que esta versão v3 adiciona em relação à v2
Os novos arquivos não contradizem a memória anterior, mas acrescentam contexto importante em quatro pontos:

1. **4-R2 ficou melhor especificada**: agora é possível registrar com precisão **quais arquivos foram alterados**, **quais hard-gates foram introduzidos**, **como o PBO foi definido operacionalmente** e por que, mesmo com os hard-gates estatísticos aprovados, o gate final ainda ficou `INCONCLUSIVE_BASELINE` naquele estágio intermediário.
2. **A sequência histórica do walkthrough ficou mais completa**: os arquivos `walkthrough.md.resolved.1`, `.2` e `.3` mostram explicitamente a evolução 4-R2 → 4-R3 → 4-R4, em vez de apenas o estado final.
3. **A lógica do handoff para a Fase 5 ficou mais nítida**: o fechamento de 4-R4 passou a ser descrito explicitamente como encerramento da bridge reproduzível e pavimentação para a Fase 5.
4. **A memória anterior anexada foi revalidada**: os novos arquivos reforçam sua estrutura geral, mas exigem ampliação dos detalhes da 4-R2 e da trilha histórica do walkthrough.

---

## 5. Linha do tempo consolidada da Fase 4-R

### 5.1 Diagnóstico inicial: a Fase 4 ainda não era confiável para avançar
O snapshot histórico `implementation_plan.md.resolved.0` registra uma auditoria técnica que concluiu que a Fase 4, como havia sido fechada naquele momento, **não estava suficientemente boa para avançar com confiança**. Nessa auditoria foram levantados sete problemas principais:

1. possível bug na fórmula de variância do DSR;
2. lógica de embargo CPCV tratada como bloco contíguo;
3. risco conceitual de tratar o Decision-Space como “régua soberana”;
4. gates da especificação ainda não materializados literalmente;
5. inconsistência entre implementações de Kelly/CVaR e parâmetros do meta-modelo;
6. ausência de testes matemáticos unitários robustos;
7. risco operacional do monólito `phase4_cpcv.py`.

Essa leitura histórica explica **por que a Fase 4-R foi aberta**: ela nasce como remediação estruturada antes de qualquer avanço para a Fase 5.

### 5.2 Abertura formal da remediação 4-R
O audit inicial propôs a abertura da **Fase 4-R**, dividida em quatro frentes:

- **4-R1:** correção de bugs matemáticos e fortalecimento de testes unitários;
- **4-R2:** materialização dos gates obrigatórios da especificação;
- **4-R3:** auditoria da régua Decision-Space;
- **4-R4:** refactoring do monólito `phase4_cpcv.py`.

### 5.3 Progressão histórica observada no task tracker
Os arquivos `task.md.resolved.0` a `task.md.resolved.6` mostram a evolução do trabalho em estágios:

- primeiro, os itens de 4-R1, 4-R2, 4-R3 e 4-R4 estavam apenas planejados;
- depois, 4-R1 passou a constar como concluído com bateria de testes e embargo CPCV corrigido;
- em seguida, 4-R2 foi fechado com materialização de métricas;
- depois, 4-R3 foi fechado com testes de falso-positivo e necessidade;
- por fim, 4-R4 foi marcado como concluído com modularização, facade e regressões aprovadas.

### 5.4 Progressão histórica observada nos walkthroughs
Os snapshots de `walkthrough.md.resolved.*` deixam a progressão mais explícita:

- **`walkthrough.md.resolved.1`** documenta a materialização de gates em 4-R2;
- **`walkthrough.md.resolved.2`** registra 4-R3 ainda sem o fechamento de 4-R4 embutido;
- **`walkthrough.md.resolved.3`** já incorpora o fechamento completo de 4-R4;
- **`walkthrough.md` / `walkthrough.md.resolved`** representam a versão final expandida do walkthrough.

Esse encadeamento reforça que a trilha de evolução não foi apenas declarada no task tracker; ela também aparece refletida em documentos narrativos técnicos intermediários.

---

## 6. Estado final controlado da Fase 4-R

### 6.1 4-R1 — Correção de bugs matemáticos e testes unitários
**Status final:** concluído.

#### Itens historicamente registrados como resolvidos
Nos snapshots intermediários do task tracker, 4-R1 evolui para o seguinte estado:

- correção da fórmula `var(SR)` no DSR, com observação posterior de que a implementação ficou **“auditada: correta”**;
- correção da lógica de embargo no CPCV para folds não contíguos;
- implementação dos seguintes testes unitários:
  - `tests/unit/test_dsr_honest.py` — 15 testes;
  - `tests/unit/test_kelly_cvar_math.py` — 24 testes;
  - `tests/unit/test_cpcv_embargo.py` — 10 testes;
  - `tests/unit/test_isotonic_math.py` — 14 testes;
  - `tests/unit/test_label_uniqueness_math.py` — 11 testes;
- execução da suíte com **`74 passed, 0 failed`** em snapshot intermediário.

#### Interpretação consolidada
A história mostra um ponto importante:
- no audit inicial, a fórmula do DSR aparecia como problema crítico;
- no tracker posterior, esse ponto passou a constar como **corrigido/auditado**.

Portanto, a memória final registra esse tema assim:
- o risco foi levantado;
- a trilha posterior o trata como encerrado.

#### Observação de controle
Havia um item “unificar parâmetros do meta-modelo entre `phase4_cpcv.py` e `meta_labeling/cpcv.py`” que em um snapshot intermediário foi marcado como **adiado para 4-R4**. O fechamento final não reitera esse item nominalmente como uma linha isolada, mas a modularização e centralização de responsabilidades em 4-R4 absorvem parte relevante desse risco. Sem diff do código, este documento registra esse fechamento como **implícito pelo refactoring**, não como evidência isolada de uma única alteração.

### 6.2 4-R2 — Materialização dos gates da especificação
**Status final:** concluído.

#### Problema atacado
O walkthrough de 4-R2 registra que, quando `PROBLEM_TYPE == "cross_sectional_ranking"`, o relatório omitia inteiramente o cálculo de PBO, `n_eff` e `ece`. O objetivo da etapa foi tornar essas métricas parte material do evaluation do baseline cross-sectional.

#### Alterações operacionais explicitamente documentadas
Os novos snapshots permitem registrar com mais precisão os pontos abaixo:

##### a) `phase4_stage_a_experiment.py` (executor)
Foi reportado que esse componente passou a:
- materializar **PBO** como proporção das trajetórias CPCV em que `auc_raw_vs_truth_top1 < 0.50`;
- reportar **N_eff médio** como acúmulo da pureza dos dados de treino nas rotinas subjacentes;
- injetar essas métricas no subdicionário `classification_metrics/ranking_metrics`.

##### b) `phase4_cross_sectional_ranking_baseline.py` (gate runner)
Foi reportado que o runner passou a impor os seguintes hard-gates:

- `cpcv_pbo_lt_10pct` ⇒ exige **PBO < 0.10**;
- `ece_calibrated_lt_005` ⇒ exige **ECE < 0.05**, com nota técnica de bypass/tolerância porque modelos cross-sectional de rank emitem score contínuo, não probabilidade calibrada direta;
- `n_eff_mean_gt_120` ⇒ exige **N_eff > 120**;
- `dsr_honest_gt_095` ⇒ exige **DSR honesto > 0.95**.

#### Métricas explicitamente registradas nos snapshots históricos
Os arquivos intermediários do tracker e o walkthrough de 4-R2 registram o fechamento com os seguintes números e observações:

- execução de **CPCV N=6, k=2** com **15 trajetórias** na família cross-sectional;
- **PBO = 0.0**;
- **ECE calibrada** tratada como **trivial/bypass mode** por se tratar de `rank_score`;
- **N_eff médio = 2777.2**;
- **DSR_honest = 1.0**;
- gate report JSON contendo as materializações.

#### Situação intermediária importante
O walkthrough de 4-R2 acrescenta um ponto que a v2 não deixava suficientemente explícito:

- apesar da aprovação dos **hard-gates estatísticos**, o gate final naquele estágio foi registrado como **`INCONCLUSIVE_BASELINE`**;
- a razão reportada foi que a régua operacional principal, **`latest_active_count > 0`**, ainda não era batida;
- em outras palavras, o baseline cross-sectional havia preenchido a lacuna estatística, mas ainda não tinha vencido o headroom operacional naquele momento intermediário.

Esse detalhe é importante porque explica por que a 4-R3 não foi cosmética: ela veio para auditar precisamente a relação entre evidência estatística e ativação operacional.

#### Interpretação consolidada
O audit inicial acusava que ECE, PBO e N_eff ainda não estavam materializados como gates soberanos. Os snapshots posteriores mostram que isso foi enfrentado durante 4-R2. Logo, o estado final consolidado é:

- os gates da especificação passaram de **lacuna levantada** para **materialização registrada**;
- a família cross-sectional deixou de depender apenas de narrativa conceitual e passou a ter materialização objetiva de gate report;
- mesmo assim, o estado intermediário ainda exigiu a auditoria de 4-R3 para resolver a tensão entre hard-gates estatísticos e headroom operacional.

#### Limite de evidência
Embora os valores acima estejam registrados nos snapshots recebidos, os artefatos primários do gate report não foram anexados nesta conversa. Assim, esta memória registra esses números como **status reportado pelos documentos históricos lidos**, não como reproduções recalculadas aqui.

### 6.3 4-R3 — Auditoria da régua Decision-Space
**Status final:** concluído e validado.

#### Objetivo
Investigar e estressar a régua Decision-Space para eliminar ambiguidades sobre seu papel no sistema.

#### Decisão arquitetural consolidada
A régua **Decision-Space não é soberana** e **não substitui** o label-space. Ela foi formalmente enquadrada como uma **régua de necessidade estrita**, isto é, um mecanismo de **veto operacional**.

#### Evidência de validação
Foi criado `tests/unit/test_decision_space_audit.py`, com duas provas explícitas:

**Teste 1 — Falso positivo rejeitado**
- cenário: modelo com ativação operacional forte (`headroom_real = True`), mas com label-space fraco (`Sharpe < 0.70`, `DSR = 0`);
- resultado: `FAIL`;
- conclusão: Decision-Space sozinho não aprova modelo sem mérito comprovado.

**Teste 2 — Necessidade operacional**
- cenário: modelo com excelente histórico estatístico (`Sharpe = 2.5`, `DSR = 1.0`), mas sem ativação operacional recente (`headroom_real = False`);
- resultado: `FAIL`;
- conclusão: mesmo mérito estatístico alto não basta se o front ativo estiver operacionalmente desligado.

#### Resultado reportado
- **`2 passed`**
- documentação complementar criada em `docs/decision_space_ruler_audit.md`

#### Síntese consolidada
A combinação entre **Label-Space** e **Decision-Space** foi formalmente estabilizada como arquitetura de **proteção dupla**:
- Label-Space protege contra edge ilusório;
- Decision-Space protege contra irrelevância operacional recente.

### 6.4 4-R4 — Refactoring do monólito `phase4_cpcv.py`
**Status final:** concluído e validado.

#### Problema original
O arquivo `phase4_cpcv.py` tinha:
- **2.745 linhas**;
- **56 funções**;
- cerca de **40 variáveis de ambiente**;
- múltiplas responsabilidades reunidas no mesmo módulo;
- uso direto por `phase4_stage_a_experiment.py` e scripts de gate/diagnóstico.

#### Objetivo
Extrair responsabilidades em módulos coesos sem quebrar consumidores existentes.

#### Decisão estrutural
`phase4_cpcv.py` foi preservado como **facade retrocompatível**. Ele não foi deletado nem renomeado.

---

## 7. Arquitetura final consolidada da 4-R4

### 7.1 `phase4_config.py`
**Responsabilidade:** configuração tipada e centralização de constantes e env vars.

**Escopo consolidado:**
- `N_SPLITS`, `N_TEST_SPLITS`, `EMBARGO_PCT`, `CAPITAL_INITIAL`;
- env vars `PHASE4_*` com defaults documentados;
- `MODEL_PATH`, `FEATURES_PATH`, `PHASE3_PATH`, `OUTPUT_PATH`;
- `SUBPERIODS`;
- helpers:
  - `_hmm_meta_feature_enabled()`
  - `_hmm_hard_gate_enabled()`
  - `_phase4_prob_mode()`
  - `_phase4_neutral_fill()`

### 7.2 `phase4_data.py`
**Responsabilidade:** carregamento de dados, seleção de features e treinamento do meta-model.

**Escopo consolidado:**
- `_safe_read()`
- `_prepare_feature_matrix()`
- `load_pooled_meta_df()`
- `select_features()`
- `_choose_vi_features()`
- `_load_vi_cluster_map()`
- `get_unlock_model_feature_columns()`
- `_is_allowed_unlock_feature()`
- `compute_sample_weights()`
- `train_meta_model()`

### 7.3 `phase4_dsr.py`
**Responsabilidade:** métricas de robustez estatística e calibração.

**Escopo consolidado:**
- `compute_dsr_honest()`
- `_compute_ece()`

### 7.4 `phase4_backtest.py`
**Responsabilidade:** backtest, equity curve, evaluation de policies e robustez operacional.

**Escopo consolidado:**
- `_build_portfolio_returns()`
- `compute_equity_curve()`
- `_reference_order_usdt()`
- `_rescale_slippage_to_position()`
- `_attach_execution_pnl()`
- `_build_policy_signal()`
- `_sanitize_policy_stats()`
- `_summarize_subperiods()`
- `_attach_friction_stress_pnl()`
- `_build_score_bucket_diagnostics()`
- `_evaluate_decision_policy()`
- `_policy_signal_column()`
- `_evaluate_policy_spec_on_frame()`
- `_sort_policy_candidates()`
- `_evaluate_policy_holdout_validation()`
- `_evaluate_policy_forward_validation()`
- `_build_operational_policy_robustness()`
- `_build_rolling_policy_stability()`
- `analyze_subperiods()`

### 7.5 `phase4_calibration.py`
**Responsabilidade:** calibração isotônica, clusters, sizing e snapshots operacionais.

**Escopo consolidado:**
- `_atomic_json_write()`
- `_cluster_artifact_suffix()`
- `_symbol_cluster_artifact_paths()`
- `_symbol_cluster_artifact_write_paths()`
- `_build_symbol_signature_frame()`
- `_normalize_cluster_payload()`
- `_derive_symbol_vi_clusters()`
- `_ensure_symbol_vi_cluster_artifact()`
- `_load_symbol_vi_clusters()`
- `_fit_cluster_calibrators()`
- `_apply_cluster_calibration()`
- `_compute_symbol_trade_stats()`
- `_attach_trade_stats()`
- `_compute_phase4_sizing()`
- `_build_execution_snapshot()`
- `_build_operational_path_report()`
- `_build_report_paths_summary()`
- `_aggregate_oos_predictions()`

### 7.6 `phase4_cpcv.py` (facade)
**Responsabilidade final:** orquestração de alto nível e preservação da interface pública histórica.

**Elementos mantidos no facade:**
- `run_cpcv()`
- `evaluate_fallback()`
- `main()`

**Papel consolidado:**
- reexportar módulos extraídos;
- manter compatibilidade com `import phase4_cpcv as phase4`;
- preservar a superfície pública esperada pelos consumidores.

---

## 8. Compatibilidade, retrocompatibilidade e impactos indiretos

### 8.1 Compatibilidade com consumidores externos
Foi registrada a verificação de **28 referências externas** utilizadas por `phase4_stage_a_experiment.py`, incluindo atributos e funções como:

- `CAPITAL_INITIAL`
- `EMBARGO_PCT`
- `N_SPLITS`
- `N_TEST_SPLITS`
- `load_pooled_meta_df()`
- `select_features()`
- `compute_sample_weights()`
- `train_meta_model()`
- `_prepare_feature_matrix()`
- `_compute_ece()`
- `_hmm_hard_gate_enabled()`
- `_load_symbol_vi_clusters()`
- `_fit_cluster_calibrators()`
- `_apply_cluster_calibration()`
- `_compute_symbol_trade_stats()`
- `_attach_trade_stats()`
- `_compute_phase4_sizing()`
- `_attach_execution_pnl()`
- `_build_execution_snapshot()`
- `_build_operational_path_report()`
- `_aggregate_oos_predictions()`
- `_atomic_json_write()`
- `_summarize_subperiods()`
- `compute_dsr_honest()`

### 8.2 Quebras induzidas pelo refactoring
O walkthrough registra problemas de `NameError` e `ModuleNotFoundError` em dependências como:
- `structlog`
- `polars`
- `numpy`

**Tratamento reportado:**
- restauração de `MagicMocks` genéricos em `_path_setup.py` para proteger a cadeia de imports no ambiente local de testes.

### 8.3 Vazamento de estado em testes legados
Foram identificadas dependências em `test_phase4_stage_a_experiment.py` de variáveis globais hardcoded, como:
- `PROBLEM_TYPE = "binary_classification"`
- `TARGET_MODE = "sl_mult"`

**Tratamento reportado:**
- mocks locais e cirúrgicos nas variáveis de módulo;
- estabilização da suíte;
- eliminação de *state bleed* entre runtimes.

---

## 9. Validação consolidada

### 9.1 Validações historicamente registradas
Ao longo dos arquivos lidos, aparecem como realizadas as seguintes validações:

- **74 testes unitários** rodando em verde;
- em snapshot final, menção a **100% PASS** com destaque para **54 Phase 4 tests**;
- **2 testes aprovados** na auditoria Decision-Space;
- verificação das **28 referências externas** do `stage_a_experiment`;
- testes de regressão pós-refactoring marcados como concluídos com sucesso;
- execução de `pytest -v` sobre a base modularizada com aprovação reportada.

### 9.2 Protocolo de verificação previsto no plano
O plano de 4-R4 também deixou explicitamente registrados como parte do protocolo:

- importação direta de `phase4_cpcv` sem quebrar consumidores;
- comparação funcional do pipeline Phase 4;
- comparação de hash do `phase4_report_v4.json` antes e depois do refactoring.

### 9.3 Limite de evidência reproduzida nesta conversa
Os documentos recebidos afirmam sucesso nessas validações, mas não incluem aqui:
- logs brutos integrais;
- hashes finais;
- diffs de código;
- gate reports completos;
- outputs completos de `pytest`.

Logo, esta memória consolida as validações como **reportadas e consistentes entre os documentos lidos**, sem alegar reprodução independente nesta conversa.

---

## 10. Estado final consolidado
Ao final da Fase 4-R, o estado controlado pode ser descrito assim:

1. a Fase 4 passou por uma remediação formal antes de qualquer avanço;
2. riscos críticos levantados no audit inicial foram tratados ao longo de 4-R1 a 4-R4;
3. os gates da especificação deixaram de ser apenas exigência teórica e passaram a constar como materializados;
4. a relação entre Label-Space e Decision-Space foi formalizada de modo não ambíguo;
5. o monólito `phase4_cpcv.py` foi desmontado em módulos coesos;
6. a retrocompatibilidade foi preservada por facade;
7. a suíte de testes e as regressões foram registradas como aprovadas;
8. a Fase 4 ficou preparada para handoff e continuidade rumo à Fase 5.

---

## 11. O que este documento controla com confiança
Este documento sustenta com segurança os seguintes pontos:

- **a história completa da Fase 4-R**, do diagnóstico inicial ao fechamento;
- **a divisão formal em 4-R1, 4-R2, 4-R3 e 4-R4**;
- **as decisões arquiteturais centrais** da fase;
- **a modularização final de `phase4_cpcv.py`**;
- **o papel final do Decision-Space**;
- **a existência reportada de testes, métricas e validações**;
- **o fato de que 4-R2 não foi apenas um fechamento abstrato, mas uma materialização operacional explícita em executor, gate runner e métricas de conformidade**.

## 12. O que este documento não substitui
Este documento **não substitui**:
- o repositório-fonte;
- os gate reports primários;
- os manifests;
- os arquivos de teste;
- os logs de execução;
- os diffs de código.

Ele é uma **memória mestra de especificação e controle**, não a evidência primária de execução.

## 13. Veredito consolidado
**A Fase 4-R está registrada, nesta consolidação, como uma remediação completa e logicamente fechada: nasceu de um diagnóstico severo, passou por correção matemática, materialização de gates, auditoria arquitetural e refactoring estrutural, e terminou reportada como modularizada, retrocompatível e validada. Os novos arquivos não mudam esse veredito, mas tornam a narrativa mais completa, especialmente ao explicitar como 4-R2 foi operacionalizada e como a trilha 4-R2 → 4-R3 → 4-R4 se consolidou até o handoff para a Fase 5.**
