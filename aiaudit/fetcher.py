"""Robust page fetcher shared by the structured-data and rendering checks.

Plain ``requests`` is often 403'd by bot-protection (datacenter IP + non-browser
fingerprint). A real headless browser usually passes, so we escalate:

  1. requests with full browser headers (browser UA, then Googlebot UA);
  2. a real headless Chromium (Playwright) that both bypasses most bot-protection
     and gives us, in one navigation, the raw server HTML (``response.text()``,
     pre-JS) *and* the JS-rendered DOM.

The result is cached per audit so the (slow) browser fetch runs once, not twice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import requests

from .http import BLOCK_CODES, BROWSER_UA

# "Lite" mode (default): no real browser, so the app stays light enough to run
# fast and free on shared hosting. Set AICRAWLER_BROWSER=1 to enable the
# headless-Chromium path (bot-protection bypass + exact SSR comparison) — that
# also needs `pip install playwright && playwright install chromium`.
_USE_BROWSER = os.environ.get("AICRAWLER_BROWSER", "0").lower() not in ("0", "false", "no", "")

GOOGLEBOT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                "compatible; Googlebot/2.1; +http://www.google.com/bot.html")

_FULL_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


@dataclass
class PageFetch:
    raw_html: Optional[str] = None       # server HTML, pre-JS (what a crawler receives)
    rendered_html: Optional[str] = None  # post-JS DOM (page.content())
    rendered_text: Optional[str] = None  # post-JS visible body text
    status: Optional[int] = None         # status of the source we trust
    method: str = ""                     # "requests:browser" | "requests:googlebot" | "browser"
    browser_status: Optional[int] = None
    blocked: bool = False                # nothing usable came back
    render_error: Optional[str] = None
    final_url: Optional[str] = None
    cc_fallback: bool = False            # raw_html came from the Common Crawl archive
    cc_info: Optional[Dict] = None
    shell: bool = False                  # anti-bot interstitial: a non-200 empty shell
    ax_tree: Optional[Dict] = None       # accessibility-tree snapshot (browser mode only)


import threading
import time

# domain -> (expiry_epoch, PageFetch). TTL'd so concurrent audits don't clobber
# each other and repeated audits of a popular domain are instant.
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 1800  # 30 minutes
_FETCH_LOCK = threading.Lock()


def clear_cache() -> None:
    _CACHE.clear()


def _visible_len(html: Optional[str]) -> int:
    """Length of the visible text (scripts/styles stripped). Cheap shell test."""
    if not html:
        return 0
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "noscript", "template"]):
            t.decompose()
        return len(" ".join(soup.get_text(" ", strip=True).split()))
    except Exception:
        return len(html)


# Vendor-specific anti-bot / challenge markers. Deliberately NOT generic
# "enable javascript" (legit SPAs put that in <noscript>), so a real JS app is
# not misread as an interstitial.
_ANTIBOT_MARKERS = (
    "awswaf", "gokuprops", "awswafcookiedomainlist",          # AWS WAF (Amazon)
    "cf-browser-verification", "challenge-platform", "__cf_chl",
    "cf_chl_opt", "turnstile", "just a moment",                # Cloudflare
    "datadome", "captcha-delivery",                            # DataDome
    "px-captcha", "perimeterx", "_pxhd",                       # PerimeterX / HUMAN
    "incapsula", "_incap_", "visid_incap",                     # Imperva
    "distil",                                                  # Distil
    "robot check", "validatecaptcha", "api-services-support@amazon",  # Amazon
    "captcha",
)


def _is_interstitial(html: Optional[str]) -> bool:
    low = (html or "").lower()
    return any(m in low for m in _ANTIBOT_MARKERS)


def _try_requests(domain: str):
    """Return (html, status, method, final_url) — html None if blocked/failed."""
    last_status = None
    for ua, label in ((BROWSER_UA, "requests:browser"), (GOOGLEBOT_UA, "requests:googlebot")):
        for scheme in ("https", "http"):
            try:
                r = requests.get(f"{scheme}://{domain}/",
                                 headers={**_FULL_HEADERS, "User-Agent": ua},
                                 timeout=20, allow_redirects=True)
                last_status = r.status_code
                if r.status_code < 400 and r.text:
                    return r.text, r.status_code, label, r.url
            except Exception:
                continue
    return None, last_status, "", None


def _render_with_browser(domain: str) -> Dict:
    out = {"nav_html": None, "nav_status": None, "rendered_text": None,
           "rendered_html": None, "ax_tree": None, "error": None}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Playwright not installed ({exc})"
        return out
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER_UA, locale="en-US",
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            resp = None
            for scheme in ("https", "http"):
                try:
                    resp = page.goto(f"{scheme}://{domain}/", wait_until="load", timeout=35000)
                    break
                except Exception as exc:  # noqa: BLE001
                    out["error"] = str(exc)
                    continue
            if resp is not None:
                out["nav_status"] = resp.status
                try:
                    out["nav_html"] = resp.text()  # server HTML, pre-JS
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                try:
                    out["rendered_text"] = " ".join((page.inner_text("body") or "").split())
                    out["rendered_html"] = page.content()
                except Exception as exc:  # noqa: BLE001
                    out["error"] = str(exc)
                # Accessibility tree — the semantic layer autonomous AI agents
                # traverse (roles/names/states), not the raw DOM. Best-effort.
                try:
                    out["ax_tree"] = page.accessibility.snapshot(interesting_only=False)
                except Exception:
                    pass
            browser.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def fetch_page(domain: str) -> PageFetch:
    now = time.time()
    hit = _CACHE.get(domain)
    if hit and hit[0] > now:
        return hit[1]
    # Serialize so two checks (or two audits) hitting the same domain fetch once.
    with _FETCH_LOCK:
        hit = _CACHE.get(domain)
        if hit and hit[0] > time.time():
            return hit[1]
        pf = _compute_page(domain)
        _CACHE[domain] = (time.time() + _CACHE_TTL, pf)
        return pf


def _compute_page(domain: str) -> PageFetch:
    pf = PageFetch()

    # 1) fast path — plain requests with browser then Googlebot UA
    raw, status, method, final = _try_requests(domain)
    pf.status = status
    if raw:
        pf.raw_html, pf.method, pf.final_url = raw, method, final

    # 2) real browser (opt-in) — gives the rendered DOM and recovers content
    #    when requests was bot-blocked. Skipped in Lite mode.
    if _USE_BROWSER:
        rr = _render_with_browser(domain)
        pf.render_error = rr.get("error")
        pf.browser_status = rr.get("nav_status")
        if rr.get("rendered_text") is not None:
            pf.rendered_text = rr["rendered_text"]
            pf.rendered_html = rr["rendered_html"]
            pf.ax_tree = rr.get("ax_tree")
            nav_status = rr.get("nav_status")
            nav_ok = nav_status is None or nav_status < 400
            if not pf.raw_html and rr.get("nav_html") and nav_ok:
                pf.raw_html = rr["nav_html"]
                pf.method = "browser"
                pf.status = nav_status or pf.status
                pf.final_url = f"https://{domain}/"

    # "Blocked" means we never got usable content. A 401/403 error page can
    # still come back with a non-empty rendered_html (a tiny error shell), so
    # don't be fooled by that: require real content, and treat a block status
    # with no server HTML as blocked.
    has_content = bool(pf.raw_html) or bool((pf.rendered_text or "").strip())
    status_blocked = (pf.browser_status in BLOCK_CODES or pf.status in BLOCK_CODES)
    pf.blocked = (not has_content) or (status_blocked and not pf.raw_html)

    # Anti-bot interstitial: a near-empty shell whose content only shows after JS,
    # served with a non-200 status (e.g. Amazon's 202). That's cloaking, not a
    # normal JS SPA (which answers 200) — and not assessable from outside.
    raw_vis = _visible_len(pf.raw_html)
    rendered_vis = len((pf.rendered_text or "").strip())
    # In Lite mode there's no rendered text to compare, so a near-empty raw page
    # is judged on its own; with a browser we also require JS to add real content.
    looks_shell = pf.raw_html is not None and raw_vis < 200 and (rendered_vis >= 500 or not _USE_BROWSER)
    # An interstitial = an empty shell that carries an anti-bot marker, or was
    # served with a non-200 status. A plain 200 shell with no markers is a real
    # JS SPA and should still fail the SSR check.
    interstitial = looks_shell and (_is_interstitial(pf.raw_html) or pf.status not in (None, 200))

    # 3) blocked (401/403) or anti-bot interstitial → read the page CCBot already
    #    archived, so we see the real crawler's view instead of giving up.
    if pf.blocked or interstitial:
        try:
            from .warc import fetch_homepage_from_cc
            res = fetch_homepage_from_cc(domain)
        except Exception:
            res = None
        if res and _visible_len(res[0]) > max(raw_vis, 200):
            html, info = res
            pf.raw_html = html
            pf.method = "common-crawl-archive"
            pf.cc_fallback = True
            pf.cc_info = info
            pf.status = 200
            pf.blocked = False
            interstitial = False
            # The earlier rendered_text/html came from the blocked/shell page,
            # not the real site — discard so the SSR check uses the CC branch.
            pf.rendered_text = None
            pf.rendered_html = None

    # A shell we couldn't see past (real crawlers from allowlisted IPs may differ).
    pf.shell = interstitial and not pf.cc_fallback

    return pf
