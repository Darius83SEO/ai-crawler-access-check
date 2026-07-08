"""Check 6 — Agentic readiness (accessibility tree + llms.txt).

The first five checks answer *retrievability*: can training and retrieval
crawlers reach, capture and represent the site? This one answers a different,
emerging question — *actionability*: can an autonomous AI agent (Operator,
ChatGPT agent, Gemini/Claude computer-use) actually **navigate and act** on the
page?

Such agents don't read a screenshot (~50k tokens) or the noisy raw DOM (~8k);
they traverse the **accessibility tree** — the semantic subset of the DOM that
exposes each node's *role*, *name* and *state*. Google now surfaces a broken
accessibility tree as an "Agentic Browsing" signal in Lighthouse. If your
interactive controls are `<div onclick>` with no role or name, an agent can't
see them as actionable — the page reads as inert.

Two sub-signals, combined:

  • Accessibility-tree readiness (70%) — semantic landmarks, real vs "fake"
    controls, accessible names on interactive elements, heading hierarchy.
    Computed from the DOM in Lite mode; enriched by the real AX snapshot
    (Playwright ``page.accessibility.snapshot``) when the browser path is on.
  • llms.txt (30%) — presence and correctness of the ``/llms.txt`` guide file
    (llmstxt.org), the machine-readable map you publish *for* AI clients.

Unlike the retrievability checks, a poor score here does NOT mean you're
invisible to ChatGPT/Perplexity — a plain content site can be perfectly
cited yet not agent-actionable. It matters most where agents complete tasks
(shop, book, sign up).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from ..fetcher import fetch_page
from ..http import BLOCK_CODES, BROWSER_UA
from ..models import CheckResult, FAIL, PASS, WARN, ERROR

WEIGHT = 10.0

# How the two sub-signals split this check's fraction.
_AX_SHARE = 0.70
_LLMS_SHARE = 0.30

_INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "summary"}
_INTERACTIVE_ROLES = {"button", "link", "menuitem", "tab", "checkbox", "radio",
                      "switch", "combobox", "textbox", "option"}
_LANDMARK_TAGS = {"main", "nav", "header", "footer", "aside"}
_LANDMARK_ROLES = {"main", "navigation", "banner", "contentinfo", "complementary", "search"}


# ─────────────────────────── accessibility tree ───────────────────────────

def _accessible_name(el) -> bool:
    """True if the element exposes a name an agent can use to identify it."""
    if el.get("aria-label", "").strip() or el.get("aria-labelledby", "").strip():
        return True
    if el.get("title", "").strip():
        return True
    txt = el.get_text(" ", strip=True)
    if txt:
        return True
    # Icon-only control: an <img alt> / <svg><title> still names it.
    img = el.find("img")
    if img is not None and img.get("alt", "").strip():
        return True
    if el.find("svg") is not None and el.find("title") is not None:
        return True
    # Form controls can be named by placeholder/value as a fallback.
    if el.name == "input":
        if el.get("placeholder", "").strip() or el.get("value", "").strip():
            return True
        if el.get("type", "").lower() in ("submit", "button", "reset") and el.get("value", "").strip():
            return True
    return False


def _heading_score(soup) -> Tuple[float, List[str]]:
    levels = [int(h.name[1]) for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]
    notes: List[str] = []
    if not levels:
        return 0.0, ["No headings at all — agents get no outline of the page."]
    n_h1 = levels.count(1)
    skips = 0
    prev = 0
    for lv in levels:
        if prev and lv > prev + 1:
            skips += 1
        prev = lv
    score = 1.0
    if n_h1 == 0:
        score -= 0.4; notes.append("No <h1>.")
    elif n_h1 > 1:
        score -= 0.2; notes.append(f"{n_h1} <h1> elements (expected one).")
    if skips:
        score -= min(0.4, 0.2 * skips); notes.append(f"{skips} skipped heading level(s).")
    if not notes:
        notes.append("Clean heading hierarchy.")
    return max(0.0, score), notes


def _dom_ax_readiness(html: str) -> Tuple[float, List[str], Dict]:
    """DOM-based approximation of accessibility-tree quality (works without a browser)."""
    soup = BeautifulSoup(html, "html.parser")

    # 1 · Semantic landmarks
    have_landmarks = {t for t in _LANDMARK_TAGS if soup.find(t)}
    have_landmarks |= {r for r in _LANDMARK_ROLES
                       if soup.find(attrs={"role": r})}
    has_main = bool(soup.find("main") or soup.find(attrs={"role": "main"}))
    landmark_frac = min(1.0, len(have_landmarks) / 4.0)
    if has_main:
        landmark_frac = min(1.0, landmark_frac + 0.15)

    # 2 · Real vs "fake" controls
    native = soup.find_all(list(_INTERACTIVE_TAGS))
    role_based = soup.find_all(attrs={"role": lambda v: v in _INTERACTIVE_ROLES if v else False})
    fake_links = [a for a in soup.find_all("a")
                  if not a.get("href") and not a.get("role")]
    fake_buttons = [el for el in soup.find_all(attrs={"onclick": True})
                    if el.name in ("div", "span") and not el.get("role")]
    n_interactive = max(1, len(native) + len(role_based))
    n_fake = len(fake_links) + len(fake_buttons)
    fake_frac = max(0.0, 1.0 - n_fake / (n_interactive + n_fake))

    # 3 · Accessible names on interactive elements
    named = 0
    checkable = [el for el in native
                 if not (el.name == "input" and el.get("type", "").lower() == "hidden")]
    for el in checkable:
        if _accessible_name(el):
            named += 1
    name_frac = named / len(checkable) if checkable else 1.0

    # 4 · Heading hierarchy
    head_frac, head_notes = _heading_score(soup)

    ax = (0.25 * landmark_frac + 0.30 * fake_frac + 0.25 * name_frac + 0.20 * head_frac)

    notes = [
        f"Landmarks present: {', '.join(sorted(have_landmarks)) or 'none'}"
        + ("" if has_main else " — no <main>"),
        (f"{n_fake} non-semantic control(s) an agent can't traverse "
         f"({len(fake_links)} <a> w/o href, {len(fake_buttons)} div/span onclick w/o role)"
         if n_fake else f"All {n_interactive} interactive elements use real roles"),
        (f"{named}/{len(checkable)} interactive elements have an accessible name"
         if checkable else "No interactive elements to name"),
    ] + head_notes[:1]

    evidence = {
        "landmarks": sorted(have_landmarks), "has_main": has_main,
        "interactive": n_interactive, "fake_controls": n_fake,
        "named": named, "checkable": len(checkable),
        "frac_landmarks": round(landmark_frac, 2), "frac_controls": round(fake_frac, 2),
        "frac_names": round(name_frac, 2), "frac_headings": round(head_frac, 2),
        "source": "dom",
    }
    return ax, notes, evidence


def _ax_snapshot_enrich(ax_tree: dict) -> Tuple[Optional[float], Optional[str], Dict]:
    """Authoritative name signal from a real AX snapshot: fraction of actionable
    nodes that carry a name. Returns (name_frac, note, evidence) or Nones."""
    total = 0
    nameless = 0

    def walk(node):
        nonlocal total, nameless
        if not isinstance(node, dict):
            return
        role = (node.get("role") or "").lower()
        if role in _INTERACTIVE_ROLES:
            total += 1
            if not (node.get("name") or "").strip():
                nameless += 1
        for child in node.get("children", []) or []:
            walk(child)

    walk(ax_tree)
    if total == 0:
        return None, None, {"ax_nodes": 0}
    frac = (total - nameless) / total
    note = (f"AX snapshot: {total - nameless}/{total} actionable nodes carry a name"
            + (f" ({nameless} nameless)" if nameless else ""))
    return frac, note, {"ax_actionable": total, "ax_nameless": nameless, "source": "ax_snapshot"}


# ─────────────────────────────── llms.txt ───────────────────────────────

def _fetch_llms_txt(domain: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (status, content_type, body) for /llms.txt, trying https then http."""
    for scheme in ("https", "http"):
        try:
            r = requests.get(f"{scheme}://{domain}/llms.txt",
                             headers={"User-Agent": BROWSER_UA, "Accept": "text/plain,text/markdown,*/*"},
                             timeout=15, allow_redirects=True)
            return r.status_code, r.headers.get("Content-Type", ""), r.text
        except Exception:
            continue
    return None, None, None


