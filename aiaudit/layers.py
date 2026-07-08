"""The two layers of AI visibility (from the guide).

A site can sit in a model's *parametric memory* (it was crawled and trained on
before the cutoff) yet be blocked from *live retrieval* today — or the reverse.
A complete audit reads both:

  • Parametric memory — "were you reachable when the model trained?"
        fed by: CCBot access + Common Crawl Index coverage.
  • Live retrieval (RAG) — "are you reachable for live fetch today?"
        fed by: live retrieval-bot access + server-side rendering.

This module derives a per-layer status from the already-computed checks and
exposes the model-family reference table.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import AuditReport, CheckResult, PASS, WARN, FAIL, ERROR, INFO

# Model-family reference table (verbatim framing from the guide).
MODEL_TABLE: List[Dict[str, str]] = [
    {"family": "GPT family", "emoji": "🟢",
     "pattern": "Trained knowledge to a stated cutoff, plus live search for newer queries",
     "layer": "Parametric + RAG"},
    {"family": "Gemini", "emoji": "🔵",
     "pattern": "Trained knowledge plus Google retrieval integration",
     "layer": "Parametric + RAG"},
    {"family": "Claude", "emoji": "🟣",
     "pattern": "Trained knowledge to a stated cutoff, with retrieval where tools are available",
     "layer": "Parametric + RAG"},
    {"family": "Perplexity", "emoji": "🟠",
     "pattern": "Retrieval-native: answers are built primarily from live fetches",
     "layer": "RAG-first"},
]

# Which layer(s) each check informs — shown as a chip on the check card.
# "Agentic" is the emerging third layer: not whether AI can find/read you
# (parametric + retrieval), but whether an autonomous agent can act on you.
CHECK_LAYER = {
    "access": "Both",
    "index": "Parametric",
    "centrality": "Parametric",
    "structured": "Both",
    "rendering": "Both",
    "agentic": "Agentic",
}

# Bots that perform live retrieval / answer-time fetches (vs CCBot = training).
RETRIEVAL_BOTS = {
    "gptbot", "oai-searchbot", "claudebot", "anthropic-ai",
    "perplexitybot", "google-extended",
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


def _retrieval(access: Optional[CheckResult], rendering: Optional[CheckResult]):
    notes: List[str] = []
    blocked_retr: List[str] = []
    if access and access.status != ERROR:
        ev = access.evidence or {}
        robots_blocked = {b.lower() for b in ev.get("robots_blocked", [])}
        blocked_retr = sorted(robots_blocked & RETRIEVAL_BOTS)
        if _waf_blocked(ev.get("status_browser"), ev.get("status_gptbot")) and "gptbot" not in blocked_retr:
            blocked_retr.append("gptbot (edge/WAF)")
    if blocked_retr:
        notes.append("Live-retrieval bots blocked: " + ", ".join(blocked_retr) + ".")
        status = FAIL
    elif access and access.status == ERROR:
        notes.append("Could not confirm retrieval-bot access.")
        status = WARN
    else:
        notes.append("Retrieval bots are allowed to fetch live.")
        status = PASS
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
    r_status, r_notes = _retrieval(access, rendering)

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
            "feeds": "Retrieval-bot access · Server-side rendering",
            "status": r_status,
            "notes": r_notes,
        },
    }
