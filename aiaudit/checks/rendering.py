"""Check 5 — Server-side rendering audit.

Many AI crawlers fetch HTML but do not execute JavaScript. If the content only
exists after JS runs, the crawler captures an empty shell. We compare the raw
server HTML (pre-JS, exactly what ``response.text()`` returns) against the
JS-rendered DOM. Both come from the shared fetcher, which uses a real headless
browser — so this works even on sites that 403 a plain ``requests`` call.
"""

from __future__ import annotations

from typing import Optional

from bs4 import BeautifulSoup

from ..fetcher import fetch_page
from ..http import BLOCK_CODES
from ..models import CheckResult, FAIL, PASS, WARN, ERROR

WEIGHT = 15.0


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def run(domain: str) -> CheckResult:
    pf = fetch_page(domain)

    # Nothing usable came back — distinguish an active block from unreachable.
    if pf.blocked:
        code = pf.browser_status or pf.status
        if code in BLOCK_CODES:
            gate = "a login/paywall" if code == 401 else "bot-protection"
            return CheckResult(
                id="rendering", title="Server-Side Rendering", status=FAIL, weight=WEIGHT,
                fraction=0.0,
                summary=f"The site refused our request (HTTP {code}) — the page is gated ({gate}), so its "
                        "content can't be assessed from outside.",
                evidence={"status": code},
                remediation=(
                    "The page is gated to non-allowlisted clients. Verify from server logs whether real AI "
                    "crawlers (their published IPs) are allowed through."
                ),
            )
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=ERROR, weight=WEIGHT,
            summary=f"Could not reach the homepage ({pf.render_error or 'no usable response'}).",
            remediation="Confirm the domain resolves and responds.",
        )

    if pf.shell:
        # Non-200 anti-bot shell: only a JS shell was served to us, so SSR can't
        # be judged from outside (not a genuine JS-only page). Excluded from score.
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=WARN, weight=WEIGHT,
            fraction=None,
            summary=f"Received an anti-bot interstitial (HTTP {pf.status}) — only a JS shell was served to "
                    "our fetch, so server-rendering can't be assessed from outside.",
            details=[f"• Live response: HTTP {pf.status}, near-empty shell; content appeared only after JS",
                     "• Real crawlers/browsers (allowlisted IPs) may receive the full server HTML"],
            evidence={"status": pf.status, "shell": True},
            remediation=("Verify from server logs whether real crawlers get full content; this is usually "
                         "IP/fingerprint cloaking, not a genuine JS-only page."),
        )

    raw_text = _visible_text(pf.raw_html) if pf.raw_html else None
    rendered_text = pf.rendered_text
    via = f" (fetched via {pf.method})" if pf.method else ""

    # Content came from the Common Crawl archive (live fetch was gated). That
    # capture IS the no-JS server HTML the real crawler received, so if it has
    # content the page is server-rendered and crawler-visible — by proof.
    if rendered_text is None and pf.cc_fallback:
        soup = BeautifulSoup(pf.raw_html, "html.parser")
        has_h1 = bool(soup.find(["h1", "h2"]))
        n = len(raw_text or "")
        ok = n >= 500 and has_h1
        info = pf.cc_info or {}
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=(PASS if ok else WARN),
            weight=WEIGHT, fraction=(1.0 if ok else 0.4),
            summary=(f"From CCBot's actual capture (Common Crawl {info.get('crawl')}): {n} chars of content"
                     + (" with headings" if has_h1 else "")
                     + " — server-rendered and crawler-visible." if ok else
                     f"CCBot's archived capture has little text ({n} chars) — content may be JS-dependent."),
            details=[f"• Live fetch gated → read CCBot's archived capture ({info.get('crawl')}, {info.get('timestamp')})",
                     f"• Captured server text: {n} chars",
                     f"• Headings present: {'yes' if has_h1 else 'no'}"],
            evidence={"raw_len": n, "rendered": False, "cc_fallback": True, "cc": info},
            remediation=("" if ok else "Ensure key content is server-rendered so crawlers capture it."),
        )

    # No rendered DOM (Lite mode) → judge the server HTML on its own. That IS
    # what a non-JS crawler receives, which is exactly the SSR question.
    if rendered_text is None:
        soup = BeautifulSoup(pf.raw_html, "html.parser")
        has_h = bool(soup.find(["h1", "h2", "h3"]))
        n = len(raw_text or "")
        details = [f"• Server HTML text (no JS): {n} chars{via}",
                   f"• Headings present: {'yes' if has_h else 'no'}"]
        evidence = {"raw_len": n, "rendered": False, "headings": has_h}
        if n >= 800 and has_h:
            return CheckResult(
                id="rendering", title="Server-Side Rendering", status=PASS, weight=WEIGHT,
                fraction=1.0,
                summary=f"Server-rendered — {n} chars of content are in the server HTML, crawler-visible.",
                details=details, evidence=evidence,
            )
        if n >= 800:
            return CheckResult(
                id="rendering", title="Server-Side Rendering", status=PASS, weight=WEIGHT,
                fraction=0.85,
                summary=f"Content is in the server HTML ({n} chars), though no clear headings were found.",
                details=details, evidence=evidence,
                remediation="Add semantic headings (h1/h2) so crawlers grasp the page structure.",
            )
        if n >= 200:
            return CheckResult(
                id="rendering", title="Server-Side Rendering", status=WARN, weight=WEIGHT,
                fraction=0.5,
                summary=f"Thin server HTML ({n} chars) — check that the main content is server-rendered, "
                        "not injected by JavaScript.",
                details=details, evidence=evidence,
                remediation="Ensure the primary content ships in the initial HTML via SSR/SSG or prerendering.",
            )
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=FAIL, weight=WEIGHT,
            fraction=0.0,
            summary=f"Near-empty server HTML ({n} chars) — the content likely needs JavaScript, so non-JS "
                    "AI crawlers see little to nothing.",
            details=details, evidence=evidence,
            remediation=("Adopt server-side rendering, static generation or prerendering so the main content "
                         "exists in the initial HTML response."),
        )

    # We have the rendered DOM but not the raw server HTML → can't compute the ratio.
    if raw_text is None:
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=WARN, weight=WEIGHT,
            fraction=0.5,
            summary="Got the JS-rendered page but not the raw server HTML, so server-rendering couldn't "
                    "be confirmed.",
            details=[f"• Rendered text: {len(rendered_text)} chars{via}"],
            evidence={"rendered_len": len(rendered_text), "raw": False},
            remediation="Re-run; if it persists the server HTML wasn't retrievable for comparison.",
        )

    raw_len = len(raw_text)
    rendered_len = max(len(rendered_text), 1)
    ratio = raw_len / rendered_len

    coverage = (f"{ratio*100:.0f}% of the visible content is already in the raw HTML"
                if ratio <= 1 else
                f"raw HTML carries {ratio:.1f}× the visible page's text")
    details = [
        f"• Raw server HTML text (no JS): {raw_len} chars{via}",
        f"• Rendered text (with JS): {rendered_len} chars",
        f"• {coverage}",
    ]
    evidence = {"raw_len": raw_len, "rendered_len": rendered_len,
                "ratio": round(ratio, 3), "rendered": True, "method": pf.method}

    # Too much MORE text in raw than rendered → the raw HTML is padded with text
    # the visible page doesn't show (hidden menus, boilerplate, JSON, or JS that
    # strips content). Crawlers still see it, but it's an anomaly worth flagging.
    if ratio > 1.5:
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=WARN, weight=WEIGHT,
            fraction=0.7,
            summary=f"Raw HTML carries {ratio:.1f}× the visible page's text — likely hidden menus/boilerplate "
                    "(crawlers still see it, but verify it's genuine content, not hidden filler).",
            details=details, evidence=evidence,
            remediation=(
                "Check that the server HTML isn't padded with hidden or duplicated text. Keep the raw HTML "
                "and the genuinely visible content aligned so crawlers index real content, not filler."
            ),
        )
    if ratio >= 0.7:
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=PASS, weight=WEIGHT,
            fraction=1.0,
            summary=f"Server-rendered — {min(ratio,1.0)*100:.0f}% of the content is in the raw HTML, crawler-visible.",
            details=details, evidence=evidence,
        )
    if ratio >= 0.3:
        return CheckResult(
            id="rendering", title="Server-Side Rendering", status=WARN, weight=WEIGHT,
            fraction=0.5,
            summary=f"Partially JS-dependent — only {ratio*100:.0f}% of content survives without JavaScript.",
            details=details, evidence=evidence,
            remediation=(
                "Move key content (headings, body copy, links) into server-rendered HTML via SSR/SSG or "
                "prerendering, so non-JS AI crawlers capture it."
            ),
        )
    return CheckResult(
        id="rendering", title="Server-Side Rendering", status=FAIL, weight=WEIGHT,
        fraction=0.0,
        summary=f"JS-injected shell — only {ratio*100:.0f}% of content exists without JavaScript.",
        details=details, evidence=evidence,
        remediation=(
            "AI crawlers likely see a near-empty page. Adopt server-side rendering, static generation "
            "or prerendering for crawlers so the main content exists in the initial HTML response."
        ),
    )
