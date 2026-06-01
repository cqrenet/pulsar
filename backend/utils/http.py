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
