"""AI Access & Agent Audit — Streamlit front-end.

Enter a domain, run the eight-check audit, and get a polished scorecard you can
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

# Keep the app usable by anyone with the link, but out of search engines: inject
# a robots noindex into the real page <head> (components run in a same-origin
# srcdoc iframe, so window.parent.document is reachable). Height 0 = invisible.
components.html(
    """
    <script>
      try {
        const d = window.parent.document;
        if (!d.querySelector('meta[name=\"robots\"]')) {
          const m = d.createElement('meta');
          m.name = 'robots';
          m.content = 'noindex, nofollow';
          d.head.appendChild(m);
        }
      } catch (e) {}
    </script>
    """,
    height=0,
)

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
  <div class="sub">Eight checks, ~90 seconds. Three layers: can the crawlers that <b>train</b>
  ChatGPT, Gemini, Claude &amp; Perplexity capture your site, can the bots behind their <b>live
  answers</b> read and quote it today — and can the autonomous AI agents that act on a user's
  behalf <b>navigate and use</b> your pages?</div>
</div>
""", unsafe_allow_html=True)

st.write("")

with st.expander("ℹ️ How the eight checks work (and what's new)"):
    st.markdown("""
**Three layers, three different questions.** An AI assistant can know you from its *training data*,
read you *live* when it searches the web to answer, and — with agents — *act* on your site.
Each layer has its own bots and its own failure modes, so the audit reads them separately.

#### 🧠 Training (parametric memory)

**1 · Training-crawler access** — can the bots that build training data (CCBot, GPTBot, ClaudeBot,
Google-Extended…) reach the site? Reads robots.txt and compares how the server answers the CCBot
user agent vs a normal browser, to catch firewall blocks a clean robots.txt would hide.
Blocking these is a legitimate choice: it only affects *future* models, not today's answers.

**4 · Common Crawl Index coverage** — are your pages actually in the archive most models train on?
If the index doesn't answer (it's often overloaded), the check shows "not determined" and is
left out of the score instead of counting as a fail.

**5 · Harmonic Centrality** — how close your domain sits to the core of the web graph: central
domains are crawled deeper and more often.

#### 🔎 Live retrieval — NEW

**2 · AI search & user-fetch bots** — when ChatGPT, Gemini, Claude, Perplexity or Copilot search the
web, they don't use Common Crawl: they use their own search crawler or a search engine's index,
plus a fetcher that reads a page when a user asks for it. The check tests each one:

- *search crawlers*: Googlebot (Gemini, AI Overviews, AI Mode), Bingbot (Copilot),
  OAI-SearchBot (ChatGPT search), Claude-SearchBot, PerplexityBot;
- *user-triggered fetchers*: ChatGPT-User, Claude-User, Perplexity-User;
- *Google-Extended*: a robots.txt token (not a crawler) that also controls whether the Gemini
  apps can ground answers on your pages — it doesn't affect AI Overviews.

Blocking a search crawler removes you from that engine's answers **today**. CDN "block AI bots"
switches often catch these together with training bots: the check separates them.
*Limit:* the test sends each bot's user agent from our server, not from the bot's official IPs.
A refusal is a strong hint, not proof — confirm in your server logs. Googlebot and Bingbot are
usually verified by IP at the CDN, so a refusal of a spoofed request is expected and not
counted as a block.

**3 · Index & snippet eligibility** — being read isn't enough: index-based answers only use pages
the index may keep and quote. On the homepage it reads:

- `noindex` → the page leaves the index (no Gemini, AI Overviews or Copilot);
- `nosnippet` / `max-snippet` → Google won't quote the text in AI Overviews and AI Mode;
- `noarchive` / `nocache` → Bing keeps the content out of Copilot answers / limits it to
  URL, title and snippet;
- the **canonical** (pointing to another site hands eligibility to that site);
- the **XML sitemap** and how fresh its `lastmod` dates are — index-backed engines replace
  stale sources quickly, and fresh dates are how updated pages get re-read.

#### 🧩 Both layers

**6 · Structured data** — Schema.org markup (Organization, page, author, breadcrumb) that helps
models represent and attribute your entity.

**7 · Server-side rendering** — is the content in the HTML without JavaScript? Many AI crawlers
don't run JS, so JS-only content is invisible to them.

#### 🤖 Agentic — UPDATED

**8 · Agentic readiness** — can an autonomous agent (ChatGPT agent, computer-use) navigate and act
on the page? Agents read the *accessibility tree*, not the raw HTML. The check looks at
landmarks, real buttons/links vs clickable `<div>`s, accessible names, heading order, and —
new — **forms** (labelled fields, `autocomplete` hints), **CAPTCHAs** (agents are built not to
solve them, so the score drops) and **consent banners** (reported, so you can check they close
with real buttons). `llms.txt` now weighs only 15% of this check: there's no public evidence
yet that the major assistants read it.

**Score.** Each check gives pass / warning / fail and a fix. The weighted total becomes a 0–100
score and an A–F grade, computed only on the checks that could be evaluated. All checks read the
**homepage**: run inner pages through a crawler for a full picture.
""")

col1, col2 = st.columns([4, 1])
with col1:
    domain = st.text_input("Domain", placeholder="example.com", label_visibility="collapsed")
with col2:
    run = st.button("Run audit", use_container_width=True)

CHECK_NAMES = {
    "robots_waf": "Training-crawler access (CCBot)",
    "ai_bots": "AI search & user-fetch bots",
    "eligibility": "Index & snippet eligibility",
    "cc_index": "Common Crawl Index coverage",
    "centrality": "Harmonic Centrality",
    "structured_data": "Structured data",
    "rendering": "Server-side rendering",
    "agentic": "Agentic readiness (AX tree, forms, blockers, llms.txt)",
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
    # Height scales with each check's detail lines, the two-layer panel, the
    # model-family table, the strategic plan and the glossary, so nothing is clipped.
    from aiaudit.recommendations import build_recommendations
    n_recs = len(build_recommendations(report))
    checks_h = sum(190 + 21 * len(c.details) for c in report.checks)
    height = 470 + checks_h + 520 + 220 + n_recs * 110 + 120 + len(report.checks) * 48
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
    st.caption("Training checks follow Common Crawl's *AI Visibility Audit* field guide; centrality via "
               "the community CC Rank Checker. Live-retrieval checks test the search and user-fetch bots "
               "of ChatGPT, Gemini, Claude, Perplexity and Copilot plus index/snippet directives. The "
               "agentic check reads the accessibility tree, forms and blockers for autonomous AI agents. "
               "Bot tests use each bot's user agent, not its IPs: confirm in server logs.")
