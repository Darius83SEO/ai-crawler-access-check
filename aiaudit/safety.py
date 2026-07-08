"""Safety guards for running as a PUBLIC tool.

A public audit fetches a domain the visitor types — a classic SSRF surface. Left
open, someone can point it at ``localhost``, an internal service, or the cloud
metadata endpoint (169.254.169.254) and read the response through our scorecard.

``assert_public_host`` resolves the host up front and refuses anything that maps
to a private, loopback, link-local, reserved or otherwise non-public address.

Residual risk: a public host that *redirects* to an internal IP is not caught
here (the fetchers follow redirects). That's a known v1 limitation — acceptable
because the pre-resolution check blocks the direct vector; tighten later by
pinning the resolved IP through the request if this tool grows.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import List


class UnsafeTargetError(ValueError):
    """The requested host is not a public internet destination."""


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def resolve_ips(host: str) -> List[str]:
    """All A/AAAA addresses a host resolves to (deduplicated)."""
    out: List[str] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            for info in socket.getaddrinfo(host, None, family, socket.SOCK_STREAM):
                ip = info[4][0]
                if ip not in out:
                    out.append(ip)
        except socket.gaierror:
            continue
    return out


def assert_public_host(host: str) -> None:
    """Raise UnsafeTargetError unless *host* is a real, public internet host."""
    if not host or "." not in host:
        # Single-label names (localhost, intranet hostnames) and empties.
        raise UnsafeTargetError("Enter a public domain, e.g. example.com.")
    low = host.lower()
    if low in ("localhost",) or low.endswith((".local", ".internal", ".localhost")):
        raise UnsafeTargetError("Internal/loopback hostnames aren't allowed.")

    # A literal IP in the box: judge it directly. (Parse separately so this
    # UnsafeTargetError — itself a ValueError — isn't swallowed by the except.)
    try:
        ipaddress.ip_address(low)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False
    if is_literal_ip:
        if not _is_public_ip(low):
            raise UnsafeTargetError("Private or reserved IP addresses aren't allowed.")
        return

    ips = resolve_ips(low)
    if not ips:
        raise UnsafeTargetError(f"Couldn't resolve '{host}' — check the domain is spelled correctly.")
    if not all(_is_public_ip(ip) for ip in ips):
        raise UnsafeTargetError("That host resolves to a private/internal address and can't be audited.")
