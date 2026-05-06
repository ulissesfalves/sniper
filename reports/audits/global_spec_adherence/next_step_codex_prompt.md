# Next Step Codex Prompt

Generated from:

- `reports/audits/global_spec_adherence/next_step_recommendation.md`
- `reports/audits/global_spec_adherence/global_spec_adherence_summary.json`
- `reports/audits/global_spec_adherence/global_implementation_checklist.md`
- `reports/gates/phase6_pr_review_global_adherence_gate/gate_report.json`

```text
$sniper-gate-governance

Revise o PR #1 da branch codex/autonomous-sniper-implementation contra a especificacao global do SNIPER.

Repo local:
C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous

Base:
codex/openclaw-sniper-handoff

Branch do PR:
codex/autonomous-sniper-implementation

PR:
https://github.com/ulissesfalves/sniper/pull/1

Gate slug:
phase6_pr_review_global_adherence_gate

Objetivo da rodada:
Revisar o PR #1 como draft de governanca/reprodutibilidade Phase6, confirmar aderencia a especificacao global, preservar os blockers DSR/CVaR/promotabilidade e impedir qualquer leitura de readiness operacional ou promocao official.

Escopo:
1. Confirmar estado do PR: draft, open, nao merged.
2. Confirmar que a branch do PR continua em codex/autonomous-sniper-implementation.
3. Confirmar que a base continua codex/openclaw-sniper-handoff.
4. Revisar os reports Phase6, a auditoria global e o gate phase6_pr_review_global_adherence_gate.
5. Se o gate pack phase6_pr_review_global_adherence_gate ja existir, revalidar e atualizar somente se houver nova evidencia ou inconsistencias.
6. Registrar resultado como documentacao/auditoria, nao como implementacao funcional.

Fora de escopo:
1. Nao implementar correcoes funcionais.
2. Nao alterar modelo, feature, sizing, bridge, execution, data ingestion ou paper daemon.
3. Nao promover research para official.
4. Nao abrir PR ready.
5. Nao fazer merge.
6. Nao reabrir A3/A4.
7. Nao criar credenciais, ordens reais ou operacao com capital real.

Fontes de verdade:
1. Estado atual do repositorio.
2. docs/SNIPER_openclaw_handoff.md
3. docs/SNIPER_regeneration_guide.md
4. reports/audits/global_spec_adherence/next_step_recommendation.md
5. reports/audits/global_spec_adherence/global_spec_adherence_summary.json
6. reports/audits/global_spec_adherence/global_implementation_checklist.md
7. reports/audits/global_spec_adherence/global_spec_adherence_report.md
8. reports/audits/global_spec_adherence/global_spec_adherence_matrix.csv
9. reports/gates/phase6_global_reproducibility_source_alignment_gate/**
10. reports/gates/phase6_source_doc_and_regeneration_preflight_gate/**
11. reports/gates/phase6_phase4_artifact_rehydration_and_dsr_stop_gate/**
12. reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/**
13. reports/gates/phase6_pr_review_global_adherence_gate/**, se existir.
14. data/models/phase4/** e data/models/research/** somente como artifacts de entrada/ignored auditados, nunca como promocao official.

Resumo da auditoria global:
Veredito global: GLOBAL_PARTIAL.

O que esta satisfatorio:
1. Clean regeneration foi provada em clone limpo/equivalente.
2. Artifacts official Phase4 foram encontrados e hasheados.
3. Artifacts research baseline foram encontrados e hasheados.
4. Source-doc-artifact alignment esta ALIGNED.
5. Phase5 clean restore retornou PASS/advance.
6. PR #1 esta draft e nao merged.

Blockers principais:
1. dsr_honest=0.0.
2. dsr_passed=false.
3. CVaR official esta em PASS_ZERO_EXPOSURE.
4. cvar_economic_status=NOT_PROVEN_ZERO_EXPOSURE.
5. Snapshot official tem zero exposure.
6. Cross-sectional permanece ALIVE_BUT_NOT_PROMOTABLE.

Lacuna que motiva o gate:
O PR deve ser revisado como evidencia de governanca/reprodutibilidade, mas nao pode ser tratado como readiness operacional ou promocao. O gate deve tornar essa classificacao inequivoca.

Instrucoes de execucao:
1. Verifique branch, commit, PR e worktree antes de alterar qualquer arquivo.
2. Leia a documentacao e os artifacts listados nas fontes de verdade.
3. Separe explicitamente official, research, sandbox e paper.
4. Leia o gate final phase6_research_baseline_rehydration_clean_regeneration_gate.
5. Leia o gate phase6_pr_review_global_adherence_gate, se ja existir.
6. Se o gate ja estiver correto e atualizado, nao gere alteracao desnecessaria.
7. Se houver nova evidencia, inconsistencias ou drift documental, atualize somente o gate pack e/ou reports de auditoria.
8. Mantenha a classificacao PARTIAL/correct enquanto DSR=0.0, CVaR zero exposure e cross-sectional not promotable continuarem.
9. Nao promova nada para official.

Entregaveis esperados:
1. reports/gates/phase6_pr_review_global_adherence_gate/gate_report.json
2. reports/gates/phase6_pr_review_global_adherence_gate/gate_report.md
3. reports/gates/phase6_pr_review_global_adherence_gate/gate_manifest.json
4. reports/gates/phase6_pr_review_global_adherence_gate/gate_metrics.parquet, se houver metrica tabular
5. Atualizacoes documentais em reports/audits/global_spec_adherence/** apenas se necessarias.

Conteudo minimo do gate_report.json:
1. status: PASS, PARTIAL ou FAIL.
2. decision: review, correct ou freeze/abandon conforme evidencia.
3. gate_slug.
4. branch e commit.
5. pr_url.
6. official_artifacts_used.
7. research_artifacts_used ou generated.
8. metricas medidas.
9. blockers.
10. riscos.
11. recomendacao objetiva.

Criterios objetivos:

PASS / review:
1. PR permanece draft.
2. PR esta open e nao merged.
3. Nenhuma promocao official foi introduzida.
4. A3/A4 continuam fechados.
5. RiskLabAI permanece oracle/shadow.
6. DSR=0.0 continua explicitamente blocker.
7. CVaR zero exposure continua explicitamente caveat tecnico, nao robustez economica.
8. Human review pode aceitar o PR apenas como governanca/reprodutibilidade.

PARTIAL / correct:
1. PR esta correto em direcao, mas precisa ajuste documental.
2. Gate pack precisa refresh por drift documental ou nova evidencia.
3. Gate pack esta presente, mas precisa refresh por drift documental.
4. Nenhuma violacao de governanca foi detectada.

FAIL / freeze:
1. Qualquer texto ou codigo implica promocao official.
2. Qualquer texto trata PASS_ZERO_EXPOSURE como robustez economica.
3. Qualquer mudanca reabre A3/A4 sem evidencia forte.
4. Qualquer mudanca trata research como official.
5. Qualquer tentativa avanca readiness com DSR honesto igual a 0.0.

INCONCLUSIVE:
1. O estado do PR nao puder ser confirmado.
2. Reports ou artifacts obrigatorios do gate final estiverem ausentes.
3. A clean regeneration nao puder ser verificada pelos reports existentes.

Comandos esperados:
```powershell
git checkout codex/autonomous-sniper-implementation
git branch --show-current
git rev-parse HEAD
git status --short
git log --oneline codex/openclaw-sniper-handoff..HEAD
git diff --name-status codex/openclaw-sniper-handoff..HEAD
Get-Content reports/audits/global_spec_adherence/next_step_recommendation.md
Get-Content reports/audits/global_spec_adherence/global_spec_adherence_summary.json
Get-Content reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/gate_report.json
Get-Content reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/phase4_artifact_integrity_report.json
Get-Content reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/portfolio_cvar_report.json
Get-Content reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/clean_regeneration_report.json
Get-Content reports/gates/phase6_pr_review_global_adherence_gate/gate_report.json
```

Teste opcional se for necessario revalidar ambiente:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py tests/unit/test_phase5_cross_sectional_latest_headroom_reconciliation_audit.py tests/unit/test_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py tests/unit/test_phase5_cross_sectional_sovereign_hardening_recheck.py tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q
```

Restricoes:
1. Nao reabrir A3/A4.
2. Nao promover para official.
3. Nao mascarar DSR=0.0.
4. Nao tratar ALIVE_BUT_NOT_PROMOTABLE como promotable.
5. Nao tratar PASS_ZERO_EXPOSURE como robustez economica.
6. Nao apagar artifacts sem registrar motivo.
7. Nao usar informacao realizada para decisao ex-ante.
8. Nao criar gate PASS sem evidencia reproduzivel.
9. Nao commitar reports pesados sem instrucao explicita.
10. Nao operar capital real.
11. Nao criar ou salvar segredos.
12. Nao fazer merge.
13. Nao fazer force push.

Resposta final esperada do Codex:
1. Veredito do gate.
2. Confirmacao objetiva dos 9 itens do PR review.
3. Metricas principais: PR draft/open/unmerged, clean regeneration, artifact integrity, DSR, CVaR, promotabilidade.
4. Arquivos alterados.
5. Artifacts criados ou atualizados.
6. Comandos executados.
7. Riscos remanescentes.
8. Proxima acao recomendada.
```
