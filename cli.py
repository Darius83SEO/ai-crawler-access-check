"""Command-line runner for the AI Access & Agent Audit.

Usage:
    python cli.py example.com
    python cli.py example.com --html scorecard.html
    python cli.py example.com --json
"""

from __future__ import annotations

import argparse
import json
import sys

from aiaudit.models import STATUS_ICON
from aiaudit.orchestrator import run_audit, CHECKS
from aiaudit.scorecard import render_html

N_CHECKS = len(CHECKS)


def main() -> int:
    # Windows consoles default to cp1252, which can't encode the status icons
    # (✓ ✕) or typographic dashes/× in summaries — force UTF-8 so output (and
    # redirected --json) never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — older/odd streams without reconfigure
            pass

    ap = argparse.ArgumentParser(
        description="AI Access & Agent Audit — 8-check training, live-retrieval + agentic audit")
    ap.add_argument("domain", help="domain or URL, e.g. example.com")
    ap.add_argument("--html", metavar="FILE", help="write the HTML scorecard to FILE")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args()

    def progress(i, name):
        if not args.json:
            print(f"  [{i}/{N_CHECKS}] {name}…", file=sys.stderr)

    report = run_audit(args.domain, progress=progress)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        g = report.grade
        print()
        print(f"  AI ACCESS & AGENT SCORECARD — {report.domain}")
        print(f"  {'='*52}")
        print(f"  SCORE: {report.score}/100   GRADE: {g['grade']} ({g['label']})")
        print(f"  Passed {report.n_pass} · Warnings {report.n_warn} · Failed {report.n_fail}")
        print(f"  {'-'*52}")
        for i, c in enumerate(report.checks, 1):
            icon = STATUS_ICON.get(c.status, "?")
            print(f"  {icon} {i}. {c.title}  [{c.status.upper()}]  {c.points}/{c.weight}")
            print(f"      {c.summary}")
            if c.remediation:
                print(f"      Fix: {c.remediation}")
        print()

    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(report))
        print(f"  Scorecard written to {args.html}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
