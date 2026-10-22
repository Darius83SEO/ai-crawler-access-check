"""Check 2 — AI search & user-fetch bot access.

ChatGPT, Gemini, Claude, Perplexity and Copilot do NOT answer from Common Crawl
when they search the web: they use their own search crawlers (or a search
engine's index) and, when a user asks about a page, a user-triggered fetcher.
Check 1 covers training; this one covers *live retrieval* — the layer that
decides whether a page can be read and used in today's answers.

Per bot, two signals:
  a) robots.txt — is the bot disallowed at '/'? (authoritative: well-behaved
     search crawlers obey it);
  b) the edge/WAF — does the homepage answer that user agent the way it
     answers a browser? This is a user-agent-only test from a non-official IP,
     so a refusal is a strong hint, not proof: real bots arrive from published
     IP ranges and some CDNs treat them differently. Confirm in server logs.

Googlebot and Bingbot get extra leniency on the edge test: most CDNs verify
those two by IP, so a spoofed UA being refused is expected and not a block.
"""

from __future__ import annotations

import concurrent.futures
from typing import Dict, List, Optional, Tuple

import requests

from ..http import BLOCK_CODES, BROWSER_UA
from ..models import CheckResult, FAIL, PASS, WARN, ERROR
from ..util import site_url
from .robots_waf import _blocked_at_root, _group_for, fetch_robots, parse_robots

WEIGHT = 25.0

# (robots token, user-agent string used for the edge test, platform, verified-by-IP)
SEARCH_BOTS: List[Tuple[str, str, str, bool]] = [
    ("Googlebot",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Googlebot/2.1; "
     "+http://www.google.com/bot.html",
     "Gemini · AI Overviews · AI Mode", True),
    ("Bingbot",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
     "+http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36",
     "Copilot · Bing index", True),
    ("OAI-SearchBot",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; "
     "+https://openai.com/searchbot",
     "ChatGPT search", False),
    ("Claude-SearchBot",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; "
     "+https://www.anthropic.com)",
     "Claude search", False),
    ("PerplexityBot",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
     "+https://perplexity.ai/perplexitybot)",
     "Perplexity", False),
]
USER_BOTS: List[Tuple[str, str, str, bool]] = [
    ("ChatGPT-User",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
     "+https://openai.com/bot",
     "ChatGPT (user asks for a page)", False),
    ("Claude-User",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; "
     "+https://www.anthropic.com)",
     "Claude (user asks for a page)", False),
    ("Perplexity-User",
     "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; "
     "+https://perplexity.ai/perplexity-user)",
     "Perplexity (user asks for a page)", False),
]
# Robots-only token: controls whether Gemini apps may ground answers on (and
# train with) your pages. It is not a crawler, so there's no edge test.
GOOGLE_EXTENDED = "Google-Extended"

# How the two groups split this check's fraction.
_SEARCH_SHARE = 0.75
_USER_SHARE = 0.25


def _edge_status(domain: str, ua: str) -> Optional[int]:
    """Homepage status for a UA; streamed and closed so no body is downloaded."""
    for scheme in ("https", "http"):
        try:
            r = requests.get(site_url(domain, scheme), headers={"User-Agent": ua},
                             timeout=15, allow_redirects=True, stream=True)
            code = r.status_code
            r.close()
            return code
        except Exception:  # noqa: BLE001
            continue
    return None


def _ok(code) -> bool:
    return isinstance(code, int) and 200 <= code < 400


def _bot_score(robots_blocked: bool, edge: Optional[int], browser: Optional[int],
               ip_verified: bool) -> Tuple[float, str]:
    """(0..1 score, short verdict) for one bot."""
    if robots_blocked:
        return 0.0, "blocked in robots.txt"
    if _ok(browser) and edge in BLOCK_CODES:
        if ip_verified:
            # Spoofed Googlebot/Bingbot refused = the CDN verifies by IP. Expected.
            return 0.9, f"edge {edge} to a spoofed UA (normal: CDN verifies this bot by IP)"
        return 0.5, f"edge returns {edge} to this UA while a browser gets {browser}"
    if edge in BLOCK_CODES:
        # The browser baseline is refused too: the site blocks all automated
        # requests from our IP, so this bot's treatment can't be told apart.
        return 1.0, f"edge not testable (HTTP {edge}, browser refused too) — robots.txt allows it"
    if edge is None:
        return 1.0, "edge not testable (network)"
    return 1.0, f"open (HTTP {edge})"


def _sentence(parts: List[str]) -> str:
    """Join fix fragments into one sentence, capitalising only the first letter."""
    if not parts:
        return ""
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:]


