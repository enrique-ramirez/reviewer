"""The wiring between the command line and the dashboard.

This module exists because of a real crash: a name that was in scope in one
entry point and not in the other, in a function nothing exercised. Every test
below the first is incidental; the first one is the point. ``--tui`` is the
default path, so a mistake here is the mistake every user meets first, and
``compileall`` cannot see it because a ``NameError`` is a runtime event.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reviewer import __main__ as entry
from reviewer import state, tui
from reviewer.config import GlobalConfig, RepoConfig
from reviewer.log import DebugSink


class RunWithTui(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["GITHUB_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)

        self.dir = Path(tempfile.mkdtemp())
        config = self.dir / "config"
        config.mkdir()
        (config / "global.json").write_text(
            Path("config/global.sample.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.global_cfg = GlobalConfig.load(config)

        repo_file = config / "acme__widgets.json"
        repo_file.write_text(
            json.dumps({"repo": "acme/widgets", "local_path": None}), encoding="utf-8"
        )
        self.repos = [RepoConfig.load(repo_file, self.global_cfg)]
        self.store = state.Store(self.dir, "state.sqlite3")
        self.addCleanup(self.store.close)

    def _run(self, **arg_overrides) -> tuple[int, object]:
        """Start the dashboard, capture the Runtime, stop immediately.

        ``tui.run`` is where the interface would take over the main thread, so
        standing in for it is what makes this runnable without a terminal — and
        the Runtime it is handed is exactly what the rest of the tool sees.
        """
        args = argparse.Namespace(
            dry_run=False, force=False, repo=None, pr=None, debug=False
        )
        for key, value in arg_overrides.items():
            setattr(args, key, value)

        captured: dict[str, object] = {}

        def fake_run(runtime: object) -> None:
            captured["runtime"] = runtime
            # Ends the reviewer thread's loop on its next check, so the call
            # returns instead of watching for fifteen minutes.
            runtime.stop.set()

        with mock.patch.object(tui, "run", fake_run), mock.patch.object(
            entry, "run_tick", lambda *a, **k: entry.TickResult()
        ):
            code = entry._run_with_tui(
                repos=self.repos,
                global_cfg=self.global_cfg,
                store=self.store,
                state_dir=self.dir,
                personality_dir=Path("personality"),
                debug=DebugSink(None),
                args=args,
                lock_path=self.dir / "reviewer.lock",
            )
        return code, captured.get("runtime")

    def test_it_starts(self) -> None:
        code, runtime = self._run()
        self.assertEqual(code, 0)
        self.assertIsNotNone(runtime)

    def test_both_background_workers_are_wired_in(self) -> None:
        _code, runtime = self._run()
        self.assertIsNotNone(runtime.backfiller, "b would say it needs --backfill")
        self.assertIsNotNone(runtime.summariser, "g would say it needs the loop")

    def test_a_dry_run_reaches_the_workers_that_can_spend(self) -> None:
        # Getting this wrong once meant a rehearsal writing to the live cache.
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                _code, runtime = self._run(dry_run=dry_run)
                self.assertEqual(runtime.summariser.dry_run, dry_run)

    def test_the_repositories_reach_the_interface(self) -> None:
        _code, runtime = self._run()
        self.assertEqual(runtime.repos, ("acme/widgets",))


if __name__ == "__main__":
    unittest.main()
