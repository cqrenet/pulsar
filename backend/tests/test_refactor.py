"""Regression tests for the shared helpers introduced by the refactor."""

from urllib.parse import urlparse

import pytest
from config import settings


def test_seed_default_rules_inserts_once_and_keeps_admin_edits(client):
    import rules

    rules.seed_default_rules()
    total = rules.rules_collection.count_documents({})
    assert total == 10
    rules.rules_collection.update_one({"name": "License Change"}, {"$set": {"enabled": False}})
    rules.seed_default_rules()
    assert rules.rules_collection.count_documents({}) == total
    assert rules.rules_collection.find_one({"name": "License Change"})["enabled"] is False


@pytest.mark.parametrize(
    "url,kwargs",
    [
        ("ftp://example.com/x", {}),
        ("http://localhost/x", {}),
        ("http://10.0.0.5/x", {}),
        ("http://169.254.169.254/latest", {}),
        ("http://example.com/x", {"https_only": True}),
        ("https://evil.example/x", {"allowed_domains": ["siem.example.com", "*.corp.example"]}),
    ],
)
def test_outbound_urls_are_rejected(url, kwargs):
    from utils.ssrf import validate_outbound_url

    with pytest.raises(ValueError):
        validate_outbound_url(url, **kwargs)


def test_outbound_urls_allowed():
    from utils.ssrf import validate_outbound_url

    assert validate_outbound_url("https://hooks.example.com/a") == "hooks.example.com"
    assert validate_outbound_url("https://x.corp.example/a", allowed_domains=["*.corp.example"]) == "x.corp.example"


def test_alert_summary_is_privacy_filtered(client, monkeypatch):
    import rules

    monkeypatch.setattr("config.settings.PRIVACY_SERVICES", {"Exchange"})
    monkeypatch.setattr("config.settings.PRIVACY_SERVICE_ROLES", {"PrivacyReader"})
    rules.alerts_collection.insert_many(
        [
            {"service": "Exchange", "status": "open", "severity": "high"},
            {"service": "Directory", "status": "open", "severity": "low"},
        ]
    )
    body = client.get("/api/alerts/summary").json()
    assert body["total_open"] == 1
    assert [r["_id"]["severity"] for r in body["by_status_severity"]] == ["low"]


def test_event_filters_build_one_query():
    from queries import EventFilters

    f = EventFilters.plain(services=["A"], operation="x.y", start="2024-01-01", end="2024-02-01")
    clauses = f.to_query()["$and"]
    assert {"service": {"$in": ["A"]}} in clauses
    assert {"timestamp": {"$gte": "2024-01-01", "$lte": "2024-02-01"}} in clauses
    assert {"operation": {"$regex": r"x\.y", "$options": "i"}} in clauses  # literal match
    assert EventFilters.plain().to_query() == {}


@pytest.mark.parametrize(
    "path,limits",
    [("/api/fetch-audit-logs", (10, 3600)), ("/api/events/bulk-tags", (20, 60)), ("/api/events", None)],
)
def test_rate_limit_categories(path, limits):
    import rate_limiter

    expected = limits or (settings.RATE_LIMIT_REQUESTS, settings.RATE_LIMIT_WINDOW_SECONDS)
    assert rate_limiter._limit_for_category(rate_limiter._get_path_category(path)) == expected


def test_unknown_rule_operator_never_matches():
    from rules import _matches

    assert not _matches({"conditions": [{"field": "a", "op": "bogus", "value": 1}]}, {"a": 1})
    assert _matches({"conditions": [{"field": "a.b", "op": "in", "value": ["x", "y"]}]}, {"a": {"b": "y"}})


