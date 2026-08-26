from __future__ import annotations

import unittest

from reviewer.tui import formatting as f
from reviewer.tui.theme import NEEDS_YOU, PENDING, URGENT

NOW = 1_700_000_000.0


class Ago(unittest.TestCase):
    def test_reads_in_the_past_tense_one_unit_at_a_time(self) -> None:
        self.assertEqual(f.ago(5), "just now")
        self.assertEqual(f.ago(59), "just now")
        self.assertEqual(f.ago(60), "1m ago")
        self.assertEqual(f.ago(3 * f.HOUR + 100), "3h ago")
        self.assertEqual(f.ago(9 * f.DAY), "9d ago")


class Elapsed(unittest.TestCase):
    def test_counts_from_the_first_second(self) -> None:
        self.assertEqual(f.elapsed(0), "0s")
        self.assertEqual(f.elapsed(20), "20s")
        self.assertEqual(f.elapsed(61), "1m 01s")
        self.assertEqual(f.elapsed(3 * f.HOUR + 4 * f.MINUTE), "3h 04m")

    def test_never_reports_negative_time(self) -> None:
        self.assertEqual(f.elapsed(-10), "0s")

    def test_brief_form_fits_one_unit(self) -> None:
        self.assertEqual(f.elapsed_brief(20), "20s")
        self.assertEqual(f.elapsed_brief(15 * f.MINUTE), "15m")
        self.assertEqual(f.elapsed_brief(4 * f.HOUR), "4h")


class Duration(unittest.TestCase):
    def test_reports_a_finished_span_in_two_units(self) -> None:
        self.assertEqual(f.duration(90), "1m")
        self.assertEqual(f.duration(2 * f.HOUR + 30 * f.MINUTE), "2h 30m")
        self.assertEqual(f.duration(3 * f.DAY + 4 * f.HOUR), "3d 4h")


class Age(unittest.TestCase):
    def test_uses_the_largest_unit_that_fits(self) -> None:
        self.assertEqual(f.age_label(NOW - 30 * f.MINUTE, NOW), "30m")
        self.assertEqual(f.age_label(NOW - 5 * f.HOUR, NOW), "5h")
        self.assertEqual(f.age_label(NOW - 3 * f.DAY, NOW), "3d")
        self.assertEqual(f.age_label(NOW - 2 * f.YEAR, NOW), "2y")

    def test_an_unscanned_pull_request_shows_a_dash(self) -> None:
        self.assertEqual(f.age_label(None, NOW), f.MISSING)
        self.assertEqual(f.age_style(None, NOW), PENDING)

    def test_colour_escalates_with_age(self) -> None:
        self.assertEqual(f.age_style(NOW - f.DAY, NOW), PENDING)
        self.assertEqual(f.age_style(NOW - 10 * f.DAY, NOW), NEEDS_YOU)
        self.assertEqual(f.age_style(NOW - 40 * f.DAY, NOW), URGENT)


class Churn(unittest.TestCase):
    def test_uses_a_minus_sign_rather_than_a_hyphen(self) -> None:
        self.assertEqual(f.churn(10, 3), "+10 −3")


if __name__ == "__main__":
    unittest.main()
