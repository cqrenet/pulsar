from prometheus_client import Counter, Histogram, generate_latest

REQUEST_DURATION = Histogram(
    "pulsar_request_duration_seconds",
    "HTTP request duration",
    ["method", "path", "status"],
)
EVENTS_FETCHED = Counter(
    "pulsar_events_fetched_total",
    "Number of audit events fetched per source",
    ["source"],
)
FETCH_ERRORS = Counter(
    "pulsar_fetch_errors_total",
    "Number of fetch errors per source",
    ["source"],
)
FETCH_DURATION = Histogram(
    "pulsar_fetch_duration_seconds",
    "Duration of fetch jobs per source",
    ["source"],
)


def observe_request(method: str, path: str, status: int, duration: float):
    REQUEST_DURATION.labels(method=method, path=path, status=str(status)).observe(duration)


def track_fetch(source: str, count: int):
    EVENTS_FETCHED.labels(source=source).inc(count)


def track_fetch_error(source: str):
    FETCH_ERRORS.labels(source=source).inc()


def track_fetch_duration(source: str, duration: float):
    FETCH_DURATION.labels(source=source).observe(duration)


def prometheus_metrics():
    return generate_latest()
