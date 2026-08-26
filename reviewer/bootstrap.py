"""``--init``: the copy-and-edit steps, asked rather than documented.

Everything here writes from the tracked samples rather than from strings in this
file, so the samples stay the single description of what a config looks like and
the comments in them come along. Nothing is ever overwritten: a second run fills
in whatever is still missing and leaves the rest alone, which makes it safe to
reach for when you are not sure what state a checkout is in.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import providers

TOKEN_URL = "https://github.com/settings/personal-access-tokens"
TOKEN_PLACEHOLDER = "GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxxxxxx"

REPO_PLACEHOLDER = '"repo": "example-org/example-repo"'
PATH_PLACEHOLDER = '"local_path": "~/Projects/example-org/example-repo"'

PROVIDER_PLACEHOLDER = '"provider": "claude",'

# Offered by --init, in the order asked. The generic "command" type is not here
# on purpose: it needs a command name to be worth anything, and someone who
# wants it is already reading the sample file.
OFFERED = ("claude", "codex", "gemini")


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    repo_root: Path

    @property
    def env(self) -> Path:
        return self.repo_root / ".env"

    @property
    def env_sample(self) -> Path:
        return self.config_dir / ".env.sample"

    @property
    def global_config(self) -> Path:
        return self.config_dir / "global.json"

    @property
    def global_sample(self) -> Path:
        return self.config_dir / "global.sample.json"

    @property
    def repos_dir(self) -> Path:
        return self.config_dir / "repos"

    @property
    def repo_sample(self) -> Path:
        return self.config_dir / "repos.sample" / "example-org__example-repo.json"


def _say(message: str = "") -> None:
    print(message)


def _ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {question}{suffix} ").strip()
    except (EOFError, KeyboardInterrupt):
        raise Abandoned() from None
    return answer or default


class Abandoned(RuntimeError):
    """The user backed out with Ctrl-C or Ctrl-D."""


def _ask_token(paths: Paths) -> bool:
    """Write ``.env``. Returns False when there was nothing to do."""
    if paths.env.exists():
        _say(f"  .env already exists — leaving it alone ({paths.env})")
        return False

    _say("  A fine-grained token, with these permissions on the repos you list:")
    _say("    Contents Read-only · Pull requests Read and write")
    _say("    Checks Read-only · Commit statuses Read-only")
    _say(f"    Create one at {TOKEN_URL}")
    _say()
    token = _ask("Paste the token (or leave blank to fill it in later):")

    sample = paths.env_sample.read_text(encoding="utf-8")
    line = f"GITHUB_TOKEN={token}" if token else TOKEN_PLACEHOLDER
    paths.env.write_text(sample.replace(TOKEN_PLACEHOLDER, line), encoding="utf-8")
    paths.env.chmod(0o600)

    _say(f"  wrote {paths.env}")
    if not token:
        _say("  ↳ fill in GITHUB_TOKEN before running it")
    return True


def _ask_provider() -> str:
    """Which coding-agent CLI does the reviewing.

    Whichever is already installed is offered as the default, so the common case
    is one Enter. Nothing is rejected for not being on PATH — installing it
    afterwards is normal, and ``--check`` is the command that has an opinion
    about that.
    """
    _say("  Reviews run through a coding-agent CLI you have already signed in,")
    _say("  so they cost that subscription's quota rather than an API key.")
    _say()

    default = ""
    for name in OFFERED:
        adapter = providers.get(name)
        where = shutil.which(adapter.default_command)
        _say(f"    {name:8} {'found at ' + where if where else 'not on PATH'}")
        if where and not default:
            default = name

    _say()
    while True:
        answer = _ask(f"Which one? ({'/'.join(OFFERED)})", default or OFFERED[0]).lower()
        if answer in OFFERED:
            return answer
        _say(f"  ↳ pick one of {', '.join(OFFERED)}")


def _write_global(paths: Paths, provider: str) -> bool:
    if paths.global_config.exists():
        _say(f"  {paths.global_config.name} already exists — leaving it alone")
        return False

    body = paths.global_sample.read_text(encoding="utf-8")
    body = body.replace(PROVIDER_PLACEHOLDER, f'"provider": {json.dumps(provider)},')
    paths.global_config.write_text(body, encoding="utf-8")

    _say(f"  wrote {paths.global_config}   (every key optional; edit at will)")
    adapter = providers.get(provider)
    if not shutil.which(adapter.default_command):
        _say(f"  ↳ {adapter.default_command!r} is not on PATH yet. {adapter.install_hint}")
    if adapter.caveat:
        _say(f"  ↳ note: {adapter.caveat}")
    return True


def _ask_repo(paths: Paths) -> bool:
    existing = sorted(paths.repos_dir.glob("*.json")) if paths.repos_dir.is_dir() else []
    if existing:
        _say(f"  {len(existing)} repo config(s) already in {paths.repos_dir}:")
        for path in existing:
            _say(f"    {path.name}")
        if _ask("Add another? (y/N)", "n").lower() not in ("y", "yes"):
            return False

    repo = ""
    while "/" not in repo:
        repo = _ask("Which repository? (owner/name)")
        if not repo:
            _say("  ↳ skipped; copy a file from config/repos.sample/ when ready")
            return False
        if "/" not in repo:
            _say('  ↳ that needs to look like "owner/name"')

    _say()
    _say("  Your everyday clone of it, if you have one. The reviewer reads")
    _say("  surrounding code from a detached worktree and never touches your")
    _say("  branches. Blank means review the diff alone.")
    local_path = _ask("Path to your clone (blank to skip):")
    if local_path and not (Path(local_path).expanduser() / ".git").exists():
        _say(f"  ↳ no .git under {local_path} — writing it anyway, fix it if wrong")

    paths.repos_dir.mkdir(parents=True, exist_ok=True)
    target = paths.repos_dir / f"{repo.replace('/', '__')}.json"
    if target.exists():
        _say(f"  {target.name} already exists — leaving it alone")
        return False

    body = paths.repo_sample.read_text(encoding="utf-8")
    body = body.replace(REPO_PLACEHOLDER, f'"repo": "{repo}"')
    body = body.replace(
        PATH_PLACEHOLDER,
        f'"local_path": "{local_path}"' if local_path else '"local_path": null',
    )
    target.write_text(body, encoding="utf-8")
    _say(f"  wrote {target}")
    return True


def run(config_dir: Path, repo_root: Path) -> int:
    """Ask what is needed, write what is missing, then say what to do next."""
    if not sys.stdin.isatty():
        print(
            "--init asks questions, so it needs a terminal. To set up by hand:\n"
            "  cp config/.env.sample .env\n"
            "  cp config/global.sample.json config/global.json\n"
            "  mkdir -p config/repos\n"
            "  cp config/repos.sample/*.json config/repos/myorg__myrepo.json",
            file=sys.stderr,
        )
        return 2

    paths = Paths(config_dir=config_dir, repo_root=repo_root)
    missing = [p for p in (paths.env_sample, paths.global_sample, paths.repo_sample)
               if not p.exists()]
    if missing:
        print(f"missing sample file(s): {', '.join(str(p) for p in missing)}",
              file=sys.stderr)
        return 1

    _say()
    _say("Setting up Blinky. Nothing here overwrites a file that exists.")
    _say()
    try:
        _say("1. Token")
        _ask_token(paths)
        _say()
        _say("2. Which model reviews")
        provider = _ask_provider()
        _say()
        _say("3. Global settings")
        _write_global(paths, provider)
        _say()
        _say("4. A repository to watch")
        _ask_repo(paths)
    except Abandoned:
        _say("\n  Left it there. Re-run --init any time.\n")
        return 130

    _say()
    _say("Next:")
    _say("  ./run.sh --check           does the token reach everything?")
    _say("  ./run.sh --once --dry-run  one pass, posting nothing")
    _say("  ./run.sh                   watch, with the dashboard")
    _say()
    _say("Then edit personality/ — that is your review voice, and the part")
    _say("worth spending time on.")
    _say()
    return 0
