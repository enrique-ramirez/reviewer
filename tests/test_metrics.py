"""What a review cost: measured, stored, totalled, shown.

The rule running through all of it is that a provider saying nothing must not
come out as a zero. "This review cost $0.00" and "nobody counted" look identical
on screen and mean opposite things, so the absent case is carried as absent all
the way to the pane.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from reviewer import model, state
from reviewer.tui import formatting
from reviewer.tui.models import Merge, PullRequest, ReviewCost
from reviewer.tui.views import board

CLAUDE_USAGE = {
    "input_tokens": 120,
    "cache_creation_input_tokens": 21_015,
    "cache_read_input_tokens": 20_902,
    "output_tokens": 1_130,
    "total_cost_usd": 0.224,
}


def result(**overrides) -> model.ModelResult:
    fields = {
        "payload": {},
        "raw": "",
        "usage": dict(CLAUDE_USAGE),
        "duration_seconds": 78.4,
        "provider": "claude",
        "model": "claude-opus-5",
    }
    fields.update(overrides)
    return model.ModelResult(**fields)


class Accumulating(unittest.TestCase):
    def test_a_split_review_totals_both_of_its_calls(self):
        spend = model.Spend()
        spend.add(result())
        spend.add(result(duration_seconds=41.2))
        self.assertEqual(spend.calls, 2)
        self.assertAlmostEqual(spend.seconds, 119.6)
        self.assertEqual(spend.output_tokens, 2_260)

    def test_fresh_and_cached_input_are_counted_apart(self):
        # Adding them would read as one number you could act on, when only the
        # fresh half responds to trimming a prompt.
        spend = model.Spend()
        spend.add(result())
        self.assertEqual(spend.input_tokens, 120 + 21_015)
        self.assertEqual(spend.cached_tokens, 20_902)

    def test_a_provider_that_reports_nothing_still_records_the_clock(self):
        spend = model.Spend()
        spend.add(result(usage={}, provider="codex", model=""))
        self.assertTrue(spend.measured)
        self.assertEqual(spend.output_tokens, 0)
        self.assertEqual(spend.cost_usd, 0.0)

    def test_nothing_at_all_is_not_worth_showing(self):
        self.assertFalse(model.Spend().measured)
        self.assertFalse(model.Spend(calls=1).measured)

    def test_a_later_silent_call_does_not_erase_the_model_name(self):
        spend = model.Spend()
        spend.add(result())
        spend.add(result(provider="", model=""))
        self.assertEqual(spend.model, "claude-opus-5")


class Storing(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = state.Store(self.dir, "state.sqlite3")
        self.addCleanup(self.store.close)

    def _event(self, number: int, spend, event: str = "COMMENT") -> None:
        self.store.record_review_event(
            "acme/widgets", number, head_sha="abc", event=event,
            findings=1, blockers=0, inline=1, summary="s", spend=spend,
        )

    def test_it_survives_the_round_trip(self):
        spend = model.Spend()
        spend.add(result())
        self._event(7, spend)
        row = self.store.latest_review_events(["acme/widgets"])[("acme/widgets", 7)]
        self.assertAlmostEqual(row["duration_seconds"], 78.4)
        self.assertEqual(row["output_tokens"], 1_130)
        self.assertEqual(row["model"], "claude-opus-5")

    def test_only_the_most_recent_round_comes_back(self):
        first, second = model.Spend(), model.Spend()
        first.add(result(duration_seconds=10.0))
        second.add(result(duration_seconds=99.0))
        self._event(7, first)
        self._event(7, second, event="APPROVE")
        row = self.store.latest_review_events(["acme/widgets"])[("acme/widgets", 7)]
        self.assertEqual(row["event"], "APPROVE")
        self.assertAlmostEqual(row["duration_seconds"], 99.0)

    def test_a_round_with_no_figures_is_null_not_zero(self):
        # So "nobody counted" stays distinguishable from "it cost nothing".
        self._event(7, None)
        row = self.store.latest_review_events(["acme/widgets"])[("acme/widgets", 7)]
        self.assertIsNone(row["duration_seconds"])
        self.assertIsNone(row["cost_usd"])
        self.assertIsNone(ReviewCost.from_row(row))

    def test_the_tally_totals_every_round(self):
        spend = model.Spend()
        spend.add(result())
        self._event(7, spend)
        self._event(7, spend)
        tally = self.store.review_tally("acme/widgets", 7)
        self.assertEqual(tally["rounds"], 2)
        # Each event stored the same accumulated Spend, so the sum is double it.
        self.assertAlmostEqual(tally["duration_seconds"], 78.4 * 2)
        self.assertAlmostEqual(tally["cost_usd"], 0.224 * 2)
        self.assertEqual(tally["model"], "claude-opus-5")

    def test_a_pull_request_we_never_reviewed_totals_to_nothing(self):
        tally = self.store.review_tally("acme/widgets", 999)
        self.assertEqual(tally["duration_seconds"], 0.0)
        self.assertEqual(tally["output_tokens"], 0)

    def test_no_repositories_is_not_a_query(self):
        self.assertEqual(self.store.latest_review_events([]), {})


class Formatting(unittest.TestCase):
    def test_tokens_stay_short_enough_for_a_detail_pane(self):
        self.assertEqual(formatting.tokens(940), "940")
        self.assertEqual(formatting.tokens(1_130), "1.1k")
        self.assertEqual(formatting.tokens(42_000), "42k")
        self.assertEqual(formatting.tokens(1_575_476), "1.6M")

    def test_small_costs_do_not_round_away_to_nothing(self):
        self.assertEqual(formatting.money(0.224), "$0.224")
        self.assertEqual(formatting.money(0.05), "$0.05")
        self.assertEqual(formatting.money(3.5), "$3.50")


def pull_request(cost: ReviewCost | None) -> PullRequest:
    return PullRequest.from_row(
        {
            "repo": "acme/widgets", "pr_number": 7, "title": "Ship it",
            "author": "ada", "seen_at": time.time(), "head_sha": "abcdef01",
            "base_ref": "main", "last_action": "COMMENT",
        },
        reviewed=True,
        cost=cost,
    )


class Rendering(unittest.TestCase):
    def test_the_last_pass_says_what_it_cost(self):
        cost = ReviewCost(
            calls=2, seconds=119.6, input_tokens=42_270, output_tokens=1_130,
            cached_tokens=41_804, cost_usd=0.448,
            provider="claude", model="claude-opus-5",
        )
        text = board.detail_text(pull_request(cost), time.time(), 0).plain
        self.assertIn("1m 59s", text)
        self.assertIn("2 calls", text)
        self.assertIn("1.1k out", text)
        self.assertIn("41.8k cached", text)
        self.assertIn("$0.448", text)
        self.assertIn("claude · claude-opus-5", text)

    def test_a_review_from_before_any_of_this_shows_no_cost_line(self):
        text = board.detail_text(pull_request(None), time.time(), 0).plain
        self.assertIn("last pass", text)
        self.assertNotIn("$", text)
        self.assertNotIn("cached", text)

    def test_a_provider_that_gave_only_a_clock_shows_only_a_clock(self):
        cost = ReviewCost(calls=1, seconds=63.0, provider="codex")
        text = board.detail_text(pull_request(cost), time.time(), 0).plain
        self.assertIn("1m 03s", text)
        self.assertNotIn("out", text.split("last pass")[1])
        self.assertNotIn("$", text)

    def test_one_call_is_not_announced_as_a_count(self):
        # "1 calls" is noise; the interesting case is a split review.
        cost = ReviewCost(calls=1, seconds=63.0, output_tokens=500)
        text = board.detail_text(pull_request(cost), time.time(), 0).plain
        self.assertNotIn("1 calls", text)


class MergedRows(unittest.TestCase):
    def _merge(self, **overrides) -> Merge:
        row = {
            "repo": "acme/widgets", "pr_number": 7, "title": "Ship it",
            "author": "ada", "merged_at": time.time(), "recorded_at": time.time(),
            "our_reviews": 2, "our_comments": 4,
        }
        row.update(overrides)
        return Merge.from_row(row)

    def test_the_lifetime_total_reaches_the_history_pane(self):
        merge = self._merge(
            review_seconds=286.0, review_output_tokens=2_260,
            review_input_tokens=42_270, review_cached_tokens=41_804,
            review_cost_usd=0.448, review_model="claude · claude-opus-5",
        )
        self.assertIsNotNone(merge.cost)
        self.assertEqual(merge.cost.tokens, 2_260 + 42_270 + 41_804)

    def test_a_backfilled_row_never_claims_a_cost(self):
        # We were not there. Zero would be a lie about work we did not do.
        self.assertIsNone(self._merge(backfilled=1).cost)


if __name__ == "__main__":
    unittest.main()
