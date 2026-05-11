$sniper-global-spec-adherence-audit

Audite globalmente a branch atual do PR draft #1 do SNIPER contra a especificacao e a governanca, sem implementar codigo.

Repo:
C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous

Branch:
codex/autonomous-sniper-implementation

PR:
https://github.com/ulissesfalves/sniper/pull/1

Objetivo:
Atualizar a auditoria global consolidada para o estado atual do PR draft depois de H06, do POST_MISSION_NEXT_STEP_DECISION_PROTOCOL e da revisao final manual do PR.

Classificacao esperada:
GLOBAL_PARTIAL ou REVIEW_DRAFT_PR, nunca readiness operacional.

Escopo:
1. Confirmar branch, HEAD, git status e PR #1.
2. Confirmar que o PR permanece draft, open e not merged.
3. Revisar `reports/state/sniper_next_step_decision.json`.
4. Revisar `reports/state/sniper_current_state.json`.
5. Revisar `reports/state/sniper_spec_gap_backlog.yaml`.
6. Revisar `reports/audits/pr_review/pr1_final_review_report.md`.
7. Revisar `reports/audits/pr_review/pr1_final_review_summary.json`.
8. Revisar `reports/audits/pr_review/pr1_final_review_checklist.md`.
9. Revisar os gates Phase6 de reprodutibilidade/source alignment.
10. Revisar os gates Phase5 research-only, falsificacao, agenda expansion, final freeze opportunity audit e H06 unlock shadow diagnostic.
11. Atualizar somente os arquivos de auditoria global em `reports/audits/global_spec_adherence/**`, se houver drift documental.

Fora de escopo:
1. Nao implementar nova feature.
2. Nao criar nova tese research-only.
3. Nao criar novo gate quantitativo.
4. Nao promover official.
5. Nao declarar paper readiness.
6. Nao fazer merge.
7. Nao marcar PR ready.
8. Nao reabrir A3/A4.
9. Nao relaxar thresholds.
10. Nao operar capital real.
11. Nao criar credenciais.

Fontes de verdade:
1. Estado atual do repositorio.
2. `AGENTS.md`
3. `docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md`
4. `docs/SNIPER_openclaw_handoff.md`, se existir.
5. `docs/SNIPER_regeneration_guide.md`, se existir.
6. `reports/state/**`
7. `reports/audits/pr_review/**`
8. `reports/audits/autonomous_stop_review/**`
9. `reports/audits/global_spec_adherence/**`
10. `reports/gates/**`
11. `data/models/phase4/**`
12. `data/models/research/**`
13. `data/parquet/unlocks/**`
14. `data/parquet/unlock_diagnostics/unlock_quality_daily.parquet`

Contexto atual:
- PR #1 esta draft, open e not merged.
- Revisao manual de governanca ja foi registrada no PR.
- H06 `phase5_research_unlock_shadow_feature_ablation_gate` foi executado.
- Resultado H06: `H06_UNLOCK_SHADOW_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE`.
- Unlock artifacts foram encontrados, hasheados e usados apenas como diagnostico shadow/proxy-heavy.
- Nao ha candidata research sobrevivente.
- Nao ha proximo gate interno seguro registrado.
- Decisao pos-missao: `REVIEW_DRAFT_PR`.
- Alternativa futura: `REQUEST_MATERIAL_EVIDENCE`.

Blockers que devem permanecer explicitos:
- `dsr_honest=0.0`.
- `dsr_passed=false`.
- CVaR official permanece zero exposure.
- `PASS_ZERO_EXPOSURE` nao prova robustez economica.
- Cross-sectional permanece `ALIVE_BUT_NOT_PROMOTABLE`.
- H06 e diagnostic-only, nao official.
- Nenhuma pesquisa/sandbox pode ser tratada como promotable.

Entregaveis esperados:
1. `reports/audits/global_spec_adherence/global_spec_adherence_report.md`
2. `reports/audits/global_spec_adherence/global_spec_adherence_matrix.csv`
3. `reports/audits/global_spec_adherence/global_spec_adherence_summary.json`
4. `reports/audits/global_spec_adherence/global_implementation_checklist.md`
5. `reports/audits/global_spec_adherence/next_step_recommendation.md`

Criterios PASS/PARTIAL/FAIL:

PASS / review:
- A auditoria global esta atualizada para o head atual.
- PR #1 permanece draft e not merged.
- Nenhuma promocao official e recomendada.
- Nenhuma paper readiness e recomendada.
- DSR/CVaR/promotabilidade permanecem blockers.
- A recomendacao final e revisar PR draft ou aguardar evidencia material nova.

PARTIAL / correct:
- Ha drift documental pequeno que exige correcao em auditoria global.
- O PR segue governado, mas a auditoria precisa atualizar head, H06 ou decisao pos-missao.

FAIL / freeze:
- Qualquer texto recomenda promocao official com `dsr_honest=0.0`.
- Qualquer texto trata CVaR zero exposure como robustez economica.
- Qualquer texto trata H06 shadow/proxy-heavy como official.
- Qualquer texto recomenda merge ou paper readiness.
- Qualquer texto reabre A3/A4 sem evidencia nova forte.

Comandos esperados:
```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git log --oneline -10
git diff --name-status codex/openclaw-sniper-handoff..HEAD
Get-Content reports/state/sniper_next_step_decision.json
Get-Content reports/state/sniper_current_state.json
Get-Content reports/audits/pr_review/pr1_final_review_summary.json
Get-Content reports/gates/phase5_research_unlock_shadow_feature_ablation_gate/gate_report.json
```

Resposta final esperada:
1. Veredito global.
2. Se a auditoria global foi atualizada.
3. Arquivos alterados.
4. Confirmacao de que PR permanece draft/not merged.
5. Confirmacao de que nao ha promocao official nem paper readiness.
6. Blockers remanescentes.
7. Proxima acao recomendada.
8. Se deve ou nao commitar os arquivos de auditoria.


<!-- Executed by phase6_post_h06_global_spec_reaudit_gate at 2026-05-11T00:54:46Z. -->
