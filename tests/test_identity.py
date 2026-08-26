from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from reviewer import identity
from reviewer.config import ConfigError, RepoConfig

SAMPLE = Path("config/repos.sample/example-org__example-repo.json")


def repo_config(**overrides: object) -> RepoConfig:
    raw = {
        k: v
        for k, v in json.loads(SAMPLE.read_text(encoding="utf-8")).items()
        if not k.startswith("$")
    }
    raw["local_path"] = None
    raw.update(overrides)
    path = Path(tempfile.mkdtemp()) / "acme__widgets.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return RepoConfig.load(path, _global_config())


def _global_config() -> object:
    """Just enough GlobalConfig for RepoConfig.load, without a token."""

    class Stub:
        default_language = "en"
        providers = {"claude": {"type": "claude", "command": "claude"}}

        def resolve_provider(self, overrides: dict | None = None) -> dict:
            return dict(self.providers["claude"], name="claude")

    return Stub()


class Checked(unittest.TestCase):
    def test_an_unset_identity_is_filled_in_from_the_token(self) -> None:
        filled = identity._checked(repo_config(identity=None), "ada")
        self.assertEqual(filled.identity, "ada")

    def test_the_sample_ships_without_one(self) -> None:
        self.assertIsNone(repo_config().identity)

    def test_a_matching_identity_is_normalised_to_the_tokens_spelling(self) -> None:
        checked = identity._checked(repo_config(identity="AdA"), "ada")
        self.assertEqual(checked.identity, "ada")

    def test_a_mismatch_is_an_error_that_names_both_sides(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            identity._checked(repo_config(identity="someone-else"), "ada")
        message = str(caught.exception)
        self.assertIn("someone-else", message)
        self.assertIn("ada", message)
        self.assertIn("own pull requests", message)

    def test_nothing_else_about_the_config_moves(self) -> None:
        before = repo_config(identity=None)
        after = identity._checked(before, "ada")
        self.assertEqual(after.repo, before.repo)
        self.assertEqual(after.gates, before.gates)
        self.assertEqual(after.review, before.review)


class ResolveOffline(unittest.TestCase):
    def setUp(self) -> None:
        logging.getLogger("reviewer").setLevel(logging.CRITICAL)
        self._real = identity.fetch_login

        def unreachable(*_args: object, **_kwargs: object) -> str:
            raise identity.LookupFailed("no network")

        identity.fetch_login = unreachable  # type: ignore[assignment]

    def tearDown(self) -> None:
        identity.fetch_login = self._real  # type: ignore[assignment]
        logging.getLogger("reviewer").setLevel(logging.NOTSET)

    def test_an_unreachable_github_leaves_the_configs_as_they_were(self) -> None:
        class Stub:
            token = "x"
            api_url = "https://api.github.invalid"

        repos = [repo_config(identity="ada")]
        self.assertEqual(identity.resolve(repos, Stub()).pop().identity, "ada")


if __name__ == "__main__":
    unittest.main()
