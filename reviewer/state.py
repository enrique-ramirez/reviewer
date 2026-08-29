"""Persistent state, in SQLite, under ``~/.local/state/blinky/``.

Deliberately outside the checkout: it holds PR titles and, under ``--debug``,
diff content. Keeping it out of the repo means a stale ``.gitignore`` cannot turn
into a leak.

State is what makes this cheap. A PR whose head SHA has not moved and which has
no new comments is skipped before any diff is fetched and long before the model
is called.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import log

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url         TEXT PRIMARY KEY,
    etag        TEXT NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pr_state (
    repo                    TEXT NOT NULL,
    pr_number               INTEGER NOT NULL,
    last_reviewed_head_sha  TEXT,
    last_reviewed_at        REAL,
    last_review_action      TEXT,
    last_seen_comment_id    INTEGER,
    last_comment_scan_at    REAL,
    review_round            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS thread_state (
    repo                 TEXT NOT NULL,
    pr_number            INTEGER NOT NULL,
    thread_id            TEXT NOT NULL,
    disagreement_rounds  INTEGER NOT NULL DEFAULT 0,
    capped               INTEGER NOT NULL DEFAULT 0,
    last_seen_comment_id TEXT,
    updated_at           REAL,
    PRIMARY KEY (repo, pr_number, thread_id)
);

-- What the interface renders. One row per open pull request, written as soon
-- as the reviewer has looked at it and BEFORE any model call, so the board is
-- populated within seconds of a scan starting rather than after a review cycle.
CREATE TABLE IF NOT EXISTS pr_view (
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    title               TEXT,
    author              TEXT,
    url                 TEXT,
    is_ours             INTEGER NOT NULL DEFAULT 0,
    is_draft            INTEGER NOT NULL DEFAULT 0,
    opened_at           REAL,
    head_sha            TEXT,
    base_ref            TEXT,
    additions           INTEGER NOT NULL DEFAULT 0,
    deletions           INTEGER NOT NULL DEFAULT 0,
    changed_files       INTEGER NOT NULL DEFAULT 0,
    labels              TEXT,
    review_decision     TEXT,
    reviews             TEXT,
    requested_reviewers TEXT,
    mergeable           TEXT,
    approved_by_others  INTEGER NOT NULL DEFAULT 0,
    our_review_state    TEXT,
    requested_from_us   INTEGER NOT NULL DEFAULT 0,
    ci_state            TEXT,
    open_threads        INTEGER NOT NULL DEFAULT 0,
    threads_awaiting_us INTEGER NOT NULL DEFAULT 0,
    capped_threads      INTEGER NOT NULL DEFAULT 0,
    needs_human         INTEGER NOT NULL DEFAULT 0,
    needs_human_reason  TEXT,
    last_action         TEXT,
    seen_at             REAL NOT NULL,
    PRIMARY KEY (repo, pr_number)
);

-- Written before a review is posted and updated after. A row in state
-- 'pending' at startup means a previous run died mid-post; the head SHA is
-- re-checked against GitHub before anything is posted again.
CREATE TABLE IF NOT EXISTS post_attempts (
    repo       TEXT NOT NULL,
    pr_number  INTEGER NOT NULL,
    head_sha   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (repo, pr_number, head_sha, kind)
);

-- What the reviewer is working on right now, so the board can say "reviewing"
-- rather than showing the previous pass's outcome for the several minutes a
-- review takes. Transient by nature: reviews run one at a time inside a tick,
-- so anything still here when a tick begins is left over from a process that
-- died mid-review and is cleared.
--
-- The columns past started_at are the difference between "this is taking a
-- while" and "this has hung", which a start time alone cannot tell you.
-- heartbeat_at is when the call last checked in, slept_seconds is how much of
-- the elapsed time the machine spent asleep, silent_seconds is how long the
-- model has gone without printing anything, and note is whatever it was last
-- seen doing.
CREATE TABLE IF NOT EXISTS active_reviews (
    repo           TEXT NOT NULL,
    pr_number      INTEGER NOT NULL,
    phase          TEXT NOT NULL,
    started_at     REAL NOT NULL,
    heartbeat_at   REAL,
    slept_seconds  REAL,
    silent_seconds REAL,
    note           TEXT,
    PRIMARY KEY (repo, pr_number)
);

-- How far back the merged log has been filled in, per repository. Backfill is
-- something the user asks for, never something that happens on its own, and
-- this is what lets the interface say what has already been covered instead of
-- offering to fetch the same year twice.
CREATE TABLE IF NOT EXISTS backfill_state (
    repo           TEXT PRIMARY KEY,
    covered_since  REAL,
    filed          INTEGER NOT NULL DEFAULT 0,
    scanned        INTEGER NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL
);

-- One row per review we actually posted, appended rather than overwritten.
-- 'How many comments did we leave before this landed' cannot be answered from
-- pr_state, which only remembers the most recent pass. Comment *bodies* are
-- deliberately not kept: the counts are what the summary shows, and the text
-- itself already lives on GitHub.
CREATE TABLE IF NOT EXISTS review_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT,
    event       TEXT NOT NULL,
    findings    INTEGER NOT NULL DEFAULT 0,
    blockers    INTEGER NOT NULL DEFAULT 0,
    inline      INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,
    -- What the round cost. Nullable throughout: not every provider reports
    -- usage, and rows written before these columns existed have none.
    calls            INTEGER,
    duration_seconds REAL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    cached_tokens    INTEGER,
    cost_usd         REAL,
    provider         TEXT,
    model            TEXT,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS review_events_pr
    ON review_events (repo, pr_number);

-- The historic record, and the only table meant to outlive a pull request:
-- pr_view holds what is open right now and is emptied as things merge. A row
-- lands here once, when a PR we reviewed leaves the open list having been
-- merged, and is never rewritten — ``description`` costs a model call, so
-- re-recording would mean paying for it twice.
CREATE TABLE IF NOT EXISTS merged_prs (
    repo               TEXT NOT NULL,
    pr_number          INTEGER NOT NULL,
    title              TEXT,
    author             TEXT,
    url                TEXT,
    base_ref           TEXT,
    labels             TEXT,
    is_ours            INTEGER NOT NULL DEFAULT 0,
    additions          INTEGER NOT NULL DEFAULT 0,
    deletions          INTEGER NOT NULL DEFAULT 0,
    changed_files      INTEGER NOT NULL DEFAULT 0,
    opened_at          REAL,
    merged_at          REAL,
    merged_by          TEXT,
    our_reviews        INTEGER NOT NULL DEFAULT 0,
    our_comments       INTEGER NOT NULL DEFAULT 0,
    our_blockers       INTEGER NOT NULL DEFAULT 0,
    last_event         TEXT,
    -- What reviewing it cost us, totalled over every round and frozen here at
    -- merge time. The events themselves survive, but this row is what the
    -- History tab reads and a query per row would turn a page into 25 of them.
    review_seconds       REAL,
    review_input_tokens  INTEGER,
    review_output_tokens INTEGER,
    review_cached_tokens INTEGER,
    review_cost_usd      REAL,
    review_model         TEXT,
    description        TEXT,
    description_source TEXT,
    description_tries  INTEGER NOT NULL DEFAULT 0,
    -- Fetched by a backfill rather than watched as it happened. ``recorded_at``
    -- says when we wrote the row down, which for a backfill is "just now" for
    -- something that merged months ago — so it cannot be used to answer "what
    -- landed while I was watching". This can.
    backfilled         INTEGER NOT NULL DEFAULT 0,
    recorded_at        REAL NOT NULL,
    PRIMARY KEY (repo, pr_number)
);

CREATE INDEX IF NOT EXISTS merged_prs_recorded ON merged_prs (recorded_at DESC);
CREATE INDEX IF NOT EXISTS merged_prs_merged   ON merged_prs (merged_at DESC);
"""


