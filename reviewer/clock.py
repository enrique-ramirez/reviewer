"""Time on a machine that goes to sleep.

This is a tool you leave open all day, on a laptop, which means the lid closes
on it. That turns out to matter more than it sounds like it should.

Python's ``time.monotonic()`` is ``mach_absolute_time()`` on macOS, and that
clock *stops while the system is asleep*. The wall clock does not. So the two
disagree by exactly as long as the machine was away, and which of them you want
depends on what you are asking:

*Deciding whether to give up on a call* wants the monotonic one. A review that
spans a twenty-minute sleep did not get twenty minutes of anything — its process
was frozen along with everything else, and charging it for that time would kill
work that was going to succeed on the machine's next breath. Time asleep is not
time a call had a chance to make progress in, so it does not come out of the
call's budget.

*Telling a person what is going on* wants the wall clock, because that is the
clock they were watching. A review that says "3m" when the lid has been shut
since breakfast is lying to them.

The failure this module exists to prevent is the gap between those two readings
being invisible. A dashboard showing "reviewing — 15m 40s" against a timeout
that has only counted three of those minutes looks exactly like a hang, and
there is no way to tell from the outside that it is not one. So: control
decisions run off the monotonic clock, the display runs off the wall clock, and
the difference between them is a number this hands out on purpose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Deadline:
    """A budget of *awake* seconds, plus the wall-clock story for the display.

    ``seconds`` of ``None`` means no limit, which is what a caller with nothing
    to enforce passes rather than a very large number.
    """

    seconds: float | None
    # Looked up through the module rather than bound here: ``default_factory``
    # captures whatever it is given at import, which would make the start times
    # come from a different clock than the readings do the moment anything
    # patches one. Only the tests do that, but a class that cannot be given a
    # fake clock cannot be tested for the thing it exists to handle.
    started_wall: float = field(default_factory=lambda: time.time())
    started_mono: float = field(default_factory=lambda: time.monotonic())

    def awake_elapsed(self) -> float:
        """Seconds since this started, not counting time spent asleep.

        The budget clock. On a platform whose monotonic clock does run through a
        suspend this is simply the elapsed time, which is the conservative way
        to be wrong: a call gets less grace, never more.
        """
        return max(0.0, time.monotonic() - self.started_mono)

    def elapsed(self) -> float:
        """Seconds since this started as a person watching would count them."""
        return max(0.0, time.time() - self.started_wall)

    def slept(self) -> float:
        """Of the elapsed time, how much the machine spent asleep.

        Zero on a machine that stayed awake. Never negative, so a wall clock
        that steps backwards under NTP reads as "no sleep" rather than as
        negative time.
        """
        return max(0.0, self.elapsed() - self.awake_elapsed())

    def remaining(self) -> float:
        if self.seconds is None:
            return float("inf")
        return self.seconds - self.awake_elapsed()

    def expired(self) -> bool:
        return self.remaining() <= 0


class Silence:
    """How long since something last happened, in awake seconds.

    The useful signal for "is this wedged", and a much sharper one than total
    elapsed: a call that has been running for twenty minutes and printed
    something four seconds ago is working, and one that has printed nothing for
    twenty minutes is not, whatever the totals say.

    Measured on the monotonic clock for the same reason the deadline is. A child
    process is silent for the whole of a suspend because it is not running, and
    counting that against it would make every wake look like a hang.
    """

    def __init__(self) -> None:
        self._at = time.monotonic()

    def beat(self) -> None:
        self._at = time.monotonic()

    def seconds(self) -> float:
        return max(0.0, time.monotonic() - self._at)
