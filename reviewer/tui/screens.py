"""Keyboard-driven questions.

Deliberately plain — a box of text and single keys, in a tool whose whole
interface is single keys. Each question is a pure function returning the screen
to push, so what the user is about to be asked can be checked without a
terminal.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import backfill
from . import prose, theme
from .filling import BackfillStatus

YES, NO = "yes", "no"


class Ask(ModalScreen[str | None]):
    """A box of text where numbers pick and Escape backs out."""

    DEFAULT_CSS = """
    Ask { align: center middle; }
    Ask > Static {
        width: 74;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $panel;
        border: round $accent;
    }
    """

    def __init__(self, body: Text, choices: Mapping[str, str]) -> None:
        super().__init__()
        self.body = body
        self.choices = dict(choices)

    def compose(self) -> ComposeResult:
        yield Static(self.body)

    def on_key(self, event: Any) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()
            return
        chosen = self.choices.get(event.key)
        if chosen is not None:
            self.dismiss(chosen)
            event.stop()


def _choice(key: str, label: str) -> Text:
    return prose.join(prose.span(f"  {key}  ", theme.KEY), prose.line(label))


def _yes_no(yes: str, no: str) -> Text:
    return prose.join(
        prose.span("  y  ", theme.KEY),
        prose.line(yes),
        prose.span("  n  ", theme.MUTED),
        prose.span(no, theme.MUTED),
    )


def range_question() -> Ask:
    """How far back to fill in merge history."""
    options = {str(index): key for index, (key, _, _) in enumerate(backfill.RANGES, 1)}
    body = prose.join(
        prose.line("Fill in merge history\n", theme.HEADING),
        prose.line(
            "Records everything merged, not only what this tool reviewed, and "
            "makes no model calls — entries show the pull request's own title.\n",
            theme.MUTED,
        ),
        prose.line("How far back?\n"),
        *[
            _choice(key, backfill.RANGE_LABELS[range_key])
            for key, range_key in options.items()
        ],
        prose.span("\n  Esc  ", theme.MUTED),
        prose.span("leave it", theme.MUTED),
    )
    return Ask(body, options)


def _cost_line(total: int, requests: int) -> Text:
    if total == 0:
        return prose.line("Nothing to fetch — history is already up to date.\n")
    if total >= backfill.LARGE:
        return prose.line(
            f"That is {total:,} pull requests, a large sweep. It makes no model "
            "calls, so the cost is time and API quota rather than tokens. It can "
            "be stopped at any point and keeps whatever it already fetched.\n",
            theme.NEEDS_YOU,
        )
    return prose.line(f"{total:,} pull request(s), about {requests} request(s).\n")


def confirm_question(status: BackfillStatus) -> Ask:
    """What that turned out to cost, before anything is fetched."""
    body = prose.join(
        prose.line("Fill in merge history\n", theme.HEADING),
        *[prose.line(f"  {line}", theme.MUTED) for line in status.lines],
        prose.blank(),
        _cost_line(status.total, status.requests),
        _yes_no("go ahead", "leave it"),
    )
    return Ask(body, {"y": YES, "n": NO})


def quit_question(running: Sequence[Text], model_calls: int) -> Ask:
    """Confirm quitting, saying plainly what is about to be thrown away."""
    idle = not running and not model_calls
    body = prose.join(
        prose.line("Quit?\n", theme.HEADING),
        prose.line("Nothing is running.\n", theme.MUTED)
        if idle
        else _work_in_flight(running, model_calls),
        _yes_no("quit", "stay"),
    )
    return Ask(body, {"y": YES, "n": NO})


def _work_in_flight(running: Sequence[Text], model_calls: int) -> Text:
    return prose.join(
        prose.line("Still working:\n", theme.MUTED),
        *[prose.join(line, prose.span("\n")) for line in running],
        prose.line(f"\n  {model_calls} model call(s) will be stopped.", theme.NEEDS_YOU)
        if model_calls
        else None,
        prose.line(
            "\nNothing half-done is posted or recorded. Whatever is in flight "
            "starts again from scratch on the next run.\n",
            theme.MUTED,
        ),
    )
