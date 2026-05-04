# Human Review Checklist - SNIPER Autonomous Mission Closure

## PR readiness

- [ ] Confirmar que o PR sera aberto como draft.
- [ ] Confirmar base `codex/openclaw-sniper-handoff`.
- [ ] Confirmar head `codex/autonomous-sniper-implementation`.
- [ ] Confirmar que nao ha pedido de merge automatico.
- [ ] Confirmar que nao ha promocao para official no texto do PR.

## Commits

- [ ] Revisar `a31bb54` - candidate decision gate.
- [ ] Revisar `eda07a6` - candidate falsification gate.
- [ ] Revisar `4d430fd` - candidate stability gate.
- [ ] Revisar `9f95447` - candidate global reaudit gate.
- [ ] Revisar `649e9a9` - skill autonomous implementation manager.
- [ ] Revisar `add6ce1` - primeiro gate Phase6.
- [ ] Revisar `b1004fb` - preflight/source-doc alignment.
- [ ] Revisar `f622842` - skill autonomous stop reviewer.
- [ ] Revisar `c4bf284` - artifact rehydration e DSR stop gate.
- [ ] Revisar `e86f0db` - suporte a clean regeneration em clone isolado.
- [ ] Revisar `d7829b7` - gate final de clean regeneration.

## Gates Phase6

- [ ] Verificar `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_phase4_artifact_rehydration_and_dsr_stop_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/gate_report.json`.
- [ ] Confirmar que todos possuem `gate_report.json`, `gate_report.md`, `gate_manifest.json` e `gate_metrics.parquet`.

## Gates research-only full-phase

- [ ] Verificar `reports/gates/phase5_research_deep_quant_diagnostic_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_alternative_exante_family_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_signal_polarity_long_short_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_signal_polarity_stability_correction_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_full_phase_family_comparison_gate/gate_report.json`.
- [ ] Confirmar que a familia long-only p_bma/sigma/hmm foi abandonada.
- [ ] Confirmar que `short_high_p_bma_k3_p60_h70` e research/sandbox apenas.
- [ ] Confirmar mediana Sharpe `1.361592`, min Sharpe `0.261111`, dias ativos medianos `471.0` e CVaR95 max `0.00344841`.
- [ ] Confirmar que exposicao short nao foi tratada como official.
- [ ] Confirmar que o candidato continua abaixo de `sr_needed=4.47`.

## Gates candidate reaudit/falsification

- [ ] Verificar `reports/gates/phase5_research_candidate_global_reaudit_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_candidate_stability_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_candidate_falsification_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_candidate_decision_gate/gate_report.json`.
- [ ] Confirmar que `phase5_research_candidate_global_reaudit_gate` foi `PASS/advance` apenas como research/sandbox.
- [ ] Confirmar que `phase5_research_candidate_stability_gate` foi `PARTIAL/correct` com 29 falhas em 49 cenarios.
- [ ] Confirmar hard falsifiers: `temporal_subperiod_min_sharpe=-1.160839` e `extra_cost_20bps_min_sharpe=-0.12201`.
- [ ] Confirmar que o controle de leakage passou.
- [ ] Confirmar classificacao final `RESEARCH_CANDIDATE_FALSIFIED`.
- [ ] Confirmar que `short_high_p_bma_k3_p60_h70` foi abandonada e nao deve ser promovida.

## Gates closed-loop post-falsification

- [ ] Verificar `reports/gates/phase5_post_candidate_falsification_global_reaudit_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_cluster_conditioned_polarity_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_cluster_conditioned_polarity_falsification_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_cluster_conditioned_polarity_decision_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_post_candidate_falsification_governed_freeze_gate/gate_report.json`.
- [ ] Confirmar que `phase5_post_candidate_falsification_global_reaudit_gate` foi `PASS/advance`.
- [ ] Confirmar que `cluster_conditioned_polarity` foi a tese materialmente nova selecionada automaticamente.
- [ ] Confirmar que `cluster_2_long_high_short_low_p60_h70_k3` teve mediana Sharpe `1.183459`, min Sharpe `0.078586`, dias ativos medianos `425.0` e CVaR95 max `0.00151815`.
- [ ] Confirmar que essa candidata foi falsificada por 13 hard falsifiers.
- [ ] Confirmar principais falsificadores: `temporal_subperiod_min_sharpe=-1.633204` e `extra_cost_20bps_min_sharpe=-0.493149`.
- [ ] Confirmar classificacao final `FULL_FREEZE_AFTER_REAUDIT`.
- [ ] Confirmar `remaining_safe_material_hypothesis_count=0`.
- [ ] Confirmar que nenhuma dessas evidencias promove official ou declara paper readiness.

## Gates next-gate chain execution

- [ ] Verificar `reports/gates/phase5_research_meta_disagreement_abstention_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_meta_disagreement_stability_falsification_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase5_research_meta_disagreement_candidate_decision_gate/gate_report.json`.
- [ ] Confirmar que `phase5_research_meta_disagreement_abstention_gate` foi `PASS/advance` apenas como research/sandbox.
- [ ] Confirmar candidata `short_bma_high_meta_low_p60_m40_k3`.
- [ ] Confirmar mediana Sharpe `0.855486`, min Sharpe `0.220622`, dias ativos medianos `322.0` e CVaR95 max `0.00455141`.
- [ ] Confirmar que a candidata foi falsificada por 25 hard falsifiers.
- [ ] Confirmar principais falsificadores: thirds temporais, custo de 20 bps, sensibilidade parametrica e stress de universo.
- [ ] Confirmar que o controle de leakage passou.
- [ ] Confirmar classificacao final `META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED`.
- [ ] Confirmar que a proxima hipotese da agenda e `AGENDA-H02` / `phase5_research_meta_uncertainty_abstention_gate`.
- [ ] Confirmar que a candidata falsificada nao foi usada para promocao official ou paper readiness.

