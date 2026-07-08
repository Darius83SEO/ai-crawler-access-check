---
title: AI Access & Agent Audit
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# 🛰️ AI Access & Agent Audit

Answers **two questions in one**, for any domain:

1. **Retrievability** — is your site reachable by the crawlers that *train* and
   *feed* ChatGPT, Gemini, Claude & Perplexity, and are you actually in the crawl?
   (Checks 1–5, a standalone implementation of Common Crawl's field guide
   *"The AI Visibility Audit — training data is the new ranking factor."*)
2. **Agentic readiness** — can the autonomous AI agents that *act on a user's
   behalf* (Operator, ChatGPT agent, computer-use) actually **navigate and use**
   your pages? (Check 6.)

> The old world was *index & rank*. The new world is *train, retrieve & act*.
> Being crawlable gets you into the answer; being agent-navigable gets you into
> the task.

## The six checks

| # | Check | What it answers | Data source |
|---|-------|-----------------|-------------|
| 1 | **CCBot access** (weight 30) | Can AI crawlers physically reach the site? | `robots.txt` + edge/WAF UA test |
| 2 | **CC Index coverage** (25) | Is the domain actually in the archive, how recently? | `index.commoncrawl.org` (free) |
| 3 | **Harmonic Centrality** (15) | Is the domain prioritised or deprioritised for crawling? | CC Web Graph via `webgraph.metehan.ai` |
| 4 | **Structured data** (15) | Is the content easy to represent in training? | Schema.org JSON-LD / microdata |
| 5 | **Server-side rendering** (15) | Does content exist without JavaScript? | raw HTML vs Playwright render |
| 6 | **Agentic readiness** (10) | Can an autonomous AI agent navigate & act on the page? | accessibility tree (DOM / Playwright AX snapshot) + `/llms.txt` |

Each check returns pass / warning / fail with a remediation. The weighted total
becomes a **0–100 score and an A–F grade** (the score auto-normalises over the
checks that could be evaluated).

### About check 6 — a *different* layer, not more of the same
Training and retrieval crawlers read HTML/text. **Autonomous agents** instead
traverse the **accessibility tree** — the semantic subset of the DOM exposing
each control's *role*, *name* and *state* — because it's the cheap way to know
what's actionable (a screenshot is ~50k tokens, the raw DOM ~8k and noisy, a
filtered AX tree ~15k). Google now surfaces a broken accessibility tree as an
"Agentic Browsing" signal in Lighthouse. A site can be perfectly cited by
ChatGPT yet unusable by an agent if its controls are `<div onclick>` with no
role or name. Check 6 combines:

- **Accessibility-tree readiness (70%)** — semantic landmarks, real vs "fake"
  controls, accessible names on interactive elements, heading hierarchy.
  Approximated from the DOM in Lite mode; enriched by the real AX snapshot in
  browser mode.
- **`llms.txt` (30%)** — presence and correctness of the `/llms.txt` guide file
  ([llmstxt.org](https://llmstxt.org)) you publish *for* AI clients.

> A weak check-6 score does **not** mean you're invisible to AI answers — it's
> about *actionability*, which matters most for transactional journeys (buy,
> book, sign up).

## Three layers

The scorecard derives the guide's **two-layer retrievability read** — *parametric
memory* ("were you reachable when the model trained?") vs *live retrieval / RAG*
("are you reachable for live fetch today?") — from checks 1–5, and shows the
model-family reference table. Check 6 surfaces the emerging **agentic** layer
("can an agent act on you today?"). A complete audit reads **all three**.

## Bonus — "Is it really CCBot?"

The user-agent can be faked. The tool includes a **forward-confirmed reverse
DNS** verifier (`*.crawl.commoncrawl.org`) for any logged IP.

## Install

```bash
pip install -r requirements.txt
```

That's it — the default **Lite mode** uses only `requests` + the Common Crawl
archive, so it's fast and runs free anywhere (no browser needed). Checks are run
in parallel and results are cached per domain (30-min TTL).

### Optional: full browser mode
For the strongest fetching (bypass bot-protection that blocks plain requests,
and an exact raw-vs-rendered SSR comparison), enable the headless-Chromium path:

```bash
pip install playwright && python -m playwright install chromium
# then run with:  AICRAWLER_BROWSER=1
```

## Deploy as a free public tool

Two free paths, depending on whether you want the full browser mode:

### A. Hugging Face Spaces — free **and** full browser mode ⭐
A Docker Space can install Chromium, so the public app runs the *complete* tool
(bot-protection bypass, exact SSR, real AX-tree snapshot) on the free CPU tier —
the one thing Streamlit Cloud can't do. The `Dockerfile` and the `sdk: docker`
front-matter at the top of this README are all it needs.

1. Push this repo to GitHub.
2. On huggingface.co → **New Space** → SDK **Docker** → **Blank**, then either
   point it at this repo or add the Space as a git remote and `git push` to it.
3. It builds from the `Dockerfile` and serves on port 7860 automatically.

### B. Streamlit Community Cloud — free, Lite mode only
Fastest to publish, but no browser (checks 1–5 fall back to the Common Crawl
archive; check 6 uses the DOM approximation).

1. Push this repo to GitHub (public).
2. On share.streamlit.io → **New app** → pick the repo, branch and `app.py`.
3. Deploy. `requirements.txt` is enough — no extra config.

### Guardrails for public traffic (built in)
- **Anti-SSRF** (`aiaudit/safety.py`): a visitor-supplied host is resolved and
  refused if it maps to a private/loopback/link-local/reserved address (blocks
  `localhost`, internal services, the `169.254.169.254` metadata endpoint).
- **Rate limiting** (`app.py`): a per-session and a global sliding window shield
  the free third-party APIs (Common Crawl index + community centrality) from
  being hammered — those, not the host, are the speed limit under load. The
  per-domain 30-min cache softens the rest.

## Use

**Web app (the client-facing scorecard):**
```bash
streamlit run app.py
```

**CLI:**
```bash
python cli.py example.com                 # pretty terminal report
python cli.py example.com --html out.html # downloadable / printable scorecard
python cli.py example.com --json          # full machine-readable report
```

**Library:**
```python
from aiaudit.orchestrator import run_audit
from aiaudit.scorecard import render_html

report = run_audit("example.com")
print(report.score, report.grade["grade"])
open("scorecard.html", "w", encoding="utf-8").write(render_html(report))
```

## Notes

- Common Crawl data, index and Web Graph rank are free and need no auth.
- `webgraph.metehan.ai` is an independent community project; verify single data
  points against official CC sources where a decision hinges on them.
- Cutoff dates and model behaviours move with each release — the model table
  shows the *pattern*, not pinned dates.

Built on the framework by Stephen Burns, Common Crawl Foundation (CC BY 4.0).
