"""App-only tokens for the ingestion app registration.

Authenticates with a certificate (signed client assertion), never a client secret: the private key
stays on this host and is never sent to Entra.
"""

import base64
import hashlib
import threading
import time
import uuid
from functools import cache
from pathlib import Path

import jwt
import requests
from config import CLIENT_CERTIFICATE_PATH, CLIENT_ID, TENANT_ID
from cryptography import x509
from cryptography.hazmat.primitives import serialization

_TOKEN_CACHE = {}
_token_lock = threading.Lock()  # fetches run in worker threads


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@cache
def _load_certificate(path: str):
    """Return (private key, JWT headers) from one PEM file holding the key and the certificate."""
    pem = Path(path).read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
    # Entra documents x5t#S256 but still looks certificates up by the SHA-1 x5t: send both.
    headers = {
        "x5t": _b64url(hashlib.sha1(der, usedforsecurity=False).digest()),
        "x5t#S256": _b64url(hashlib.sha256(der).digest()),
    }
    return key, headers


def client_assertion(token_url: str) -> str:
    if not CLIENT_CERTIFICATE_PATH:
        raise RuntimeError("CLIENT_CERTIFICATE_PATH is not set")
    key, headers = _load_certificate(CLIENT_CERTIFICATE_PATH)
    now = int(time.time())
    claims = {
        "aud": token_url,
        "iss": CLIENT_ID,
        "sub": CLIENT_ID,
        "jti": str(uuid.uuid4()),
        "nbf": now,
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(claims, key, algorithm="PS256", headers=headers)


def get_access_token(scope: str = "https://graph.microsoft.com/.default"):
    """Request an application token from Microsoft identity platform.
    Tokens are cached and reused until 5 minutes before expiry."""
    with _token_lock:
        return _get_access_token_locked(scope)


def _get_access_token_locked(scope: str):
    now = time.time()
    cached = _TOKEN_CACHE.get(scope)
    if cached and cached["exp"] > now + 300:
        return cached["token"]

    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": client_assertion(url),
        "scope": scope,
    }
    try:
        res = requests.post(url, data=data, timeout=15)
        res.raise_for_status()
        payload = res.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Token endpoint returned no access_token")
        expires_in = payload.get("expires_in", 3600)
        _TOKEN_CACHE[scope] = {"token": token, "exp": now + expires_in}
        return token
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to obtain access token: {exc}") from exc
