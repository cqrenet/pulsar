"""Regression tests for the shared helpers introduced by the refactor."""

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
        def raise_for_status(self):
            pass

    monkeypatch.setattr("config.settings.SIEM_ENABLED", True)
    monkeypatch.setattr(siem.requests.Session, "post", lambda self, url, json, timeout: posted.append(json) or Resp())

    monkeypatch.setattr("config.settings.SIEM_WEBHOOK_URL", "http://siem.example.com/x")  # not https
    siem.forward_events([{"id": "0"}])
    assert posted == []

    monkeypatch.setattr("config.settings.SIEM_WEBHOOK_URL", "https://siem.example.com/x")
    siem.forward_events([{"id": str(i)} for i in range(10)])
    assert sorted(e["id"] for e in posted) == sorted(str(i) for i in range(10))


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
