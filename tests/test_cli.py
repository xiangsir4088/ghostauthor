"""CLI window-bound parsing: deterministic, no wall clock.

2026-01-01T00:00:00Z == 1767225600 (matches the fixture BASE_TS).
"""
import unittest

from ghostauthor.cli import _parse_window_value

MIDNIGHT = 1767225600


class WindowBoundTests(unittest.TestCase):
    def test_epoch_passthrough(self):
        self.assertEqual(_parse_window_value("1767225600"), 1767225600)

    def test_date_is_utc_midnight(self):
        self.assertEqual(_parse_window_value("2026-01-01"), MIDNIGHT)

    def test_iso_datetime_with_z(self):
        self.assertEqual(_parse_window_value("2026-01-01T06:00:00Z"),
                         MIDNIGHT + 6 * 3600)

    def test_naive_datetime_is_utc(self):
        self.assertEqual(_parse_window_value("2026-01-01 06:00:00"),
                         MIDNIGHT + 6 * 3600)

    def test_offset_datetime_same_instant(self):
        self.assertEqual(_parse_window_value("2026-01-01T08:00:00+02:00"),
                         MIDNIGHT + 6 * 3600)

    def test_garbage_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_window_value("next tuesday")
        self.assertIn("window-bound-invalid", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
