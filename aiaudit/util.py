"""Small helpers shared across checks."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_domain(raw: str) -> str:
    """Reduce any user input to a bare registrable host.

    "https://www.Example.com/path?q=1" -> "example.com"
    "Example.com"                        -> "example.com"
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).netloc or urlparse(raw).path
    host = host.split("@")[-1].split(":")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def site_url(domain: str, scheme: str = "https") -> str:
    return f"{scheme}://{domain}/"