# Board columns holding a JSON list rather than a scalar. Stored as text
# because SQLite has no list type, and decoded on the way back out so callers
# never see the encoding.
JSON_COLUMNS = ("labels", "reviews", "requested_reviewers")


def _sum(row: Any, name: str) -> float:
    """A SUM() that came back NULL means no row had a figure. Read it as zero."""
    if row is None:
        return 0
    value = row[name]
    return value if isinstance(value, (int, float)) else 0


def _decode_list(raw: Any) -> list[Any]:
    """A JSON list column as a list. A missing or unreadable value is empty."""
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


@dataclass
class PRState:
    last_reviewed_head_sha: str | None = None
    last_reviewed_at: float | None = None
    last_review_action: str | None = None
    last_seen_comment_id: int | None = None
    last_comment_scan_at: float | None = None
    review_round: int = 0


LEGACY_STATE_DIR_NAME = "pr-reviewer"
STATE_DIR_NAME = "blinky"


def default_state_dir() -> Path:
    override = os.environ.get("BLINKY_STATE_DIR") or os.environ.get(
        "PR_REVIEWER_STATE_DIR"
    )
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return root / STATE_DIR_NAME


def adopt_legacy_state_dir(state_dir: Path) -> Path | None:
    """Carry an older build's state over to the renamed directory.

    The database in there is the whole history — every merge, every review, and
    every summary already paid for. A rename that left it behind would look
    exactly like the tool having forgotten everything.

    One move, only when the new directory does not exist yet, and only for the
    default location: someone who passed ``--state-dir`` meant that directory.
    Returns where it came from, so the caller can say so.
    """
    if state_dir.name != STATE_DIR_NAME or state_dir.exists():
        return None
    legacy = state_dir.with_name(LEGACY_STATE_DIR_NAME)
    if not legacy.is_dir():
        return None
    try:
        legacy.rename(state_dir)
    except OSError:
        # Different filesystem, or no permission. Not worth failing over: a
        # fresh directory is a working tool, just an empty one.
        return None
    return legacy