def test_run_fetch_stores_events_and_loads_rules_once(client, mock_events_collection, monkeypatch):
    import ingest

    def fake_source(**kwargs):
        return [
            {
                "id": str(i),
                "activityDateTime": "2024-01-01T00:00:00Z",
                "category": "Directory",
                "activityDisplayName": "x",
            }
            for i in range(3)
        ]

    def broken_source(**kwargs):
        raise RuntimeError("boom")

    calls = {"load": 0, "eval": 0}

    def fake_load():
        calls["load"] += 1
        return []

    monkeypatch.setattr(
        ingest, "SOURCES", [("directory", "Directory", fake_source), ("intune", "Intune", broken_source)]
    )
    # mongomock does not support this pymongo version's bulk_write(UpdateOne); upsert one by one instead.
    monkeypatch.setattr(ingest, "_upsert", lambda docs: mock_events_collection.insert_many(docs))
    monkeypatch.setattr("config.settings.ALERTS_ENABLED", True)
    monkeypatch.setattr(ingest, "load_rules", fake_load)
    monkeypatch.setattr(ingest, "evaluate_event", lambda doc, rules: calls.__setitem__("eval", calls["eval"] + 1))

    result = ingest.run_fetch(hours=1)
    assert result["stored_events"] == 3
    assert result["errors"] == ["Intune: boom"]
    assert mock_events_collection.count_documents({}) == 3
    assert calls == {"load": 1, "eval": 3}


def test_siem_forwards_all_events_and_blocks_bad_urls(monkeypatch):
    import siem

    posted = []

    class Resp:
        status_code = 202

        def raise_for_status(self):
            pass

    monkeypatch.setattr("config.settings.SIEM_ENABLED", True)
    monkeypatch.setattr(
        siem.requests.Session,
        "post",
        lambda self, url, json, timeout, allow_redirects: (
            posted.append({"event": json, "allow_redirects": allow_redirects}) or Resp()
        ),
    )

    monkeypatch.setattr("config.settings.SIEM_WEBHOOK_URL", "http://siem.example.com/x")  # not https
    siem.forward_events([{"id": "0"}])
    assert posted == []

    monkeypatch.setattr("config.settings.SIEM_WEBHOOK_URL", "https://siem.example.com/x")
    siem.forward_events([{"id": str(i)} for i in range(10)])
    assert sorted(entry["event"]["id"] for entry in posted) == [str(i) for i in range(10)]
    assert all(entry["allow_redirects"] is False for entry in posted)


def test_siem_redirect_is_not_reported_as_delivered(monkeypatch):
    import siem

    class Redirect:
        status_code = 302

        def raise_for_status(self):
            pass  # requests does not raise for 3xx responses

    logged = []
    monkeypatch.setattr(siem.requests.Session, "post", lambda self, *args, **kwargs: Redirect())
    monkeypatch.setattr(siem.logger, "warning", lambda message, **kwargs: logged.append(message))
    monkeypatch.setattr(siem.logger, "debug", lambda message, **kwargs: logged.append(message))
    siem._post(siem.requests.Session(), {"id": "example"})
    assert "SIEM forward failed" in logged
    assert "Event forwarded to SIEM" not in logged


def test_notification_webhook_disables_redirects(monkeypatch):
    import notifications

    calls = []

    class Resp:
        pass

    monkeypatch.setattr(
        notifications.requests,
        "post",
        lambda url, json, timeout, headers, allow_redirects: (
            calls.append({"url": url, "allow_redirects": allow_redirects}) or Resp()
        ),
    )

    notifications._post_webhook("https://hooks.example.com/alert", {})
    assert calls == [{"url": "https://hooks.example.com/alert", "allow_redirects": False}]


def test_notification_redirect_is_not_reported_as_delivered(monkeypatch):
    import notifications

    class Redirect:
        status_code = 302

        def raise_for_status(self):
            pass

    monkeypatch.setattr(notifications, "_post_webhook", lambda url, payload: Redirect())
    assert (
        notifications.send_notification("https://hooks.example.com/alert", "generic", "rule", "high", "msg", {})
        is False
    )