## Gate AGENDA-H02 meta-uncertainty

- [ ] Verificar `reports/gates/phase5_research_meta_uncertainty_abstention_gate/gate_report.json`.
- [ ] Confirmar que `phase5_research_meta_uncertainty_abstention_gate` foi `FAIL/abandon`.
- [ ] Confirmar classificacao `META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS`.
- [ ] Confirmar best policy `long_bma_meta_agree_p65_m50_s10_k3`.
- [ ] Confirmar mediana Sharpe `0.447334`, min Sharpe `-0.375889`, dias ativos medianos `55.0` e CVaR95 max `0.00284763`.
- [ ] Confirmar que a linha foi falsificada por 19 hard falsifiers.
- [ ] Confirmar que a linha era long-only research/sandbox e nao official.
- [ ] Confirmar proximo gate registrado `phase5_research_cvar_constrained_meta_sizing_gate`.

## Clean regeneration

- [ ] Confirmar que `phase6_research_baseline_rehydration_clean_regeneration_gate` tem `clean_clone_or_equivalent=true`.
- [ ] Confirmar `regeneration_returncode=0`.
- [ ] Confirmar `phase5_clean_gate_status=PASS`.
- [ ] Confirmar que o clone limpo estava no mesmo HEAD da branch fonte.
- [ ] Confirmar que o clone estava limpo antes da regeneracao.
- [ ] Confirmar que os artifacts base foram copiados para o clone, nao fabricados.

## Artifacts

- [ ] Confirmar hashes dos artifacts official Phase4 no gate pack.
- [ ] Confirmar hashes dos artifacts research baseline no gate pack.
- [ ] Confirmar que `missing_required_artifacts=[]`.
- [ ] Confirmar que `missing_regeneration_baseline_artifacts=[]`.
- [ ] Confirmar que artifacts em `data/models/**` continuam tratados como base externa/ignored, nao como promocao official.

## Governanca

- [ ] Confirmar que A3/A4 nao foram reabertos.
- [ ] Confirmar que RiskLabAI permaneceu oracle/shadow.
- [ ] Confirmar que Fast path permaneceu official.
- [ ] Confirmar que cross-sectional permanece `ALIVE_BUT_NOT_PROMOTABLE`.
- [ ] Confirmar que research nao virou official.
- [ ] Confirmar que nao houve credenciais, ordens reais, capital real, merge ou force push.

## Blockers obrigatorios

- [ ] Confirmar `dsr_honest=0.0`.
- [ ] Confirmar `dsr_passed=false`.
- [ ] Confirmar que DSR continua blocker de promocao.
- [ ] Confirmar `cvar_technical_status=PASS_ZERO_EXPOSURE`.
- [ ] Confirmar `cvar_economic_status=NOT_PROVEN_ZERO_EXPOSURE`.
- [ ] Confirmar que CVaR zero exposure nao foi tratado como robustez economica.
- [ ] Confirmar que o novo CVaR research com exposicao short nao foi tratado como CVaR economico official.
- [ ] Confirmar que o candidato research-only falsificado nao foi usado para declarar paper readiness.
- [ ] Confirmar que a candidata meta-disagreement falsificada tambem nao foi usada para declarar paper readiness.
- [ ] Confirmar que a linha meta-uncertainty falsificada tambem nao foi usada para declarar paper readiness.

## Testes

- [ ] Rodar ou revisar resultado:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py tests/unit/test_phase5_cross_sectional_latest_headroom_reconciliation_audit.py tests/unit/test_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py tests/unit/test_phase5_cross_sectional_sovereign_hardening_recheck.py tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q
```

- [ ] Esperado: `26 passed`.

- [ ] Rodar ou revisar resultado full-phase:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase5_research_deep_quant_diagnostic.py tests/unit/test_phase5_research_alternative_exante_family.py tests/unit/test_phase5_research_signal_polarity_long_short.py tests/unit/test_phase5_research_full_phase_family_comparison.py -q
```

- [ ] Esperado observado: `11 passed`.

- [ ] Rodar ou revisar resultado candidate reaudit/falsification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase5_research_candidate_validation.py -q
```

- [ ] Esperado observado: `6 passed`.

- [ ] Rodar ou revisar resultado closed-loop:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase5_post_candidate_falsification_global_reaudit.py tests/unit/test_phase5_research_cluster_conditioned_polarity.py tests/unit/test_phase5_post_candidate_falsification_governed_freeze.py tests/unit/test_gate_reports.py -q
```

- [ ] Esperado observado: `12 passed`.

- [ ] Rodar ou revisar resultado next-gate chain:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase5_research_meta_disagreement_abstention.py -q
```

- [ ] Esperado observado: `10 passed`.

- [ ] Rodar ou revisar resultado AGENDA-H02:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase5_research_meta_uncertainty_abstention.py -q
```

- [ ] Esperado observado: `5 passed`.

## Decisao humana

- [ ] Aprovar PR draft como evidencia de reprodutibilidade/governanca Phase6.
- [ ] Nao aprovar promocao official.
- [ ] Decidir se a linha cross-sectional deve ser congelada como research-only.
- [ ] Revisar o abandono do candidato sandbox `short_high_p_bma_k3_p60_h70`.
- [ ] Revisar a classificacao `FULL_FREEZE_AFTER_REAUDIT`.
- [ ] Nao pedir promocao official, paper readiness, merge ou reabertura A3/A4 a partir deste PR.
- [ ] Proxima rodada autonoma so deve ocorrer com evidencia materialmente nova, artifact externo ou nova direcao research segura.
