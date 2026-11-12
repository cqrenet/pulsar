"""Outbound-URL checks shared by alert webhooks and SIEM forwarding."""

import ipaddress
from urllib.parse import urlparse

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def validate_outbound_url(url: str, *, https_only: bool = False, allowed_domains: list[str] | None = None) -> str:
    """Reject non-HTTP(S) schemes, internal/reserved addresses and (optionally) hosts off an allowlist.

    Hostnames are not resolved here: a public name can still point at a private address, so callers disable
    redirects and the host should have an egress firewall. Returns the lower-cased hostname. Raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in (("https",) if https_only else ("http", "https")):
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL must have a valid hostname")
    if hostname in _BLOCKED_HOSTS:
        raise ValueError(f"hostname '{hostname}' is not allowed")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None  # a DNS name, not an IP literal
    if ip and _blocked(ip):
        raise ValueError(f"IP '{hostname}' is not allowed")
    if allowed_domains and not any(
        hostname == d or (d.startswith("*.") and hostname.endswith(d[1:])) for d in allowed_domains
    ):
        raise ValueError(f"domain '{hostname}' is not in the allowlist")
    return hostname
