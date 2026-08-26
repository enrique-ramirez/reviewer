"""Which GitHub account the token belongs to.

Almost every gate depends on this. ``skip_own_prs`` compares it against the pull
request's author, the thread walker uses it to tell our replies from everyone
else's, and the board's "yours" marker comes from it. Wrong, and all three fail
silently: the reviewer works through your own pull requests, cannot see which
threads are waiting on you, and nothing anywhere says why.

So it is not configuration by default. The token knows who it belongs to, and
this asks it. Setting ``identity`` in a repo config is still allowed, and is then
checked against the answer rather than trusted.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace

from . import log
from .config import ConfigError, GlobalConfig, RepoConfig
from .gh.rest import ACCEPT, API_VERSION, USER_AGENT


class LookupFailed(RuntimeError):
    """GitHub could not be reached, or would not say."""


def fetch_login(token: str, api_url: str, timeout: float = 15.0) -> str:
    """The login of the account the token authenticates as."""
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        raise LookupFailed(f"GET /user returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LookupFailed(str(exc)) from exc

    login = (body or {}).get("login") if isinstance(body, dict) else None
    if not login:
        raise LookupFailed("GET /user returned no login")
    return str(login)


def _checked(cfg: RepoConfig, login: str) -> RepoConfig:
    if not cfg.identity or cfg.identity.lower() == login.lower():
        # Always the token's own spelling, so the board shows it the way GitHub
        # does rather than however it was typed into a config file.
        return replace(cfg, identity=login)
    raise ConfigError(
        f'{cfg.source_file}: "identity" is {cfg.identity!r}, but the token '
        f"belongs to {login!r}.\n"
        "  Either correct it or remove the key — it is detected from the token "
        "when omitted.\n"
        "  Left as it is, the reviewer would work through your own pull "
        "requests and miss the threads waiting on you."
    )


def resolve(repos: list[RepoConfig], global_cfg: GlobalConfig) -> list[RepoConfig]:
    """Fill in every unset ``identity``, and verify the ones that are set.

    A GitHub that cannot be reached is not fatal: an explicitly configured
    identity still works, and one that was never set is warned about rather than
    fabricated.
    """
    try:
        login = fetch_login(global_cfg.token, global_cfg.api_url)
    except LookupFailed as exc:
        log.get().warning("could not confirm which account the token belongs to: %s", exc)
        for cfg in repos:
            if not cfg.identity:
                log.get().warning(
                    '%s has no "identity" and it could not be detected — the '
                    "reviewer cannot recognise your own pull requests this run",
                    cfg.repo,
                )
        return repos

    return [_checked(cfg, login) for cfg in repos]
