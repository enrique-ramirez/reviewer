"""Diff compression and patch parsing.

Two jobs.

**Compression.** A PR that is +10,000 lines is usually +9,000 lines of generated
snapshot and +1,000 lines of code someone actually wrote. Three tiers handle
that: ``exclude`` drops a file to a one-line mention, ``summarize_only`` keeps
the path and hunk headers so the reviewer can still say "this migration changed,
worth a look" without reading it, and everything else is sent with its context
trimmed. The size budget is applied *after* all of that, so an artifact-heavy PR
is measured by its real content.

Nothing is dropped quietly. Every exclusion and truncation is returned in the
manifest and printed in the summary comment.

**Parsing.** GitHub rejects a review comment whose line is not part of the diff,
and rejects the whole review with it. So the valid line numbers are computed
here, from the *untrimmed* patch, and every proposed comment is checked against
them before anything is posted.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Files whose content rarely changes a review's conclusion get dropped first
# when a PR is over budget. Higher score = kept longer.
_SOURCE_SUFFIXES = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rb",
    ".rs", ".java", ".kt", ".swift", ".php", ".cs", ".scala", ".vue", ".svelte",
)
_CONFIG_SUFFIXES = (".json", ".yaml", ".yml", ".toml", ".ini", ".env")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str
    lines: list[str] = field(default_factory=list)

    @property
    def header(self) -> str:
        old = f"{self.old_start},{self.old_count}" if self.old_count != 1 else f"{self.old_start}"
        new = f"{self.new_start},{self.new_count}" if self.new_count != 1 else f"{self.new_start}"
        return f"@@ -{old} +{new} @@{self.heading}"


@dataclass
class FileDiff:
    path: str
    previous_path: str | None
    status: str
    additions: int
    deletions: int
    patch: str | None
    tier: str = "full"          # full | summary | excluded
    rendered: str = ""
    truncated: bool = False

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass
class DiffBundle:
    files: list[FileDiff]
    excluded: list[FileDiff]
    summarized: list[FileDiff]
    dropped_for_size: list[FileDiff]
    truncated: list[FileDiff]
    reviewable_lines: int
    total_lines: int

    @property
    def changed_paths(self) -> list[str]:
        return [f.path for f in self.files + self.excluded + self.summarized]

    def has_content(self) -> bool:
        return any(f.tier != "excluded" for f in self.files + self.summarized)


# ---------------------------------------------------------------- parsing


def parse_hunks(patch: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    current: Hunk | None = None
    for line in patch.splitlines():
        match = HUNK_RE.match(line)
        if match:
            current = Hunk(
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or 1),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or 1),
                heading=match.group(5) or "",
            )
            hunks.append(current)
        elif current is not None:
            current.lines.append(line)
    return hunks


def commentable_lines(patch: str | None) -> dict[str, set[int]]:
    """Line numbers GitHub will accept a comment on, per side.

    ``RIGHT`` covers added and context lines; ``LEFT`` covers removed and context
    lines. Computed from the untrimmed patch, so trimming context for the prompt
    can only ever shrink what the model sees relative to what is valid.
    """
    valid: dict[str, set[int]] = {"RIGHT": set(), "LEFT": set()}
    if not patch:
        return valid

    for hunk in parse_hunks(patch):
        old = hunk.old_start
        new = hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("\\"):
                continue
            if raw.startswith("+"):
                valid["RIGHT"].add(new)
                new += 1
            elif raw.startswith("-"):
                valid["LEFT"].add(old)
                old += 1
            else:
                valid["RIGHT"].add(new)
                valid["LEFT"].add(old)
                old += 1
                new += 1
    return valid


def trim_context(patch: str, keep: int) -> str:
    """Re-emit a patch keeping at most ``keep`` context lines around each change.

    Hunk headers are recomputed so the result stays a valid unified diff — the
    model reads it as a diff, and a mangled header would quietly mislead it about
    which line is which.
    """
    if keep >= 3:
        return patch

    out: list[str] = []
    for hunk in parse_hunks(patch):
        entries: list[tuple[str, str, int, int]] = []
        old, new = hunk.old_start, hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("\\"):
                entries.append(("\\", raw, old, new))
            elif raw.startswith("+"):
                entries.append(("+", raw, old, new))
                new += 1
            elif raw.startswith("-"):
                entries.append(("-", raw, old, new))
                old += 1
            else:
                entries.append((" ", raw, old, new))
                old += 1
                new += 1

        kept: set[int] = set()
        for index, (kind, _, _, _) in enumerate(entries):
            if kind in "+-":
                low = max(0, index - keep)
                high = min(len(entries), index + keep + 1)
                kept.update(range(low, high))
        for index, (kind, _, _, _) in enumerate(entries):
            if kind == "\\" and index - 1 in kept:
                kept.add(index)

        if not kept:
            continue

        for group in _consecutive(sorted(kept)):
            first = entries[group[0]]
            old_count = sum(1 for i in group if entries[i][0] in " -")
            new_count = sum(1 for i in group if entries[i][0] in " +")
            sub = Hunk(
                old_start=first[2],
                old_count=old_count,
                new_start=first[3],
                new_count=new_count,
                heading=hunk.heading,
            )
            out.append(sub.header)
            out.extend(entries[i][1] for i in group)

    return "\n".join(out)


def _consecutive(indices: list[int]) -> Iterable[list[int]]:
    group: list[int] = []
    for index in indices:
        if group and index != group[-1] + 1:
            yield group
            group = []
        group.append(index)
    if group:
        yield group


def truncate_patch(patch: str, max_lines: int) -> tuple[str, bool]:
    """Keep the head and tail of an overlong patch, with the middle marked."""
    lines = patch.splitlines()
    if len(lines) <= max_lines:
        return patch, False
    head = max_lines * 2 // 3
    tail = max_lines - head
    omitted = len(lines) - head - tail
    body = (
        lines[:head]
        + [f"... [{omitted} lines of this file were not sent for review] ..."]
        + lines[-tail:]
    )
    return "\n".join(body), True


def hunk_summary(patch: str) -> str:
    """Hunk headers only — enough to say where a file changed, not what to."""
    headers = [h.header for h in parse_hunks(patch)]
    if not headers:
        return "(no textual diff)"
    shown = headers[:20]
    if len(headers) > len(shown):
        shown.append(f"... and {len(headers) - len(shown)} more hunks")
    return "\n".join(shown)


# ------------------------------------------------------------ compression


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _risk_score(path: str) -> int:
    lowered = path.lower()
    if any(part in lowered for part in ("/test/", "/tests/", "__tests__", ".test.", ".spec.")):
        return 1
    if lowered.endswith(".md") or "/docs/" in lowered:
        return 1
    if lowered.endswith(_SOURCE_SUFFIXES):
        return 3
    if lowered.endswith(_CONFIG_SUFFIXES):
        return 2
    return 2


def build(files: list[dict[str, Any]], policy: dict[str, Any]) -> DiffBundle:
    """Turn the GitHub files payload into a compressed, budgeted bundle."""
    exclude = policy.get("exclude") or []
    summarize = policy.get("summarize_only") or []
    max_file_lines = int(policy.get("max_file_lines") or 400)
    max_total_lines = int(policy.get("max_total_lines") or 3000)
    context_lines = int(policy.get("context_lines") if policy.get("context_lines") is not None else 3)

    parsed: list[FileDiff] = []
    for entry in files:
        parsed.append(
            FileDiff(
                path=entry.get("filename") or "",
                previous_path=entry.get("previous_filename"),
                status=entry.get("status") or "modified",
                additions=int(entry.get("additions") or 0),
                deletions=int(entry.get("deletions") or 0),
                patch=entry.get("patch"),
            )
        )

    total_lines = sum(f.changed_lines for f in parsed)

    excluded: list[FileDiff] = []
    summarized: list[FileDiff] = []
    reviewable: list[FileDiff] = []

    for file in parsed:
        if _matches_any(file.path, exclude):
            file.tier = "excluded"
            excluded.append(file)
        elif _matches_any(file.path, summarize) or file.patch is None:
            file.tier = "summary"
            file.rendered = hunk_summary(file.patch or "")
            summarized.append(file)
        else:
            body = trim_context(file.patch, context_lines)
            body, was_truncated = truncate_patch(body, max_file_lines)
            file.rendered = body
            file.truncated = was_truncated
            reviewable.append(file)

    # Budget applies to what is actually being read, so an artifact-heavy PR is
    # measured by its real content rather than its headline line count.
    reviewable_lines = sum(f.changed_lines for f in reviewable)
    dropped: list[FileDiff] = []

    if reviewable_lines > max_total_lines:
        # Lowest-risk files go first: tests and docs before source.
        order = sorted(
            reviewable,
            key=lambda f: (_risk_score(f.path), -f.changed_lines),
        )
        running = reviewable_lines
        for file in order:
            if running <= max_total_lines:
                break
            dropped.append(file)
            running -= file.changed_lines
        dropped_paths = {f.path for f in dropped}
        reviewable = [f for f in reviewable if f.path not in dropped_paths]
        reviewable_lines = running

    return DiffBundle(
        files=reviewable,
        excluded=excluded,
        summarized=summarized,
        dropped_for_size=dropped,
        truncated=[f for f in reviewable if f.truncated],
        reviewable_lines=reviewable_lines,
        total_lines=total_lines,
    )


def render(bundle: DiffBundle) -> str:
    """The diff as the model sees it."""
    blocks: list[str] = []

    for file in bundle.files:
        header = f"### {file.path}  (+{file.additions} -{file.deletions}, {file.status})"
        if file.previous_path:
            header += f"  [renamed from {file.previous_path}]"
        blocks.append(f"{header}\n```diff\n{file.rendered}\n```")

    for file in bundle.summarized:
        blocks.append(
            f"### {file.path}  (+{file.additions} -{file.deletions}, {file.status})  "
            f"[summarised — hunk locations only, contents not sent]\n"
            f"```\n{file.rendered}\n```"
        )

    if bundle.excluded:
        listing = "\n".join(
            f"- {f.path}  (+{f.additions} -{f.deletions})" for f in bundle.excluded
        )
        blocks.append(
            "### Files excluded from review by repository policy\n"
            "These changed but were not sent. Treat them as unreviewed.\n" + listing
        )

    if bundle.dropped_for_size:
        listing = "\n".join(
            f"- {f.path}  (+{f.additions} -{f.deletions})"
            for f in bundle.dropped_for_size
        )
        blocks.append(
            "### Files dropped because the PR exceeded its size budget\n"
            "These were not sent. Say so in the summary.\n" + listing
        )

    return "\n\n".join(blocks) if blocks else "(no reviewable changes)"


def manifest_note(bundle: DiffBundle) -> str:
    """The human-readable "what wasn't read" line for the summary comment."""
    parts: list[str] = []
    if bundle.excluded:
        parts.append(f"{len(bundle.excluded)} excluded by policy")
    if bundle.summarized:
        parts.append(f"{len(bundle.summarized)} summarised")
    if bundle.truncated:
        parts.append(f"{len(bundle.truncated)} truncated")
    if bundle.dropped_for_size:
        parts.append(f"{len(bundle.dropped_for_size)} dropped for size")
    if not parts:
        return ""
    return ", ".join(parts)