def _validate_llms_txt(domain: str) -> Tuple[Optional[float], List[str], Dict]:
    """Grade presence + correctness of /llms.txt per the llmstxt.org format.
    Returns (fraction 0..1 or None if unreachable, notes, evidence)."""
    status, ctype, body = _fetch_llms_txt(domain)
    ev: Dict = {"status": status, "content_type": ctype}

    if status is None:
        return None, ["Could not reach /llms.txt (network)."], ev
    if status != 200 or not (body or "").strip():
        return 0.0, [f"No /llms.txt (HTTP {status}) — you don't yet publish a machine-readable "
                     "map for AI clients."], ev

    stripped = body.lstrip()
    # SPA/soft-404 catch-all serving the app shell instead of a real file.
    looks_html = stripped[:1].startswith("<") or "<html" in body[:2000].lower() \
        or ("text/html" in (ctype or "").lower())
    if looks_html:
        ev["looks_html"] = True
        return 0.2, ["/llms.txt returns HTML, not plain text — likely a soft-404/SPA fallback, "
                     "not a real llms.txt file."], ev

    lines = [ln.rstrip() for ln in body.splitlines()]
    has_h1 = any(ln.startswith("# ") for ln in lines)
    has_blockquote = any(ln.lstrip().startswith(">") for ln in lines)
    has_h2 = any(ln.startswith("## ") for ln in lines)
    import re as _re
    n_links = len(_re.findall(r"\[[^\]]+\]\([^)]+\)", body))
    plain_ct = any(x in (ctype or "").lower() for x in ("text/plain", "text/markdown", "text/x-markdown"))
    ev.update({"has_h1": has_h1, "has_h2": has_h2, "has_blockquote": has_blockquote,
               "n_links": n_links, "plain_content_type": plain_ct, "bytes": len(body)})

    notes: List[str] = []
    if has_h1 and n_links >= 1:
        frac = 1.0
        notes.append(f"Valid llms.txt: H1 title, {n_links} link(s)"
                     + (", summary blockquote" if has_blockquote else "")
                     + (f", {'section headers' if has_h2 else 'flat list'}") + ".")
        if not plain_ct:
            frac = 0.9
            notes.append(f"Served as '{ctype}' — set Content-Type to text/plain or text/markdown.")
    elif has_h1 or n_links >= 1:
        frac = 0.5
        missing = "an H1 title (# ...)" if not has_h1 else "at least one [name](url) link"
        notes.append(f"Present but incomplete — missing {missing} (see llmstxt.org format).")
    else:
        frac = 0.3
        notes.append("File exists but doesn't follow the llms.txt format (no H1, no links).")
    return frac, notes, ev


