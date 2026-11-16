"""Check 2 — Common Crawl Index coverage audit.

Permission (check 1) is not the same as reality. This reads the public
Common Crawl Index to confirm the domain is actually in the archive, how
recently, and roughly how many pages — from the index files on
data.commoncrawl.org, falling back to the index server (index.commoncrawl.org).
"""

from __future__ import annotations

import json
import re
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from ..http import BROWSER_UA
from ..models import CheckResult, FAIL, PASS, WARN, ERROR

WEIGHT = 20.0

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
# How many of the most recent monthly crawls to probe for presence.
RECENT_CRAWLS = 3
# Cap on records pulled per crawl when estimating page count.
PAGE_CAP = 1000


def get_recent_crawls(n: int = RECENT_CRAWLS) -> List[Dict]:
    r = requests.get(COLLINFO, headers={"User-Agent": BROWSER_UA}, timeout=20)
    r.raise_for_status()
    return r.json()[:n]


# --- Fallback: the same index, read as files from data.commoncrawl.org ------
# index.commoncrawl.org refuses connections from some cloud IP ranges
# (Streamlit Cloud included). The index it serves is published as plain files:
# a sorted cluster.idx (one line per block of ~3000 records) plus gzipped
# cdx-NNNNN.gz shards. HTTP range requests let us binary-search cluster.idx
# and download only the block(s) that hold the domain.
DATA_BASE = "https://data.commoncrawl.org/cc-index/collections/"
MAX_BLOCKS = 3


def _data_get(url: str, start: Optional[int] = None, end: Optional[int] = None) -> requests.Response:
    headers = {"User-Agent": BROWSER_UA}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r


def get_recent_crawls_from_data(n: int = RECENT_CRAWLS) -> List[Dict]:
    html = _data_get(DATA_BASE + "index.html").text
    ids = sorted(set(re.findall(r"CC-MAIN-\d{4}-\d{2}", html)), reverse=True)
    return [{"id": i, "cdx-api": None} for i in ids[:n]]


def _surt_prefix(domain: str) -> str:
    host = domain.lower().strip().strip("/")
    if host.startswith("www."):
        host = host[4:]
    return ",".join(reversed(host.split("."))) + ")/"


def _query_crawl_data(crawl_id: str, domain: str) -> Tuple[int, Optional[str], bool]:
    """Same output as _query_crawl, read from the zipnum index files."""
    idx_url = f"{DATA_BASE}{crawl_id}/indexes/cluster.idx"
    size = int(requests.head(idx_url, headers={"User-Agent": BROWSER_UA}, timeout=20)
               .headers["Content-Length"])
    prefix = _surt_prefix(domain)

    def line_at(offset: int) -> Tuple[int, str]:
        """First complete line starting at or after `offset`: (line_start, line)."""
        chunk = _data_get(idx_url, offset, min(offset + 4095, size - 1)).content
        skip = 0 if offset == 0 else chunk.index(b"\n") + 1
        line = chunk[skip:].split(b"\n", 1)[0].decode("utf-8", "replace")
        return offset + skip, line

    # binary search: last line whose key sorts before the prefix
    lo, hi = 0, size
    while hi - lo > 4096:
        mid = (lo + hi) // 2
        start, line = line_at(mid)
        if line.split(" ", 1)[0] < prefix:
            lo = start
        else:
            hi = mid
    # `lo` is a line start; read the cluster lines from there on
    tail = _data_get(idx_url, lo, min(lo + 65535, size - 1)).content.decode("utf-8", "replace")
    entries = []
    for ln in tail.split("\n")[:-1]:          # last piece may be cut mid-line
        parts = ln.split("\t")
        if len(parts) >= 4:
            entries.append((parts[0].split(" ", 1)[0], parts[1], int(parts[2]), int(parts[3])))
    # a block holds keys from its own key up to the next block's key, so the
    # domain's records start in the last block keyed before the prefix
    first = next((i for i, e in enumerate(entries) if e[0] >= prefix), len(entries))
    blocks = entries[max(first - 1, 0):first]
    more = False                               # domain continues past the blocks we read
    for e in entries[first:]:
        if not e[0].startswith(prefix):
            break
        if len(blocks) >= MAX_BLOCKS:
            more = True
            break
        blocks.append(e)

    seen, latest, n = set(), None, 0
    for _, shard, off, length in blocks:
        data = _data_get(f"{DATA_BASE}{crawl_id}/indexes/{shard}", off, off + length - 1).content
        for raw in zlib.decompress(data, 16 + zlib.MAX_WBITS).decode("utf-8", "replace").splitlines():
            key, ts, js = (raw.split(" ", 2) + ["", ""])[:3]
            if not key.startswith(prefix):
                continue
            try:
                rec = json.loads(js)
            except json.JSONDecodeError:
                continue
            if rec.get("status") != "200":
                continue
            n += 1
            seen.add(key)
            if latest is None or ts > latest:
                latest = ts
            if n >= PAGE_CAP:
                return len(seen), latest, True
    return len(seen), latest, more


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
    # The index files on data.commoncrawl.org are the primary source: the index
    # server often 504s and refuses connections from cloud hosts.
    from_data = True
    try:
        crawls = get_recent_crawls_from_data()
    except Exception:  # noqa: BLE001
        try:
            crawls = get_recent_crawls()
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                id="index", title="CC Index Coverage", status=ERROR, weight=WEIGHT,
                summary=f"Could not reach the Common Crawl Index nor its data files ({exc}).",
                remediation="Retry later — Common Crawl may be rate-limiting.",
            )
        from_data = False

    def query(c):
        if from_data:
            return _query_crawl_data(c["id"], domain)
        return _query_crawl(c["cdx-api"], domain)

    def query_with_retry(c):
        try:
            return query(c)
        except Exception:  # noqa: BLE001 — transient errors under load; retry once
            return query(c)

    with ThreadPoolExecutor(max_workers=len(crawls) or 1) as pool:
        futures = [pool.submit(query_with_retry, c) for c in crawls]

    first_seen_crawl = None
    total_pages = 0
    latest_ts = None
    capped_any = False

    failed = 0
    for c, fut in zip(crawls, futures):
        try:
            pages, latest, capped = fut.result()
        except Exception as exc:  # noqa: BLE001
            failed += 1
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

    # A timed-out query is not evidence of absence: if any crawl couldn't be read
    # and none showed the domain, report "not determined" and keep it out of the score.
    if first_seen_crawl is None and failed:
        return CheckResult(
            id="index", title="CC Index Coverage", status=ERROR, weight=WEIGHT,
            summary=(f"The Common Crawl Index didn't answer for {failed} of {len(crawls)} crawls "
                     "(timeouts) — coverage can't be confirmed right now, so it's excluded from the score."),
            details=details, evidence=evidence,
            remediation="Retry in a few minutes — Common Crawl is often overloaded.",
        )

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
