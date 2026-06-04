"""AI Crawler Access Check — Streamlit front-end.

Enter a domain, run the five-check AI Visibility Audit, and get a polished
scorecard you can show a client or download as a one-page HTML/PDF.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from aiaudit.orchestrator import run_audit
from aiaudit.scorecard import render_html
from aiaudit.verify import verify_ccbot_ip

st.set_page_config(page_title="AI Crawler Access Check", page_icon="🛰️", layout="centered")

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
  <div class="tag">Training data is the new ranking factor</div>
  <h1>🛰️ AI Crawler Access Check</h1>
  <div class="sub">Five checks, ~90 seconds. Is your site reachable by the crawlers that train
  ChatGPT, Gemini, Claude &amp; Perplexity — and are you actually in the crawl?</div>
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
}

if run and domain.strip():
    prog = st.progress(0, text="Running 5 checks in parallel…")

    def _progress(done, _name=""):
        prog.progress(min(done, 5) / 5,
                      text=(f"{done}/5 checks done…" if done < 5 else "Finishing…"))

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
    st.caption("Based on Common Crawl's *AI Visibility Audit* field guide. "
               "Centrality via the community CC Rank Checker.")