# ──────────────────────────────── run ────────────────────────────────

def _verdict(frac: float) -> str:
    if frac >= 0.75:
        return PASS
    if frac >= 0.4:
        return WARN
    return FAIL


def run(domain: str) -> CheckResult:
    pf = fetch_page(domain)

    # llms.txt is a separate lightweight fetch — evaluate it regardless of whether
    # the homepage itself was reachable.
    llms_frac, llms_notes, llms_ev = _validate_llms_txt(domain)

    # Pick the best HTML for the AX approximation: the rendered DOM if we have it
    # (closest to what an agent traverses), else the raw/archived server HTML.
    html = pf.rendered_html or pf.raw_html

    if not html:
        # Homepage unusable — grade on llms.txt alone if we at least got that.
        if pf.blocked and (pf.browser_status in BLOCK_CODES or pf.status in BLOCK_CODES):
            return CheckResult(
                id="agentic", title="Agentic Readiness", status=FAIL, weight=WEIGHT,
                fraction=0.0,
                summary=f"The site refused our request (HTTP {pf.browser_status or pf.status}) — the page "
                        "is gated, so agent-navigability can't be assessed from outside.",
                evidence={"llms": llms_ev},
                remediation="Verify from server logs whether real agents/crawlers are allowed through.",
            )
        if llms_frac is None:
            return CheckResult(
                id="agentic", title="Agentic Readiness", status=ERROR, weight=WEIGHT,
                summary=f"Could not reach the homepage ({pf.render_error or 'no usable response'}).",
                remediation="Confirm the domain resolves and serves HTML.",
            )
        # Only llms.txt is informative → report it but exclude from the score.
        return CheckResult(
            id="agentic", title="Agentic Readiness", status=_verdict(llms_frac), weight=WEIGHT,
            fraction=None,
            summary="Couldn't read the page's structure, so only the llms.txt signal was assessed "
                    "(excluded from the score).",
            details=["• " + n for n in llms_notes],
            evidence={"llms": llms_ev},
            remediation="Re-run; if the homepage stays unreachable the AX-tree read can't be computed.",
        )

    if pf.shell:
        return CheckResult(
            id="agentic", title="Agentic Readiness", status=WARN, weight=WEIGHT,
            fraction=None,
            summary=f"Received an anti-bot interstitial (HTTP {pf.status}) — the page's real structure "
                    "can't be assessed from outside; allowlisted agents may see full content.",
            details=["• " + n for n in llms_notes],
            evidence={"status": pf.status, "shell": True, "llms": llms_ev},
            remediation="Verify from server logs whether real agents get the full HTML.",
        )

    # AX-tree readiness — DOM approximation, enriched by a real snapshot if present.
    ax_frac, ax_notes, ax_ev = _dom_ax_readiness(html)
    if pf.ax_tree:
        snap_name_frac, snap_note, snap_ev = _ax_snapshot_enrich(pf.ax_tree)
        if snap_name_frac is not None:
            # Blend the authoritative name signal into the readiness score.
            ax_frac = round(ax_frac * 0.7 + (
                0.25 * ax_ev["frac_landmarks"] + 0.30 * ax_ev["frac_controls"]
                + 0.25 * snap_name_frac + 0.20 * ax_ev["frac_headings"]) * 0.3, 3)
            ax_notes.append(snap_note)
            ax_ev.update(snap_ev)

    # Combine. If llms.txt was unreachable, score the AX part alone (renormalised).
    if llms_frac is None:
        combined = ax_frac
        detail_llms = ["• llms.txt: not checked (unreachable)."]
    else:
        combined = round(_AX_SHARE * ax_frac + _LLMS_SHARE * llms_frac, 3)
        detail_llms = ["• " + n for n in llms_notes]

    status = _verdict(combined)
    src = "rendered DOM" if pf.rendered_html else ("Common Crawl archive" if pf.cc_fallback else "server HTML")
    ax_pct = round(ax_frac * 100)

    summary = (f"Agent-navigability {ax_pct}%"
               + (f", llms.txt {'present & valid' if (llms_frac or 0) >= 0.9 else 'present' if (llms_frac or 0) > 0 else 'missing'}"
                  if llms_frac is not None else "")
               + ". "
               + ("Well-structured for autonomous AI agents." if status == PASS
                  else "Some gaps for agents that navigate and act on the page." if status == WARN
                  else "Hard for autonomous agents to traverse — key controls aren't semantic."))

    remediation_bits = []
    if ax_ev.get("fake_controls"):
        remediation_bits.append("replace <div onclick> with real <button>/<a href> (or add role + accessible name)")
    if not ax_ev.get("has_main"):
        remediation_bits.append("wrap primary content in semantic landmarks (<main>, <nav>)")
    if ax_ev.get("checkable") and ax_ev.get("named", 0) < ax_ev["checkable"]:
        remediation_bits.append("give every interactive element an accessible name (text or aria-label)")
    if llms_frac is not None and llms_frac < 0.9:
        remediation_bits.append("publish a well-formed /llms.txt (llmstxt.org): H1 title + [name](url) links, served as text/plain")
    remediation = ("For agent-driven journeys (buy, book, sign up): " + "; ".join(remediation_bits) + "."
                   ) if remediation_bits else ""

    return CheckResult(
        id="agentic", title="Agentic Readiness", status=status, weight=WEIGHT,
        fraction=combined,
        summary=summary,
        details=[f"• AX-tree read from the {src}"] + ["• " + n for n in ax_notes] + detail_llms,
        evidence={"ax": ax_ev, "ax_frac": ax_frac, "llms": llms_ev, "llms_frac": llms_frac},
        remediation=remediation,
    )
