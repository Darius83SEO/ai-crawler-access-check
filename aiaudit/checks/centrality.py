"""Check 3 — Harmonic Centrality check.

Reads the domain's position in the Common Crawl Web Graph via the community
CC Rank Checker (webgraph.metehan.ai). Low centrality means the domain is
deprioritised in crawl budget: even with open access it gets crawled shallowly
and infrequently. The guide frames this as a strategic risk + link-building
target, not a hard blocker — so it never zeroes the score on its own.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import requests

from ..http import BROWSER_UA
from ..models import CheckResult, PASS, WARN, INFO, ERROR

WEIGHT = 15.0

API = "https://webgraph.metehan.ai/api/check"
# The community API is slow (8–17s warm, more on a cold start), so use a
# generous read timeout and retry transient timeouts before giving up.
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 55
_ATTEMPTS = 3

# Snapshots oldest -> newest; we report the most recent one available.
SNAPSHOT_ORDER = [
    "data_2023_mid", "data_2023_late", "data_2024_early", "data_2024_mid",
    "data_2025_mid", "data_2025_late", "data_2026_jan",
]
SNAPSHOT_LABEL = {
    "data_2023_mid": "mid 2023", "data_2023_late": "late 2023",
    "data_2024_early": "early 2024", "data_2024_mid": "mid 2024",
    "data_2025_mid": "mid 2025", "data_2025_late": "late 2025",
    "data_2026_jan": "Jan 2026",
}


def fetch(domain: str) -> Optional[Dict]:
    """Query the CC Rank Checker, retrying transient slowness/timeouts."""
    last_exc: Optional[Exception] = None
    for attempt in range(_ATTEMPTS):
        try:
            r = requests.get(
                API, params={"domains": domain},
                headers={"User-Agent": BROWSER_UA},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            r.raise_for_status()
            results = r.json().get("results") or []
            return results[0] if results else None
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < _ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))  # 2s, 4s backoff
        except Exception as exc:  # noqa: BLE001 — non-retryable (bad JSON, HTTP 4xx/5xx)
            raise exc
    raise last_exc if last_exc else RuntimeError("centrality fetch failed")


def _latest_snapshot(row: Dict) -> Tuple[Optional[str], Optional[Dict]]:
    for key in reversed(SNAPSHOT_ORDER):
        snap = row.get(key)
        if isinstance(snap, dict) and snap.get("hc_rank"):
            return key, snap
    return None, None


def run(domain: str) -> CheckResult:
    try:
        row = fetch(domain)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            id="centrality", title="Harmonic Centrality", status=ERROR, weight=WEIGHT,
            summary=f"Could not read the CC Web Graph rank ({exc}).",
            remediation="Check manually at webgraph.metehan.ai; the community API may be busy.",
        )

    if not row:
        return CheckResult(
            id="centrality", title="Harmonic Centrality", status=WARN, weight=WEIGHT,
            fraction=0.2,
            summary="Domain not found in the Common Crawl Web Graph — effectively at the edge.",
            remediation=(
                "A domain absent from the graph is poorly connected to the web core. Earn links from "
                "central, authoritative sites to enter the graph and lift crawl priority."
            ),
            evidence={"found": False},
        )

    snap_key, snap = _latest_snapshot(row)
    tier = row.get("tier") or {}
    if not snap:
        return CheckResult(
            id="centrality", title="Harmonic Centrality", status=INFO, weight=WEIGHT,
            fraction=0.3,
            summary=f"In the graph (tier {tier.get('name','?')}) but no recent harmonic-centrality rank.",
            evidence={"tier": tier, "row": row},
        )

    hc = snap["hc_rank"]
    pr = snap.get("pr_rank")
    hosts = snap.get("hosts")
    when = SNAPSHOT_LABEL.get(snap_key, snap_key)
    tier_name = tier.get("name", "?")
    tier_emoji = tier.get("emoji", "")

    details = [
        f"• Tier: {tier_emoji} {tier_name}",
        f"• Harmonic Centrality rank: #{hc:,} ({when} snapshot)",
    ]
    if pr:
        details.append(f"• PageRank rank: #{pr:,}")
    if hosts:
        details.append(f"• Hosts under the domain: {hosts:,}")

    evidence = {"tier": tier, "snapshot": snap_key, "hc_rank": hc,
                "pr_rank": pr, "hosts": hosts}

    # Lower rank number = closer to the core.
    if hc <= 100_000:
        return CheckResult(
            id="centrality", title="Harmonic Centrality", status=PASS, weight=WEIGHT,
            fraction=1.0,
            summary=f"Well connected — HC rank #{hc:,} ({tier_name}). Crawled often and deeply.",
            details=details, evidence=evidence,
        )
    if hc <= 1_000_000:
        return CheckResult(
            id="centrality", title="Harmonic Centrality", status=WARN, weight=WEIGHT,
            fraction=0.6,
            summary=f"Mid connectivity — HC rank #{hc:,} ({tier_name}). Room to raise crawl priority.",
            details=details, evidence=evidence,
            remediation=(
                "Build links from core-connected, authoritative domains (connectivity, not raw volume) "
                "to move toward the web core and earn deeper, more frequent crawling."
            ),
        )
    return CheckResult(
        id="centrality", title="Harmonic Centrality", status=WARN, weight=WEIGHT,
        fraction=0.3,
        summary=f"Low centrality — HC rank #{hc:,} ({tier_name}). Deprioritised for crawl budget.",
        details=details, evidence=evidence,
        remediation=(
            "Treat low centrality as a strategic risk. Target link building at sites near the web core; "
            "a single link from a central domain lifts centrality more than dozens from isolated ones."
        ),
    )
