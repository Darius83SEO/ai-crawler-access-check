"""AI Access & Agent Audit — Streamlit front-end.

Enter a domain, run the six-check audit, and get a polished scorecard you can
show a client or download as a one-page HTML/PDF. It answers two questions in
one: are you *retrievable* by the crawlers that train and feed the models, and
are you *actionable* by the autonomous agents that navigate on a user's behalf?
"""

from __future__ import annotations

import threading
import time
from collections import deque

import streamlit as st
import streamlit.components.v1 as components

from aiaudit.orchestrator import run_audit, CHECKS
from aiaudit.scorecard import render_html
from aiaudit.verify import verify_ccbot_ip

N_CHECKS = len(CHECKS)

# ── Abuse guards for the public deployment ────────────────────────────
# Per session: keep one visitor from spamming distinct domains. Global: shield
# the free third-party APIs (Common Crawl index, community centrality) from a
# single container being hammered. Both are sliding windows; in-memory is fine
# because each free host runs one container.
_SESSION_MAX, _SESSION_WINDOW = 8, 300       # 8 audits / 5 min per session
_GLOBAL_MAX, _GLOBAL_WINDOW = 40, 60         # 40 audits / min across all sessions
_GLOBAL_HITS: deque = deque()
_GLOBAL_LOCK = threading.Lock()


def _rate_limited() -> str:
    """Return a user-facing message if this request should be blocked, else ''."""
    now = time.time()
    hits = st.session_state.setdefault("_audit_hits", [])
    hits[:] = [t for t in hits if now - t < _SESSION_WINDOW]
    if len(hits) >= _SESSION_MAX:
        wait = int(_SESSION_WINDOW - (now - hits[0]))
        return f"You've run several audits in a row — give it about {wait}s and try again."
    with _GLOBAL_LOCK:
        while _GLOBAL_HITS and now - _GLOBAL_HITS[0] >= _GLOBAL_WINDOW:
            _GLOBAL_HITS.popleft()
        if len(_GLOBAL_HITS) >= _GLOBAL_MAX:
            return "The tool is busy right now (protecting the free data sources) — try again in a minute."
        _GLOBAL_HITS.append(now)
    hits.append(now)
    return ""

st.set_page_config(page_title="AI Access & Agent Audit", page_icon="🛰️", layout="centered")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;800&display=swap');
  html, body, [class*="css"], .stMarkdown, .stButton, h1, h2, h3 { font-family:'Montserrat',sans-serif; }
  .hero h1 { font-size:30px; font-weight:800; color:#1a2b42; margin-bottom:4px; letter-spacing:-.5px;}
  .hero .sub { color:#64748b; font-size:14px; }
  .hero .tag { display:inline-block; background:#eff6ff; color:#066aab; font-weight:700;
               font-size:11px; letter-spacing:1.5px; text-transform:uppercase; padding:4px 10px;
               border-radius:20px; margin-bottom:10px;}
  .stButton>button { background:linear-gradient(135deg,#317fd2,#066aab); color:#fff; font-weight:700;
                     border:none; border-radius:10px; padding:10px 22px; }
  .stButton>button:hover { filter:brightness(1.07); color:#fff; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <div class="tag">Reachable for training &amp; retrieval · Navigable for AI agents</div>
  <h1>🛰️ AI Access &amp; Agent Audit</h1>
  <div class="sub">Six checks, ~90 seconds. Two questions in one: can the crawlers that train and
  feed ChatGPT, Gemini, Claude &amp; Perplexity <b>reach and capture</b> your site — and can the
  autonomous AI agents that act on a user's behalf <b>navigate and use</b> your pages?</div>
</div>
""", unsafe_allow_html=True)

st.write("")

col1, col2 = st.columns([4, 1])
with col1:
    domain = st.text_input("Domain", placeholder="example.com", label_visibility="collapsed")
with col2:
    run = st.button("Run audit", use_container_width=True)

CHECK_NAMES = {
    "robots_waf": "CCBot access (robots.txt + WAF)",
    "cc_index": "Common Crawl Index coverage",
    "centrality": "Harmonic Centrality",
    "structured_data": "Structured data",
    "rendering": "Server-side rendering",
    "agentic": "Agentic readiness (AX tree + llms.txt)",
}

if run and domain.strip():
    blocked = _rate_limited()
    if blocked:
        st.warning(blocked)
        st.stop()

    prog = st.progress(0, text=f"Running {N_CHECKS} checks in parallel…")

    def _progress(done, _name=""):
        prog.progress(min(done, N_CHECKS) / N_CHECKS,
                      text=(f"{done}/{N_CHECKS} checks done…" if done < N_CHECKS else "Finishing…"))

    try:
        report = run_audit(domain, progress=_progress)
    except Exception as exc:  # noqa: BLE001
        prog.empty()
        st.error(f"Could not run the audit: {exc}")
        st.stop()

    prog.empty()
    html = render_html(report)
    # Height scales with the number of checks, the two-layer panel, the
    # model-family table and the strategic plan, so nothing is clipped.
    from aiaudit.recommendations import build_recommendations
    n_recs = len(build_recommendations(report))
    height = 470 + len(report.checks) * 250 + 520 + 140 + n_recs * 96 + 230
    components.html(html, height=height, scrolling=True)

    st.download_button(
        "⬇️  Download scorecard (HTML / printable to PDF)",
        data=html.encode("utf-8"),
        file_name=f"ai-visibility-{report.domain}.html",
        mime="text/html",
        use_container_width=True,
    )
    with st.expander("Raw data (JSON)"):
        st.json(report.to_dict())

elif run:
    st.warning("Enter a domain first.")

# ── Bonus utility: verify a logged CCBot IP ───────────────────
with st.sidebar:
    st.markdown("### 🔎 Is it really CCBot?")
    st.caption("The user-agent can be faked. Verify a logged IP with forward-confirmed reverse DNS.")
    ip = st.text_input("IP address", placeholder="18.97.14.84")
    if st.button("Verify IP"):
        if ip.strip():
            res = verify_ccbot_ip(ip.strip())
            if res["verified"]:
                st.success(res["reason"])
            else:
                st.error(res["reason"])
            st.caption(f"PTR: {res.get('ptr')} · forward: {res.get('forward')}")
        else:
            st.warning("Enter an IP.")
    st.divider()
    st.caption("Checks 1–5 (retrievability) follow Common Crawl's *AI Visibility Audit* field guide; "
               "centrality via the community CC Rank Checker. Check 6 (agentic readiness) adds the "
               "accessibility-tree + llms.txt read for autonomous AI agents.")
