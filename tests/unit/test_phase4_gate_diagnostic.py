from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase4_gate_diagnostic as gate_diag


def _write_parquet_like(df: pd.DataFrame, path: Path) -> None:
    df.to_pickle(path)


class Phase4GateDiagnosticTest(unittest.TestCase):
    def test_collect_git_baseline_prefers_env_override(self) -> None:
        original = {
            key: os.environ.get(key)
            for key in [
                "SNIPER_GIT_BRANCH",
                "SNIPER_GIT_HEAD",
                "SNIPER_GIT_STATUS_SHORT",
                "SNIPER_GIT_DIFF_STAT",
                "SNIPER_GIT_WORKTREE_STATE",
            ]
        }
        try:
            os.environ["SNIPER_GIT_BRANCH"] = "main"
            os.environ["SNIPER_GIT_HEAD"] = "abc123"
            os.environ["SNIPER_GIT_STATUS_SHORT"] = " M foo.py\n?? bar.py"
            os.environ["SNIPER_GIT_DIFF_STAT"] = " foo.py | 2 +-"
            os.environ["SNIPER_GIT_WORKTREE_STATE"] = "dirty"

            result = gate_diag.collect_git_baseline(repo_root=Path.cwd())
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(result["source"], "env_override")
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["head"], "abc123")
        self.assertEqual(result["status_short"], [" M foo.py", "?? bar.py"])
        self.assertEqual(result["working_tree_state"], "dirty")

    def test_collect_ia_official_status_returns_false_when_official_path_has_no_ia(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            features = base / "models" / "features"
            phase3 = base / "models" / "phase3"
            phase4 = base / "models" / "phase4"
            features.mkdir(parents=True)
            phase3.mkdir(parents=True)
            phase4.mkdir(parents=True)

            _write_parquet_like(pd.DataFrame({"ret_1d": [0.01], "btc_ma200_flag": [1.0]}), features / "AAA.parquet")
            _write_parquet_like(pd.DataFrame({"p_bma": [0.6], "y_target": [1]}), phase3 / "AAA_meta.parquet")
            _write_parquet_like(pd.DataFrame({"symbol": ["AAA"], "p_bma_pkf": [0.7]}), phase4 / "phase4_execution_snapshot.parquet")
            _write_parquet_like(pd.DataFrame({"symbol": ["AAA"], "p_bma_pkf": [0.7]}), phase4 / "phase4_aggregated_predictions.parquet")

            phase4_report = {
                "selected_features": ["p_bma_pkf", "btc_ma200_flag", "close_fracdiff"],
            }

            result = gate_diag.collect_ia_official_status(
                phase4_report=phase4_report,
                features_path=features,
                phase3_path=phase3,
                phase4_paths=[
                    phase4 / "phase4_execution_snapshot.parquet",
                    phase4 / "phase4_aggregated_predictions.parquet",
                ],
                phase4_source_path=Path("missing_phase4_cpcv.py"),
            )

        self.assertFalse(result["official_path_uses_ia"])
        self.assertEqual(result["binary_answer"], "NAO")
        self.assertEqual(result["selected_feature_ia_overlap"], [])
        self.assertIn("manifest_or_config_official_declaring_ia_path_disabled", result["cleanup_needed"])

    def test_collect_phase2_auc_provenance_reclassifies_downstream_meta_auc_as_not_directly_auditable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            phase2 = base / "models" / "phase2"
            phase3 = base / "models" / "phase3"
            phase2.mkdir(parents=True)
            phase3.mkdir(parents=True)

            phase2_report = {
                "pbma_presence": {
                    "logical_location": "/data/models/phase3/*_meta.parquet",
                }
            }
            (phase2 / "diagnostic_report.json").write_text(json.dumps(phase2_report), encoding="utf-8")

            meta_df = pd.DataFrame(
                {
                    "p_bma": [0.9, 0.2, 0.8, 0.1],
                    "y_target": [1, 0, 1, 0],
                }
            )
            _write_parquet_like(meta_df, phase3 / "AAA_meta.parquet")
            main_source = base / "main.py"
            main_source.write_text(
                """
def run_meta_labeling_for_symbol():
    \"\"\"Meta-Labeling pipeline (Partes 8-9 spec v10.10)\"\"\"
    return {"p_bma": 0.5}

def save_phase3_results():
    payload = {"p_bma": meta_result["p_bma"]}
""".strip(),
                encoding="utf-8",
            )

            result = gate_diag.collect_phase2_auc_provenance(
                phase2_report=phase2_report,
                phase2_path=phase2,
                phase3_path=phase3,
                main_source_path=main_source,
            )

        self.assertFalse(result["directly_auditable"])
        self.assertFalse(result["official_phase2_auc_artifact_found"])
        self.assertTrue(result["phase3_meta_is_downstream_meta_labeling"])
        self.assertAlmostEqual(result["derived_auc_from_phase3_meta"]["auc_oos_pooled"], 1.0, places=6)

    def test_build_blocker_reclassification_marks_direct_phase4_failures_as_hard_blockers(self) -> None:
        phase4_report = {
            "dsr": {"dsr_honest": 0.0, "n_trials_honest": 5000, "sr_needed": 4.26},
            "fallback": {"policy": "fixed_small_080", "sharpe": 0.3494, "n_active": 222},
            "subperiods": [
                {"period": "2021", "positive": True},
                {"period": "2022", "positive": True},
                {"period": "2023", "positive": False},
                {"period": "2024+", "positive": True},
            ],
        }
        blockers = gate_diag.build_blocker_reclassification(
            phase4_report=phase4_report,
            ia_status={"official_path_uses_ia": False, "source_artifacts": []},
            phase2_auc_provenance={
                "directly_auditable": False,
                "official_gate_explanation": "not directly auditable",
                "derived_auc_from_phase3_meta": {"auc_oos_pooled": 0.52},
                "source_artifacts": [],
                "official_phase2_auc_artifact_found": False,
            },
            cvar_empirical_audit={"structure_present": True, "source_artifacts": []},
        )

        self.assertEqual(blockers["DSR honesto"]["classification"], gate_diag.CLASS_HARD_BLOCKER)
        self.assertEqual(blockers["Sharpe OOS"]["classification"], gate_diag.CLASS_HARD_BLOCKER)
        self.assertEqual(blockers["Subperiodos"]["classification"], gate_diag.CLASS_HARD_BLOCKER)
        self.assertTrue(blockers["Sharpe OOS"]["alone_blocks_advance"])
        self.assertEqual(blockers["AUC OOS da Fase 2"]["classification"], gate_diag.CLASS_NAO_AUDITADO)
        self.assertEqual(blockers["IA alpha/r2"]["classification"], gate_diag.CLASS_GOVERNANCA)

    def test_collect_cvar_empirical_audit_separates_structural_guard_from_missing_empirical_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            phase3 = base / "models" / "phase3"
            phase4 = base / "models" / "phase4"
            phase3.mkdir(parents=True)
            phase4.mkdir(parents=True)
            _write_parquet_like(pd.DataFrame({"kelly_frac": [0.05], "position_usdt": [1000.0]}), phase3 / "AAA_sizing.parquet")
            _write_parquet_like(pd.DataFrame({"symbol": ["AAA"], "position_usdt": [0.0]}), phase4 / "phase4_execution_snapshot.parquet")
            _write_parquet_like(pd.DataFrame({"symbol": ["AAA"], "p_bma_pkf": [0.7]}), phase4 / "phase4_aggregated_predictions.parquet")

            main_source = base / "main.py"
            main_source.write_text(
                """
PORTFOLIO_CVAR_LIMIT = 0.15
from sizing.kelly_cvar import compute_cvar_stress
reduction = CVAR_LIMIT / max(cvar_stress, 1e-8)
""".strip(),
                encoding="utf-8",
            )
            kelly_source = base / "kelly_cvar.py"
            kelly_source.write_text(
                """
CVAR_LIMIT          = 0.15
MAX_KELLY_CAP       = 0.08
""".strip(),
                encoding="utf-8",
            )
            result = gate_diag.collect_cvar_empirical_audit(
                phase4_report={},
                main_source_path=main_source,
                kelly_cvar_source_path=kelly_source,
                phase3_path=phase3,
                phase4_paths=[
                    phase4 / "phase4_execution_snapshot.parquet",
                    phase4 / "phase4_aggregated_predictions.parquet",
                ],
            )

        self.assertTrue(result["structure_present"])
        self.assertFalse(result["persisted_empirical_artifact_found"])

    def test_build_hard_blocker_causality_distinguishes_meta_model_from_policy_layer(self) -> None:
        phase4_report = {
            "cpcv": {"status": "PASS"},
            "dsr": {"sharpe_is": 0.3494, "dsr_honest": 0.0, "sr_needed": 4.26, "n_trials_honest": 5000},
            "fallback": {
                "policy": "fixed_small_080",
                "threshold": 0.80,
                "sharpe": 0.3494,
                "n_active": 222,
                "win_rate": 0.5586,
                "score_bucket_diagnostics": [
                    {"bucket": "0.55-0.60", "sharpe": -0.37, "n_trades": 1000},
                    {"bucket": ">0.80", "sharpe": 0.3494, "n_trades": 222, "cum_return": 0.0096, "subperiods_positive": 3, "subperiods_total": 4},
                ],
                "local_threshold_sensitivity": {
                    "fixed_small_080": {"sharpe": 0.3494, "n_active": 222, "subperiods_positive": 3, "subperiods_total": 4, "dsr_honest": 0.0},
                    "fixed_small_082": {"sharpe": 0.9431, "n_active": 125, "subperiods_positive": 3, "subperiods_total": 4, "dsr_honest": 0.0},
                },
                "temporal_robustness": {
                    "fixed_small_080": {
                        "summary": {
                            "positive_count": 3,
                            "tested_count": 4,
                            "positive_periods": ["2021", "2022", "2024+"],
                            "negative_periods": ["2023"],
                            "skipped_periods": ["2020-H1", "2020-H2"],
                        }
                    }
                },
            },
            "subperiods": [
                {"period": "2020-H1", "status": "SKIP", "positive": None, "n_active": 0, "sharpe": 0.0, "cum_return": 0.0},
                {"period": "2021", "status": "PASS", "positive": True, "n_active": 32, "sharpe": 0.705, "cum_return": 0.0039},
                {"period": "2023", "status": "FAIL", "positive": False, "n_active": 23, "sharpe": -0.0258, "cum_return": 0.0},
            ],
        }
        blockers = {
            "DSR honesto": {"classification": gate_diag.CLASS_HARD_BLOCKER},
            "DSR invalidação global": {"classification": gate_diag.CLASS_HARD_BLOCKER},
            "Sharpe OOS": {"classification": gate_diag.CLASS_HARD_BLOCKER},
            "Subperiodos": {"classification": gate_diag.CLASS_HARD_BLOCKER},
        }

        result = gate_diag.build_hard_blocker_causality(
            phase4_report=phase4_report,
            snapshot_summary={"active_count": 0, "max_p_bma_pkf": 0.7794},
            aggregated_summary={"latest_count_above_threshold": 0},
            blocker_reclassification=blockers,
        )

        self.assertEqual(result["summary"]["meta_model_layer_status"], "PASS")
        self.assertEqual(result["summary"]["policy_layer_status"], "FAIL")
        self.assertTrue(result["summary"]["current_snapshot_is_flat"])
        self.assertIn("Sharpe OOS", result["items"])
        self.assertIn("fallback_policy_layer_underpowered_and_temporally_unstable", result["items"]["Sharpe OOS"]["root_cause_class"])


if __name__ == "__main__":
    unittest.main()
