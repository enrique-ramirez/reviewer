"""The wait between passes: counting down, holding, and cutting it short.

Driven with a fake clock rather than real time, so a fifteen-minute tick is a
few loop iterations. ``sleep`` is what advances it — the same call the real loop
blocks in — which keeps the shape of the test the same as the shape of the code.
"""

from __future__ import annotations

import threading
import unittest
from typing import Any

from reviewer.__main__ import wait_between_ticks

TICK = 900.0
STEP = 0.5


class Clock:
    """A monotonic clock that only moves when the loop sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps = 0

    def monotonic(self) -> float:
        return self.now

    def sleeping(self, seconds: float) -> None:
        self.now += seconds
        self.sleeps += 1


class Harness:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.pause = threading.Event()
        self.status: dict[str, Any] = {}
        self.clock = Clock()

    def run(self, *, after: int = 0, then: Any = None, limit: int = 10_000) -> None:
        """Wait, optionally doing something after ``after`` sleeps.

        ``limit`` is a guard: a wait that should hold forever has to be stopped
        by the test rather than by hanging it.
        """
        def sleep(seconds: float) -> None:
            self.clock.sleeping(seconds)
            if then is not None and self.clock.sleeps == after:
                then()
            if self.clock.sleeps >= limit:
                self.stop.set()

        wait_between_ticks(
            seconds=TICK,
            stop=self.stop,
            wake=self.wake,
            pause=self.pause,
            status=self.status,
            monotonic=self.clock.monotonic,
            sleep=sleep,
        )


class CountingDown(unittest.TestCase):
    def test_it_returns_when_the_wait_runs_out(self) -> None:
        h = Harness()
        h.run()
        self.assertEqual(h.status["phase"], "waiting")
        self.assertEqual(h.status["remaining"], 0.0)
        # 900 seconds at half a second a step.
        self.assertEqual(h.clock.sleeps, int(TICK / STEP))

    def test_it_reports_how_much_is_left_as_it_goes(self) -> None:
        h = Harness()
        seen: list[float] = []

        def sleep(seconds: float) -> None:
            h.clock.sleeping(seconds)
            if h.clock.sleeps in (10, 20):
                seen.append(h.status["remaining"])

        wait_between_ticks(
            seconds=TICK,
            stop=h.stop,
            wake=h.wake,
            pause=h.pause,
            status=h.status,
            monotonic=h.clock.monotonic,
            sleep=sleep,
        )

        # Ten steps apart, so exactly ten steps' worth of countdown apart. The
        # absolute values are one step behind the sleep count — the first pass
        # through the loop has had no time to spend yet — which is why this
        # asserts the interval rather than the reading.
        self.assertEqual(len(seen), 2)
        self.assertAlmostEqual(seen[0] - seen[1], 10 * STEP)
        self.assertLess(seen[0], TICK)

    def test_a_stop_ends_the_wait_wherever_it_is(self) -> None:
        h = Harness()
        h.run(after=4, then=h.stop.set)
        self.assertEqual(h.clock.sleeps, 4)


class AskingForAScan(unittest.TestCase):
    def test_a_wake_cuts_the_wait_short(self) -> None:
        h = Harness()
        h.run(after=3, then=h.wake.set)
        self.assertEqual(h.status["phase"], "scanning…")
        self.assertEqual(h.clock.sleeps, 3)

    def test_the_wake_is_consumed_so_it_does_not_fire_twice(self) -> None:
        h = Harness()
        h.run(after=3, then=h.wake.set)
        self.assertFalse(h.wake.is_set())

    def test_the_countdown_gives_way_to_the_phase(self) -> None:
        # Progress.counting_down is false with no remaining, which is what makes
        # the header show "scanning…" instead of a frozen track.
        h = Harness()
        h.run(after=3, then=h.wake.set)
        self.assertIsNone(h.status["remaining"])
        self.assertFalse(h.status["paused"])


class Pausing(unittest.TestCase):
    def test_a_pause_holds_the_countdown_still(self) -> None:
        h = Harness()
        h.pause.set()
        h.run(limit=50)
        # Fifty steps of wall clock, none of them spent.
        self.assertEqual(h.status["remaining"], TICK)
        self.assertEqual(h.status["phase"], "paused")
        self.assertTrue(h.status["paused"])

    def test_it_holds_indefinitely_rather_than_expiring(self) -> None:
        h = Harness()
        h.pause.set()
        # Far more steps than the tick is long. Without the hold this would have
        # run out and returned long ago.
        h.run(limit=int(TICK / STEP) * 3)
        self.assertEqual(h.status["remaining"], TICK)

    def test_a_pause_partway_through_keeps_the_time_already_served(self) -> None:
        h = Harness()

        def sleep(seconds: float) -> None:
            h.clock.sleeping(seconds)
            if h.clock.sleeps == 10:
                h.pause.set()
            elif h.clock.sleeps == 60:
                h.pause.clear()
            if h.clock.sleeps >= int(TICK / STEP) + 100:
                h.stop.set()

        wait_between_ticks(
            seconds=TICK,
            stop=h.stop,
            wake=h.wake,
            pause=h.pause,
            status=h.status,
            monotonic=h.clock.monotonic,
            sleep=sleep,
        )

        self.assertEqual(h.status["remaining"], 0.0)
        # Ten steps counted, fifty held, then the rest — so the wait took fifty
        # steps longer than it otherwise would.
        self.assertEqual(h.clock.sleeps, int(TICK / STEP) + 50)

    def test_a_scan_can_still_be_asked_for_while_held(self) -> None:
        h = Harness()
        h.pause.set()
        h.run(after=5, then=h.wake.set, limit=100)
        self.assertEqual(h.status["phase"], "scanning…")
        self.assertEqual(h.clock.sleeps, 5)

    def test_asking_for_a_scan_does_not_let_the_pause_go(self) -> None:
        h = Harness()
        h.pause.set()
        h.run(after=5, then=h.wake.set, limit=100)
        self.assertTrue(h.pause.is_set())


if __name__ == "__main__":
    unittest.main()
