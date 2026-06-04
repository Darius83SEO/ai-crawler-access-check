# 🛰️ AI Crawler Access Check

A standalone implementation of the **five-check AI Visibility Audit** from
Common Crawl's field guide *"The AI Visibility Audit — training data is the new
ranking factor."* It answers, for any domain: **is your site reachable by the
crawlers that train and feed ChatGPT, Gemini, Claude & Perplexity — and are you
actually in the crawl?**

> The old world was *index & rank*. The new world is *train & retrieve*.
> If you're not in the crawl, you're not in the model.

## The five checks

| # | Check | What it answers | Data source |
|---|-------|-----------------|-------------|
| 1 | **CCBot access** (weight 30) | Can AI crawlers physically reach the site? | `robots.txt` + edge/WAF UA test |
| 2 | **CC Index coverage** (25) | Is the domain actually in the archive, how recently? | `index.commoncrawl.org` (free) |
| 3 | **Harmonic Centrality** (15) | Is the domain prioritised or deprioritised for crawling? | CC Web Graph via `webgraph.metehan.ai` |
| 4 | **Structured data** (15) | Is the content easy to represent in training? | Schema.org JSON-LD / microdata |
| 5 | **Server-side rendering** (15) | Does content exist without JavaScript? | raw HTML vs Playwright render |

Each check returns pass / warning / fail with a remediation. The weighted total
becomes a **0–100 score and an A–F grade**.

## Two layers

The scorecard also derives the guide's **two-layer read** — *parametric memory*
("were you reachable when the model trained?") vs *live retrieval / RAG* ("are
you reachable for live fetch today?") — and shows the model-family reference
table. A complete audit checks **both**.

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

## Deploy (Streamlit Community Cloud, free)

1. Push this repo to GitHub (public).
2. On share.streamlit.io → **New app** → pick the repo, branch and `app.py`.
3. Deploy. Lite mode needs no extra config — `requirements.txt` is enough.

The app gets a dedicated `…streamlit.app` URL. Note: the audit speed depends on
two third-party services (the Common Crawl index and the centrality API); under
heavy public traffic those — not Streamlit — are the limiting factor, which the
per-domain cache is there to soften.

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
