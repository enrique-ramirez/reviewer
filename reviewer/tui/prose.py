from __future__ import annotations

import textwrap
from typing import Sequence

from rich.text import Text

from . import theme
from .theme import MUTED

FIELD_WIDTH = 11
RULE_WIDTH = 40


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
    return Text(f" {label} ", style=style)


def badges(labels: Sequence[str], style: str = theme.BADGE) -> Text:
    whole = Text()
    for index, label in enumerate(labels):
        if index:
            whole.append(" ")
        whole.append_text(badge(label, style))
    return whole


def churn(additions: int, deletions: int) -> Text:
    whole = Text()
    whole.append(f"+{additions}", style=theme.ADDED)
    whole.append(" ")
    whole.append(f"−{deletions}", style=theme.REMOVED)
    return whole


def size(additions: int, deletions: int, changed_files: int) -> Text:
    value = churn(additions, deletions)
    value.append(f" in {changed_files} files", style=theme.MUTED)
    return field_text("size", value)


def callout(
    body: str, width: int = 0, style: str = "white", bar: str = theme.KEY
) -> Text:
    room = max(20, (width or RULE_WIDTH) - 2)
    whole = Text()
    for para in body.strip().splitlines():
        for wrapped in textwrap.wrap(para, room) or [""]:
            whole.append(f"{theme.CURSOR} ", style=bar)
            whole.append(f"{wrapped}\n", style=style)
    return whole


def field_text(name: str, value: Text) -> Text:
    fragment = Text(f"{name:<{FIELD_WIDTH}}", style=MUTED)
    fragment.append_text(value)
    fragment.append("\n")
    return fragment
