from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ML_ENGINE_ROOT = ROOT / "services" / "ml_engine"
DATA_INSERTER_ROOT = ROOT / "services" / "data_inserter"

for path in (ROOT, ML_ENGINE_ROOT, DATA_INSERTER_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
