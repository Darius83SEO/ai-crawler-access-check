"""Check 2 — Common Crawl Index coverage audit.

Permission (check 1) is not the same as reality. This queries the public
Common Crawl Index (index.commoncrawl.org) to confirm the domain is actually
in the archive, how recently, and roughly how many pages.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from ..http import BROWSER_UA
from ..models import CheckResult, FAIL, PASS, WARN, ERROR

WEIGHT = 25.0

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
# How many of the most recent monthly crawls to probe for presence.
RECENT_CRAWLS = 3
# Cap on records pulled per crawl when estimating page count.
PAGE_CAP = 1000


def get_recent_crawls(n: int = RECENT_CRAWLS) -> List[Dict]:
    r = requests.get(COLLINFO, headers={"User-Agent": BROWSER_UA}, timeout=20)
    r.raise_for_status()
    return r.json()[:n]


def _query_crawl(cdx_api: str, domain: str) -> Tuple[int, Optional[str], bool]:
    """Return (unique_page_count, latest_timestamp_iso, capped)."""
    url = f"{cdx_api}?url={domain}/*&output=json&limit={PAGE_CAP}&filter=status:200"
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    if r.status_code == 404 or not r.text.strip():
        return 0, None, False
    r.raise_for_status()
    seen = set()
    latest = None
    n = 0
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        seen.add(rec.get("urlkey", rec.get("url", n)))
        ts = rec.get("timestamp")
        if ts and (latest is None or ts > latest):
            latest = ts
    capped = n >= PAGE_CAP
    return len(seen), latest, capped


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%d %b %Y")
    except (ValueError, TypeError):
        return ts or "?"


def run(domain: str) -> CheckResult:
    details: List[str] = []
    evidence: Dict = {}
    try:
        crawls = get_recent_crawls()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            id="index", title="CC Index Coverage", status=ERROR, weight=WEIGHT,
            summary=f"Could not reach the Common Crawl Index ({exc}).",
            remediation="Retry later — index.commoncrawl.org may be rate-limiting.",
        )

    first_seen_crawl = None
    total_pages = 0
    latest_ts = None
    capped_any = False

    for c in crawls:
        try:
            pages, latest, capped = _query_crawl(c["cdx-api"], domain)
        except Exception as exc:  # noqa: BLE001
            details.append(f"• {c['id']}: query failed ({exc})")
            continue
        capped_any = capped_any or capped
        label = f"{pages}{'+' if capped else ''} pages"
        if pages > 0:
            details.append(f"• {c['id']}: {label}, last seen {_fmt_ts(latest)}")
            if first_seen_crawl is None:
                first_seen_crawl = c["id"]
                total_pages = pages
                latest_ts = latest
        else:
            details.append(f"• {c['id']}: not present")

    evidence.update({
        "crawls_checked": [c["id"] for c in crawls],
        "present_in": first_seen_crawl,
        "pages": total_pages,
        "pages_capped": capped_any,
        "latest_timestamp": latest_ts,
    })

    if first_seen_crawl is None:
        return CheckResult(
            id="index", title="CC Index Coverage", status=FAIL, weight=WEIGHT,
            fraction=0.0,
            summary=f"Not found in the {len(crawls)} most recent monthly crawls.",
            details=details, evidence=evidence,
            remediation=(
                "If access is open (check 1) the domain still isn't being captured. Improve crawl "
                "discovery: earn links from well-connected sites (see centrality), submit a sitemap, "
                "and confirm key pages aren't JS-only (see rendering). Presence accrues over future crawls."
            ),
        )

    pages_label = f"{total_pages}{'+' if capped_any else ''}"
    if total_pages >= 50:
        return CheckResult(
            id="index", title="CC Index Coverage", status=PASS, weight=WEIGHT,
            fraction=1.0,
            summary=f"In the archive: ~{pages_label} pages in {first_seen_crawl} (last seen {_fmt_ts(latest_ts)}).",
            details=details, evidence=evidence,
        )
    # present but shallow
    frac = 0.5 if total_pages >= 10 else 0.35
    return CheckResult(
        id="index", title="CC Index Coverage", status=WARN, weight=WEIGHT,
        fraction=frac,
        summary=f"Present but shallow: only ~{pages_label} pages captured in {first_seen_crawl}.",
        details=details, evidence=evidence,
        remediation=(
            "The domain is reachable but barely crawled. Raise crawl priority via connectivity "
            "(centrality), internal linking and a clean sitemap so more pages land in monthly snapshots."
        ),
    )
