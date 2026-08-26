"""Configuration loading.

Layout:

    config/.env                  token (gitignored)
    config/global.json           tick interval, claude flags (gitignored)
    config/repos/*.json          one file per repository (gitignored)
    config/repos.sample/*.json   tracked examples

Keys beginning with ``$`` are documentation and are stripped on load, so the
sample files can explain themselves without a separate reference doc drifting
out of date.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import log

DEFAULT_API_URL = "https://api.github.com"
DEFAULT_GRAPHQL_URL = "https://api.github.com/graphql"

GLOBAL_DEFAULTS: dict[str, Any] = {
    "tick_seconds": 900,
    "default_language": "en",
    "max_reviews_per_tick": 6,
    "claude": {
        "command": "claude",
        "model": None,
        "extra_args": [],
        "allowed_tools": ["Read", "Glob", "Grep"],
        "timeout_seconds": 900,
    },
    "merge_summary": {
        "enabled": True,
        "model": None,
        "timeout_seconds": 180,
        "max_per_tick": 5,
        "max_tries": 3,
    },
    "notifications": {"enabled": True, "command": "osascript"},
    "logging": {"level": "INFO"},
}

REPO_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "local_path": None,
    "identity": None,
    "language": None,
    "agent_language": None,
    "gates": {
        "skip_drafts": True,
        "skip_own_prs": True,
        "skip_if_approved_by_others": True,
        "require_ci_green": True,
        "ci_source": "auto",
        "ignore_checks": [],
        "treat_neutral_as_pass": True,
        "treat_skipped_as_pass": True,
        "blocking_labels": [],
        "required_labels": [],
        "only_if_review_requested": False,
        "base_branches": [],
    },
    "diff": {
        "exclude": [],
        "summarize_only": [],
        "max_file_lines": 400,
        "max_total_lines": 3000,
        "context_lines": 3,
    },
    "review": {
        "axes": ["standards", "spec"],
        "split_axes_into_separate_calls": False,
        "personality": [
            "00-core",
            "05-severity",
            "06-voice-human",
            "07-voice-agent",
            "40-conventions",
        ],
        "repo_context": {
            "enabled": True,
            "from_ref": "default_branch",
            "paths": ["CLAUDE.md", ".claude/*.md"],
            "max_chars": 20000,
        },
        "max_disagreement_rounds_per_thread": 3,
    },
    "approval": {
        "mode": "manual",
        "no_blocker_action": "comment_and_invite",
        "manual_only_when": {
            "changed_lines_over": None,
            "touches_paths": [],
            "pr_has_labels": [],
        },
    },
    "notify_on": ["manual_approval_needed", "disagreement_cap_reached", "error"],
}


class ConfigError(RuntimeError):
    pass


def _strip_comments(value: Any) -> Any:
    """Drop ``$``-prefixed documentation keys, recursively."""
    if isinstance(value, dict):
        return {
            k: _strip_comments(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.startswith("$"))
        }
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` onto a copy of ``base``.

    Dicts merge key by key. Lists replace wholesale — a repo that sets
    ``exclude`` means *that* list, not the defaults plus that list.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return _strip_comments(raw)


ENV_SOURCES: dict[str, str] = {}
"""Where each loaded variable came from — ``.env`` or ``environment``."""


def load_env(config_dir: Path, repo_root: Path) -> dict[str, str]:
    """Load ``.env`` into the process environment.

    Checked in both the repo root and ``config/``, because both are plausible
    places to have put it. An already-exported variable wins, so
    ``GITHUB_TOKEN=... ./run.sh`` still works as a one-off override.

    That precedence has a nasty failure mode, though: export a token in a shell,
    rotate it in ``.env``, and every later run in that shell silently keeps using
    the dead one and reports an authentication error that looks like a GitHub
    problem. So a shadowed variable is announced rather than applied quietly, and
    ``--check`` prints which source won.

    Returns a map of variable name to the source that supplied it.
    """
    ENV_SOURCES.clear()

    for candidate in (repo_root / ".env", config_dir / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue

            existing = os.environ.get(key)
            if existing is None:
                os.environ[key] = value
                ENV_SOURCES.setdefault(key, f"{candidate.name} ({candidate.parent.name}/)")
            elif existing != value:
                ENV_SOURCES.setdefault(key, "environment (shadowing .env)")
                log.get().warning(
                    "%s is exported in your shell and differs from the value in "
                    "%s — the exported one is being used. If you rotated the "
                    "token, run `unset %s` or open a new terminal.",
                    key,
                    candidate,
                    key,
                )
            else:
                ENV_SOURCES.setdefault(key, "environment (same as .env)")

    return dict(ENV_SOURCES)


@dataclass
class GlobalConfig:
    tick_seconds: int
    default_language: str
    max_reviews_per_tick: int
    claude: dict[str, Any]
    merge_summary: dict[str, Any]
    notifications: dict[str, Any]
    logging: dict[str, Any]
    api_url: str
    graphql_url: str
    token: str

    @classmethod
    def load(cls, config_dir: Path) -> "GlobalConfig":
        path = config_dir / "global.json"
        data = _deep_merge(GLOBAL_DEFAULTS, _read_json(path) if path.exists() else {})

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "GITHUB_TOKEN is not set. Copy config/.env.sample to .env and "
                "fill it in, or export GITHUB_TOKEN in your shell."
            )

        return cls(
            tick_seconds=int(data["tick_seconds"]),
            default_language=str(data["default_language"]),
            max_reviews_per_tick=int(data["max_reviews_per_tick"]),
            claude=data["claude"],
            merge_summary=data["merge_summary"],
            notifications=data["notifications"],
            logging=data["logging"],
            api_url=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL).rstrip("/"),
            graphql_url=os.environ.get("GITHUB_GRAPHQL_URL", DEFAULT_GRAPHQL_URL),
            token=token,
        )


@dataclass
class RepoConfig:
    repo: str
    owner: str
    name: str
    enabled: bool
    local_path: Path | None
    identity: str | None
    language: str
    agent_language: str
    gates: dict[str, Any]
    diff: dict[str, Any]
    review: dict[str, Any]
    approval: dict[str, Any]
    notify_on: list[str]
    source_file: Path
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def slug(self) -> str:
        """Filesystem-safe identifier, e.g. ``owner__name``."""
        return self.repo.replace("/", "__")

    @classmethod
    def load(cls, path: Path, global_config: GlobalConfig) -> "RepoConfig":
        data = _deep_merge(REPO_DEFAULTS, _read_json(path))

        repo = str(data.get("repo") or "").strip()
        if "/" not in repo:
            raise ConfigError(f'{path}: "repo" must look like "owner/name", got {repo!r}')
        owner, _, name = repo.partition("/")

        local_path: Path | None = None
        if data.get("local_path"):
            local_path = Path(str(data["local_path"])).expanduser().resolve()
            if not (local_path / ".git").exists():
                raise ConfigError(
                    f"{path}: local_path {local_path} does not look like a git "
                    "checkout (no .git). Fix the path, or set it to null to "
                    "review from the diff alone."
                )

        language = data.get("language") or global_config.default_language
        agent_language = data.get("agent_language") or language

        return cls(
            repo=repo,
            owner=owner,
            name=name,
            enabled=bool(data.get("enabled", True)),
            local_path=local_path,
            identity=(str(data["identity"]).strip() if data.get("identity") else None),
            language=str(language),
            agent_language=str(agent_language),
            gates=data["gates"],
            diff=data["diff"],
            review=data["review"],
            approval=data["approval"],
            notify_on=list(data.get("notify_on") or []),
            source_file=path,
            raw=data,
        )


def load_repos(config_dir: Path, global_config: GlobalConfig) -> list[RepoConfig]:
    """Load every repo config, in filename order.

    Filename order is priority order: quota is shared, and repos are walked in
    the order returned here.
    """
    repos_dir = config_dir / "repos"
    if not repos_dir.is_dir():
        raise ConfigError(
            f"{repos_dir} does not exist. Create it and copy a file from "
            f"{config_dir / 'repos.sample'} into it."
        )

    configs: list[RepoConfig] = []
    for path in sorted(repos_dir.glob("*.json")):
        try:
            cfg = RepoConfig.load(path, global_config)
        except ConfigError as exc:
            log.get().error("skipping %s: %s", path.name, exc)
            continue
        if not cfg.enabled:
            log.get().info("%s is disabled in config, skipping", cfg.repo)
            continue
        configs.append(cfg)

    if not configs:
        raise ConfigError(f"no enabled repo configs found in {repos_dir}")
    return configs
