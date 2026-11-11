"""Client-address helpers for allowlists and rate limiting."""

import ipaddress

import structlog
from config import settings
from fastapi import Request

logger = structlog.get_logger("pulsar.net")


def parse_networks(value: str) -> list:
    """Parse a comma-separated CIDR/IP list. Invalid entries are skipped (and logged), never trusted."""
    networks = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid network in configuration", value=item)
    return networks


def ip_in(ip: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


_TRUSTED_PROXIES = parse_networks(settings.TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    """Real client IP for allowlists and rate limiting.

    X-Forwarded-For is client-controlled, so it is only read through trusted proxies and from the right:
    each trusted hop vouches for the address to its left. The first untrusted address is the client.
    """
    ip = request.client.host if request.client else ""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    while hops and ip_in(ip, _TRUSTED_PROXIES):
        ip = hops.pop()
    return ip
