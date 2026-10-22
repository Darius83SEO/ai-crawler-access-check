"""Check 3 — Index & snippet eligibility.

Being reachable is not enough: AI answers built on a search index (Gemini,
AI Overviews, AI Mode on Google's; Copilot on Bing's) only use a page the index
is allowed to keep and quote. Three signals on the homepage:

  a) usage directives — robots meta + X-Robots-Tag. ``noindex`` drops the page
     from the index altogether; Google applies ``nosnippet`` / ``max-snippet:0``
     to AI Overviews and AI Mode too; Bing's ``noarchive`` keeps content out of
     Copilot answers and ``nocache`` limits it to URL, title and snippet;
  b) canonical — a canonical pointing to another host hands the page's
     eligibility to that host;
  c) sitemap freshness — a sitemap with ``<lastmod>`` dates tells crawlers what
     changed. Index-backed engines replace stale sources quickly, so fresh
     lastmods are how updated content gets re-read.
"""

from __future__ import annotations

import gzip
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..http import BROWSER_UA
from ..models import CheckResult, FAIL, PASS, WARN, ERROR
from ..util import normalize_domain
from .robots_waf import fetch_robots

WEIGHT = 15.0

_DIRECTIVES_SHARE = 0.50
_SITEMAP_SHARE = 0.35
_CANONICAL_SHARE = 0.15

# Bots whose meta/X-Robots-Tag directives matter for AI answers.
_RELEVANT_AGENTS = {"robots", "googlebot", "bingbot", "msnbot"}
_MAX_SITEMAPS = 3          # child sitemaps to open from a sitemap index
_MAX_BYTES = 8_000_000


def _get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=timeout,
                            allow_redirects=True)
    except Exception:  # noqa: BLE001
        return None


def _homepage(domain: str) -> Optional[requests.Response]:
    for scheme in ("https", "http"):
        r = _get(f"{scheme}://{domain}/")
        if r is not None:
            return r
    return None


# ── a) directives ──────────────────────────────────────────────
def _parse_directive_values(value: str) -> List[str]:
    return [v.strip().lower() for v in value.split(",") if v.strip()]


def _collect_directives(resp: requests.Response, soup: BeautifulSoup) -> Dict[str, List[str]]:
    """agent -> list of directive tokens, from meta tags and X-Robots-Tag."""
    found: Dict[str, List[str]] = {}
    for m in soup.find_all("meta", attrs={"name": True, "content": True}):
        name = m["name"].strip().lower()
        if name in _RELEVANT_AGENTS:
            found.setdefault(name, []).extend(_parse_directive_values(m["content"]))
    # requests folds repeated X-Robots-Tag headers into one comma-joined value.
    # "googlebot: noindex, nofollow" scopes the following tokens to one agent;
    # a bare token applies to every agent.
    agent = "robots"
    for tok in (resp.headers.get("X-Robots-Tag", "") or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"([A-Za-z-]+)\s*:\s*(.*)$", tok)
        if m and not m.group(1).lower().startswith(("max-", "unavailable_after")):
            agent = m.group(1).lower()
            tok = m.group(2).strip()
            if not tok:
                continue
        if agent in _RELEVANT_AGENTS:
            found.setdefault(agent, []).append(tok.lower())
    return found


def _score_directives(directives: Dict[str, List[str]], n_data_nosnippet: int):
    """(fraction, notes, issues dict)."""
    tokens = {agent: set(vals) for agent, vals in directives.items()}

    def has(tok, agents=("robots", "googlebot", "bingbot", "msnbot")):
        return [a for a in agents if tok in tokens.get(a, set())]

    issues: Dict[str, List[str]] = {}
    notes: List[str] = []
    frac = 1.0

    noindex = has("noindex") + has("none")
    if noindex:
        issues["noindex"] = noindex
        notes.append(f"noindex for {', '.join(sorted(set(noindex)))} — the page leaves the index, "
                     "so index-based AI answers can't use it.")
        return 0.0, notes, issues

    snippet_off = has("nosnippet", ("robots", "googlebot"))
    for agent in ("robots", "googlebot"):
        for t in tokens.get(agent, set()):
            if t.startswith("max-snippet:"):
                try:
                    n = int(t.split(":", 1)[1])
                except ValueError:
                    continue
                if n == 0:
                    snippet_off.append(agent)
                elif 0 < n < 50:
                    issues.setdefault("max_snippet_low", []).append(f"{agent}={n}")
    if snippet_off:
        issues["nosnippet"] = snippet_off
        frac -= 0.6
        notes.append("nosnippet / max-snippet:0 — Google won't use the page's text in AI Overviews or AI Mode.")
    if issues.get("max_snippet_low"):
        frac -= 0.2
        notes.append(f"Very short max-snippet ({', '.join(issues['max_snippet_low'])}) — little text "
                     "can be quoted in AI answers.")

    bing_archive = has("noarchive", ("robots", "bingbot", "msnbot"))
    bing_cache = has("nocache", ("robots", "bingbot", "msnbot"))
    if bing_archive:
        issues["noarchive"] = bing_archive
        frac -= 0.3
        notes.append("noarchive — Bing keeps the content out of Copilot answers and grounding.")
    if bing_cache:
        issues["nocache"] = bing_cache
        frac -= 0.15
        notes.append("nocache — Copilot may only use URL, title and snippet.")

    nonstandard = sorted({t for vals in tokens.values() for t in vals if t in ("noai", "noimageai")})
    if nonstandard:
        notes.append(f"{'/'.join(nonstandard)} present — non-standard, ignored by the major AI crawlers.")

    if n_data_nosnippet:
        notes.append(f"{n_data_nosnippet} section(s) marked data-nosnippet — excluded from Google "
                     "snippets and AI features.")
        if n_data_nosnippet >= 5:
            frac -= 0.1

    if frac == 1.0 and not nonstandard and not n_data_nosnippet:
        notes.append("No directive limits indexing or quoting in AI answers.")
    return max(0.0, frac), notes, issues


