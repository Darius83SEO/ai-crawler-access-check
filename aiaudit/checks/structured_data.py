"""Check 4 — Structured data completeness.

Entities without structured data are harder to represent cleanly in training
and harder for models to attribute. We extract Schema.org markup (JSON-LD,
Microdata, RDFa) from the homepage and report which key types are present.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Set

from bs4 import BeautifulSoup

from ..fetcher import fetch_page
from ..http import BLOCK_CODES
from ..models import CheckResult, FAIL, PASS, WARN, ERROR

WEIGHT = 10.0

# Key schema families the guide calls out, each with accepted subtypes.
KEY_TYPES = {
    "Organization": {"organization", "localbusiness", "corporation", "onlinestore",
                     "ngo", "educationalorganization", "newsmediaorganization", "store"},
    "Page/Content": {"webpage", "website", "article", "blogposting", "newsarticle",
                     "product", "service", "faqpage", "collectionpage", "itemlist",
                     "offer", "aggregateoffer"},
    "Author/Person": {"person", "author"},
    "Breadcrumb": {"breadcrumblist"},
}
# The families that actually matter for AI to represent/attribute the entity.
# The other two (Author/Person, Breadcrumb) are situational (editorial sites),
# so a shop shouldn't be marked down for lacking them.
CORE_FAMILIES = {"Organization", "Page/Content"}


def _collect_jsonld_types(soup: BeautifulSoup) -> Set[str]:
    found: Set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            t = obj.get("@type")
            if isinstance(t, str):
                found.add(t.lower())
            elif isinstance(t, list):
                found.update(str(x).lower() for x in t)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except json.JSONDecodeError:
            # Some sites concatenate multiple JSON objects; try a lenient split.
            for chunk in re.split(r"}\s*{", raw):
                chunk = chunk if chunk.startswith("{") else "{" + chunk
                chunk = chunk if chunk.endswith("}") else chunk + "}"
                try:
                    walk(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
    return found


def _collect_microdata_types(soup: BeautifulSoup) -> Set[str]:
    found: Set[str] = set()
    for tag in soup.find_all(attrs={"itemtype": True}):
        itemtype = tag.get("itemtype", "")
        if "schema.org" in itemtype.lower():
            found.add(itemtype.rstrip("/").split("/")[-1].lower())
    # RDFa typeof
    for tag in soup.find_all(attrs={"typeof": True}):
        for t in tag.get("typeof", "").split():
            found.add(t.split(":")[-1].lower())
    return found


def _types_from(html: Optional[str]) -> Set[str]:
    if not html:
        return set()
    soup = BeautifulSoup(html, "html.parser")
    return _collect_jsonld_types(soup) | _collect_microdata_types(soup)


def run(domain: str) -> CheckResult:
    pf = fetch_page(domain)

    if pf.blocked:
        code = pf.browser_status or pf.status
        if code in BLOCK_CODES:
            gate = "a login/paywall" if code == 401 else "bot-protection"
            return CheckResult(
                id="structured", title="Structured Data", status=FAIL, weight=WEIGHT,
                fraction=0.0,
                summary=f"The site refused our request (HTTP {code}) — the page is gated ({gate}), so its "
                        "markup can't be assessed from outside.",
                evidence={"status": code, "method": "blocked"},
                remediation=(
                    "The page is gated to non-allowlisted clients. Verify from server logs whether real AI "
                    "crawlers (e.g. Googlebot/GPTBot from their published IPs) are allowed through; if they "
                    "are, the markup they see can't be audited externally."
                ),
            )
        return CheckResult(
            id="structured", title="Structured Data", status=ERROR, weight=WEIGHT,
            summary=f"Could not reach the homepage to inspect markup ({pf.render_error or 'no usable response'}).",
            remediation="Confirm the domain resolves and serves HTML.",
        )

    if pf.shell:
        # Anti-bot interstitial — not a verdict on the real markup (excluded from score).
        return CheckResult(
            id="structured", title="Structured Data", status=WARN, weight=WEIGHT,
            fraction=None,
            summary=f"Received an anti-bot interstitial (HTTP {pf.status}) — the markup can't be assessed "
                    "from outside; allowlisted crawlers/browsers may see full content.",
            evidence={"status": pf.status, "shell": True},
            remediation=("Confirm from server logs that real crawlers get the full HTML — Amazon-style "
                         "anti-bot pages often serve content to allowlisted crawlers but a shell to others."),
        )

    raw_types = _types_from(pf.raw_html)
    rendered_types = _types_from(pf.rendered_html)
    all_types = raw_types | rendered_types
    js_only = rendered_types - raw_types

    present_raw = [f for f, acc in KEY_TYPES.items() if raw_types & acc]
    present_all = [f for f, acc in KEY_TYPES.items() if all_types & acc]
    missing = [f for f in KEY_TYPES if f not in present_all]

    if pf.cc_fallback:
        _i = pf.cc_info or {}
        _src = f"Live fetch was gated → read from the Common Crawl archive ({_i.get('crawl')}, {_i.get('timestamp')})"
    else:
        _src = f"Fetched via {pf.method or 'browser'}"
    details = [
        f"• {_src}",
        f"• Schema in server HTML (crawler-visible): {', '.join(sorted(raw_types)) or 'none'}",
    ]
    if js_only:
        details.append(f"• Schema only after JavaScript (a non-JS crawler won't see it): {', '.join(sorted(js_only))}")
    if present_all:
        details.append(f"• Key families present: {', '.join(present_all)}")
    if missing:
        details.append(f"• Key families missing: {', '.join(missing)}")

    evidence = {"raw_types": sorted(raw_types), "rendered_types": sorted(rendered_types),
                "js_only": sorted(js_only), "present": present_all, "missing": missing,
                "method": pf.method}

    if not all_types:
        return CheckResult(
            id="structured", title="Structured Data", status=FAIL, weight=WEIGHT,
            fraction=0.0,
            summary="No Schema.org structured data found on the homepage.",
            details=details, evidence=evidence,
            remediation=(
                "Add JSON-LD for Organization (name, logo, sameAs), the page's main entity "
                "(Article/Product/WebPage), author/Person and BreadcrumbList. Validate with Google's "
                "Rich Results Test. Low effort, high leverage for attribution."
            ),
        )

    if not raw_types and rendered_types:
        return CheckResult(
            id="structured", title="Structured Data", status=WARN, weight=WEIGHT,
            fraction=0.3,
            summary="Structured data exists but only after JavaScript — non-JS crawlers (incl. CCBot) "
                    "won't see it.",
            details=details, evidence=evidence,
            remediation="Render your Schema.org JSON-LD server-side so it's in the initial HTML, not injected by JS.",
        )

    core_present = [f for f in present_raw if f in CORE_FAMILIES]
    opt_missing = [f for f in KEY_TYPES if f not in CORE_FAMILIES and f not in present_raw]

    if core_present:
        # The entity is described in the markup → green. Don't penalise a shop
        # for lacking Author/Breadcrumb; reward what's there, suggest the rest.
        frac = min(1.0, 0.7 + 0.15 * len(present_raw))
        return CheckResult(
            id="structured", title="Structured Data", status=PASS, weight=WEIGHT,
            fraction=frac,
            summary=f"Structured data present — {', '.join(present_raw)} ({len(raw_types)} schema types).",
            details=details, evidence=evidence,
            remediation=(f"Optional: add {', '.join(opt_missing)} where relevant (e.g. for articles or "
                         "deeper navigation)." if opt_missing else ""),
        )

    # Some markup exists, but no Organization / main-entity schema to attribute to.
    return CheckResult(
        id="structured", title="Structured Data", status=WARN, weight=WEIGHT,
        fraction=0.5,
        summary=f"Some markup found ({', '.join(sorted(raw_types))}) but no Organization or main-entity "
                "schema for clean attribution.",
        details=details, evidence=evidence,
        remediation="Add an Organization (name, logo, sameAs) and the page's main entity (WebPage/Product/Article).",
    )
