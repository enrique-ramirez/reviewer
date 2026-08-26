"""GitHub REST client, standard library only.

Two things worth knowing about it:

* Every GET is conditional. The ETag from the previous response goes out as
  ``If-None-Match``; a ``304`` costs no rate-limit quota and returns the cached
  body. On a quiet repo a tick is a handful of 304s and nothing else.
* Rate-limit and secondary-rate-limit responses are respected rather than
  retried blindly, because a script that hammers a 403 gets the token throttled
  harder.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from .. import log
from ..state import Store

USER_AGENT = "blinky/0.1 (+https://github.com)"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"


def _run_is_newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Whether ``candidate`` is a later attempt than ``current``.

    Prefers ``run_attempt``, then ``run_number``, then the creation timestamp —
    ISO 8601 in UTC, so a string comparison orders correctly.
    """
    for key in ("run_attempt", "run_number"):
        a, b = candidate.get(key), current.get(key)
        if isinstance(a, int) and isinstance(b, int) and a != b:
            return a > b
    return str(candidate.get("created_at") or "") > str(current.get("created_at") or "")


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {message}")
        self.status = status
        self.url = url


class RestClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        store: Store,
        *,
        dry_run: bool = False,
    ) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.store = store
        self.dry_run = dry_run
        self._remaining: int | None = None

    # ------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.api_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        use_cache: bool = False,
        accept: str | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        url = self._url(path)
        headers = self._headers()
        if accept:
            headers["Accept"] = accept

        cached = self.store.get_cached(url) if (use_cache and method == "GET") else None
        if cached:
            headers["If-None-Match"] = cached[0]

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                self._note_rate_limit(resp_headers)
                payload = self._parse(raw, resp_headers)
                etag = resp_headers.get("etag")
                if use_cache and method == "GET" and etag:
                    self.store.put_cached(url, etag, payload)
                return resp.status, payload, resp_headers
        except urllib.error.HTTPError as exc:
            resp_headers = {k.lower(): v for k, v in exc.headers.items()}
            self._note_rate_limit(resp_headers)

            if exc.code == 304 and cached:
                return 304, cached[1], resp_headers

            detail = exc.read().decode("utf-8", errors="replace")[:500]

            if exc.code in (403, 429) and self._is_rate_limited(resp_headers, detail):
                wait = self._retry_after(resp_headers)
                log.get().warning(
                    "GitHub rate limit hit; sleeping %.0fs before continuing", wait
                )
                time.sleep(wait)
                return self._request(
                    method, path, body=body, use_cache=use_cache, accept=accept
                )

            raise GitHubError(exc.code, detail, url) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(0, f"network error: {exc.reason}", url) from exc

    @staticmethod
    def _parse(raw: str, headers: dict[str, str]) -> Any:
        if not raw:
            return None
        if "json" in headers.get("content-type", ""):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    def _note_rate_limit(self, headers: dict[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is None:
            return
        try:
            self._remaining = int(remaining)
        except ValueError:
            return
        if self._remaining < 100:
            log.get().warning("GitHub rate limit low: %s remaining", self._remaining)

    @staticmethod
    def _is_rate_limited(headers: dict[str, str], detail: str) -> bool:
        if headers.get("x-ratelimit-remaining") == "0":
            return True
        if "retry-after" in headers:
            return True
        return "secondary rate limit" in detail.lower()

    @staticmethod
    def _retry_after(headers: dict[str, str], default: float = 60.0) -> float:
        if "retry-after" in headers:
            try:
                return max(1.0, float(headers["retry-after"]))
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(1.0, float(reset) - time.time() + 1)
            except ValueError:
                pass
        return default

    # ----------------------------------------------------------- verbs

    def get(self, path: str, *, use_cache: bool = True, accept: str | None = None) -> Any:
        _, payload, _ = self._request("GET", path, use_cache=use_cache, accept=accept)
        return payload

    def get_with_status(self, path: str, *, use_cache: bool = True) -> tuple[int, Any]:
        status, payload, _ = self._request("GET", path, use_cache=use_cache)
        return status, payload

    def post(self, path: str, body: Any) -> Any:
        if self.dry_run:
            log.get().info("[dry-run] POST %s", path)
            return {"dry_run": True}
        _, payload, _ = self._request("POST", path, body=body)
        return payload

    def patch(self, path: str, body: Any) -> Any:
        if self.dry_run:
            log.get().info("[dry-run] PATCH %s", path)
            return {"dry_run": True}
        _, payload, _ = self._request("PATCH", path, body=body)
        return payload

    def paginate(self, path: str, *, per_page: int = 100, cap: int = 10) -> Iterator[Any]:
        """Walk a paginated collection.

        ``cap`` bounds the number of pages so a pathological PR cannot turn one
        tick into thousands of requests.
        """
        sep = "&" if "?" in path else "?"
        url = f"{path}{sep}per_page={per_page}"
        pages = 0
        while url and pages < cap:
            _, payload, headers = self._request("GET", url, use_cache=(pages == 0))
            if isinstance(payload, list):
                yield from payload
            elif payload:
                yield payload
            pages += 1
            url = self._next_link(headers.get("link", ""))

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        for part in link_header.split(","):
            section = part.split(";")
            if len(section) < 2:
                continue
            if 'rel="next"' in section[1].strip():
                return section[0].strip().strip("<>")
        return None

    # ------------------------------------------------------ domain calls

    def list_open_pulls(self, owner: str, repo: str) -> list[dict[str, Any]]:
        return list(
            self.paginate(
                f"/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc",
                cap=5,
            )
        )

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self.get(f"/repos/{owner}/{repo}/pulls/{number}", use_cache=False)

    def list_pull_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return list(
            self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/files", cap=30)
        )

    def list_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        return list(
            self.paginate(f"/repos/{owner}/{repo}/issues/{number}/comments", cap=5)
        )

    def list_review_comments(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        return list(
            self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/comments", cap=10)
        )

    def list_reviews(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self.paginate(f"/repos/{owner}/{repo}/pulls/{number}/reviews", cap=5))

    def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any] | None:
        try:
            return self.get(f"/repos/{owner}/{repo}/issues/{number}")
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return self.get(f"/repos/{owner}/{repo}")

    def combined_status(
        self, owner: str, repo: str, ref: str
    ) -> dict[str, Any] | None:
        """Legacy combined commit status for a ref.

        Fallback for tokens that cannot read the GraphQL check rollup. It covers
        only *commit statuses* — the older mechanism used by services like
        Vercel, CircleCI, and Codecov — and not GitHub Actions check runs, so an
        empty result means "nothing here to see", never "everything passed".
        """
        try:
            return self.get(f"/repos/{owner}/{repo}/commits/{ref}/status")
        except GitHubError as exc:
            log.get().debug("combined status unavailable for %s: %s", ref[:8], exc)
            return None

    def check_runs(self, owner: str, repo: str, ref: str) -> dict[str, Any] | None:
        try:
            return self.get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
        except GitHubError as exc:
            log.get().debug("check-runs unavailable for %s: %s", ref[:8], exc)
            return None

    def actions_runs(self, owner: str, repo: str, sha: str) -> list[dict[str, Any]]:
        """Latest GitHub Actions workflow run per workflow, for one commit.

        The way to read CI with ``Actions: Read`` when ``Checks: Read`` is not
        available on the account. Covers anything running in Actions, which for
        most repositories is all of CI; a check run posted by a third-party App
        would not appear here.

        Re-runs matter: Actions keeps every attempt for a SHA, so a failure
        followed by a green re-run would otherwise read as failing. Only the most
        recent run per workflow counts.
        """
        try:
            payload = self.get(
                f"/repos/{owner}/{repo}/actions/runs?head_sha={sha}&per_page=100"
            )
        except GitHubError as exc:
            log.get().debug("actions runs unavailable for %s: %s", sha[:8], exc)
            return []

        runs = (payload or {}).get("workflow_runs") or []
        latest: dict[Any, dict[str, Any]] = {}
        for run in runs:
            key = run.get("workflow_id") or run.get("name")
            current = latest.get(key)
            if current is None or _run_is_newer(run, current):
                latest[key] = run
        return list(latest.values())

    def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        commit_id: str,
        event: str,
        body: str,
        comments: list[dict[str, Any]] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "commit_id": commit_id,
            "event": event,
            "body": body,
        }
        if comments:
            payload["comments"] = comments
        return self.post(f"/repos/{owner}/{repo}/pulls/{number}/reviews", payload)

    def reply_to_review_comment(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> Any:
        return self.post(
            f"/repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies",
            {"body": body},
        )

    def create_issue_comment(
        self, owner: str, repo: str, number: int, body: str
    ) -> Any:
        return self.post(f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body})
