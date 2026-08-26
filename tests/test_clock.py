"""The two clocks, and what each of them is for.

These tests fake a suspend by moving the wall clock forward while leaving the
monotonic clock where it was — which is exactly what macOS does to a process
that was running when the lid closed.
"""

from __future__ import annotations

import unittest
from unittest import mock

from reviewer import clock


class FakeClocks:
    """A wall clock and a monotonic clock that can be moved independently."""

    def __init__(self, wall: float = 1_000.0, mono: float = 500.0) -> None:
        self.wall = wall
        self.mono = mono

    def advance(self, seconds: float) -> None:
        """Time passing on a machine that is awake: both clocks move."""
        self.wall += seconds
        self.mono += seconds

    def sleep(self, seconds: float) -> None:
        """Time passing with the lid shut: only the wall clock moves."""
        self.wall += seconds

    def patch(self):
        return mock.patch.multiple(
            clock.time,
            time=lambda: self.wall,
            monotonic=lambda: self.mono,
        )


class DeadlineAcrossASuspend(unittest.TestCase):
    def setUp(self) -> None:
        self.clocks = FakeClocks()
        patcher = self.clocks.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_time_asleep_does_not_come_out_of_the_budget(self) -> None:
        # The point of the whole module: a call frozen by a suspend did not get
        # a chance to make progress, so it is not charged for the time.
        deadline = clock.Deadline(900)
        self.clocks.advance(60)
        self.clocks.sleep(3600)
        self.assertFalse(deadline.expired())
        self.assertAlmostEqual(deadline.remaining(), 840)

    def test_the_budget_still_runs_out_while_awake(self) -> None:
        deadline = clock.Deadline(900)
        self.clocks.advance(901)
        self.assertTrue(deadline.expired())

    def test_a_suspend_is_reported_separately(self) -> None:
        deadline = clock.Deadline(900)
        self.clocks.advance(60)
        self.clocks.sleep(1200)
        self.assertAlmostEqual(deadline.slept(), 1200)
        self.assertAlmostEqual(deadline.awake_elapsed(), 60)
        # What a person watching the dashboard would have counted.
        self.assertAlmostEqual(deadline.elapsed(), 1260)

    def test_a_machine_that_stayed_awake_reports_no_sleep(self) -> None:
        deadline = clock.Deadline(900)
        self.clocks.advance(300)
        self.assertEqual(deadline.slept(), 0.0)

    def test_a_wall_clock_that_steps_backwards_is_not_negative_sleep(self) -> None:
        # NTP correcting a fast clock, which must not read as "negative time
        # asleep" and must not hand the call extra budget.
        deadline = clock.Deadline(900)
        self.clocks.advance(300)
        self.clocks.wall -= 120
        self.assertEqual(deadline.slept(), 0.0)
        self.assertAlmostEqual(deadline.awake_elapsed(), 300)

    def test_no_limit_never_expires(self) -> None:
        deadline = clock.Deadline(None)
        self.clocks.advance(10_000)
        self.assertFalse(deadline.expired())
        self.assertEqual(deadline.remaining(), float("inf"))


class SilenceAcrossASuspend(unittest.TestCase):
    def setUp(self) -> None:
        self.clocks = FakeClocks()
        patcher = self.clocks.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_silence_accumulates_while_awake(self) -> None:
        silence = clock.Silence()
        self.clocks.advance(120)
        self.assertAlmostEqual(silence.seconds(), 120)

    def test_a_beat_resets_it(self) -> None:
        silence = clock.Silence()
        self.clocks.advance(120)
        silence.beat()
        self.clocks.advance(5)
        self.assertAlmostEqual(silence.seconds(), 5)

    def test_a_suspend_is_not_counted_as_silence(self) -> None:
        # A child process says nothing for the whole of a suspend because it is
        # not running. Counting that would make every wake look like a hang.
        silence = clock.Silence()
        self.clocks.sleep(3600)
        self.assertEqual(silence.seconds(), 0.0)


if __name__ == "__main__":
    unittest.main()
