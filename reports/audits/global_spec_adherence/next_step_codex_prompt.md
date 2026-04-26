[$sniper-gate-governance](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-gate-governance\SKILL.md)
[$sniper-quant-research-implementation](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-quant-research-implementation\SKILL.md)
[$sniper-paper-execution-hardening](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-paper-execution-hardening\SKILL.md)

Execute o próximo gate recomendado do SNIPER.

Repo local:
C:\Users\uliss\Documentos\Meus_projetos\sniper

Branch base:
codex/openclaw-sniper-handoff

Próximo gate:
phase6_global_reproducibility_source_alignment_gate

Branch sugerida:
codex/phase6-global-reproducibility-source-alignment

Objetivo da rodada:
Executar uma rodada de evidência reproduzível que alinhe documentação, source rastreado e artifacts de gate. O gate deve fechar lacunas de auditoria sem promover modelo, sem reabrir A3/A4 e sem transformar research em official.

Escopo:
1. Criar ou atualizar o gate `phase6_global_reproducibility_source_alignment_gate`.
2. Provar clean regeneration dos gates Phase 5 relevantes ou registrar divergências com hash, causa objetiva e classificação.
3. Alinhar source e documentação da Fase 4-R4, especialmente a divergência entre a memória que documenta `phase4_config.py`, `phase4_data.py`, `phase4_dsr.py`, `phase4_backtest.py`, `phase4_calibration.py` e o source rastreado atual, que contém `phase4_cpcv.py`, `phase4_gate_diagnostic.py` e `phase4_stage_a_experiment.py`.
4. Persistir artifact empírico de CVaR/stress `rho=1`, drawdown, exposure e decisões de corte para o snapshot/portfolio current.
5. Revalidar que official, research, sandbox, shadow e paper estão separados.
6. Revalidar a bridge/Nautilus apenas como mecanismo técnico, sem alimentar paper official com snapshot não-promotable.
7. Gerar relatório de gate com decisão objetiva PASS/PARTIAL/FAIL/INCONCLUSIVE.

Fora de escopo:
1. Não implementar ajuste de performance, tuning de threshold ou nova hipótese alpha antes do gate de alinhamento.
2. Não promover artifacts para official.
3. Não reabrir A3/A4.
4. Não transformar `ALIVE_BUT_NOT_PROMOTABLE` em promotable.
5. Não usar RiskLabAI como caminho official.
6. Não declarar paper readiness, testnet prolongado ou capital readiness.
7. Não corrigir métrica para passar.
8. Não apagar artifacts sem registrar motivo, hash e impacto.

Fontes de verdade, nesta ordem:
1. Estado atual do repositório.
2. Documentação canônica em `docs/`, especialmente:
   - `docs/SNIPER_v10_10_Especificacao_Definitiva.pdf`
   - `docs/SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf`
   - `docs/SNIPER_openclaw_handoff.md`
   - `docs/SNIPER_regeneration_guide.md`
   - `docs/SNIPER_memoria_especificacao_controle_fase4R_v3.md`
   - `docs/unlock_pressure_rank_technical.md`
3. Relatórios da auditoria global:
   - `reports/audits/global_spec_adherence/next_step_recommendation.md`
   - `reports/audits/global_spec_adherence/global_spec_adherence_summary.json`
   - `reports/audits/global_spec_adherence/global_implementation_checklist.md`
   - `reports/audits/global_spec_adherence/global_spec_adherence_report.md`
   - `reports/audits/global_spec_adherence/global_spec_adherence_matrix.csv`
4. `reports/gates/**`.
5. `data/models/**`, `data/models/research/**` e `data/parquet/**`, quando aplicável.

Resumo da auditoria global:
1. Veredito global atual: `GLOBAL_PARTIAL`.
2. O SNIPER tem implementação substancial, mas ainda não está aderente o suficiente para promoção operational/paper official.
3. Top blockers:
   - Artifacts oficiais atuais registram `dsr_honest=0.0` com `n_trials_honest=5000`.
   - Diagnóstico official aponta Sharpe OOS `0.3494` e subperíodos insuficientes em caminho bloqueante.
   - Família cross-sectional está `ALIVE_BUT_NOT_PROMOTABLE`.
   - Memória Fase 4-R4 documenta módulos `phase4_config/data/dsr/backtest/calibration` que não existem como source rastreado.
   - CVaR existe em código, mas falta artifact empírico official persistido para auditoria direta.
4. Lacunas que motivam este gate:
   - Prova de regeneração limpa dos artifacts.
   - Alinhamento source-doc-artifact da Fase 4-R4.
   - Persistência auditável de CVaR/stress/drawdown/exposure.
   - Separação verificável entre official, research, sandbox, shadow e paper.
   - Isolamento explícito do PDF `SNIPER_v10.10_Technical_Architecture_presentation.pdf`, que permaneceu inconclusivo sem OCR/render dedicado.
