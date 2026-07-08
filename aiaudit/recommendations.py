"""Turn the audit findings into a prioritised strategic action plan.

Ordering follows the guide's leverage logic: open access is the highest-leverage,
lowest-effort move; centrality is strategic and slow. Each recommendation pairs
*what to do* (the check's remediation) with *why it matters* (the payoff).
"""

from __future__ import annotations

from typing import Dict, List

from .models import AuditReport, FAIL, WARN, ERROR

# Priority vocabulary -> display colour.
PRIORITY_COLOR = {
    "Critical": "#dc2626",
    "High": "#ea580c",
    "Medium": "#d97706",
    "Low": "#317fd2",
    "Strategic": "#7c3aed",
}


def _headline(report: AuditReport) -> str:
    g = report.grade["grade"]
    if g in ("A", "B"):
        return ("Solid foundation. You clear the decisive access checks — close the remaining "
                "gaps below to widen your reach into AI answers.")
    if g in ("C", "D"):
        return ("Real gaps are costing you AI visibility. Work the list top-down: the first items "
                "are the highest-leverage, lowest-effort fixes.")
    return ("Critical — you are largely invisible to AI systems. Start at the very top: until crawlers "
            "can reach and capture you, nothing downstream matters.")


def build_recommendations(report: AuditReport) -> List[Dict[str, str]]:
    by = {c.id: c for c in report.checks}
    recs: List[Dict[str, str]] = []

    def add(priority, title, action, impact):
        recs.append({"priority": priority, "title": title, "action": action,
                     "impact": impact, "color": PRIORITY_COLOR.get(priority, "#317fd2")})

    # 1 · Access — highest leverage
    a = by.get("access")
    if a and a.status == FAIL:
        add("Critical", "Unblock AI crawlers (robots.txt + CDN/WAF)", a.remediation,
            "The single highest-leverage, lowest-effort move. Well-behaved crawlers resume on their "
            "normal schedule the moment you allow them, and presence then compounds every month.")
    elif a and a.status == WARN:
        add("Medium", "Review which AI crawlers you block", a.remediation,
            "An accidental CDN default may be excluding you from some engines — make it a deliberate choice.")

    # 2 · Index coverage
    idx = by.get("index")
    if idx and idx.status == FAIL:
        add("High", "Get captured into the Common Crawl archive", idx.remediation,
            "Open access is not the same as presence. Links + sitemap + crawlable pages put you in monthly "
            "snapshots, which is what training data is built from.")
    elif idx and idx.status == WARN:
        add("Medium", "Deepen your archive coverage", idx.remediation,
            "Shallow capture means most of your content never reaches the training set.")

    # 3 · Rendering
    r = by.get("rendering")
    if r and r.status == FAIL:
        add("High", "Server-render your content", r.remediation,
            "Crawlers that don't run JavaScript capture an empty shell — invisible to both training and "
            "live retrieval. SSR/SSG/prerendering makes the content exist in the first response.")
    elif r and r.status == WARN:
        add("Medium", "Reduce JavaScript dependency", r.remediation,
            "Part of your content risks being missed by non-JS crawlers.")

    # 4 · Structured data
    s = by.get("structured")
    if s and s.status in (FAIL, WARN):
        add("Medium" if s.status == FAIL else "Low", "Complete your Schema.org markup", s.remediation,
            "Structured data helps models represent and attribute your entity cleanly — low effort, "
            "high leverage for citation.")

    # 6 · Agentic readiness — emerging action layer (AX tree + llms.txt)
    ag = by.get("agentic")
    if ag and ag.status in (FAIL, WARN):
        add("Medium" if ag.status == FAIL else "Low", "Make the site navigable for AI agents", ag.remediation,
            "A different layer from retrievability: autonomous agents (Operator, computer-use) traverse the "
            "accessibility tree, not the raw HTML. Semantic controls, accessible names and a valid llms.txt let "
            "them complete journeys (buy, book, sign up) instead of reading the page as inert. Highest payoff "
            "for transactional sites.")

    # 5 · Centrality — strategic, slow payoff
    c = by.get("centrality")
    if c and c.status in (FAIL, WARN) and (c.fraction is None or c.fraction < 0.7):
        add("Strategic", "Raise crawl priority through connectivity", c.remediation,
            "A slow-moving asset: a single link from a core-connected site lifts centrality more than "
            "dozens from isolated ones, earning deeper and more frequent crawling. The payoff arrives "
            "on the model's schedule, so start early.")

    # Evergreen strategic plays — always worth stating in a client deliverable.
    add("Strategic", "Publish a strong English edition of cornerstone pages",
        "Mirror your highest-value pages in genuinely useful English with proper hreflang and distinct, "
        "crawlable URLs (not machine-translated filler).",
        "AI retrievers tilt toward English (~41% of the crawl). For a non-English market this is one of "
        "the few content-side moves that widens your AI footprint.")
    add("Low", "Re-audit on a schedule",
        "Re-run this audit quarterly and after any CDN/WAF or platform migration.",
        "CDN defaults change and block AI bots silently; catching a regression early protects months of "
        "accumulated crawl presence.")

    return recs
