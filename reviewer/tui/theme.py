"""The dashboard's visual vocabulary: one meaning per colour, one flag per reason.

The distinction that matters most is ``DONE`` against ``NEEDS_YOU``: "we reviewed
it and nothing is outstanding" and "somebody has to sign this off" are opposite
answers to "does this want me", and sharing a colour made the board lie.
"""

from __future__ import annotations

from dataclasses import dataclass

URGENT = "bold red"
NEEDS_YOU = "bold yellow"
READY = "bold green"
SETTLED = "green"
DONE = "cyan"
PENDING = "dim"
LIVE = "bold magenta"

MUTED = "dim"
FAINT = "grey50"
HEADING = "bold"
KEY = "bold cyan"

# A label is a thing the team put on a pull request, not a sentence. Giving it
# a background makes it read as an object you could pick up, the way it does on
# GitHub, rather than as more comma-separated prose.
BADGE = "grey85 on grey27"
BADGE_WARN = "black on yellow"

# Churn, in the two colours every diff already uses.
ADDED = "green"
REMOVED = "red"

RULE = "grey30"

# Single-width glyphs throughout: emoji are double-width in some terminals and
# would break the table's column alignment.
PAC_OPEN = "ᗧ"
PAC_SHUT = "●"
GHOST = "ᗣ"
DOT = "·"
CURSOR = "▌"
ALL_REPOS = "◆"
ONE_REPO = "▪"
EXPAND = "›"
COLLAPSE = "‹"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spinner_frame(frame: int) -> str:
    return SPINNER[frame % len(SPINNER)]


def pac_frame(frame: int) -> str:
    return PAC_OPEN if frame % 2 else PAC_SHUT


@dataclass(frozen=True, slots=True)
class Flag:
    """Why a pull request wants a human. Lower rank sorts higher on the board."""

    glyph: str
    style: str
    label: str
    rank: int
    detail: str


DISAGREEMENT = Flag(
    "!", URGENT, "settle a disagreement", 0,
    "a thread went back and forth too many times — it is parked for you",
)
APPROVAL = Flag(
    "?", NEEDS_YOU, "needs your approval", 1,
    "clean, but held back for a human to approve",
)
CONFLICT = Flag(
    "×", URGENT, "yours · conflicts", 2,
    "yours: approved, but it has conflicts to resolve first",
)
MERGEABLE = Flag(
    "✓", READY, "yours · ready to merge", 3,
    "yours: approved and ready to merge",
)
MERGE_PENDING = Flag(
    "~", SETTLED, "yours · merge check pending", 4,
    "yours: approved — GitHub is still working out mergeability",
)
REPLIES = Flag(
    "»", NEEDS_YOU, "replies to check", 5,
    "someone replied to your review comments",
)

FLAGS: tuple[Flag, ...] = (
    DISAGREEMENT,
    APPROVAL,
    CONFLICT,
    MERGEABLE,
    MERGE_PENDING,
    REPLIES,
)

UNFLAGGED_RANK = len(FLAGS) + 1
