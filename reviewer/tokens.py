"""What a prompt is actually made of.

Printed by ``--dry-run`` so that tuning ``max_total_lines`` or trimming
``personality/`` is done against measurements rather than against a guess.

Two numbers matter and they are not the same number.

*What this tool sends* is the only part you control, and it is estimated here by
character count — no tokeniser ships with the standard library, and pulling one
in for a diagnostic would cost more than the diagnostic is worth. Treat it as
±15% and as a way to compare sections against each other, which is what tuning
needs.

*What the CLI reports* is exact, and is always larger, because every coding-agent
CLI prepends a system prompt and a set of tool definitions of its own. That gap
is fixed overhead you cannot edit from this repository — worth seeing once, so
that effort goes where it can actually change something.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: English prose and source code both land near this. See the module docstring
#: for how much precision to expect.
CHARS_PER_TOKEN = 3.7

#: Sections are separated by a blank line and a markdown heading; personality
#: files are separated by a horizontal rule.
USER_SPLIT = re.compile(r"\n\n(?=#{2,3} )")
SYSTEM_SPLIT = "\n\n---\n\n"

#: Width of the label column, measured from the left margin rather than from
#: each row's own indent — otherwise the totals sit two characters off the
#: sections they are totalling.
LABEL_WIDTH = 38
MAX_LABEL = LABEL_WIDTH - 6
CHARS_WIDTH = 9
TOKENS_WIDTH = 10
TOTAL_WIDTH = LABEL_WIDTH + CHARS_WIDTH + TOKENS_WIDTH


def estimate(text: str) -> int:
    """Roughly how many tokens ``text`` is worth."""
    return round(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Section:
    label: str
    chars: int

    @property
    def tokens(self) -> int:
        return round(self.chars / CHARS_PER_TOKEN)


def _label(chunk: str) -> str:
    """The first heading in a chunk, or its first few words."""
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("#").strip()
        return line[:MAX_LABEL].lower()
    return "(empty)"


def split(text: str, *, system: bool) -> list[Section]:
    """Break a prompt into its parts, largest first.

    Best-effort: a diff that happens to contain something heading-shaped could
    be split in two and labelled oddly. Totals are measured from the whole
    string rather than summed from here, so a bad guess costs a confusing row
    and nothing else.
    """
    chunks = text.split(SYSTEM_SPLIT) if system else USER_SPLIT.split(text)
    sections = [Section(_label(chunk), len(chunk)) for chunk in chunks if chunk.strip()]
    return sorted(sections, key=lambda s: s.chars, reverse=True)


def _row(label: str, chars: int | None, tokens: int, indent: str = "  ") -> str:
    size = f"{chars:>{CHARS_WIDTH},}" if chars is not None else " " * CHARS_WIDTH
    return (
        f"{indent}{label:<{LABEL_WIDTH - len(indent)}}{size}{tokens:>{TOKENS_WIDTH},}"
    )


def report(system: str, user: str, usage: dict[str, Any] | None = None) -> str:
    """A breakdown of one model call, for the log.

    ``usage`` is whatever the provider reported back. Providers differ in what
    they count and some report nothing at all, so every line drawn from it is
    conditional — a missing figure drops its row rather than printing a zero
    that reads like a measurement.
    """
    lines = [
        f"  {'':<{LABEL_WIDTH - 2}}{'chars':>{CHARS_WIDTH}}{'est. tok':>{TOKENS_WIDTH}}"
    ]

    for title, text, is_system in (("system", system, True), ("user", user, False)):
        lines.append(f"  {title}")
        for section in split(text, system=is_system):
            lines.append(_row(section.label, section.chars, section.tokens, "    "))

    sent = system + user
    lines.append("  " + "─" * (TOTAL_WIDTH - 2))
    lines.append(_row("sent by this tool", len(sent), estimate(sent)))

    if not usage:
        lines.append("  (the provider reported no usage figures)")
        return "\n".join(lines)

    reported = usage.get("input_tokens")
    cached = usage.get("cache_read_input_tokens") or 0
    created = usage.get("cache_creation_input_tokens") or 0
    total_in = sum(v for v in (reported, cached, created) if isinstance(v, int))

    if total_in:
        lines.append(_row("counted in by the provider", None, total_in))
        overhead = total_in - estimate(system + user)
        if overhead > 0:
            lines.append(
                _row("↳ of which CLI overhead", None, overhead)
                + "   fixed; not editable here"
            )
    if cached:
        lines.append(
            _row("↳ of which read from cache", None, cached) + "   billed at a fraction"
        )
    if usage.get("output_tokens"):
        lines.append(_row("out", None, int(usage["output_tokens"])))
    return "\n".join(lines)