def _enable_wal(conn: sqlite3.Connection, attempts: int = 20, wait: float = 0.05) -> None:
    """Turn on WAL, tolerating another connection doing the same thing.

    The dashboard opens two connections to the same file — one on the reviewer's
    thread, one for the interface — and on a first run they race. Switching
    journal mode wants a brief exclusive lock, and SQLite answers ``database is
    locked`` immediately for it rather than waiting out ``busy_timeout`` the way
    an ordinary statement would. So the loser of that race used to raise, on the
    very first launch and never again, which is the worst shape a bug can have.

    Retried rather than fatal, and then given up on quietly: journal mode is a
    property of the database rather than of a connection, so if this never wins
    it is because somebody else already set it to exactly what we wanted.
    """
    for attempt in range(attempts):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == attempts - 1:
                log.get().debug("could not set WAL; another connection got there first")
                return
            time.sleep(wait)


def heartbeat(store: "Store", repo: str, pr_number: int) -> Any:
    """An ``on_progress`` callback for ``model.run`` that records a heartbeat.

    Here rather than in ``model`` because the database is this module's business
    and the model call has no idea one exists. Takes anything with the fields of
    a ``model.Progress`` — the two modules stay unaware of each other, and the
    tests can beat this with a stub.
    """

    def beat(progress: Any) -> None:
        store.beat_active(
            repo,
            pr_number,
            slept_seconds=getattr(progress, "slept", 0.0),
            silent_seconds=getattr(progress, "silent_for", 0.0),
            note=getattr(progress, "note", "") or "",
        )

    return beat