5. O que já está satisfatório e não deve ser refeito sem necessidade:
   - Arquitetura rev5 de `unlock_pressure_rank` com observado, reconstruído, proxies, `feature_state` e audit fields fora de `X`.
   - Fracdiff em log-space com `tau=1e-5` e seleção expanding.
   - Regime filter com winsorização, `RobustScaler`, PCA e HMM walk-forward.
   - Triple-barrier HLC e market impact por raiz quadrada com `sigma_intraday`.
   - Bridge paper/Nautilus com Redis Streams, `FULL_SNAPSHOT`, idempotência, stale snapshot, daemon e status terminal.

Contexto fixo de governança:
1. A3 está encerrado como structural choke.
2. Não reabrir A3/A4 sem evidência nova forte.
3. RiskLabAI permanece oracle/shadow, não official.
4. Fast path permanece official.
5. Família cross-sectional soberana está `ALIVE_BUT_NOT_PROMOTABLE`.
6. Baseline research-only correta: `phase5_cross_sectional_sovereign_closure_restored`.
7. Não promover research para official sem gate explícito.
8. Não tratar melhoria parcial como aprovação se DSR honesto continuar 0.0.
9. Não usar narrativa para compensar ausência de artifact.
10. Se artifacts necessários estiverem ausentes, classificar como `INCONCLUSIVE`, não como PASS.

Instruções de execução:
1. Antes de qualquer alteração, rode:
   - `git status --short`
   - `git branch --show-current`
   - `git rev-parse HEAD`
2. Se houver mudanças não commitadas, identifique-as e não sobrescreva trabalho pré-existente. Se houver artifacts da auditoria global ainda untracked, preserve-os.
3. Crie ou troque para a branch sugerida:
   - Se a branch não existir, crie a partir de `codex/openclaw-sniper-handoff`.
   - Se a branch já existir, faça checkout dela.
4. Leia a documentação e artifacts relevantes antes de editar.
5. Faça um plano curto e explícito antes de alterar código.
6. Implemente apenas o necessário para o gate. Se já existir script de gate equivalente, use-o/ajuste-o em vez de criar fluxo paralelo.
7. Se precisar criar script dedicado, use o caminho:
   - `services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py`
8. Gere obrigatoriamente os artifacts do gate em:
   - `reports/gates/phase6_global_reproducibility_source_alignment_gate/`
9. O gate deve produzir evidência reproduzível, não narrativa.
10. Não promova nada para official.

Entregáveis obrigatórios:
1. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`
2. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.md`
3. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_manifest.json`
4. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_metrics.parquet`, se houver métrica tabular.
5. `reports/gates/phase6_global_reproducibility_source_alignment_gate/source_doc_alignment.json`
6. `reports/gates/phase6_global_reproducibility_source_alignment_gate/portfolio_cvar_report.json`
7. Um relatório ou seção de clean regeneration com:
   - comandos executados;
   - exit codes;
   - hashes before/after;
   - artifacts esperados;
   - artifacts ausentes;
   - divergências objetivas;
   - classificação por artifact.
8. Um relatório ou seção de fronteiras de governança com:
   - official;
   - research;
   - sandbox;
   - shadow;
   - paper;
   - RiskLabAI excluído de official;
   - cross-sectional marcado como `ALIVE_BUT_NOT_PROMOTABLE`.
9. Um registro explícito dos blockers quantitativos preservados:
   - `dsr_honest`;
   - `n_trials_honest`;
   - Sharpe OOS;
   - subperíodos;
   - cross-sectional status;
   - CVaR empirical status.

Estrutura mínima de `gate_report.json`:
1. `status`: `PASS`, `PARTIAL`, `FAIL` ou `INCONCLUSIVE`.
2. `decision`: `advance`, `correct`, `abandon` ou `inconclusive`.
3. `gate_slug`: `phase6_global_reproducibility_source_alignment_gate`.
4. `branch`.
5. `commit`.
6. `base_branch`.
7. `artifacts_official_used`.
8. `artifacts_research_used`.
9. `artifacts_generated`.
10. `metrics`.
11. `source_doc_alignment`.
12. `clean_regeneration`.
13. `portfolio_cvar`.
14. `governance_boundaries`.
15. `blockers`.
16. `risks`.
17. `recommendation`.
18. `commands`.

Critérios objetivos:

PASS:
1. Clean regeneration reproduz os artifacts esperados ou explica diferenças com hashes e causa objetiva.
2. Source e documentação Fase 4-R4 ficam alinhados por restauração de módulos ou correção documental versionada.
3. CVaR empirical artifact é persistido e validável.
4. Nenhum artifact research/RiskLabAI é tratado como official.
5. A3/A4 permanecem fechados.
6. `ALIVE_BUT_NOT_PROMOTABLE` permanece não-promotable.
7. O PASS do gate significa apenas "alinhamento/reprodutibilidade aprovados"; não significa model promotion, paper readiness ou capital readiness.

PARTIAL:
1. Regeneration roda, mas algum artifact local não reproduz exatamente e a divergência fica explicada.
2. CVaR é persistido, mas ainda não há PASS quantitativo global.
3. Technical Architecture PDF permanece inconclusivo, mas a lacuna fica documentada e isolada.
4. Source-doc Fase 4-R4 é parcialmente resolvido, mas resta pendência objetiva com plano de correção.

