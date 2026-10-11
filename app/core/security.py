"""SSRF protection for any feature that fetches a user-supplied URL (used by later steps).

Note: resolving then connecting leaves a small DNS-rebinding window; for the generic
fetch feature we'll also pin the resolved IP. Allowlists are the stronger defence.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.errors import ServiceError


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def host_allowed(host: str, allowed) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allowed)


def assert_public_url(url: str, allowed_hosts=None) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ServiceError("Only http/https URLs are allowed", 400)
    if not parts.hostname or parts.username or parts.password:
        raise ServiceError("Invalid URL", 400)
    if allowed_hosts is not None and not host_allowed(parts.hostname, allowed_hosts):
        raise ServiceError("This site is not on the allowlist", 403)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ServiceError("Cannot resolve host", 502)
    for info in infos:
        if not is_public_ip(info[4][0]):
            raise ServiceError("Target is not a public address", 403)
    return url
