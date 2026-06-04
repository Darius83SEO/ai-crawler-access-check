"""Bonus utility — 'Is it really CCBot?'

The user-agent string is not proof of identity. The genuine CCBot runs from
dedicated IP ranges with reverse DNS resolving to *.crawl.commoncrawl.org.
This does a forward-confirmed reverse DNS check, exactly as the guide shows.
"""

from __future__ import annotations

import socket
from typing import Dict


def verify_ccbot_ip(ip: str) -> Dict[str, object]:
    """Forward-confirmed reverse DNS check for a logged IP claiming to be CCBot."""
    out: Dict[str, object] = {"ip": ip, "ptr": None, "forward": None,
                              "verified": False, "reason": ""}
    try:
        ptr = socket.gethostbyaddr(ip)[0]
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"No reverse DNS ({exc}) — fails verification."
        return out
    out["ptr"] = ptr
    if not ptr.endswith(".crawl.commoncrawl.org"):
        out["reason"] = f"PTR '{ptr}' is not a *.crawl.commoncrawl.org host — impostor."
        return out
    try:
        fwd = socket.gethostbyname(ptr)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"PTR did not forward-resolve ({exc}) — fails verification."
        return out
    out["forward"] = fwd
    if fwd == ip:
        out["verified"] = True
        out["reason"] = "Forward-confirmed: PTR resolves back to the same IP. Genuine CCBot."
    else:
        out["reason"] = f"Forward IP {fwd} != {ip} — fails verification (impostor)."
    return out
