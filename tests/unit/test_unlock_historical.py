from __future__ import annotations

from datetime import UTC, date, datetime
import unittest

from tests import _path_setup  # noqa: F401
from services.data_inserter.collectors.unlock_support.historical import (
    ParsedDocument,
    choose_best_capture,
    deterministic_parse,
    validate_reconstruction,
)
from services.data_inserter.collectors.unlock_support.providers import WaybackCapture
from services.data_inserter.collectors.unlock_support.utils import ParsedUnlockEvent


class UnlockHistoricalTest(unittest.TestCase):
    def test_choose_best_capture_never_uses_future_capture(self) -> None:
        captures = [
            WaybackCapture(
                timestamp="20240110120000",
                original_url="https://example.org/tokenomics",
                mime_type="text/html",
                status_code="200",
            ),
            WaybackCapture(
                timestamp="20240215120000",
                original_url="https://example.org/tokenomics",
                mime_type="text/html",
                status_code="200",
            ),
        ]

        best = choose_best_capture(captures, as_of_date=date(2024, 1, 31))

        self.assertIsNotNone(best)
        self.assertEqual(best.timestamp, "20240110120000")

    def test_validate_reconstruction_rejects_lookahead_capture(self) -> None:
        future_capture = WaybackCapture(
            timestamp="20240215120000",
            original_url="https://example.org/tokenomics",
            mime_type="text/html",
            status_code="200",
        )
        parsed = ParsedDocument(
            events=[ParsedUnlockEvent(date(2024, 2, 20), "team", "Team/Founders", 10.0)],
            distribution_items=[],
            parse_score=1.0,
            is_structured=True,
            parser_name="html_table_v1",
        )

        validation = validate_reconstruction(
            future_capture,
            parsed,
            as_of_date=date(2024, 1, 31),
            circ_hint=100.0,
        )

        self.assertEqual(validation.quality_flag, "lookahead_rejected")
        self.assertEqual(validation.confidence, 0.0)

    def test_deterministic_parse_rejects_non_pdf_body_even_if_url_ends_with_pdf(self) -> None:
        parsed = deterministic_parse(
            b"<!DOCTYPE html><html><body>not a pdf</body></html>",
            "text/html",
            "https://example.org/tokenomics.pdf",
        )

        self.assertEqual(parsed.parser_name, "pdf_invalid")
        self.assertFalse(parsed.is_structured)
        self.assertEqual(parsed.events, [])


if __name__ == "__main__":
    unittest.main()
