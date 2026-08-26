from __future__ import annotations

import unittest

from reviewer.tui.formatting import DAY
from reviewer.tui.session import ALL_REPOSITORIES, WINDOWS, Session

NOW = 1_700_000_000.0
REPOS = ("acme/widgets", "acme/gadgets")


def session(**kwargs: object) -> Session:
    return Session(repos=REPOS, started_at=NOW, **kwargs)  # type: ignore[arg-type]


class Scope(unittest.TestCase):
    def test_the_first_entry_is_everything(self) -> None:
        self.assertEqual(session().entries[0], (ALL_REPOSITORIES, None))
        self.assertEqual(session().scope, REPOS)
        self.assertTrue(session().whole_estate)

    def test_picking_one_narrows_every_view_to_it(self) -> None:
        chosen = session().with_repo(2)
        self.assertEqual(chosen.scope, ("acme/gadgets",))
        self.assertFalse(chosen.whole_estate)

    def test_moving_past_the_end_wraps_round(self) -> None:
        self.assertEqual(session().moved_by(-1).scope, ("acme/gadgets",))
        self.assertEqual(session(repo_index=2).moved_by(1).repo_index, 0)

    def test_one_repository_needs_no_sidebar(self) -> None:
        self.assertFalse(Session(repos=("acme/widgets",), started_at=NOW).multi_repo)


class Paging(unittest.TestCase):
    def test_narrowing_returns_to_the_first_page(self) -> None:
        deep = session(page=4)
        self.assertEqual(deep.with_repo(1).page, 0)
        self.assertEqual(deep.with_author("ada").page, 0)
        self.assertEqual(deep.with_next_window().page, 0)

    def test_a_page_is_never_negative(self) -> None:
        self.assertEqual(session().with_page(-3).page, 0)


class Filters(unittest.TestCase):
    def test_an_author_is_stored_trimmed(self) -> None:
        self.assertEqual(session().with_author("  ada  ").author, "ada")

    def test_windows_cycle_back_to_all_time(self) -> None:
        rolled = session(window=len(WINDOWS) - 1).with_next_window()
        self.assertEqual(rolled.window_label, WINDOWS[0][0])

    def test_all_time_has_no_cutoff(self) -> None:
        self.assertIsNone(session().merged_after(NOW))
        self.assertAlmostEqual(
            session(window=1).merged_after(NOW), NOW - 7 * DAY
        )

    def test_only_the_clearable_filters_count_as_filtered(self) -> None:
        scoped = session().with_repo(1)
        self.assertFalse(scoped.filtered)
        self.assertTrue(scoped.narrowed)
        self.assertTrue(scoped.with_author("ada").filtered)

    def test_clearing_leaves_the_chosen_repository_alone(self) -> None:
        messy = session(repo_index=1).with_author("ada").with_next_window()
        cleared = messy.unfiltered()
        self.assertEqual(cleared.author, "")
        self.assertEqual(cleared.window, 0)
        self.assertEqual(cleared.repo_index, 1)


if __name__ == "__main__":
    unittest.main()