FAIL:
1. Regeneration não roda.
2. Artifacts official divergem sem explicação objetiva.
3. Source-doc Fase 4-R4 permanece contraditório.
4. Qualquer research/shadow é promovido como official.
5. A3/A4 são reabertos sem nova evidência causal forte.
6. O gate tenta alterar threshold para forçar PASS.

INCONCLUSIVE:
1. Artifacts necessários estão ausentes.
2. Dependência externa, parquet, PDF, modelo ou manifest necessário não pode ser lido.
3. CVaR empírico não pode ser calculado por falta de snapshot/portfolio/input verificável.
4. Clean regeneration não pode ser concluída por dependência ambiental não resolvida.
5. Evidência insuficiente para PASS/FAIL sem inventar inferência.

Comandos esperados:

```powershell
$env:PYTHONUTF8='1'
git status --short
git branch --show-current
git rev-parse HEAD
git checkout codex/openclaw-sniper-handoff
git checkout -b codex/phase6-global-reproducibility-source-alignment
```

Se a branch já existir:

```powershell
$env:PYTHONUTF8='1'
git checkout codex/phase6-global-reproducibility-source-alignment
```

Leitura e inspeção inicial:

```powershell
$env:PYTHONUTF8='1'
Get-Content docs\SNIPER_openclaw_handoff.md -Encoding UTF8
Get-Content docs\SNIPER_regeneration_guide.md -Encoding UTF8
Get-Content reports\audits\global_spec_adherence\next_step_recommendation.md -Encoding UTF8
Get-Content reports\audits\global_spec_adherence\global_spec_adherence_summary.json -Encoding UTF8
Get-ChildItem reports\gates -Directory
Get-ChildItem data\models\research -Directory
git ls-files services/ml_engine/phase4_*.py services/ml_engine/phase5_*.py
```

Regeneration e diagnósticos Phase 5/Phase 4:

```powershell
$env:PYTHONUTF8='1'
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py
python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
python services/ml_engine/phase4_gate_diagnostic.py
```

Script do gate, se criado ou existente:

```powershell
$env:PYTHONUTF8='1'
python services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py
```

Testes quantitativos e de governança aplicáveis:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/unit -q
```

Testes bridge/Nautilus mínimos se o gate tocar snapshot, paper bridge, Redis Streams, idempotência ou daemon:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/unit/test_nautilus_bridge_contract.py tests/unit/test_nautilus_bridge_acceptance.py tests/unit/test_nautilus_bridge_publisher.py tests/unit/test_nautilus_bridge_consumer.py tests/unit/test_nautilus_bridge_reconciler.py tests/unit/test_nautilus_bridge_daemon.py
```

Comandos Docker apenas se o gate tocar execução/paper com Redis real:

```powershell
docker compose ps
docker compose up -d redis
docker compose logs --tail=200 redis
docker compose down
```

Validação dos artifacts:

```powershell
$env:PYTHONUTF8='1'
python -m json.tool reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json > $null
python -m json.tool reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_manifest.json > $null
python -m json.tool reports/gates/phase6_global_reproducibility_source_alignment_gate/source_doc_alignment.json > $null
python -m json.tool reports/gates/phase6_global_reproducibility_source_alignment_gate/portfolio_cvar_report.json > $null
Get-ChildItem reports\gates\phase6_global_reproducibility_source_alignment_gate
git status --short
```

Se `gate_metrics.parquet` for gerado, valide leitura com o runtime disponível. Se não houver engine parquet instalada, registre como `INCONCLUSIVE` ou adicione validação compatível sem mascarar erro.

Restrições invioláveis:
1. Não reabrir A3/A4.
2. Não promover para official.
3. Não mascarar `DSR=0.0`.
4. Não tratar `ALIVE_BUT_NOT_PROMOTABLE` como promotable.
5. Não apagar artifacts sem registrar motivo.
6. Não usar informação realizada para decisão ex-ante.
7. Não criar gate PASS sem evidência reproduzível.
8. Não commitar reports pesados sem instrução explícita.
9. Não alterar thresholds de DSR, Sharpe, PBO, ECE, N_eff ou subperíodos para forçar PASS.
10. Não alimentar paper official com snapshot não-promotable.
11. Não usar RiskLabAI como official.
12. Não compensar falta de artifact com narrativa.

Resposta final esperada:
1. Veredito do gate.
2. Métricas principais, incluindo `dsr_honest`, Sharpe OOS, subperíodos, CVaR/stress/drawdown/exposure e status cross-sectional.
3. Arquivos alterados.
4. Artifacts criados.
5. Comandos executados e resultado resumido.
6. Próximo passo recomendado.
7. Blocos de risco/remediação.
8. Confirmação explícita de que nada foi promovido para official, A3/A4 não foram reabertos e `ALIVE_BUT_NOT_PROMOTABLE` não foi tratado como promotable.
