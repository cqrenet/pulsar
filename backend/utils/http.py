import json
from urllib.parse import urlparse

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger("pulsar.http")

RETRY_CONFIG = {
    "stop": stop_after_attempt(3),
    "wait": wait_exponential(multiplier=1, min=2, max=10),
    "retry": retry_if_exception_type(requests.RequestException),
    "before_sleep": lambda retry_state: logger.warning(
        "Retrying HTTP request",
        attempt=retry_state.attempt_number,
        exception=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    ),
}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


@retry(**RETRY_CONFIG)
def get_with_retry(
    url: str,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 20,
    *,
    stream: bool = False,
) -> requests.Response:
    return requests.get(
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        stream=stream,
        allow_redirects=False,
    )


@retry(**RETRY_CONFIG)
def post_with_retry(
    url: str,
    headers: dict | None = None,
    data: dict | None = None,
    params: dict | None = None,
    timeout: float = 15,
) -> requests.Response:
    return requests.post(
        url,
        headers=headers,
        data=data,
        params=params,
        timeout=timeout,
        allow_redirects=False,
    )


def _read_json_with_limit(res: requests.Response, max_bytes: int) -> object:
    """Read a streamed response without allowing an unbounded body into memory."""
    content_length = res.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ValueError(f"Response too large: {content_length} bytes (max {max_bytes})")
        except ValueError as exc:
            if "Response too large" in str(exc):
                raise
            raise ValueError("Response has an invalid content length") from exc

    chunks = []
    total = 0
    try:
        for chunk in res.iter_content(chunk_size=64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Response exceeded {max_bytes} bytes")
            chunks.append(chunk)
    finally:
        res.close()
    return json.loads(b"".join(chunks))


def read_json_response(res: requests.Response, *, what: str, max_bytes: int = MAX_RESPONSE_BYTES) -> object:
    """Parse a response body with the shared size limit and a stable error context."""
    try:
        res.raise_for_status()
        return _read_json_with_limit(res, max_bytes)
    except Exception as exc:
        raise RuntimeError(f"Failed to {what}: {exc}") from exc


def get_json(
    url: str,
    *,
    what: str,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 20,
    max_bytes: int = MAX_RESPONSE_BYTES,
):
    """GET with retry and return a bounded parsed JSON body."""
    try:
        res = get_with_retry(url, headers=headers, params=params, timeout=timeout, stream=True)
        res.raise_for_status()
        return _read_json_with_limit(res, max_bytes)
    except Exception as exc:
        raise RuntimeError(f"Failed to {what}: {exc}") from exc


def fetch_graph_pages(url: str, *, headers: dict, what: str, max_pages: int = 50) -> list[dict]:
    """Follow @odata.nextLink and collect every `value` item. Aborts rather than looping forever.

    The bearer token goes with every page request, so a next link must stay on the HTTPS origin of the first URL.
    """
    origin = urlparse(url)
    items: list[dict] = []
    for _ in range(max_pages):
        body = get_json(url, what=f"fetch {what}", headers=headers)
        items.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
        if not url:
            return items
        nxt = urlparse(url)
        if nxt.scheme != "https" or nxt.hostname != origin.hostname:
            raise RuntimeError(f"Refusing {what} next link to another host: {nxt.hostname}")
    raise RuntimeError(f"Aborting {what} pagination after {max_pages} pages to avoid runaway fetch.")
