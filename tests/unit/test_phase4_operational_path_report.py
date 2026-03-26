from __future__ import annotations

import unittest

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase4_cpcv as phase4


class Phase4OperationalPathReportTest(unittest.TestCase):
    def test_operational_path_report_marks_snapshot_governor_and_score_choke(self) -> None:
        aggregated = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    [
                        "2024-01-01",
                        "2024-01-02",
                        "2024-01-01",
                        "2024-01-02",
                    ]
                ),
                "symbol": ["AAA", "AAA", "BBB", "BBB"],
                "p_meta_raw": [0.61, 0.52, 0.49, 0.51],
                "p_meta_calibrated": [0.56, 0.40, 0.48, 0.46],
                "mu_adj_meta": [0.010, 0.0, 0.0, 0.0],
                "kelly_frac_meta": [0.005, 0.0, 0.0, 0.0],
                "position_usdt_meta": [1000.0, 0.0, 0.0, 0.0],
                "pnl_exec_meta": [0.01, 0.0, 0.0, 0.0],
                "p_bma_pkf": [0.72, 0.68, 0.55, 0.54],
            }
        )

        snapshot = phase4._build_execution_snapshot(aggregated)
        result = phase4._build_operational_path_report(aggregated, snapshot)

        self.assertTrue(result["governs_snapshot"])
        self.assertEqual(result["path_name"], "operational_meta_path")
        self.assertEqual(result["activation_funnel"]["aggregated_p_meta_calibrated_gt_050"], 1)
        self.assertEqual(result["activation_funnel"]["aggregated_position_usdt_meta_gt_0"], 1)
        self.assertEqual(result["activation_funnel"]["latest_snapshot_active_count"], 0)
        self.assertEqual(result["choke_point"]["latest_snapshot_stage"], "score_calibration")
        self.assertEqual(result["latest_top_candidates"][0]["symbol"], "BBB")

    def test_report_paths_summary_explicitly_marks_snapshot_governor(self) -> None:
        result = phase4._build_report_paths_summary()

        self.assertEqual(result["snapshot_governed_by"], "operational_meta_path")
        self.assertTrue(result["operational_meta_path"]["governs_snapshot"])
        self.assertFalse(result["fallback_policy_path"]["governs_snapshot"])
        self.assertIn("operational_path", [result["operational_meta_path"]["report_block"]])


if __name__ == "__main__":
    unittest.main()
