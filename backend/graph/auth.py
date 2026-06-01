import time

import requests
from config import CLIENT_ID, CLIENT_SECRET, TENANT_ID

_TOKEN_CACHE = {}


def get_access_token(scope: str = "https://graph.microsoft.com/.default"):
    """Request an application token from Microsoft identity platform.
    Tokens are cached and reused until 5 minutes before expiry."""
    now = time.time()
    cached = _TOKEN_CACHE.get(scope)
    if cached and cached["exp"] > now + 300:
        return cached["token"]

    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
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
