"""Assembling a detail pane out of small, independent fragments.

Each helper returns a fresh ``Text``; ``join`` glues them together and drops the
ones a record did not warrant. Nothing here reads or writes shared state, so a
pane's whole contents can be asserted on in one comparison.
"""

from __future__ import annotations

from rich.text import Text

from .theme import MUTED

FIELD_WIDTH = 11


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
        blank(2),
    )
