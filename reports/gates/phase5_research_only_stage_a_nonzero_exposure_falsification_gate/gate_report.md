## Resumo executivo

Research-only Stage A nonzero exposure thesis result: `FAIL/abandon`. Classification: `ONLY_REALIZED_ELIGIBILITY_LOOKS_GOOD_SAFE_POLICY_FAILS`.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation`, commit `bd0719c722326c808a05a66aaa7c43ae1edcefb8`. Official Phase4 remains blocked by `dsr_honest=0.0` and zero-exposure CVaR.

## Mudanças implementadas

Added a research/sandbox-only falsification harness. The safe policy selects top1 by `rank_score_stage_a` per combo/date without using realized eligibility. The unsafe `stage_a_eligible` policy is reported only as a leakage diagnostic.

## Artifacts gerados

- `reports\gates\phase5_research_only_stage_a_nonzero_exposure_falsification_gate\stage_a_nonzero_exposure_summary.json`
- `reports\gates\phase5_research_only_stage_a_nonzero_exposure_falsification_gate\stage_a_nonzero_exposure_combo_metrics.parquet`
- `gate_report.json`
- `gate_report.md`
- `gate_manifest.json`
- `gate_metrics.parquet`

## Resultados

Safe selected events: `10576`; safe selected dates: `2112`; safe median combo Sharpe: `-0.727203`. Unsafe diagnostic median combo Sharpe: `14.525046`.

## Avaliação contra gates

PASS would require nonzero exposure plus safe median combo Sharpe above the historical promotion SR need `4.47` and positive minimum combo Sharpe. The safe policy fails that merit criterion. The only high Sharpe path uses realized `stage_a_eligible`, so it is not an admissible ex-ante decision rule.

## Riscos residuais

DSR remains zero, official CVaR remains zero exposure, and cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`. This gate does not support paper readiness or promotion.

## Veredito final: advance / correct / abandon

`abandon`. Abandon this Stage A nonzero-exposure thesis as a promotion path; future work needs a materially different research-only hypothesis or a freeze decision.
