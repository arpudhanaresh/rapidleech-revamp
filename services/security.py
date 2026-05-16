from __future__ import annotations
import ipaddress
import re
import socket
from urllib.parse import urlparse

import validators

ALLOWED_SCHEMES = {"http", "https", "magnet"}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
}

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class SecurityError(Exception):
    pass


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return True


async def validate_and_resolve(url: str) -> str:
    """Validate URL scheme, resolve DNS, block SSRF targets. Returns cleaned URL."""
    url = url.strip()
    if len(url) > 2048:
        raise SecurityError("URL exceeds maximum length")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme == "magnet":
        return url  # magnet URIs don't involve HTTP connections from the server

    if scheme not in ALLOWED_SCHEMES:
        raise SecurityError(f"Scheme '{scheme}' is not allowed")

    host = parsed.hostname
    if not host:
        raise SecurityError("URL has no hostname")

    if host.lower() in _BLOCKED_HOSTS:
        raise SecurityError(f"Host '{host}' is blocked")

    if not validators.domain(host) and not validators.ipv4(host) and not validators.ipv6(host):
        raise SecurityError(f"Invalid hostname: {host}")

    # Resolve all A/AAAA records and check each
    try:
        results = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SecurityError(f"DNS resolution failed for '{host}': {e}")

    for res in results:
        ip = res[4][0]
        if _is_private_ip(ip):
            raise SecurityError(
                f"'{host}' resolves to private/internal address {ip} — blocked (SSRF protection)"
            )

    return url


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters and normalise a download filename."""
    # Strip directory traversal
    name = name.replace("..", "").replace("/", "_").replace("\\", "_")
    # Remove control chars and Windows-illegal chars
    name = _UNSAFE_FILENAME_RE.sub("_", name)
    name = name.strip(". ")
    name = name[:240] or "download"
    return name
