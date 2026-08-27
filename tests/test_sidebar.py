from __future__ import annotations

import unittest

from reviewer.tui import theme
from reviewer.tui.models import Activity
from reviewer.tui.session import Session
from reviewer.tui.views import sidebar
from tests.builders import NOW, pull_request

REPOS = ("acme/widgets", "acme/gadgets")


def session(**kwargs: object) -> Session:
    return Session(repos=REPOS, started_at=NOW, **kwargs)  # type: ignore[arg-type]


def board() -> list[object]:
    return [
        pull_request(repo="acme/widgets", pr_number=1, reviewed=True),
        pull_request(repo="acme/widgets", pr_number=2, needs_human=1, reviewed=True),
        pull_request(repo="acme/widgets", pr_number=3),
        pull_request(
            repo="acme/gadgets",
            pr_number=4,
            activity=Activity("reviewing", NOW - 10),
        ),
    ]


class Summarise(unittest.TestCase):
    def test_the_rows_line_up_with_the_sessions_entries(self) -> None:
        stats = sidebar.summarise(board(), session())
        self.assertEqual(len(stats), len(session().entries))
        self.assertEqual([s.label for s in stats][1:], ["widgets", "gadgets"])

    def test_everything_comes_first_and_totals_the_rest(self) -> None:
        everything = sidebar.summarise(board(), session())[0]
        self.assertTrue(everything.is_everything)
        self.assertEqual(everything.subtitle, "2 repositories")
        self.assertEqual(everything.open_count, 4)
        self.assertEqual(everything.reviewed, 2)
        self.assertEqual(everything.waiting, 1)
        self.assertTrue(everything.busy)

    def test_each_repository_carries_its_owner_and_its_counts(self) -> None:
        widgets = sidebar.summarise(board(), session())[1]
        self.assertEqual(widgets.subtitle, "acme")
        self.assertEqual((widgets.open_count, widgets.reviewed, widgets.waiting), (3, 2, 1))
        self.assertFalse(widgets.busy)

    def test_work_held_for_later_is_not_counted_as_needing_you(self) -> None:
        # It touches paths held for manual approval, so it is marked — but its
        # author still has changes to make, so there is nothing here for you to
        # do. Counting it sends you to a repository to find no job waiting.
        held = [
            pull_request(
                repo="acme/widgets",
                pr_number=7,
                needs_human=1,
                review_decision="CHANGES_REQUESTED",
                reviewed=True,
            )
        ]
        widgets = sidebar.summarise(held, session())[1]
        self.assertEqual(widgets.open_count, 1)
        self.assertEqual(widgets.waiting, 0)

    def test_a_repository_with_nothing_open_still_gets_a_row(self) -> None:
        stats = sidebar.summarise([], session())
        self.assertEqual([s.open_count for s in stats], [0, 0, 0])

    def test_counting_ignores_the_scope_so_you_can_see_elsewhere(self) -> None:
        scoped = session().with_repo(2)
        stats = sidebar.summarise(board(), scoped)
        self.assertEqual(stats[1].open_count, 3)


class EntryText(unittest.TestCase):
    def _stats(self, index: int = 1) -> sidebar.RepoStats:
        return sidebar.summarise(board(), session())[index]

    def test_a_repository_shows_name_owner_and_counts_on_three_lines(self) -> None:
        lines = sidebar.entry_text(self._stats(), chosen=False, frame=0).plain.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertIn("widgets", lines[0])
        self.assertIn("acme", lines[1])
        self.assertEqual(lines[2].strip(), "3 open  2 reviewed  1 need you")

    def test_zero_counts_are_left_out_rather_than_shown_as_zero(self) -> None:
        quiet = sidebar.summarise([], session())[1]
        stats_line = sidebar.entry_text(quiet, chosen=False, frame=0).plain.split("\n")[2]
        self.assertEqual(stats_line.strip(), "0 open")

    def test_the_chosen_row_carries_the_cursor_and_everything_else_does_not(self) -> None:
        chosen = sidebar.entry_text(self._stats(), chosen=True, frame=0).plain
        other = sidebar.entry_text(self._stats(), chosen=False, frame=0).plain
        self.assertTrue(chosen.startswith(theme.CURSOR))
        self.assertFalse(other.startswith(theme.CURSOR))

    def test_all_repositories_is_marked_differently_from_a_repository(self) -> None:
        quiet = sidebar.summarise([], session())
        everything = sidebar.entry_text(quiet[0], chosen=False, frame=0).plain
        one = sidebar.entry_text(quiet[1], chosen=False, frame=0).plain
        self.assertIn(theme.ALL_REPOS, everything)
        self.assertIn(theme.ONE_REPO, one)

    def test_a_busy_repository_spins_where_its_bullet_would_be(self) -> None:
        gadgets = self._stats(2)
        self.assertTrue(gadgets.busy)
        text = sidebar.entry_text(gadgets, chosen=False, frame=3).plain
        self.assertIn(theme.spinner_frame(3), text)


class RailText(unittest.TestCase):
    def _stats(self, index: int) -> sidebar.RepoStats:
        return sidebar.summarise(board(), session())[index]

    def test_the_rail_is_one_line_and_one_glyph_per_row(self) -> None:
        rail = sidebar.rail_text(self._stats(1), chosen=False, frame=0).plain
        self.assertNotIn("\n", rail)
        self.assertEqual(len(rail), 2)

    def test_everything_is_pac_man_and_a_repository_is_a_ghost(self) -> None:
        quiet = sidebar.summarise([], session())
        self.assertIn(theme.PAC_OPEN, sidebar.rail_text(quiet[0], chosen=False, frame=0).plain)
        self.assertIn(theme.GHOST, sidebar.rail_text(quiet[1], chosen=False, frame=0).plain)

    def test_a_repository_wanting_you_stays_visible_when_folded(self) -> None:
        waiting = sidebar.rail_text(self._stats(1), chosen=False, frame=0)
        quiet = sidebar.rail_text(sidebar.summarise([], session())[1], chosen=False, frame=0)
        self.assertEqual(str(waiting.spans[-1].style), theme.NEEDS_YOU)
        self.assertNotEqual(str(quiet.spans[-1].style), theme.NEEDS_YOU)


if __name__ == "__main__":
    unittest.main()
