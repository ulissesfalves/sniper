## Resumo executivo

H06 diagnostic gate result: PASS/advance. Classification: H06_UNLOCK_SHADOW_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation` at `0f951fad4afbcf0055ff3339c03b7f3588e79900`.

## Mudanças implementadas

Added research/diagnostic-only unlock shadow feature ablation evidence from canonical unlock artifacts.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_unlock_shadow_feature_ablation_gate\unlock_artifact_inventory.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_unlock_shadow_feature_ablation_gate\unlock_shadow_feature_ablation_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_unlock_shadow_feature_ablation_gate\unlock_shadow_feature_ablation_metrics.parquet`

## Resultados

classification=H06_UNLOCK_SHADOW_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE
unlock_file_count=69
unlock_symbols=69
phase4_joined_rows_with_selected_unlock=15665
phase4_joined_coverage_selected_unlock=0.165016
shadow_mode_detected=true
research_candidate_found=false
official_promotion_allowed=false
paper_readiness_allowed=false

## Avaliação contra gates

- required_artifacts_present: True / true => PASS
- unlock_file_count: 69 / >= 1 => PASS
- quality_summary_present: True / true => PASS
- phase4_joined_rows_with_selected_unlock: 15665 / > 0 for diagnostic => PASS
- phase4_joined_coverage_selected_unlock: 0.165016 / > 0 for diagnostic => PASS
- shadow_mode_detected: True / reported, not promotion => PASS
- observed_coverage_median: 0.0 / reported => PASS
- proxy_fallback_coverage_median: 0.979592 / reported => PASS
- selected_unlock_spearman_to_pnl_real_diagnostic: -0.021533 / diagnostic only => PASS
- research_candidate_found: False / false for diagnostic-only H06 => PASS
- official_promotion_allowed: False / false => PASS
- paper_readiness_allowed: False / false => PASS

## Riscos residuais

- H06 used pnl_real only as a diagnostic outcome, not as an ex-ante rule.
- Unlock artifacts remain research/shadow diagnostics and are not official promotion evidence.
- The diagnostic does not authorize paper readiness, official promotion, merge, A3/A4 reopening or threshold relaxation.

## Veredito final: advance / correct / abandon

PASS/advance. H06 diagnostic complete; no research candidate or official promotion.
