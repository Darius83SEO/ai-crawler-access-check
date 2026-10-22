"""The two layers of AI visibility (from the guide).

A site can sit in a model's *parametric memory* (it was crawled and trained on
before the cutoff) yet be blocked from *live retrieval* today — or the reverse.
A complete audit reads both:

  • Parametric memory — "were you reachable when the model trained?"
        fed by: CCBot access + Common Crawl Index coverage.
  • Live retrieval (RAG) — "are you reachable for live fetch today?"
        fed by: search & user-fetch bot access + index/snippet eligibility
        + server-side rendering.

This module derives a per-layer status from the already-computed checks and
exposes the model-family reference table.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import AuditReport, CheckResult, PASS, WARN, FAIL, ERROR, INFO

# Model-family reference table (verbatim framing from the guide).
MODEL_TABLE: List[Dict[str, str]] = [
    {"family": "GPT family", "emoji": "🟢",
     "pattern": "Trained knowledge (GPTBot) plus live search: OAI-SearchBot index, ChatGPT-User fetches",
     "layer": "Parametric + RAG"},
    {"family": "Gemini", "emoji": "🔵",
     "pattern": "Trained knowledge plus grounding on the Google Search index (Googlebot); "
                "Google-Extended governs Gemini training and Gemini-app grounding",
     "layer": "Parametric + RAG"},
    {"family": "Claude", "emoji": "🟣",
     "pattern": "Trained knowledge (ClaudeBot) plus web search: Claude-SearchBot, Claude-User fetches",
     "layer": "Parametric + RAG"},
    {"family": "Perplexity", "emoji": "🟠",
     "pattern": "Retrieval-native: PerplexityBot index plus Perplexity-User live fetches",
     "layer": "RAG-first"},
    {"family": "Copilot", "emoji": "⚪",
     "pattern": "Grounded on the Bing index (Bingbot); noarchive/nocache limit use in answers",
     "layer": "RAG-first"},
]

# Which layer(s) each check informs — shown as a chip on the check card.
# "Agentic" is the emerging third layer: not whether AI can find/read you
# (parametric + retrieval), but whether an autonomous agent can act on you.
CHECK_LAYER = {
    "access": "Parametric",
    "ai_bots": "Retrieval",
    "eligibility": "Retrieval",
    "index": "Parametric",
    "centrality": "Parametric",
    "structured": "Both",
    "rendering": "Both",
    "agentic": "Agentic",
}

# One-line "what it measures" per check, rendered in check order on the scorecard.
GLOSSARY = {
    "access": ("Training-Crawler Access", "Can the crawlers that build training data (CCBot above all, "
               "plus GPTBot, ClaudeBot, Google-Extended…) reach the site? Reads robots.txt and tests the "
               "edge/WAF with the CCBot user agent vs a browser."),
    "ai_bots": ("AI Search & User-Fetch Bots", "Can the bots behind live answers read you today? Search "
                "crawlers (Googlebot, Bingbot, OAI-SearchBot, Claude-SearchBot, PerplexityBot) and "
                "user-triggered fetchers (ChatGPT-User, Claude-User, Perplexity-User), via robots.txt and a "
                "user-agent edge test. A user-agent test is a hint, not proof — confirm in logs."),
    "eligibility": ("Index & Snippet Eligibility", "May index-based answers keep and quote the page? "
                    "noindex, nosnippet/max-snippet (Google AI Overviews & AI Mode), noarchive/nocache "
                    "(Copilot), canonical, and a sitemap with fresh lastmod dates."),
    "index": ("CC Index Coverage", "Is the domain actually in the Common Crawl archive — how many pages, "
              "and how recently captured?"),
    "centrality": ("Harmonic Centrality", "How close the domain sits to the web's core in the CC Web Graph. "
                   "Central domains get crawled deeper and more often."),
    "structured": ("Structured Data", "Is key Schema.org markup (Organization, page, author, breadcrumb) "
                   "in the server HTML, so models can represent and attribute the entity?"),
    "rendering": ("Server-Side Rendering", "Does the content exist in the raw HTML without JavaScript? "
                  "Many AI crawlers don't run JS, so JS-only content is invisible to them."),
    "agentic": ("Agentic Readiness", "Can an autonomous AI agent navigate and act on the page? Accessibility "
                "tree (landmarks, real controls, accessible names, headings), usable forms (labels, "
                "autocomplete), CAPTCHA and consent-banner blockers, plus a light llms.txt signal."),
}

_BLOCK_CODES = {401, 403, 429, 503}


def _by_id(report: AuditReport, cid: str) -> Optional[CheckResult]:
    return next((c for c in report.checks if c.id == cid), None)


def _ok(code) -> bool:
    return isinstance(code, int) and 200 <= code < 400


def _waf_blocked(browser_code, bot_code) -> bool:
    return _ok(browser_code) and bot_code in _BLOCK_CODES


def _parametric(access: Optional[CheckResult], index: Optional[CheckResult]):
    notes: List[str] = []
    ccbot_blocked = False
    if access and access.status != ERROR:
        ev = access.evidence or {}
        robots_blocked = {b.lower() for b in ev.get("robots_blocked", [])}
        ccbot_blocked = ("ccbot" in robots_blocked) or _waf_blocked(
            ev.get("status_browser"), ev.get("status_ccbot"))
    if ccbot_blocked:
        notes.append("CCBot (the training crawler) is blocked — content can't enter the training archive.")
        return FAIL, notes
    if access and access.status == ERROR:
        notes.append("Could not confirm CCBot access.")
    # presence in the archive
    if index is None or index.status == ERROR:
        notes.append("Index coverage unknown.")
        return WARN, notes
    if index.status == FAIL:
        notes.append("Open to CCBot, but not yet captured in the archive — parametric presence still building.")
        return WARN, notes
    if index.status == WARN:
        notes.append("Present in the archive but shallow.")
        return WARN, notes
    notes.append("CCBot can reach you and you're in the Common Crawl archive.")
    return PASS, notes


def _retrieval(bots: Optional[CheckResult], elig: Optional[CheckResult],
               rendering: Optional[CheckResult]):
    notes: List[str] = []
    if bots is None or bots.status == ERROR:
        notes.append("Could not confirm search/user-fetch bot access.")
        status = WARN
    else:
        ev = bots.evidence or {}
        search_blocked = ev.get("search_blocked", [])
        if search_blocked:
            notes.append("Search crawlers blocked: " + ", ".join(search_blocked) + ".")
        if ev.get("user_blocked"):
            notes.append("User-triggered fetchers limited: " + ", ".join(ev["user_blocked"]) + ".")
        status = bots.status
        if status == PASS:
            notes.append("Search crawlers and user-triggered fetchers can read you live.")
    # index eligibility decides whether index-based answers may use what they fetched
    if elig is not None and elig.status != ERROR:
        issues = (elig.evidence or {}).get("issues", {})
        if issues.get("noindex"):
            notes.append("But the homepage is noindex — index-based answers (Gemini, AI Overviews, Copilot) can't use it.")
            status = FAIL
        elif issues.get("nosnippet") or issues.get("noarchive"):
            notes.append("Snippet/usage directives limit how much AI answers can quote.")
            if status == PASS:
                status = WARN
    # server-side rendering decides whether a live fetch actually sees content
    if rendering is not None and rendering.status != ERROR:
        if rendering.status == FAIL:
            notes.append("But the page is a JS shell — a live fetch sees almost no content.")
            status = FAIL
        elif rendering.status == WARN and status == PASS:
            notes.append("Some content is JS-dependent, so a live fetch may miss part of it.")
            status = WARN
    return status, notes


def derive_layers(report: AuditReport) -> Dict[str, Dict]:
    access = _by_id(report, "access")
    index = _by_id(report, "index")
    rendering = _by_id(report, "rendering")

    p_status, p_notes = _parametric(access, index)
    r_status, r_notes = _retrieval(_by_id(report, "ai_bots"), _by_id(report, "eligibility"), rendering)

    return {
        "parametric": {
            "title": "Parametric memory",
            "question": "Were you reachable when the model trained?",
            "feeds": "CCBot access · Common Crawl Index",
            "status": p_status,
            "notes": p_notes,
        },
        "retrieval": {
            "title": "Live retrieval (RAG)",
            "question": "Are you reachable for live fetch today?",
            "feeds": "Search & user-fetch bots · Index eligibility · Server-side rendering",
            "status": r_status,
            "notes": r_notes,
        },
    }
