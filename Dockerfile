# Full build for a free PUBLIC deployment on Hugging Face Spaces (Docker SDK).
# Unlike Streamlit Community Cloud, a Docker Space can install a real browser,
# so this image runs the tool in *full browser mode* (bot-protection bypass,
# exact raw-vs-rendered SSR, and the real accessibility-tree snapshot for the
# agentic check) — all on the free CPU tier.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AICRAWLER_BROWSER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Python deps (Lite set) + Playwright and its Chromium with system libraries.
COPY requirements.txt ./
RUN pip install -r requirements.txt playwright \
 && playwright install --with-deps chromium

COPY . .

# Hugging Face Spaces routes external traffic to this port.
EXPOSE 7860

# CORS/XSRF are relaxed because Streamlit runs behind the HF reverse proxy;
# the app is read-only (no auth, no user data) so this is safe here.
CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.headless=true", "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