def run(domain: str) -> CheckResult:
    try:
        robots_status, robots_text = fetch_robots(domain)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            id="ai_bots", title="AI Search & User-Fetch Bots", status=ERROR, weight=WEIGHT,
            summary=f"Could not fetch robots.txt ({exc}).",
            remediation="Make sure the domain resolves and serves over HTTP(S).",
        )
    groups = parse_robots(robots_text) if robots_text else []

    bots = SEARCH_BOTS + USER_BOTS
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(bots) + 1) as ex:
        browser_fut = ex.submit(_edge_status, domain, BROWSER_UA)
        edge_futs = {name: ex.submit(_edge_status, domain, ua) for name, ua, _, _ in bots}
        browser = browser_fut.result()
        edges = {name: f.result() for name, f in edge_futs.items()}

    if browser is None and all(v is None for v in edges.values()):
        return CheckResult(
            id="ai_bots", title="AI Search & User-Fetch Bots", status=ERROR, weight=WEIGHT,
            summary="The site did not respond on HTTP or HTTPS.",
            remediation="Confirm the domain is live and reachable.",
        )

    rows: Dict[str, Dict] = {}
    for name, _, platform, ip_verified in bots:
        blocked = _blocked_at_root(_group_for(groups, name)) if groups else False
        score, verdict = _bot_score(blocked, edges[name], browser, ip_verified)
        rows[name] = {"platform": platform, "robots_blocked": blocked,
                      "edge": edges[name], "score": score, "verdict": verdict}

    gext_blocked = _blocked_at_root(_group_for(groups, GOOGLE_EXTENDED)) if groups else False

    search_frac = sum(rows[n]["score"] for n, *_ in SEARCH_BOTS) / len(SEARCH_BOTS)
    user_frac = sum(rows[n]["score"] for n, *_ in USER_BOTS) / len(USER_BOTS)
    fraction = round(_SEARCH_SHARE * search_frac + _USER_SHARE * user_frac, 3)

    search_robots_blocked = [n for n, *_ in SEARCH_BOTS if rows[n]["robots_blocked"]]
    search_edge_blocked = [n for n, *_ in SEARCH_BOTS
                           if not rows[n]["robots_blocked"] and rows[n]["score"] == 0.5]
    user_blocked = [n for n, *_ in USER_BOTS if rows[n]["score"] < 1.0]

    details = [f"• Browser baseline: HTTP {browser}",
               "• Search crawlers (they build the index AI answers draw from):"]
    details += [f"   – {n} ({rows[n]['platform']}): {rows[n]['verdict']}" for n, *_ in SEARCH_BOTS]
    details.append("• User-triggered fetchers (a user asks the assistant to read a page):")
    details += [f"   – {n} ({rows[n]['platform']}): {rows[n]['verdict']}" for n, *_ in USER_BOTS]
    details.append(f"• Google-Extended (Gemini apps grounding + training): "
                   + ("disallowed in robots.txt" if gext_blocked else "not disallowed"))
    details.append("• Edge test uses the bot's user agent from a non-official IP: a refusal is a "
                   "strong hint, not proof — confirm in server logs.")

    evidence = {"browser": browser, "bots": rows, "google_extended_blocked": gext_blocked,
                "search_blocked": search_robots_blocked + search_edge_blocked,
                "user_blocked": user_blocked, "robots_status": robots_status}

    if "Googlebot" in search_robots_blocked or "Bingbot" in search_robots_blocked:
        status = FAIL
    elif search_robots_blocked:
        status = FAIL
    elif search_edge_blocked or user_blocked or gext_blocked:
        status = WARN
    else:
        status = PASS

    parts = []
    if search_robots_blocked:
        parts.append(f"robots.txt blocks {', '.join(search_robots_blocked)}")
    if search_edge_blocked:
        parts.append(f"the edge refuses {', '.join(search_edge_blocked)}")
    if user_blocked:
        parts.append(f"user fetchers limited: {', '.join(user_blocked)}")
    if gext_blocked:
        parts.append("Google-Extended disallowed (Gemini apps can't ground on your pages)")
    if status == PASS:
        summary = "All AI search crawlers and user-triggered fetchers can reach the homepage."
    elif status == FAIL:
        summary = "Live AI answers can't read you on some engines — " + "; ".join(parts) + "."
    else:
        summary = "Mostly open to live retrieval, with gaps — " + "; ".join(parts) + "."

    fixes = []
    if search_robots_blocked:
        fixes.append(f"remove the robots.txt disallow for {', '.join(search_robots_blocked)} "
                     "(blocking a search crawler removes you from that engine's answers, not just its training)")
    if search_edge_blocked or user_blocked:
        fixes.append("check the CDN/WAF bot rules (e.g. Cloudflare 'Block AI bots' / AI Crawl Control): "
                     "allow search and user-fetch bots even if you keep training bots out")
    if gext_blocked:
        fixes.append("if you want to appear in Gemini app answers, drop the Google-Extended disallow — "
                     "it does not affect Google Search or AI Overviews")
    remediation = _sentence(fixes)

    return CheckResult(
        id="ai_bots", title="AI Search & User-Fetch Bots", status=status, weight=WEIGHT,
        fraction=fraction, summary=summary, details=details,
        evidence=evidence, remediation=remediation,
    )
