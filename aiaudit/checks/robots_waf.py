"""Check 1 — CCBot access check.

Two layers, exactly as the guide describes:
  a) robots.txt — is any AI crawler disallowed at the root?
  b) the edge/WAF — does the server actually serve the CCBot user agent,
     or does a firewall return 403 by user agent even when robots.txt is clean?
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..http import BLOCK_CODES, BROWSER_UA, CCBOT_UA, GPTBOT_UA, get
from ..models import CheckResult, FAIL, PASS, WARN, ERROR
from ..util import site_url

WEIGHT = 30.0

# AI / training crawlers the audit cares about (the four named in the guide
# plus the other common ones a CDN rule typically toggles).
AI_BOTS = [
    "CCBot", "GPTBot", "ClaudeBot", "Google-Extended",
    "anthropic-ai", "PerplexityBot", "Applebot-Extended",
    "Bytespider", "Amazonbot", "Meta-ExternalAgent", "OAI-SearchBot",
]
# CCBot is the one that actually feeds training data, so it is decisive.
DECISIVE_BOTS = {"ccbot", "gptbot", "claudebot", "google-extended"}


# ── robots.txt parsing ─────────────────────────────────────────
def parse_robots(text: str) -> List[Tuple[List[str], List[Tuple[str, str]]]]:
    """Parse robots.txt into groups of (user-agents, rules).

    Consecutive ``User-agent`` lines share the rule block that follows them.
    """
    groups: List[Tuple[List[str], List[Tuple[str, str]]]] = []
    agents: List[str] = []
    rules: List[Tuple[str, str]] = []
    last_was_rule = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if last_was_rule and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_rule = False
        elif field in ("allow", "disallow"):
            rules.append((field, value))
            last_was_rule = True
    if agents or rules:
        groups.append((agents, rules))
    return groups


def _group_for(groups, ua: str):
    ua = ua.lower()
    specific = star = None
    for agents, rules in groups:
        if ua in agents:
            specific = (agents, rules)
        if "*" in agents:
            star = (agents, rules)
    return specific if specific is not None else star


def _blocked_at_root(group) -> bool:
    """True if the group disallows the whole site at '/'.

    An explicit ``Allow: /`` in the same group overrides a ``Disallow: /``.
    An empty ``Disallow:`` means "allow everything".
    """
    if not group:
        return False
    disallow_root = allow_root = False
    for typ, path in group[1]:
        if typ == "disallow" and path == "/":
            disallow_root = True
        if typ == "allow" and path == "/":
            allow_root = True
    return disallow_root and not allow_root


def analyze_robots(text: str) -> Dict[str, bool]:
    """Map each AI bot -> blocked-at-root boolean."""
    groups = parse_robots(text)
    result = {}
    for bot in AI_BOTS:
        result[bot] = _blocked_at_root(_group_for(groups, bot))
    return result


# ── WAF / edge test ────────────────────────────────────────────
def _status(domain: str, ua: str) -> Tuple[int, str]:
    for scheme in ("https", "http"):
        try:
            r = get(site_url(domain, scheme), user_agent=ua, timeout=15)
            return r.status_code, r.url
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
    return -1, last


def run(domain: str) -> CheckResult:
    details: List[str] = []
    evidence: Dict = {}

    # a) robots.txt
    robots_map = {}
    robots_status = None
    robots_text = ""
    try:
        for scheme in ("https", "http"):
            try:
                r = get(f"{scheme}://{domain}/robots.txt", user_agent=BROWSER_UA, timeout=15)
                robots_status = r.status_code
                if r.status_code < 400 and r.text.strip():
                    robots_text = r.text
                break
            except Exception:
                continue
        robots_map = analyze_robots(robots_text) if robots_text else {b: False for b in AI_BOTS}
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            id="access", title="CCBot Access Check", status=ERROR, weight=WEIGHT,
            summary=f"Could not fetch robots.txt ({exc}).",
            remediation="Make sure the domain resolves and serves over HTTP(S).",
        )

    blocked_bots = [b for b, blk in robots_map.items() if blk]
    decisive_blocked = [b for b in blocked_bots if b.lower() in DECISIVE_BOTS]
    evidence["robots_blocked"] = blocked_bots
    evidence["robots_status"] = robots_status

    if not robots_text:
        details.append("• No robots.txt found (or empty) → nothing is disallowed there. Open by default.")
    elif blocked_bots:
        details.append(f"• robots.txt disallows at '/': {', '.join(blocked_bots)}")
    else:
        details.append("• robots.txt present, no AI-crawler disallow at '/'.")

    # b) edge / WAF UA test
    ccbot_code, ccbot_final = _status(domain, CCBOT_UA)
    browser_code, _ = _status(domain, BROWSER_UA)
    gptbot_code, _ = _status(domain, GPTBOT_UA)
    evidence.update({
        "status_ccbot": ccbot_code, "status_browser": browser_code,
        "status_gptbot": gptbot_code, "final_url": ccbot_final,
    })
    details.append(f"• Edge response — browser UA: {browser_code} · CCBot UA: {ccbot_code} · GPTBot UA: {gptbot_code}")

    browser_ok = isinstance(browser_code, int) and 200 <= browser_code < 400
    ccbot_ok = isinstance(ccbot_code, int) and 200 <= ccbot_code < 400
    ua_discrimination = browser_ok and ccbot_code in BLOCK_CODES

    # ── verdict ────────────────────────────────────────────────
    if ccbot_code == -1 and browser_code == -1:
        return CheckResult(
            id="access", title="CCBot Access Check", status=ERROR, weight=WEIGHT,
            summary="The site did not respond on HTTP or HTTPS.",
            details=details, evidence=evidence,
            remediation="Confirm the domain is live and reachable.",
        )

    # Hard fail: robots disallows a decisive bot, OR the edge serves a browser
    # but 403s CCBot (clear UA-based block).
    if decisive_blocked or ua_discrimination:
        reason = []
        if decisive_blocked:
            reason.append(f"robots.txt blocks {', '.join(decisive_blocked)}")
        if ua_discrimination:
            reason.append(f"the edge/WAF returns {ccbot_code} to CCBot while a browser gets {browser_code}")
        return CheckResult(
            id="access", title="CCBot Access Check", status=FAIL, weight=WEIGHT,
            fraction=0.0, summary="AI crawlers are blocked — " + "; ".join(reason) + ".",
            details=details, evidence=evidence,
            remediation=(
                "Allow the AI user agents. In robots.txt remove the disallow rules for "
                "CCBot/GPTBot/ClaudeBot/Google-Extended. If the block is at the edge, turn off the "
                "'block AI bots' rule in your CDN/WAF dashboard — a clean robots.txt is useless if the "
                "firewall 403s CCBot."
            ),
        )

    # The edge refuses CCBot (and the browser too): aggressive bot-protection
    # blocks automated requests. We can't confirm access — never call this open.
    if ccbot_code in BLOCK_CODES:
        return CheckResult(
            id="access", title="CCBot Access Check", status=WARN, weight=WEIGHT,
            fraction=0.4,
            summary=(f"The edge returns HTTP {ccbot_code} to the CCBot user agent — bot-protection "
                     "refuses automated requests, so CCBot access can't be confirmed."),
            details=details, evidence=evidence,
            remediation=(
                "The site blocks automated requests at the edge (often by datacenter IP, not just UA). "
                "Verify from server logs whether the genuine CCBot (its published IP ranges) is allowed; "
                "if not, allowlist AI crawlers in the CDN/WAF. Note: the domain may still appear in the "
                "index if real CCBot's IPs are treated differently than this test."
            ),
        )

    if not ccbot_ok:
        return CheckResult(
            id="access", title="CCBot Access Check", status=WARN, weight=WEIGHT,
            fraction=0.5,
            summary=f"CCBot received an unexpected HTTP {ccbot_code} — access not clearly confirmed.",
            details=details, evidence=evidence,
            remediation="Check why the homepage returns this status to the CCBot user agent.",
        )

    if blocked_bots:  # CCBot OK, but some non-decisive AI bots blocked
        return CheckResult(
            id="access", title="CCBot Access Check", status=WARN, weight=WEIGHT,
            fraction=0.6, summary=f"CCBot is allowed, but some AI crawlers are blocked: {', '.join(blocked_bots)}.",
            details=details, evidence=evidence,
            remediation="Decide deliberately whether those bots should be blocked; if not, remove their disallow rules.",
        )

    return CheckResult(
        id="access", title="CCBot Access Check", status=PASS, weight=WEIGHT,
        fraction=1.0, summary=f"No AI-crawler disallow and CCBot gets HTTP {ccbot_code}. The door is open.",
        details=details, evidence=evidence,
        remediation="",
    )
