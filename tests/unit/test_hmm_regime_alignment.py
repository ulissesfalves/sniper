from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine.regime import hmm_filter


class HmmRegimeAlignmentTest(unittest.TestCase):
    def test_build_regime_target_matches_contemporaneous_return_sign(self) -> None:
        returns = pd.Series([0.10, 0.10, -0.05, -0.20, np.nan], dtype=float)
        target = hmm_filter.build_regime_target(returns)

        self.assertEqual(target.iloc[0], 1.0)
        self.assertEqual(target.iloc[1], 1.0)
        self.assertEqual(target.iloc[2], 0.0)
        self.assertEqual(target.iloc[3], 0.0)
        self.assertTrue(pd.isna(target.iloc[4]))

    def test_validate_hmm_diagnostics_scores_against_contemporaneous_regime_target(self) -> None:
        index = pd.date_range("2024-01-01", periods=120, freq="D")
        returns = pd.Series(np.r_[np.repeat(0.03, 60), np.repeat(-0.03, 60)], index=index)
        y_true = hmm_filter.build_regime_target(returns)
        hmm_result = pd.DataFrame(
            {
                "hmm_prob_bull": np.where(y_true.fillna(0).astype(int) == 1, 0.8, 0.2),
                "hmm_is_bull": np.where(y_true.fillna(False), True, False),
            },
            index=index,
        )

        diag = hmm_filter.validate_hmm_diagnostics(hmm_result, returns, min_f1=0.45)

        self.assertEqual(diag["status"], "PASS")
        self.assertEqual(diag["f1_oos"], 1.0)


if __name__ == "__main__":
    unittest.main()
