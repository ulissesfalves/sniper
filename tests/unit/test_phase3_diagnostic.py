from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase3_diagnostic as phase3_diag


def _build_valid_phase3_frames() -> dict[str, pd.DataFrame]:
    index = pd.date_range("2024-01-01", periods=3, freq="D")
    barrier_df = pd.DataFrame(
        {
            "t_touch": index + pd.Timedelta(days=2),
            "label": [1, 0, -1],
            "barrier_tp": [110.0, 110.0, 110.0],
            "barrier_sl": [90.0, 90.0, 90.0],
            "exit_price": [105.0, 100.0, 95.0],
            "pnl_real": [0.05, 0.0, -0.05],
            "slippage_frac": [0.0, 0.0, 0.02],
            "sigma_at_entry": [0.2, 0.2, 0.2],
            "p0": [100.0, 100.0, 100.0],
            "holding_days": [2, 2, 2],
        },
        index=index,
    )
    meta_df = pd.DataFrame(
        {
            "p_bma": [0.60, 0.62, 0.64],
            "p_calibrated": [0.60, 0.62, 0.64],
            "y_target": [1, 0, 1],
            "uniqueness": phase3_diag.compute_label_uniqueness(barrier_df),
        },
        index=index,
    )

    sizing_rows = []
    for dt, p_cal in zip(index, meta_df["p_calibrated"], strict=True):
        sigma = 0.20
        mu_adj = p_cal * 0.05 - (1 - p_cal) * 0.05
        kelly = phase3_diag.compute_kelly_fraction(mu=mu_adj, sigma=sigma, p_cal=p_cal)
        sizing_rows.append(
            {
                "date": dt,
                "kelly_frac": kelly,
                "position_usdt": min(kelly * phase3_diag.CAPITAL_TOTAL, phase3_diag.POSITION_CAP),
                "p_cal": p_cal,
                "sigma": sigma,
                "mu_adj": mu_adj,
            }
        )
    sizing_df = pd.DataFrame(sizing_rows).set_index("date")
    feature_df = pd.DataFrame({"hmm_prob_bull": [0.40, 0.40, 0.40]}, index=index)
    return {
        "barriers": barrier_df,
        "meta": meta_df,
        "sizing": sizing_df,
        "features": feature_df,
    }


class Phase3DiagnosticTest(unittest.TestCase):
    def _prepare_paths(self, base: Path, symbol: str = "AAA") -> tuple[Path, Path]:
        phase3_path = base / "models" / "phase3"
        features_path = base / "models" / "features"
        phase3_path.mkdir(parents=True, exist_ok=True)
        features_path.mkdir(parents=True, exist_ok=True)
        for path in [
            phase3_path / f"{symbol}_barriers.parquet",
            phase3_path / f"{symbol}_meta.parquet",
            phase3_path / f"{symbol}_sizing.parquet",
            features_path / f"{symbol}.parquet",
        ]:
            path.write_text("placeholder", encoding="utf-8")
        return phase3_path, features_path

    def test_audit_symbol_treats_hard_gate_breaches_as_advisory_when_gate_is_off(self) -> None:
        frames = _build_valid_phase3_frames()

        def fake_normalize_df(path: Path, *_args, **_kwargs) -> pd.DataFrame:
            if path.name.endswith("_barriers.parquet"):
                return frames["barriers"]
            if path.name.endswith("_meta.parquet"):
                return frames["meta"]
            if path.name.endswith("_sizing.parquet"):
                return frames["sizing"]
            return frames["features"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            phase3_path, features_path = self._prepare_paths(Path(tmp_dir))
            with (
                patch.object(phase3_diag, "PHASE3_PATH", phase3_path),
                patch.object(phase3_diag, "FEATURES_PATH", features_path),
                patch.object(phase3_diag, "HMM_HARD_GATE_MODE", "off"),
                patch.object(phase3_diag, "_normalize_df", side_effect=fake_normalize_df),
            ):
                result = phase3_diag.audit_symbol("AAA")

        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["hard_gate_blocking_enabled"])
        self.assertEqual(result["hard_gate_rows"], 3)
        self.assertGreater(result["p_bma_gate_breaches"], 0)
        self.assertGreater(result["p_cal_gate_breaches"], 0)

    def test_audit_symbol_fails_when_hard_gate_breaches_are_blocking(self) -> None:
        frames = _build_valid_phase3_frames()

        def fake_normalize_df(path: Path, *_args, **_kwargs) -> pd.DataFrame:
            if path.name.endswith("_barriers.parquet"):
                return frames["barriers"]
            if path.name.endswith("_meta.parquet"):
                return frames["meta"]
            if path.name.endswith("_sizing.parquet"):
                return frames["sizing"]
            return frames["features"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            phase3_path, features_path = self._prepare_paths(Path(tmp_dir))
            with (
                patch.object(phase3_diag, "PHASE3_PATH", phase3_path),
                patch.object(phase3_diag, "FEATURES_PATH", features_path),
                patch.object(phase3_diag, "HMM_HARD_GATE_MODE", "on"),
                patch.object(phase3_diag, "_normalize_df", side_effect=fake_normalize_df),
            ):
                result = phase3_diag.audit_symbol("AAA")

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["hard_gate_blocking_enabled"])
        self.assertGreater(result["p_bma_gate_breaches"], 0)
        self.assertGreater(result["p_cal_gate_breaches"], 0)

    def test_main_treats_manifested_phase3_exclusion_as_controlled_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            phase3_path, features_path = self._prepare_paths(base, symbol="AAA")

            for path in phase3_path.glob("AAA_*.parquet"):
                path.unlink()

            report_path = base / "models" / "phase3_diagnostic_report.json"
            nested_report_path = phase3_path / "diagnostic_report.json"
            exclusions_path = phase3_path / "phase3_exclusions.json"
            exclusions_path.write_text(
                """
                {
                  "exclusions": {
                    "AAA": {
                      "reason": "meta_returned_none",
                      "n_barriers": 50,
                      "n_feature_rows": 250
                    }
                  }
                }
                """.strip(),
                encoding="utf-8",
            )

            with (
                patch.object(phase3_diag, "PHASE3_PATH", phase3_path),
                patch.object(phase3_diag, "FEATURES_PATH", features_path),
                patch.object(phase3_diag, "PHASE3_REPORT_PATH", report_path),
                patch.object(phase3_diag, "PHASE3_NESTED_REPORT_PATH", nested_report_path),
                patch.object(phase3_diag, "PHASE3_EXCLUSIONS_PATH", exclusions_path),
                patch.object(phase3_diag, "audit_source_guards", return_value={"status": "PASS"}),
            ):
                phase3_diag.main()

            with open(nested_report_path, "r", encoding="utf-8") as fin:
                report = json.load(fin)
            self.assertEqual(report["overall_status"], "PASS")
            self.assertEqual(report["summary"]["controlled_exclusions"], 1)
            self.assertEqual(report["details"][0]["symbol"], "AAA")
            self.assertEqual(report["details"][0]["reason"], "controlled_phase3_exclusion")
            self.assertEqual(report["details"][0]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