def test_get_json_rejects_oversized_response(monkeypatch):
    import utils.http as http

    class Response:
        headers = {"content-length": str(http.MAX_RESPONSE_BYTES + 1)}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(http, "get_with_retry", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="Response too large"):
        http.get_json("https://graph.microsoft.com/test", what="test")


def test_json_response_stream_is_bounded_and_closed(monkeypatch):
    import utils.http as http

    class Response:
        status_code = 200
        headers = {}
        closed = False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"x" * 5
            yield b"x" * 5

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setattr(http.requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="exceeded"):
        http.get_json("https://graph.microsoft.com/test", what="test", max_bytes=6)
    assert response.closed


def test_listing_and_graph_resolver_use_streaming_json(monkeypatch):
    import graph.resolve as resolve
    import sources.unified_audit as audit

    calls = []

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.payload

        def close(self):
            pass

    def get(url, **kwargs):
        calls.append(kwargs)
        return Response(b"{}" if urlparse(url).hostname == "graph.microsoft.com" else b"[]")

    monkeypatch.setattr(audit, "get_with_retry", get)
    monkeypatch.setattr(resolve, "get_with_retry", get)
    assert audit._list_content("Audit.Exchange", "token", "tenant", 1) == []
    assert resolve._request_json("https://graph.microsoft.com/v1.0/users/id", "token") == {}
    assert all(call["stream"] is True for call in calls)


def test_unified_content_uri_is_restricted_to_microsoft_hosts(monkeypatch):
    import sources.unified_audit as unified_audit

    calls = []
    monkeypatch.setattr(
        unified_audit,
        "get_json",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    with pytest.raises(ValueError, match="content URI host"):
        unified_audit._download_content("https://evil.example/audit", "token")
    assert calls == []

    assert unified_audit._download_content("https://manage.office.com/audit", "token") == []
    assert calls


def test_settings_parse_env_lists_and_derive_values(monkeypatch):
    from config import Settings

    monkeypatch.setenv("AUTH_ALLOWED_ROLES", "Reader, Admin,")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("SIEM_ALLOWED_DOMAINS", "SIEM.Example.com,*.Corp.example")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "pulsar.example.com")
    monkeypatch.setenv("MCP_API_KEY", "  key  ")
    monkeypatch.setenv("TENANT_ID", "tenant-1")
    monkeypatch.setenv("AUTH_TENANT_ID", "")
    monkeypatch.setenv("CLIENT_ID", "ingest")
    s = Settings(_env_file=None)
    assert sorted(s.AUTH_ALLOWED_ROLES) == ["Admin", "Reader"]
    assert s.CORS_ORIGINS == ["https://a.example", "https://b.example"]
    assert s.SIEM_ALLOWED_DOMAINS == ["siem.example.com", "*.corp.example"]
    assert s.MCP_ALLOWED_HOSTS == ["pulsar.example.com"]
    assert s.MCP_API_KEY == "key"
    assert s.AUTH_TENANT_ID == "tenant-1"  # falls back to TENANT_ID
    assert s.AUTH_CLIENT_ID == ""  # never falls back to the ingestion app's CLIENT_ID


def test_graph_pagination_never_sends_the_token_to_another_host(monkeypatch):
    import utils.http as http

    pages = {
        "https://graph.microsoft.com/v1.0/a": {"value": [1], "@odata.nextLink": "https://graph.microsoft.com/v1.0/b"},
        "https://graph.microsoft.com/v1.0/b": {"value": [2], "@odata.nextLink": "https://evil.example/c"},
    }
    requested = []
    monkeypatch.setattr(http, "get_json", lambda url, **kw: requested.append(url) or pages[url])
    with pytest.raises(RuntimeError, match="another host"):
        http.fetch_graph_pages("https://graph.microsoft.com/v1.0/a", headers={"Authorization": "Bearer t"}, what="x")
    assert requested == ["https://graph.microsoft.com/v1.0/a", "https://graph.microsoft.com/v1.0/b"]
