from __future__ import annotations

from datetime import UTC
from datetime import datetime

from services.nautilus_bridge.phase4_publisher import _coerce_as_of


def test_coerce_as_of_accepts_datetime() -> None:
    assert _coerce_as_of(datetime(2024, 12, 16, 15, 30, tzinfo=UTC)) == "2024-12-16T15:30:00Z"


def test_coerce_as_of_accepts_space_separated_datetime() -> None:
    assert _coerce_as_of("2024-12-16 00:00:00") == "2024-12-16T00:00:00Z"


def test_coerce_as_of_accepts_date_only() -> None:
    assert _coerce_as_of("2024-12-16") == "2024-12-16T00:00:00Z"


def test_coerce_as_of_accepts_pandas_timestamp_when_available() -> None:
    try:
        import pandas as pd
    except ImportError:
        return
    assert _coerce_as_of(pd.Timestamp("2024-12-16 00:00:00")) == "2024-12-16T00:00:00Z"