class Store:
    def __init__(self, state_dir: Path, filename: str = "state.sqlite3") -> None:
        """Open the state database.

        ``filename`` exists so ``--dry-run`` can use a separate file. A dry run
        must not record "reviewed" against live state — that would make the next
        real run skip a PR it never actually reviewed. Equally, repeating a dry
        run should not repeat the whole review, which is minutes of work. A
        second database gives both: rehearsals are idempotent among themselves
        and invisible to the real one.
        """
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.path = self.state_dir / filename
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        _enable_wal(self.conn)
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        os.chmod(self.path, 0o600)

    # Columns added after this table first shipped. CREATE TABLE IF NOT EXISTS
    # brings in whole new tables but says nothing about new columns on an
    # existing one, so those are listed here and added on open.
    ADDED_COLUMNS: dict[str, dict[str, str]] = {
        "pr_view": {
            "reviews": "TEXT",
            "requested_reviewers": "TEXT",
            "opened_at": "REAL",
        },
        "merged_prs": {
            "backfilled": "INTEGER NOT NULL DEFAULT 0",
            "review_seconds": "REAL",
            "review_input_tokens": "INTEGER",
            "review_output_tokens": "INTEGER",
            "review_cached_tokens": "INTEGER",
            "review_cost_usd": "REAL",
            "review_model": "TEXT",
        },
        "active_reviews": {
            "heartbeat_at": "REAL",
            "slept_seconds": "REAL",
            "silent_seconds": "REAL",
            "note": "TEXT",
        },
        "review_events": {
            "calls": "INTEGER",
            "duration_seconds": "REAL",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
            "cached_tokens": "INTEGER",
            "cost_usd": "REAL",
            "provider": "TEXT",
            "model": "TEXT",
        },
    }

    def _migrate(self) -> None:
        """Bring an older database up to the current shape.

        Additive only: columns are appended, never dropped, renamed, or
        rewritten, so an older build reading this database still works and
        running it twice changes nothing. A column that is missing reads as
        NULL on existing rows, which every caller already handles.
        """
        added: set[str] = set()
        for table, columns in self.ADDED_COLUMNS.items():
            existing = {
                row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            for name, decl in columns.items():
                if name not in existing:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}"
                    )
                    added.add(f"{table}.{name}")

        if "merged_prs.backfilled" in added:
            # Rows written by a backfill before the column existed. They carry a
            # signature nothing else writes — the author's title as the summary,
            # with description retries already exhausted — and without this they
            # would go on claiming to have merged during whichever run fetched
            # them.
            self.conn.execute(
                "UPDATE merged_prs SET backfilled = 1 "
                "WHERE description_source = 'title' AND description_tries >= 99"
            )

        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- HTTP

    def get_cached(self, url: str) -> tuple[str, Any] | None:
        row = self.conn.execute(
            "SELECT etag, body FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        try:
            return row["etag"], json.loads(row["body"])
        except json.JSONDecodeError:
            return None

    def put_cached(self, url: str, etag: str, body: Any) -> None:
        self.conn.execute(
            "INSERT INTO http_cache (url, etag, body, fetched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, body=excluded.body, "
            "fetched_at=excluded.fetched_at",
            (url, etag, json.dumps(body), time.time()),
        )
        self.conn.commit()

    def prune_cache(self, older_than_seconds: float = 7 * 24 * 3600) -> None:
        self.conn.execute(
            "DELETE FROM http_cache WHERE fetched_at < ?",
            (time.time() - older_than_seconds,),
        )
        self.conn.commit()

    # ------------------------------------------------------------ PR state

    def get_pr(self, repo: str, pr_number: int) -> PRState:
        row = self.conn.execute(
            "SELECT * FROM pr_state WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ).fetchone()
        if row is None:
            return PRState()
        return PRState(
            last_reviewed_head_sha=row["last_reviewed_head_sha"],
            last_reviewed_at=row["last_reviewed_at"],
            last_review_action=row["last_review_action"],
            last_seen_comment_id=row["last_seen_comment_id"],
            last_comment_scan_at=row["last_comment_scan_at"],
            review_round=row["review_round"] or 0,
        )

    def record_review(
        self, repo: str, pr_number: int, head_sha: str, action: str
    ) -> None:
        current = self.get_pr(repo, pr_number)
        self.conn.execute(
            """
            INSERT INTO pr_state (repo, pr_number, last_reviewed_head_sha,
                                  last_reviewed_at, last_review_action, review_round)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number) DO UPDATE SET
                last_reviewed_head_sha = excluded.last_reviewed_head_sha,
                last_reviewed_at       = excluded.last_reviewed_at,
                last_review_action     = excluded.last_review_action,
                review_round           = excluded.review_round
            """,
            (repo, pr_number, head_sha, time.time(), action, current.review_round + 1),
        )
        self.conn.commit()

    def record_comment_scan(
        self, repo: str, pr_number: int, last_comment_id: int | None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO pr_state (repo, pr_number, last_seen_comment_id, last_comment_scan_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo, pr_number) DO UPDATE SET
                last_seen_comment_id = excluded.last_seen_comment_id,
                last_comment_scan_at = excluded.last_comment_scan_at
            """,
            (repo, pr_number, last_comment_id, time.time()),
        )
        self.conn.commit()

    # -------------------------------------------------------- thread state

    def get_thread_rounds(self, repo: str, pr_number: int, thread_id: str) -> int:
        row = self.conn.execute(
            "SELECT disagreement_rounds FROM thread_state "
            "WHERE repo = ? AND pr_number = ? AND thread_id = ?",
            (repo, pr_number, thread_id),
        ).fetchone()
        return int(row["disagreement_rounds"]) if row else 0

    def is_thread_capped(self, repo: str, pr_number: int, thread_id: str) -> bool:
        row = self.conn.execute(
            "SELECT capped FROM thread_state "
            "WHERE repo = ? AND pr_number = ? AND thread_id = ?",
            (repo, pr_number, thread_id),
        ).fetchone()
        return bool(row["capped"]) if row else False

    def bump_thread_round(
        self, repo: str, pr_number: int, thread_id: str, cap: int
    ) -> tuple[int, bool]:
        """Increment a thread's disagreement counter.

        Returns ``(rounds, capped)``. The cap is per thread — other threads on
        the same PR, and later review rounds, are unaffected.
        """
        rounds = self.get_thread_rounds(repo, pr_number, thread_id) + 1
        capped = rounds >= cap
        self.conn.execute(
            """
            INSERT INTO thread_state (repo, pr_number, thread_id, disagreement_rounds,
                                      capped, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number, thread_id) DO UPDATE SET
                disagreement_rounds = excluded.disagreement_rounds,
                capped              = excluded.capped,
                updated_at          = excluded.updated_at
            """,
            (repo, pr_number, thread_id, rounds, int(capped), time.time()),
        )
        self.conn.commit()
        return rounds, capped

    def mark_thread_seen(
        self, repo: str, pr_number: int, thread_id: str, last_comment_id: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO thread_state (repo, pr_number, thread_id, last_seen_comment_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number, thread_id) DO UPDATE SET
                last_seen_comment_id = excluded.last_seen_comment_id,
                updated_at           = excluded.updated_at
            """,
            (repo, pr_number, thread_id, last_comment_id, time.time()),
        )
        self.conn.commit()

    def thread_last_seen(
        self, repo: str, pr_number: int, thread_id: str
    ) -> str | None:
        row = self.conn.execute(
            "SELECT last_seen_comment_id FROM thread_state "
            "WHERE repo = ? AND pr_number = ? AND thread_id = ?",
            (repo, pr_number, thread_id),
        ).fetchone()
        return row["last_seen_comment_id"] if row else None

    # ------------------------------------------------------------- board

    @staticmethod
    def _repo_filter(repos: list[str] | None) -> tuple[str, list[Any]]:
        """The ``WHERE repo IN (...)`` shared by every board-side query."""
        if not repos:
            return "", []
        return f" WHERE repo IN ({', '.join('?' for _ in repos)})", list(repos)

    def upsert_pr_view(self, row: dict[str, Any]) -> None:
        """Record or refresh one pull request on the board.

        ``last_action`` is preserved when the caller omits it, so a fast scan
        pass does not wipe the outcome written by the previous review.
        """
        row = dict(row)
        row.setdefault("seen_at", time.time())
        for column in JSON_COLUMNS:
            if column in row and not isinstance(row[column], str):
                row[column] = json.dumps(row[column])

        keep_action = "last_action" not in row
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in columns if c not in ("repo", "pr_number")
        )
        if keep_action:
            updates += ", last_action=COALESCE(pr_view.last_action, NULL)"

        self.conn.execute(
            f"INSERT INTO pr_view ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(repo, pr_number) DO UPDATE SET {updates}",
            [row[c] for c in columns],
        )
        self.conn.commit()

    def set_pr_action(self, repo: str, pr_number: int, action: str) -> None:
        self.conn.execute(
            "UPDATE pr_view SET last_action = ?, seen_at = ? "
            "WHERE repo = ? AND pr_number = ?",
            (action, time.time(), repo, pr_number),
        )
        self.conn.commit()

    def list_pr_view(self, repos: list[str] | None = None) -> list[dict[str, Any]]:
        where, params = self._repo_filter(repos)
        sql = f"SELECT * FROM pr_view{where} ORDER BY repo, pr_number DESC"
        rows = []
        for record in self.conn.execute(sql, params).fetchall():
            item = dict(record)
            for column in JSON_COLUMNS:
                if column in item:
                    item[column] = _decode_list(item[column])
            rows.append(item)
        return rows

    def board_numbers(self, repo: str) -> list[int]:
        """Pull request numbers currently on the board for one repository."""
        rows = self.conn.execute(
            "SELECT pr_number FROM pr_view WHERE repo = ?", (repo,)
        ).fetchall()
        return [int(r["pr_number"]) for r in rows]

    def our_board_numbers(self, repo: str) -> set[int]:
        """Which of those we wrote ourselves.

        Read while the row is still on the board, because that is the only
        record that a pull request was ours: the reviewer never reviews its
        owner's work, so nothing in ``review_events`` remembers it, and once it
        merges and the board row is dropped there is nothing left to ask.
        """
        rows = self.conn.execute(
            "SELECT pr_number FROM pr_view WHERE repo = ? AND is_ours = 1", (repo,)
        ).fetchall()
        return {int(r["pr_number"]) for r in rows}

    def forget_closed(self, repo: str, open_numbers: list[int]) -> None:
        """Drop board rows for pull requests that are no longer open.

        An empty list means every pull request closed, not "no information" —
        the caller returns early when the listing itself failed, so reaching
        here with nothing means the repository genuinely has none open. Bailing
        out on empty would leave the last PR on the board forever.
        """
        if not open_numbers:
            self.conn.execute("DELETE FROM pr_view WHERE repo = ?", (repo,))
            self.conn.commit()
            return
        placeholders = ", ".join("?" for _ in open_numbers)
        self.conn.execute(
            f"DELETE FROM pr_view WHERE repo = ? AND pr_number NOT IN ({placeholders})",
            [repo, *open_numbers],
        )
        self.conn.commit()

    # ------------------------------------------------------ work in flight

    def begin_active(self, repo: str, pr_number: int, phase: str) -> None:
        self.conn.execute(
            """
            INSERT INTO active_reviews (repo, pr_number, phase, started_at,
                                        heartbeat_at, slept_seconds,
                                        silent_seconds, note)
            VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL)
            ON CONFLICT(repo, pr_number) DO UPDATE SET
                phase = excluded.phase,
                -- A phase change is a fresh piece of work on the same row, so
                -- the previous one's heartbeat must not carry over and describe
                -- it. Left NULL until the new call checks in for the first time.
                heartbeat_at = NULL,
                slept_seconds = NULL,
                silent_seconds = NULL,
                note = NULL
            """,
            (repo, pr_number, phase, time.time()),
        )
        self.conn.commit()

    def beat_active(
        self,
        repo: str,
        pr_number: int,
        *,
        slept_seconds: float = 0.0,
        silent_seconds: float = 0.0,
        note: str = "",
    ) -> None:
        """Record that live work is still live, and what it is doing.

        Written every few seconds from inside the model call. The point is not
        the timestamp on its own but what it lets the board say: a review that
        has been on screen for fifteen minutes is alarming, and the same review
        annotated "twelve of those asleep, last spoke four seconds ago" is not.

        Deliberately an UPDATE and not an upsert. If the row is gone the work is
        over — or the tick that owned it died and cleared it — and a heartbeat
        must not be the thing that resurrects a review nobody is running.
        """
        self.conn.execute(
            """
            UPDATE active_reviews
               SET heartbeat_at = ?, slept_seconds = ?, silent_seconds = ?, note = ?
             WHERE repo = ? AND pr_number = ?
            """,
            (
                time.time(),
                float(slept_seconds),
                float(silent_seconds),
                note or None,
                repo,
                pr_number,
            ),
        )
        self.conn.commit()

    def end_active(self, repo: str, pr_number: int) -> None:
        self.conn.execute(
            "DELETE FROM active_reviews WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        )
        self.conn.commit()

    def clear_active(self, repo: str) -> None:
        """Drop anything left in flight, called as a tick begins.

        Reviews run one at a time within a tick, so at the start of one nothing
        can legitimately be in progress. Whatever is here outlived the process
        that wrote it, and showing it as live work would be a lie that persists
        until the next restart.
        """
        self.conn.execute("DELETE FROM active_reviews WHERE repo = ?", (repo,))
        self.conn.commit()

    def active_reviews(self, repos: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """In-flight work, keyed ``repo#number`` for the interface to look up."""
        where, params = self._repo_filter(repos)
        sql = f"SELECT * FROM active_reviews{where}"
        return {
            f"{r['repo']}#{r['pr_number']}": dict(r)
            for r in self.conn.execute(sql, params).fetchall()
        }

    # ------------------------------------------------------- review history

    def record_review_event(
        self,
        repo: str,
        pr_number: int,
        *,
        head_sha: str,
        event: str,
        findings: int = 0,
        blockers: int = 0,
        inline: int = 0,
        summary: str = "",
        spend: Any = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO review_events (repo, pr_number, head_sha, event, findings,
                                       blockers, inline, summary, calls,
                                       duration_seconds, input_tokens,
                                       output_tokens, cached_tokens, cost_usd,
                                       provider, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo,
                pr_number,
                head_sha,
                event,
                findings,
                blockers,
                inline,
                summary or None,
                # Left NULL rather than zeroed when nothing was measured, so a
                # provider that reports no usage is distinguishable from a
                # review that genuinely cost nothing.
                getattr(spend, "calls", None),
                getattr(spend, "seconds", None),
                getattr(spend, "input_tokens", None),
                getattr(spend, "output_tokens", None),
                getattr(spend, "cached_tokens", None),
                getattr(spend, "cost_usd", None) or None,
                getattr(spend, "provider", None) or None,
                getattr(spend, "model", None) or None,
                time.time(),
            ),
        )
        self.conn.commit()

    def latest_review_events(self, repos: list[str]) -> dict[tuple[str, int], dict[str, Any]]:
        """The most recent round for each pull request, keyed by repo and number.

        One query rather than one per row: the board redraws on a timer, and a
        query per open pull request would turn a poll into a fan of them.
        """
        if not repos:
            return {}
        marks = ",".join("?" for _ in repos)
        rows = self.conn.execute(
            f"""
            SELECT e.* FROM review_events e
            JOIN (
                SELECT repo, pr_number, MAX(id) AS id FROM review_events
                WHERE repo IN ({marks}) GROUP BY repo, pr_number
            ) last ON e.id = last.id
            """,
            repos,
        ).fetchall()
        return {(row["repo"], row["pr_number"]): dict(row) for row in rows}

    def has_reviewed(self, repo: str, pr_number: int) -> bool:
        """Whether we ever posted anything on this pull request.

        Approval is not the bar — a PR we commented on or requested changes on
        is one we have an opinion about, and its merge is worth recording.

        ``pr_state`` is consulted as well as ``review_events`` so that pull
        requests reviewed by earlier versions of this tool, before the events
        table existed, still count.
        """
        row = self.conn.execute(
            "SELECT 1 FROM review_events WHERE repo = ? AND pr_number = ? LIMIT 1",
            (repo, pr_number),
        ).fetchone()
        if row is not None:
            return True
        row = self.conn.execute(
            "SELECT last_review_action FROM pr_state WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ).fetchone()
        return bool(row and row["last_review_action"])

    def reviewed_pull_requests(self, repos: list[str] | None = None) -> set[str]:
        """Keys, ``repo#number``, of every pull request we have posted on.

        Two queries for the whole board rather than one per row: the interface
        asks this on every poll, to say how much of each repository has been
        looked at and to tell "we skipped this because nothing changed" apart
        from "we have never looked at this".

        ``pr_state`` is consulted as well as ``review_events`` for the same
        reason :meth:`has_reviewed` consults it — pull requests reviewed by
        earlier versions of this tool, before the events table existed, still
        count.
        """
        where, params = self._repo_filter(repos)
        joiner = " AND" if where else " WHERE"
        reviewed: set[str] = set()
        for sql in (
            f"SELECT DISTINCT repo, pr_number FROM review_events{where}",
            f"SELECT repo, pr_number FROM pr_state{where}{joiner} "
            "last_review_action IS NOT NULL",
        ):
            reviewed.update(
                f"{row['repo']}#{row['pr_number']}"
                for row in self.conn.execute(sql, params).fetchall()
            )
        return reviewed

    def review_tally(self, repo: str, pr_number: int) -> dict[str, Any]:
        """What we did to one pull request, totalled across every round."""
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS rounds,
                   COALESCE(SUM(inline), 0)   AS comments,
                   COALESCE(SUM(blockers), 0) AS blockers,
                   SUM(calls)            AS calls,
                   SUM(duration_seconds) AS duration_seconds,
                   SUM(input_tokens)     AS input_tokens,
                   SUM(output_tokens)    AS output_tokens,
                   SUM(cached_tokens)    AS cached_tokens,
                   SUM(cost_usd)         AS cost_usd
            FROM review_events WHERE repo = ? AND pr_number = ?
            """,
            (repo, pr_number),
        ).fetchone()
        last = self.conn.execute(
            "SELECT event, summary, provider, model FROM review_events "
            "WHERE repo = ? AND pr_number = ? ORDER BY id DESC LIMIT 1",
            (repo, pr_number),
        ).fetchone()

        tally = {
            "rounds": int(row["rounds"]) if row else 0,
            "comments": int(row["comments"]) if row else 0,
            "blockers": int(row["blockers"]) if row else 0,
            "last_event": last["event"] if last else None,
            "summary": (last["summary"] if last else None) or "",
            # Totalled over every round, so this answers "what did reviewing
            # this pull request cost" rather than "what did the last pass cost".
            "calls": _sum(row, "calls"),
            "duration_seconds": float(_sum(row, "duration_seconds")),
            "input_tokens": _sum(row, "input_tokens"),
            "output_tokens": _sum(row, "output_tokens"),
            "cached_tokens": _sum(row, "cached_tokens"),
            "cost_usd": float(_sum(row, "cost_usd")),
            "provider": (last["provider"] if last else None) or "",
            "model": (last["model"] if last else None) or "",
        }
        if not tally["rounds"]:
            # Reviewed before the events table existed. The round count is lost,
            # but the outcome is not, and one known round beats claiming zero.
            fallback = self.conn.execute(
                "SELECT last_review_action, review_round FROM pr_state "
                "WHERE repo = ? AND pr_number = ?",
                (repo, pr_number),
            ).fetchone()
            if fallback and fallback["last_review_action"]:
                tally["rounds"] = int(fallback["review_round"] or 1)
                tally["last_event"] = fallback["last_review_action"]
        return tally

    # ---------------------------------------------------------- merged log

    def is_merge_recorded(self, repo: str, pr_number: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM merged_prs WHERE repo = ? AND pr_number = ? LIMIT 1",
            (repo, pr_number),
        ).fetchone()
        return row is not None

    def record_merged(self, row: dict[str, Any]) -> bool:
        """File a merged pull request. Returns False if it was already filed.

        ``DO NOTHING`` rather than an upsert on purpose: the description costs a
        model call, and a second sighting of the same merge must not throw it
        away and buy another one.
        """
        row = dict(row)
        row.setdefault("recorded_at", time.time())
        if "labels" in row and not isinstance(row["labels"], str):
            row["labels"] = json.dumps(row["labels"])

        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        cur = self.conn.execute(
            f"INSERT INTO merged_prs ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(repo, pr_number) DO NOTHING",
            [row[c] for c in columns],
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_merge_description(
        self, repo: str, pr_number: int, description: str, source: str
    ) -> None:
        self.conn.execute(
            "UPDATE merged_prs SET description = ?, description_source = ? "
            "WHERE repo = ? AND pr_number = ?",
            (description, source, repo, pr_number),
        )
        self.conn.commit()

    def _merged_filters(
        self,
        repos: list[str] | None,
        since: float | None,
        author: str | None,
        merged_after: float | None = None,
        live_only: bool = False,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if repos:
            clauses.append(f"repo IN ({', '.join('?' for _ in repos)})")
            params.extend(repos)
        if live_only:
            clauses.append("backfilled = 0")
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since)
        if merged_after is not None:
            clauses.append("COALESCE(merged_at, recorded_at) >= ?")
            params.append(merged_after)
        if author:
            clauses.append("LOWER(author) LIKE ?")
            params.append(f"%{author.lower()}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_merged(
        self,
        repos: list[str] | None = None,
        *,
        since: float | None = None,
        author: str | None = None,
        merged_after: float | None = None,
        live_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Merged pull requests, newest first.

        ``since`` filters on when *we noticed* the merge rather than when it
        happened, which is what makes "this run" mean the same thing as the log
        pane beside it. ``merged_after`` filters on the merge itself, which is
        what a "last 30 days" window means.
        """
        where, params = self._merged_filters(
            repos, since, author, merged_after, live_only
        )
        sql = (
            f"SELECT * FROM merged_prs{where} "
            "ORDER BY COALESCE(merged_at, recorded_at) DESC, pr_number DESC "
            "LIMIT ? OFFSET ?"
        )
        rows = []
        for record in self.conn.execute(sql, [*params, limit, offset]).fetchall():
            item = dict(record)
            try:
                item["labels"] = json.loads(item.get("labels") or "[]")
            except json.JSONDecodeError:
                item["labels"] = []
            rows.append(item)
        return rows

    def count_merged(
        self,
        repos: list[str] | None = None,
        *,
        since: float | None = None,
        author: str | None = None,
        merged_after: float | None = None,
        live_only: bool = False,
    ) -> int:
        where, params = self._merged_filters(
            repos, since, author, merged_after, live_only
        )
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM merged_prs{where}", params
        ).fetchone()
        return int(row["n"]) if row else 0

    def merged_pending_description(
        self,
        repos: list[str] | None = None,
        *,
        limit: int = 20,
        max_tries: int = 3,
    ) -> list[dict[str, Any]]:
        """Merges still owed a description, oldest first.

        A description can fail — the model call times out, the CLI is missing —
        and the merge is recorded either way, so these are retried on later
        ticks and a transient failure costs a delay rather than the summary.

        ``max_tries`` is what stops that retry from becoming permanent: with the
        configured provider's CLI missing from PATH, every merge would otherwise
        buy a failed subprocess every tick, forever.
        """
        where, params = self._merged_filters(repos, None, None)
        joiner = " AND" if where else " WHERE"
        rows = self.conn.execute(
            f"SELECT * FROM merged_prs{where}{joiner} description IS NULL "
            "AND description_tries < ? ORDER BY recorded_at ASC LIMIT ?",
            [*params, max_tries, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def merged_repos(self) -> list[str]:
        """Repositories that have anything in the merged log, for the filter."""
        return [
            r["repo"]
            for r in self.conn.execute(
                "SELECT DISTINCT repo FROM merged_prs ORDER BY repo"
            )
        ]

    def merged_authors(self, repos: list[str] | None = None) -> list[str]:
        where, params = self._merged_filters(repos, None, None)
        return [
            r["author"]
            for r in self.conn.execute(
                f"SELECT DISTINCT author FROM merged_prs{where} ORDER BY author", params
            )
            if r["author"]
        ]

    # -------------------------------------------------------- backfill log

    def backfill_coverage(self, repo: str) -> dict[str, Any] | None:
        """What a previous backfill already covered for this repository."""
        row = self.conn.execute(
            "SELECT * FROM backfill_state WHERE repo = ?", (repo,)
        ).fetchone()
        return dict(row) if row else None

    def record_backfill(
        self, repo: str, covered_since: float | None, filed: int, scanned: int
    ) -> None:
        """Note how far back this repository has been filled in.

        ``covered_since`` of None means "all time". Coverage only ever widens:
        backfilling last month after having done all time must not narrow the
        record to last month.
        """
        existing = self.backfill_coverage(repo)
        if existing:
            previous = existing["covered_since"]
            if previous is None or covered_since is None:
                covered_since = None  # one side covered everything
            else:
                covered_since = min(float(previous), float(covered_since))
            filed += int(existing["filed"] or 0)
            scanned += int(existing["scanned"] or 0)

        self.conn.execute(
            """
            INSERT INTO backfill_state (repo, covered_since, filed, scanned, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                covered_since = excluded.covered_since,
                filed         = excluded.filed,
                scanned       = excluded.scanned,
                updated_at    = excluded.updated_at
            """,
            (repo, covered_since, filed, scanned, time.time()),
        )
        self.conn.commit()

    def bump_description_try(self, repo: str, pr_number: int) -> int:
        """Count a description attempt. Returns the new total."""
        self.conn.execute(
            "UPDATE merged_prs SET description_tries = description_tries + 1 "
            "WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT description_tries FROM merged_prs WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ).fetchone()
        return int(row["description_tries"]) if row else 0

    # ------------------------------------------------------- idempotency

    def already_posted(
        self, repo: str, pr_number: int, head_sha: str, kind: str = "review"
    ) -> bool:
        """Whether this exact post already completed.

        Checked *before* the model is called, not just before posting. A crash
        between "GitHub accepted the review" and "state recorded it" would
        otherwise leave the next tick spending several minutes of model time on
        a review it then throws away.
        """
        row = self.conn.execute(
            "SELECT status FROM post_attempts "
            "WHERE repo = ? AND pr_number = ? AND head_sha = ? AND kind = ?",
            (repo, pr_number, head_sha, kind),
        ).fetchone()
        return row is not None and row["status"] == "done"

    def begin_post(self, repo: str, pr_number: int, head_sha: str, kind: str) -> bool:
        """Claim the right to post.

        Returns False when this exact post already completed, which is what stops
        a crash between "posted to GitHub" and "wrote state" from producing a
        duplicate review on the next tick.
        """
        row = self.conn.execute(
            "SELECT status FROM post_attempts "
            "WHERE repo = ? AND pr_number = ? AND head_sha = ? AND kind = ?",
            (repo, pr_number, head_sha, kind),
        ).fetchone()
        if row is not None and row["status"] == "done":
            return False
        self.conn.execute(
            """
            INSERT INTO post_attempts (repo, pr_number, head_sha, kind, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(repo, pr_number, head_sha, kind) DO UPDATE SET
                status = 'pending', created_at = excluded.created_at
            """,
            (repo, pr_number, head_sha, kind, time.time()),
        )
        self.conn.commit()
        return True

    def finish_post(self, repo: str, pr_number: int, head_sha: str, kind: str) -> None:
        """Mark a post as completed.

        An upsert rather than an update: a plain UPDATE against a missing row
        succeeds while changing nothing, which would leave a posted review
        unrecorded and let the next tick post it again. The claim is what this
        table exists for, so write it either way.
        """
        self.conn.execute(
            """
            INSERT INTO post_attempts (repo, pr_number, head_sha, kind, status, created_at)
            VALUES (?, ?, ?, ?, 'done', ?)
            ON CONFLICT(repo, pr_number, head_sha, kind) DO UPDATE SET status = 'done'
            """,
            (repo, pr_number, head_sha, kind, time.time()),
        )
        self.conn.commit()

    def abandon_post(self, repo: str, pr_number: int, head_sha: str, kind: str) -> None:
        self.conn.execute(
            "DELETE FROM post_attempts "
            "WHERE repo = ? AND pr_number = ? AND head_sha = ? AND kind = ?",
            (repo, pr_number, head_sha, kind),
        )
        self.conn.commit()
