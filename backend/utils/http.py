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


@retry(**RETRY_CONFIG)
def get_with_retry(
    url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 20
) -> requests.Response:
    res = requests.get(url, headers=headers, params=params, timeout=timeout)
    return res


@retry(**RETRY_CONFIG)
def post_with_retry(
    url: str, headers: dict | None = None, data: dict | None = None, params: dict | None = None, timeout: float = 15
) -> requests.Response:
    res = requests.post(url, headers=headers, data=data, params=params, timeout=timeout)
    return res


def get_json(url: str, *, what: str, headers: dict | None = None, params: dict | None = None, timeout: float = 20):
    """GET with retry and return the parsed JSON body. Failures surface as RuntimeError("Failed to <what>: ...")."""
    try:
        res = get_with_retry(url, headers=headers, params=params, timeout=timeout)
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        raise RuntimeError(f"Failed to {what}: {exc}") from exc


def fetch_graph_pages(url: str, *, headers: dict, what: str, max_pages: int = 50) -> list[dict]:
    """Follow @odata.nextLink and collect every `value` item. Aborts rather than looping forever."""
    items: list[dict] = []
    for _ in range(max_pages):
        body = get_json(url, what=f"fetch {what}", headers=headers)
        items.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
        if not url:
            return items
    raise RuntimeError(f"Aborting {what} pagination after {max_pages} pages to avoid runaway fetch.")
