from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ML_ENGINE_ROOT = ROOT / "services" / "ml_engine"
DATA_INSERTER_ROOT = ROOT / "services" / "data_inserter"

for path in (ROOT, ML_ENGINE_ROOT, DATA_INSERTER_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


if "structlog" not in sys.modules:
    class _Logger:
        def info(self, *args, **kwargs) -> None:
            pass

        def warning(self, *args, **kwargs) -> None:
            pass

        def error(self, *args, **kwargs) -> None:
            pass

        def debug(self, *args, **kwargs) -> None:
            pass

    structlog_stub = types.ModuleType("structlog")
    structlog_stub.get_logger = lambda *args, **kwargs: _Logger()
    structlog_stub.configure = lambda *args, **kwargs: None
    structlog_stub.processors = types.SimpleNamespace(
        TimeStamper=lambda *args, **kwargs: None,
        add_log_level=lambda *args, **kwargs: None,
        JSONRenderer=lambda *args, **kwargs: None,
    )
    sys.modules["structlog"] = structlog_stub


if "decouple" not in sys.modules:
    decouple_stub = types.ModuleType("decouple")

    def _config(key: str, default=None, cast=None):
        if cast is None:
            return default
        try:
            return cast(default)
        except Exception:
            return default

    decouple_stub.config = _config
    sys.modules["decouple"] = decouple_stub


if "polars" not in sys.modules:
    try:
        import polars  # noqa: F401
    except ImportError:
        class _PolarsFrame:
            def __init__(self, data=None):
                self._data = data

            def to_pandas(self):
                import pandas as pd

                if hasattr(self._data, "copy"):
                    return self._data.copy()
                return pd.DataFrame()

            def write_parquet(self, *args, **kwargs) -> None:
                return None

            def is_empty(self) -> bool:
                return True

            def sort(self, *args, **kwargs):
                return self

        polars_stub = types.ModuleType("polars")
        polars_stub.DataFrame = _PolarsFrame
        polars_stub.from_pandas = lambda df: _PolarsFrame(df)
        polars_stub.read_parquet = lambda *args, **kwargs: _PolarsFrame()
        sys.modules["polars"] = polars_stub
