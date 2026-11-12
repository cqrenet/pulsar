#!/usr/bin/env python3
"""Run a bounded, read-only HTTP baseline against a PULSAR deployment."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

DEFAULT_PATHS = (
    "/health",
    "/metrics",
    "/api/version",
    "/api/config/auth",
    "/api/config/features",
    "/docs",
    "/openapi.json",
    "/.well-known/oauth-authorization-server",
    "/api/events",
    "/api/webhooks/graph",
    "/mcp/sse",
)


def _response_record(path: str, response) -> dict:
    return {
        "path": path,
        "status": response.status,
        "content_type": response.headers.get("Content-Type", ""),
        "content_length": response.headers.get("Content-Length", ""),
        "server": response.headers.get("Server", ""),
        "location": response.headers.get("Location", ""),
        "security_headers": {
            name.lower(): response.headers.get(name, "")
            for name in (
                "Content-Security-Policy",
                "Strict-Transport-Security",
                "X-Content-Type-Options",
                "X-Frame-Options",
                "Referrer-Policy",
                "Permissions-Policy",
            )
            if response.headers.get(name)
        },
    }


def _error_record(path: str, exc: Exception) -> dict:
    return {"path": path, "error": type(exc).__name__, "detail": str(exc)[:160]}


def probe(base_url: str, paths: tuple[str, ...], timeout: float, insecure: bool) -> list[dict]:
    base_url = base_url.rstrip("/")
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context))
    results = []
    for path in paths:
        request = Request(f"{base_url}{path}", headers={"Accept": "application/json, text/plain"})
        try:
            with opener.open(request, timeout=timeout) as response:
                results.append(_response_record(path, response))
        except HTTPError as exc:
            results.append(_response_record(path, exc))
        except (OSError, URLError, TimeoutError) as exc:
            results.append(_error_record(path, exc))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="deployment to probe, e.g. https://pulsar.example.com")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--insecure", action="store_true", help="skip TLS certificate verification for a dev target")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    for result in probe(args.base_url, DEFAULT_PATHS, args.timeout, args.insecure):
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