# ── b) canonical ───────────────────────────────────────────────
def _score_canonical(soup: BeautifulSoup, resp: requests.Response, domain: str):
    link = None
    for tag in soup.find_all("link", href=True):
        rel = tag.get("rel")
        rels = [r.lower() for r in (rel if isinstance(rel, list) else [rel or ""])]
        if "canonical" in rels:
            link = urljoin(resp.url, tag["href"].strip())
            break
    if not link:
        return 0.6, "No canonical tag on the homepage — engines pick the canonical URL themselves.", None
    host = normalize_domain(link)
    if host and host != domain:
        return 0.0, f"Canonical points to another host ({host}) — eligibility goes to that site.", link
    return 1.0, f"Self-referencing canonical ({link}).", link


# ── c) sitemap freshness ───────────────────────────────────────
def _sitemap_candidates(domain: str, robots_text: str) -> List[str]:
    urls = re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots_text or "")
    if urls:
        return urls
    return [f"https://{domain}/sitemap.xml", f"https://{domain}/sitemap_index.xml"]


def _read_xml(url: str) -> Optional[str]:
    r = _get(url, timeout=20)
    if r is None or r.status_code >= 400 or not r.content:
        return None
    body = r.content[:_MAX_BYTES]
    if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:  # noqa: BLE001
            return None
    text = body.decode("utf-8", errors="replace")
    # Some generators (e.g. All in One SEO) wrap every value in CDATA.
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    return text if "<urlset" in text or "<sitemapindex" in text else None


def _parse_lastmod(value: str) -> Optional[datetime]:
    v = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v.replace("Z", "+00:00") if "T" in v else v, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _score_sitemap(domain: str, robots_text: str):
    """(fraction, notes, evidence)."""
    ev: Dict = {}
    for cand in _sitemap_candidates(domain, robots_text):
        xml = _read_xml(cand)
        if not xml:
            continue
        ev["sitemap"] = cand
        if "<sitemapindex" in xml:
            children = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
            ev["children"] = len(children)
            # Most CMSs list post/page sitemaps first; open a few, not hundreds.
            texts = [t for t in (_read_xml(c) for c in children[:_MAX_SITEMAPS]) if t]
            index_lastmods = re.findall(r"<lastmod>\s*([^<]+?)\s*</lastmod>", xml)
        else:
            texts, index_lastmods = [xml], []
        n_urls = sum(len(re.findall(r"<url>", t)) for t in texts)
        lastmods = [d for d in (_parse_lastmod(v) for t in texts
                                for v in re.findall(r"<lastmod>\s*([^<]+?)\s*</lastmod>", t)) if d]
        lastmods += [d for d in (_parse_lastmod(v) for v in index_lastmods) if d]
        ev.update({"urls_sampled": n_urls, "lastmods": len(lastmods)})
        if not lastmods:
            return 0.5, [f"Sitemap found ({cand}, {n_urls} URLs sampled) but no <lastmod> dates — "
                         "crawlers can't tell what changed."], ev
        newest = max(lastmods)
        age = (datetime.now(timezone.utc) - newest).days
        coverage = min(1.0, len(lastmods) / max(1, n_urls)) if n_urls else 1.0
        ev.update({"newest_lastmod": newest.date().isoformat(), "age_days": age,
                   "lastmod_coverage": round(coverage, 2)})
        note = (f"Sitemap {cand}: {n_urls} URLs sampled, lastmod on {round(coverage * 100)}%, "
                f"newest {newest.date().isoformat()} ({age} days ago).")
        if age <= 90:
            return (1.0 if coverage >= 0.5 else 0.8), [note], ev
        if age <= 365:
            return 0.6, [note + " Nothing updated in the last 3 months."], ev
        return 0.3, [note + " No update in over a year — sources this stale get replaced."], ev
    ev["sitemap"] = None
    return 0.0, ["No XML sitemap found (robots.txt Sitemap: line, /sitemap.xml, /sitemap_index.xml)."], ev


