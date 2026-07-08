"""AI Access & Agent Audit — checks 1–5 implement Common Crawl's five-check
AI Visibility Audit (retrievability for training & retrieval crawlers); check 6
adds an agentic-readiness read (accessibility tree + llms.txt) for the
autonomous AI agents that navigate and act on a user's behalf.

Public entry point:
    from aiaudit.orchestrator import run_audit
    report = run_audit("example.com")
"""

__version__ = "1.1.0"
