"""Common Crawl archive fallback.

When a live fetch is gated (401/403) we can't see what the page actually
serves — but CCBot already crawled it from its own (often allowlisted) IPs.
This module retrieves the homepage HTML straight from the Common Crawl WARC
archive: find the latest captured record via the CDX index, range-fetch just
that record from S3, gunzip it, and pull the HTML body out of the WARC/HTTP
envelope. The result is exactly the server HTML the real crawler received.
"""

from __future__ import annotations

import json
import zlib
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

from .http import BROWSER_UA

DATA_BASE = "https://data.commoncrawl.org/"
_CRAWLS_TO_TRY = 3  # search recent crawls for an archived capture (reliability first)


def _gunzip(data: bytes) -> bytes:
    # wbits=31 → gzip header, and it stops cleanly at the end of one stream
    # (tolerant of any trailing bytes a range request may include).
    return zlib.decompress(data, 31)


def _recent_crawls(n: int):
    # Imported lazily to avoid any import cycle with the checks package.
    from .checks.cc_index import get_recent_crawls, get_recent_crawls_from_data
    try:
        return get_recent_crawls_from_data(n)
    except Exception:
        return get_recent_crawls(n)


def _crawl_records(c: Dict, domain: str):
    """Status-200 CDX records for the domain in one crawl: from the index files
    on data.commoncrawl.org, or from the index server when that is the source."""
    from .checks.cc_index import iter_records_from_data
    if not c.get("cdx-api"):
        recs = []
        for _, rec in iter_records_from_data(c["id"], domain):
            recs.append(rec)
            if len(recs) >= 200:
                break
        return recs
    url = f"{c['cdx-api']}?url={domain}/*&output=json&limit=200&filter=status:200"
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=15)
    if r.status_code != 200 or not r.text.strip():
        return []
    recs = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return recs


def _is_homepage(url: str) -> bool:
    # The bare homepage is often only a 301/403 in CC, while a query-string
    # variant (e.g. /?hsLang=en) is the captured 200. Treat path "/" as home.
    try:
        return urlparse(url).path in ("", "/")
    except Exception:
        return False


def _find_capture(domain: str) -> Optional[Dict]:
    """Most recent status-200 capture with a real WARC location, preferring
    the homepage. Uses the /* wildcard because the bare homepage is frequently
    only a redirect/403 in the archive."""
    for c in _recent_crawls(_CRAWLS_TO_TRY):
        try:
            recs = [rec for rec in _crawl_records(c, domain)
                    if "/warc/" in rec.get("filename", "")]  # skip redirects/diagnostics
        except Exception:
            continue
        if recs:
            homes = [x for x in recs if _is_homepage(x.get("url", ""))]
            pool = homes or recs
            pool.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            rec = pool[0]
            rec["_crawl"] = c["id"]
            return rec
    return None


def _extract_html(record: bytes) -> Optional[str]:
    """Pull the HTML body out of a WARC 'response' record."""
    parts = record.split(b"\r\n\r\n", 2)  # WARC hdrs | HTTP hdrs | body
    if len(parts) < 3:
        return None
    http_headers = parts[1].decode("latin-1", "ignore").lower()
    body = parts[2]
    if "content-encoding: gzip" in http_headers or body[:2] == b"\x1f\x8b":
        try:
            body = _gunzip(body)
        except Exception:
            pass
    return body.decode("utf-8", "ignore")


def fetch_homepage_from_cc(domain: str) -> Optional[Tuple[str, Dict]]:
    """Return (html, info) from the Common Crawl archive, or None."""
    rec = _find_capture(domain)
    if not rec:
        return None
    try:
        off = int(rec["offset"])
        ln = int(rec["length"])
        r = requests.get(
            DATA_BASE + rec["filename"],
            headers={"User-Agent": BROWSER_UA, "Range": f"bytes={off}-{off + ln - 1}"},
            timeout=25,
        )
        if r.status_code not in (200, 206) or not r.content:
            return None
        html = _extract_html(_gunzip(r.content))
        if not html:
            return None
        info = {"crawl": rec.get("_crawl"), "timestamp": rec.get("timestamp"),
                "url": rec.get("url")}
        return html, info
    except Exception:
        return None
