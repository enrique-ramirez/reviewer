"""What the dashboard is currently showing, as a value.

Every key that changes the view returns a new ``Session`` rather than mutating
one, so "what is on screen" is a single object the app can swap and re-render
from, and every transition can be tested without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .formatting import DAY

ALL_REPOSITORIES = "All repositories"

# Offered by the date picker on History. None first, so the default is
# "everything" and the list reads shortest-span-last.
WINDOWS: tuple[tuple[str, float | None], ...] = (
    ("all time", None),
    ("last 7 days", 7 * DAY),
    ("last 30 days", 30 * DAY),
    ("last 90 days", 90 * DAY),
)


@dataclass(frozen=True, slots=True)
class Session:
    repos: tuple[str, ...]
    started_at: float
    repo_index: int = 0
    only_attention: bool = False
    author: str = ""
    window: int = 0
    page: int = 0

    @property
    def multi_repo(self) -> bool:
        return len(self.repos) > 1

    @property
    def entries(self) -> tuple[tuple[str, str | None], ...]:
        """The sidebar's rows: everything, then one per repository."""
        return ((ALL_REPOSITORIES, None), *((repo, repo) for repo in self.repos))

    @property
    def chosen_repo(self) -> str | None:
        return self.entries[self.repo_index % len(self.entries)][1]

    @property
    def scope(self) -> tuple[str, ...]:
        """The repositories every view is showing."""
        chosen = self.chosen_repo
        return (chosen,) if chosen else self.repos

    @property
    def whole_estate(self) -> bool:
        return self.chosen_repo is None

    @property
    def window_label(self) -> str:
        return WINDOWS[self.window % len(WINDOWS)][0]

    @property
    def filtered(self) -> bool:
        """Filters Escape can clear."""
        return bool(self.author) or self.window != 0

    @property
    def narrowed(self) -> bool:
        """Anything at all that is hiding rows."""
        return self.filtered or not self.whole_estate

    def merged_after(self, now: float) -> float | None:
        span = WINDOWS[self.window % len(WINDOWS)][1]
        return now - span if span else None

    # Paging is per-repository and per-filter: keeping page 4 after narrowing
    # would land on an empty page.
    def with_repo(self, index: int) -> "Session":
        return replace(self, repo_index=index % len(self.entries), page=0)

    def moved_by(self, delta: int) -> "Session":
        return self.with_repo(self.repo_index + delta)

    def with_attention_only(self, only: bool) -> "Session":
        return replace(self, only_attention=only)

    def with_author(self, author: str) -> "Session":
        return replace(self, author=author.strip(), page=0)

    def with_window(self, window: int) -> "Session":
        return replace(self, window=window % len(WINDOWS), page=0)

    def with_next_window(self) -> "Session":
        return self.with_window(self.window + 1)

    def with_page(self, page: int) -> "Session":
        return replace(self, page=max(0, page))

    def unfiltered(self) -> "Session":
        return replace(self, author="", window=0, page=0)
