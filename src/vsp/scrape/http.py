"""Polite HTTP layer (SPEC §15.2.1): robots, UA, delay, timeout, retries.

Transport (httpx.Client, incl. MockTransport in tests) and the sleep function
(fake clock in tests) are injectable. Requests are sequential, never parallel.
"""
from __future__ import annotations
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

USER_AGENT = "viral-script-pipeline/0.1.0 (+owner-operated research scraper)"
TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3


class ScrapeError(RuntimeError):
    """Transport-level failure (exit 1 upstream)."""


class RobotsRefused(SystemExit):
    """robots.txt forbids the path, or permission is unverifiable (exit 2)."""


def build_client(transport=None) -> httpx.Client:
    return httpx.Client(transport=transport,
                        headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT_SECONDS,
                        follow_redirects=True)


def robots_url_for(page_url: str) -> str:
    parts = urlparse(page_url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def parse_robots_rules(text: str) -> list:
    """Return [(agent_pat, kind, prefix)]. `*` matches every agent."""
    rules = []
    agents: list = []
    in_group = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not in_group:
                agents = []
            agents.append(value.lower())
            in_group = True
        elif key in ("disallow", "allow") and in_group and agents:
            for a in agents:
                rules.append((a, key, value))
        else:
            in_group = False
            agents = []
    return rules


def robots_allows(robots_text: str, ua: str, path: str) -> bool:
    """Longest-match wins between Allow and Disallow for `*` and our UA."""
    best = None
    for agent, kind, prefix in parse_robots_rules(robots_text):
        if agent not in ("*", ua.lower()):
            continue
        if not prefix:
            cand = (0, kind == "allow")
        elif path.startswith(prefix):
            cand = (len(prefix), kind == "allow")
        else:
            continue
        if best is None or cand[0] >= best[0]:
            best = cand
    return True if best is None else best[1]


class PoliteSession:
    """Sequential polite fetcher with injectable transport, delay and clock."""

    def __init__(self, client: httpx.Client, delay: float = 1.5,
                 sleep_fn=time.sleep):
        self.client = client
        self.delay = float(delay)
        self.sleep_fn = sleep_fn
        self.requests_made = 0

    def get(self, url: str, purpose: str = "fetch") -> httpx.Response:
        if self.requests_made > 0:
            self.sleep_fn(self.delay)
        self.requests_made += 1
        last_err = "unknown"
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.client.get(url)
            except httpx.TooManyRedirects as e:
                # Deterministic redirect loop (e.g. a self-redirecting page):
                # retries cannot help, fail fast with a clear message (exit 1).
                raise ScrapeError(f"redirect loop detected ({purpose} {url}): {e}")
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_err = f"network error ({purpose} {url}): {e}"
            else:
                code = resp.status_code
                if code == 200:
                    return resp
                if code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {code} ({purpose} {url})"
                else:
                    raise ScrapeError(f"HTTP {code} ({purpose} {url})")
            if attempt < MAX_RETRIES:
                self.sleep_fn(self.delay * (2 ** attempt))
        raise ScrapeError(f"failed after {MAX_RETRIES + 1} attempts: {last_err}")


class RobotsFetchResult:
    """Outcome of a robots.txt fetch: body sha (None on 404 allow-all),
    how it was fetched ("direct" | "cache-busted" | "missing"), and the
    final URL actually fetched (cache-busted URL when retried)."""
    def __init__(self, sha256: str | None, fetch: str, url: str):
        self.sha256 = sha256
        self.fetch = fetch
        self.url = url


def _cache_busted_robots_url(url: str) -> str:
    import time as _time
    sep = "&" if urlparse(url).query else "?"
    return f"{url}{sep}_vsp={int(_time.time())}"


def fetch_robots(client: httpx.Client, page_url: str) -> RobotsFetchResult:
    """Fetch and obey robots.txt, tolerating one Cloudflare-style redirect
    loop via a single cache-busting retry (`?_vsp=<unix-ts>`).
    Returns sha256 (None on 404), fetch mode, and the final URL.
    Raises RobotsRefused when disallowed or unverifiable (exit 2) — never
    exit 1 and never assume allow-all on a loop."""
    from ..io import sha256_text
    url = robots_url_for(page_url)
    try:
        resp = client.get(url)
    except (httpx.TransportError, httpx.TimeoutException) as e:
        raise RobotsRefused(f"REFUSAL stage=scrape check=robots path={url} "
                            f"unreachable: {e}")
    except httpx.TooManyRedirects as e:
        resp = _retry_robots_cache_busted(client, url, e)
        return _finish_robots(resp, page_url, fetch="cache-busted")
    if resp.status_code == 404:
        return RobotsFetchResult(None, "missing", url)
    if resp.status_code != 200:
        raise RobotsRefused(f"REFUSAL stage=scrape check=robots path={url} "
                            f"HTTP {resp.status_code}")
    return _finish_robots(resp, page_url, fetch="direct", url=url)


def _retry_robots_cache_busted(client: httpx.Client, url: str,
                               first_err: Exception) -> httpx.Response:
    busted = _cache_busted_robots_url(url)
    try:
        return client.get(busted)
    except (httpx.TransportError, httpx.TimeoutException,
            httpx.TooManyRedirects) as e2:
        raise RobotsRefused(f"REFUSAL stage=scrape check=robots path={url} "
                            f"redirect loop, cache-bust retry failed: {e2}")


def _finish_robots(resp: httpx.Response, page_url: str, fetch: str,
                   url: str | None = None) -> RobotsFetchResult:
    from ..io import sha256_text
    final_url = url or str(resp.url)
    if resp.status_code == 404:
        return RobotsFetchResult(None, "missing", final_url)
    if resp.status_code != 200:
        raise RobotsRefused(f"REFUSAL stage=scrape check=robots path={final_url} "
                            f"HTTP {resp.status_code}")
    body = resp.text
    path = urlparse(page_url).path or "/"
    if not robots_allows(body, "viral-script-pipeline", path):
        raise RobotsRefused(f"REFUSAL stage=scrape check=robots path={path} "
                            f"disallowed by robots.txt")
    return RobotsFetchResult(sha256_text(body), fetch, final_url)


def check_robots(client: httpx.Client, page_url: str) -> str | None:
    """Fetch and obey robots.txt. Returns body sha256, or None on 404
    (allow-all). Raises RobotsRefused when disallowed or unverifiable.
    Kept for backward compatibility; new code should use fetch_robots."""
    return fetch_robots(client, page_url).sha256