def _sentence(parts: List[str]) -> str:
    """Join fix fragments into one sentence, capitalising only the first letter."""
    if not parts:
        return ""
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:]


def run(domain: str) -> CheckResult:
    resp = _homepage(domain)
    if resp is None:
        return CheckResult(
            id="eligibility", title="Index & Snippet Eligibility", status=ERROR, weight=WEIGHT,
            summary="The site did not respond on HTTP or HTTPS.",
            remediation="Confirm the domain is live and reachable.",
        )
    if resp.status_code >= 400:
        return CheckResult(
            id="eligibility", title="Index & Snippet Eligibility", status=ERROR, weight=WEIGHT,
            summary=f"The homepage answered HTTP {resp.status_code} to a browser request, so its "
                    "directives can't be read from outside.",
            remediation="Re-run, or read the robots meta / X-Robots-Tag from a crawl tool.",
        )

    soup = BeautifulSoup(resp.text or "", "html.parser")
    directives = _collect_directives(resp, soup)
    n_dns = len(soup.find_all(attrs={"data-nosnippet": True}))
    d_frac, d_notes, issues = _score_directives(directives, n_dns)
    c_frac, c_note, canonical = _score_canonical(soup, resp, domain)
    try:
        _, robots_text = fetch_robots(domain)
    except Exception:  # noqa: BLE001
        robots_text = ""
    s_frac, s_notes, s_ev = _score_sitemap(domain, robots_text)

    if issues.get("noindex"):
        fraction = 0.0
    else:
        fraction = round(_DIRECTIVES_SHARE * d_frac + _SITEMAP_SHARE * s_frac
                         + _CANONICAL_SHARE * c_frac, 3)

    if issues.get("noindex") or c_frac == 0.0 or fraction < 0.4:
        status = FAIL
    elif fraction < 0.8:
        status = WARN
    else:
        status = PASS

    details = ["• Directives: " + n for n in d_notes] + ["• Canonical: " + c_note] \
        + ["• Freshness: " + n for n in s_notes]

    if status == PASS:
        summary = ("Index engines may keep and quote the homepage — no limiting directive"
                   + (", sitemap dates are fresh." if s_frac == 1.0 else "; the sitemap could be fresher."))
    elif issues.get("noindex"):
        summary = "The homepage is noindex — it can't be used by index-based AI answers (Gemini, AI Overviews, Copilot)."
    else:
        gaps = []
        if issues.get("nosnippet") or issues.get("max_snippet_low"):
            gaps.append("snippet limits")
        if issues.get("noarchive") or issues.get("nocache"):
            gaps.append("Bing/Copilot usage limits")
        if c_frac < 1.0:
            gaps.append("canonical")
        if s_frac < 1.0:
            gaps.append("sitemap freshness")
        summary = "Eligible for indexing, with gaps: " + ", ".join(gaps) + "."

    fixes = []
    if issues.get("noindex"):
        fixes.append("remove noindex from pages you want AI answers to use")
    if issues.get("nosnippet") or issues.get("max_snippet_low"):
        fixes.append("drop nosnippet / raise max-snippet on pages meant to be quoted (use data-nosnippet "
                     "only on the sections you really want excluded)")
    if issues.get("noarchive") or issues.get("nocache"):
        fixes.append("remove noarchive/nocache unless you deliberately want to stay out of Copilot answers")
    if c_frac == 0.0:
        fixes.append("make the homepage canonical point to itself")
    elif c_frac < 1.0:
        fixes.append("add a self-referencing canonical")
    if s_frac < 1.0:
        fixes.append("publish an XML sitemap with accurate <lastmod> dates, declare it in robots.txt, and "
                     "ping changes via IndexNow (Bing) so updated pages are re-read quickly")
    remediation = _sentence(fixes)

    return CheckResult(
        id="eligibility", title="Index & Snippet Eligibility", status=status, weight=WEIGHT,
        fraction=fraction, summary=summary, details=details,
        evidence={"directives": directives, "issues": issues, "data_nosnippet": n_dns,
                  "canonical": canonical, **s_ev},
        remediation=remediation,
    )
