"""Shared HTTP helpers: a requests session with sane defaults and the two
user-agent strings the audit needs (a real browser, and the genuine CCBot).
"""

from __future__ import annotations

import requests

# The CCBot UA exactly as documented at commoncrawl.org/ccbot.
CCBOT_UA = "CCBot/2.0 (https://commoncrawl.org/faq/)"
GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20

# Statuses that mean "the edge actively refused the request" (WAF / bot block /
# rate limit / unavailable) — distinct from a usable response or a network error.
BLOCK_CODES = {401, 403, 429, 503}


def fetch_homepage(domain: str, user_agent: str = BROWSER_UA,
                   timeout: int = DEFAULT_TIMEOUT):
    """Fetch the homepage HTML, trying https then http.

    Returns (html_or_None, status_or_None, final_url_or_None, error_or_None).
    A non-None status with html=None means the server answered but refused/failed
    (e.g. 403); a None status means we could not connect at all (network/DNS).
    """
    last_status = None
    last_err = None
    final_url = None
    for scheme in ("https", "http"):
        try:
            r = get(f"{scheme}://{domain}/", user_agent=user_agent, timeout=timeout)
            last_status = r.status_code
            final_url = r.url
            if r.status_code < 400 and r.text:
                return r.text, r.status_code, r.url, None
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue
    return None, last_status, final_url, last_err


def make_session(user_agent: str = BROWSER_UA) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    })
    return s


def get(url: str, user_agent: str = BROWSER_UA, *, timeout: int = DEFAULT_TIMEOUT,
        allow_redirects: bool = True, method: str = "GET") -> requests.Response:
    """Single request with a chosen UA. Raises on connection errors."""
    headers = {"User-Agent": user_agent}
    return requests.request(
        method, url, headers=headers, timeout=timeout,
        allow_redirects=allow_redirects,
    )
