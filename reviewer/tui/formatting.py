"""Turning numbers into the short strings a narrow column can hold.

Every function here is pure and takes the clock as an argument, so what the
board will say at a given moment is a question that can be answered in a test.
"""

from __future__ import annotations

import time

from .theme import NEEDS_YOU, PENDING, URGENT

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0
YEAR = 365 * DAY

STALE_DAYS = 7
OLD_DAYS = 30

MISSING = "—"


def ago(seconds: float) -> str:
    """How long since something happened, one unit, in the past tense."""
    if seconds < MINUTE:
        return "just now"
    if seconds < HOUR:
        return f"{int(seconds // MINUTE)}m ago"
    if seconds < DAY:
        return f"{int(seconds // HOUR)}h ago"
    return f"{int(seconds // DAY)}d ago"


def elapsed(seconds: float) -> str:
    """Time on work still going, ticking from the first second."""
    whole = max(0, int(seconds))
    if whole < MINUTE:
        return f"{whole}s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m"


def elapsed_brief(seconds: float) -> str:
    """The same, one unit only, for the width-bound status column."""
    whole = max(0, int(seconds))
    if whole < MINUTE:
        return f"{whole}s"
    if whole < HOUR:
        return f"{int(whole // MINUTE)}m"
    return f"{int(whole // HOUR)}h"


def duration(seconds: float) -> str:
    """A finished span. Two units is as precise as useful."""
    whole = max(0.0, seconds)
    if whole < HOUR:
        return f"{int(whole // MINUTE)}m"
    if whole < DAY:
        hours, rest = divmod(int(whole), 3600)
        return f"{hours}h {rest // 60}m"
    days, rest = divmod(int(whole), 86400)
    return f"{days}d {rest // 3600}h"


def age_label(opened_at: float | None, now: float) -> str:
    """How long a pull request has been open, in the largest unit that fits."""
    if not opened_at:
        return MISSING
    seconds = max(0.0, now - opened_at)
    if seconds < HOUR:
        return f"{int(seconds // MINUTE)}m"
    if seconds < DAY:
        return f"{int(seconds // HOUR)}h"
    if seconds < YEAR:
        return f"{int(seconds // DAY)}d"
    return f"{int(seconds // YEAR)}y"


def age_style(opened_at: float | None, now: float) -> str:
    """Colour by age: weeks open is a different thing from opened this morning."""
    if not opened_at:
        return PENDING
    days = max(0.0, now - opened_at) / DAY
    if days >= OLD_DAYS:
        return URGENT
    if days >= STALE_DAYS:
        return NEEDS_YOU
    return PENDING


def timestamp(epoch: float) -> str:
    return time.strftime("%d %b %Y, %H:%M", time.localtime(epoch))


def date_only(epoch: float) -> str:
    """A merge date for a narrow column. History spans years, where "3d ago"
    stops meaning anything and the date itself is what you want."""
    return time.strftime("%d %b %y", time.localtime(epoch)) if epoch else MISSING


def churn(additions: int, deletions: int) -> str:
    return f"+{additions} −{deletions}"


def tokens(count: int) -> str:
    """A token count at a glance. 1_575_476 -> '1.6M'.

    Exact figures belong in the log and in ``--dry-run``; what a detail pane is
    for is noticing that one review cost ten times what its neighbours did.
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k".replace(".0k", "k")
    return str(count)


def money(usd: float) -> str:
    """A cost small enough that two decimal places would round it away."""
    if usd >= 1:
        return f"${usd:.2f}"
    return f"${usd:.3f}".rstrip("0").rstrip(".")


def clip(value: str, width: int) -> str:
    return value[:width]


def one_line(value: str) -> str:
    return value.replace("\n", " ")
