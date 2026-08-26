"""Assembling a detail pane out of small, independent fragments.

Each helper returns a fresh ``Text``; ``join`` glues them together and drops the
ones a record did not warrant. Nothing here reads or writes shared state, so a
pane's whole contents can be asserted on in one comparison.
"""

from __future__ import annotations

import textwrap
from typing import Sequence

from rich.text import Text

from . import theme
from .theme import MUTED

FIELD_WIDTH = 11
RULE_WIDTH = 40
"""Fallback width for a rule drawn before the pane knows how wide it is."""


def span(value: str, style: str = "") -> Text:
    return Text(value, style=style)


def line(value: str, style: str = "") -> Text:
    return Text(f"{value}\n", style=style)


def blank(count: int = 1) -> Text:
    return Text("\n" * count)


def field(name: str, value: str, style: str = "") -> Text:
    fragment = Text(f"{name:<{FIELD_WIDTH}}", style=MUTED)
    fragment.append(f"{value}\n", style=style)
    return fragment


def join(*parts: Text | None) -> Text:
    whole = Text()
    for part in parts:
        if part is not None:
            whole.append_text(part)
    return whole


def headline(number: int, title: str, subtitle: Text) -> Text:
    return join(
        span(f"#{number}  ", "bold"),
        line(title, "bold white"),
        subtitle,
    )


def rule(width: int = 0, label: str = "") -> Text:
    """A line across the pane, with an optional name sitting on it.

    Sections were previously separated by blank lines alone, which asks the
    reader to infer the grouping from spacing. A rule states it.
    """
    width = max(8, width or RULE_WIDTH)
    if not label:
        return join(span("─" * width, theme.RULE), Text("\n"))
    head = f"── {label} "
    return join(
        span("── ", theme.RULE),
        span(label, theme.MUTED),
        span(" " + "─" * max(1, width - len(head)), theme.RULE),
        Text("\n"),
    )


def badge(label: str, style: str = theme.BADGE) -> Text:
    """One label, drawn as something you could pick up."""
    return Text(f" {label} ", style=style)


def badges(labels: Sequence[str], style: str = theme.BADGE) -> Text:
    whole = Text()
    for index, label in enumerate(labels):
        if index:
            whole.append(" ")
        whole.append_text(badge(label, style))
    return whole


def churn(additions: int, deletions: int) -> Text:
    """Added and removed, in the two colours every diff already uses."""
    whole = Text()
    whole.append(f"+{additions}", style=theme.ADDED)
    whole.append(" ")
    whole.append(f"−{deletions}", style=theme.REMOVED)
    return whole


def callout(
    body: str, width: int = 0, style: str = "white", bar: str = theme.KEY
) -> Text:
    """A paragraph with a bar down its left edge.

    For the one thing on a pane that someone came to read — on History, what a
    pull request actually landed. Without it the summary is the same weight as
    the row of metadata around it, and gets skimmed past.

    Wrapped here rather than left to the renderer: a bar drawn once at the top
    of a paragraph that then wraps to four lines is a bar against one of them.
    """
    room = max(20, (width or RULE_WIDTH) - 2)
    whole = Text()
    for para in body.strip().splitlines():
        for wrapped in textwrap.wrap(para, room) or [""]:
            whole.append(f"{theme.CURSOR} ", style=bar)
            whole.append(f"{wrapped}\n", style=style)
    return whole


def field_text(name: str, value: Text) -> Text:
    """``field``, for a value that carries its own styling."""
    fragment = Text(f"{name:<{FIELD_WIDTH}}", style=MUTED)
    fragment.append_text(value)
    fragment.append("\n")
    return fragment
