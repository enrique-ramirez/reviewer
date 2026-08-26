"""The --dry-run prompt breakdown.

The report is a diagnostic, so the bar is that it never lies and never crashes:
a wrong number here sends someone tuning the wrong knob, and an exception here
would take down a rehearsal that was otherwise fine.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from reviewer import log, tokens

SYSTEM = "# Who you are\n\nbe terse\n\n---\n\n## Language\n\nEnglish"
USER = "## Pull request\n\n#42\n\n## Diff\n\n+ one\n+ two\n\n## Your task\n\ngo"


class Splitting(unittest.TestCase):
    def test_the_system_prompt_breaks_at_its_rules(self):
        labels = [s.label for s in tokens.split(SYSTEM, system=True)]
        self.assertEqual(sorted(labels), ["language", "who you are"])

    def test_the_user_prompt_breaks_at_its_headings(self):
        labels = [s.label for s in tokens.split(USER, system=False)]
        self.assertIn("diff", labels)
        self.assertIn("pull request", labels)

    def test_biggest_first_because_that_is_what_gets_tuned(self):
        sections = tokens.split(USER, system=False)
        self.assertEqual(sections, sorted(sections, key=lambda s: -s.chars))

    def test_an_empty_prompt_does_not_explode(self):
        self.assertEqual(tokens.split("", system=True), [])
        self.assertEqual(tokens.split("   \n\n  ", system=False), [])


class Report(unittest.TestCase):
    def test_every_row_lines_up(self):
        # Columns that drift are worse than no columns; the whole point is
        # comparing one section against another down the page.
        report = tokens.report(SYSTEM, USER, {"input_tokens": 900})
        widths = {
            len(line)
            for line in report.splitlines()
            if line.startswith(("    ", "  sent", "  count", "  out"))
        }
        self.assertEqual(widths, {tokens.TOTAL_WIDTH})

    def test_the_total_is_measured_not_summed(self):
        # So a mis-split section can never produce a wrong total.
        system, user = "a" * 370, "b" * 370
        report = tokens.report(system, user)
        self.assertIn("740", report)
        self.assertIn("200", report)  # 740 chars / 3.7

    def test_a_provider_that_reports_nothing_gets_a_sentence_not_zeros(self):
        for usage in (None, {}):
            with self.subTest(usage=usage):
                report = tokens.report(SYSTEM, USER, usage)
                self.assertIn("reported no usage", report)
                self.assertNotIn("counted in by the provider", report)

    def test_cache_reads_count_towards_what_went_in(self):
        # They are input the model saw; leaving them out would make the CLI
        # overhead line look impossibly small.
        report = tokens.report(
            "s", "u", {"input_tokens": 10, "cache_read_input_tokens": 20_000}
        )
        self.assertIn("20,010", report)
        self.assertIn("read from cache", report)

    def test_overhead_is_only_claimed_when_there_is_some(self):
        # An estimate above what the provider counted means the estimate was
        # high, not that overhead was negative.
        report = tokens.report("x" * 100_000, "", {"input_tokens": 5})
        self.assertNotIn("CLI overhead", report)

    def test_output_tokens_appear_when_reported(self):
        self.assertIn("1,130", tokens.report("s", "u", {"output_tokens": 1130}))


class Wiring(unittest.TestCase):
    """That the report reaches the log on a dry run, and only on a dry run."""

    def _reviewer(self, *, dry_run: bool):
        from reviewer import pipeline

        # Only the four attributes _call_model touches; building a real
        # Reviewer would need a token, a database and a GitHub client to test
        # one logging branch.
        reviewer = object.__new__(pipeline.Reviewer)
        reviewer.dry_run = dry_run
        reviewer.debug = SimpleNamespace(write=lambda *a, **k: None)
        reviewer.cfg = SimpleNamespace(repo="acme/widgets", model={})
        reviewer.global_cfg = SimpleNamespace(
            provider_for=lambda _repo: {"type": "claude"}
        )
        return reviewer

    def _run(self, *, dry_run: bool) -> str:
        from reviewer import pipeline

        result = pipeline.model.ModelResult(
            payload={"findings": []}, raw="{}", usage={"output_tokens": 10},
            duration_seconds=1.0, provider="claude",
        )
        with mock.patch.object(pipeline.model, "run", return_value=result):
            with self.assertLogs(log.LOGGER_NAME, level="INFO") as caught:
                payload = self._reviewer(dry_run=dry_run)._call_model(
                    SYSTEM, USER, None, 42, "standards"
                )
        self.assertEqual(payload, {"findings": []})
        return "\n".join(caught.output)

    def test_a_dry_run_logs_the_breakdown(self):
        logged = self._run(dry_run=True)
        self.assertIn("prompt composition", logged)
        self.assertIn("sent by this tool", logged)
        self.assertIn("diff", logged)

    def test_a_live_run_does_not(self):
        # A wall of numbers nobody reads when the thing is working.
        self.assertNotIn("prompt composition", self._run(dry_run=False))


if __name__ == "__main__":
    unittest.main()
