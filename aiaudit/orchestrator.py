"""Run all six checks and assemble an AuditReport.

The checks are independent network calls, so they run concurrently (the slow
third-party centrality API no longer serializes everything). Results are cached
per domain with a TTL, which makes repeated audits instant and shields the
upstream APIs (Common Crawl, the rank checker) under heavier traffic.
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Callable, List, Optional

from .checks import agentic, cc_index, centrality, rendering, robots_waf, structured_data
from .models import AuditReport, CheckResult, ERROR
from .safety import assert_public_host
from .util import normalize_domain

# Order matters for display: most decisive to most strategic.
CHECKS: List[Callable[[str], CheckResult]] = [
    robots_waf.run,       # 1 · access (weight 30)
    cc_index.run,         # 2 · index coverage (weight 25)
    centrality.run,       # 3 · harmonic centrality (weight 15)
    structured_data.run,  # 4 · structured data (weight 15)
    rendering.run,        # 5 · server-side rendering (weight 15)
    agentic.run,          # 6 · agentic readiness: AX tree + llms.txt (weight 10)
]

# domain -> (expiry_epoch, AuditReport)
_RESULTS: dict = {}
_RESULTS_TTL = 1800  # 30 minutes


def clear_results_cache() -> None:
    _RESULTS.clear()


def _safe(check: Callable[[str], CheckResult], domain: str) -> CheckResult:
    try:
        return check(domain)
    except Exception as exc:  # noqa: BLE001 — a check must never crash the audit
        name = check.__module__.split(".")[-1]
        return CheckResult(id=name, title=name, status=ERROR, weight=0.0,
                           summary=f"Unexpected error: {exc}")


def run_audit(raw_domain: str,
              progress: Optional[Callable[[int, str], None]] = None,
              fresh: bool = False) -> AuditReport:
    domain = normalize_domain(raw_domain)
    if not domain:
        raise ValueError("Empty or invalid domain.")

    # SSRF guard: never let a public visitor point the fetchers at localhost,
    # an internal service or the cloud metadata endpoint.
    assert_public_host(domain)

    if not fresh:
        hit = _RESULTS.get(domain)
        if hit and hit[0] > time.time():
            if progress:
                progress(len(CHECKS), "cached")
            return hit[1]

    results: List[Optional[CheckResult]] = [None] * len(CHECKS)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CHECKS)) as ex:
        futures = {ex.submit(_safe, c, domain): i for i, c in enumerate(CHECKS)}
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()
            done += 1
            if progress:
                progress(done, "")

    report = AuditReport(domain=domain, checks=[r for r in results if r is not None])
    _RESULTS[domain] = (time.time() + _RESULTS_TTL, report)
    return report
