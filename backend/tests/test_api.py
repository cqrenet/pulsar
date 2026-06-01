from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_config_features(client):
    response = client.get("/api/config/features")
    assert response.status_code == 200
    data = response.json()
    assert "default_page_size" in data


def test_mcp_sse_mount_exists():
    from main import app

    mcp_mounts = [r for r in app.routes if getattr(r, "path", "") == "/mcp"]
    assert len(mcp_mounts) == 1, "MCP mount not found in app routes"


def test_mcp_messages_no_session(client):
    response = client.post("/mcp/messages/")
    # MCP transport returns 400 when session_id is missing, 404 when session not found
    assert response.status_code in (400, 404)


def test_mcp_sse_auth_required_when_enabled(monkeypatch):
    monkeypatch.setattr("routes.mcp.AUTH_ENABLED", True)
    from routes.mcp import build_mcp_app

    mcp_app = build_mcp_app()
    client = TestClient(mcp_app)
    response = client.get("/mcp/sse")
    assert response.status_code == 401


def test_summary_empty(client):
    response = client.get("/api/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["by_service"] == []


def test_summary_with_events(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-s1",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Directory",
            "operation": "Add user",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
        }
    )
    mock_events_collection.insert_one(
        {
            "id": "evt-s2",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Intune",
            "operation": "Assign policy",
            "result": "success",
            "actor_display": "Bob",
            "raw_text": "",
        }
    )
    response = client.get("/api/summary?days=7")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    services = [s["name"] for s in data["by_service"]]
    assert "Directory" in services
    assert "Intune" in services


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


def test_metrics(client):
    response = client.get("/metrics", headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 200
    assert "pulsar_request_duration_seconds" in response.text


def test_list_events_empty(client):
    response = client.get("/api/events")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["next_cursor"] is None


def test_list_events_cursor_pagination(client, mock_events_collection):
    for i in range(5):
        mock_events_collection.insert_one(
            {
                "id": f"evt-{i}",
                "timestamp": datetime.now(UTC).isoformat(),
                "service": "Directory",
                "operation": "Add user",
                "result": "success",
                "actor_display": f"Actor {i}",
                "raw_text": "",
            }
        )
    response = client.get("/api/events?page_size=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["next_cursor"] is not None

    response2 = client.get(f"/api/events?page_size=2&cursor={data['next_cursor']}")
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["items"]) == 2


def test_list_events_filter_by_service(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-1",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Exchange",
            "operation": "Update",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
        }
    )
    mock_events_collection.insert_one(
        {
            "id": "evt-2",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Directory",
            "operation": "Add",
            "result": "success",
            "actor_display": "Bob",
            "raw_text": "",
        }
    )
    response = client.get("/api/events?service=Exchange")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["service"] == "Exchange"


def test_list_events_page_size_validation(client):
    response = client.get("/api/events?page_size=0")
    assert response.status_code == 422
    response = client.get("/api/events?page_size=501")
    assert response.status_code == 422


def test_filter_options(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-1",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Intune",
            "operation": "Assign",
            "result": "failure",
            "actor_display": "Charlie",
            "actor_upn": "charlie@example.com",
            "raw_text": "",
        }
    )
    response = client.get("/api/filter-options")
    assert response.status_code == 200
    data = response.json()
    assert "Intune" in data["services"]
    assert "Assign" in data["operations"]
    assert "failure" in data["results"]
    assert "Charlie" in data["actors"]


def test_fetch_audit_logs_validation(client):
    response = client.get("/api/fetch-audit-logs?hours=0")
    assert response.status_code == 422
    response = client.get("/api/fetch-audit-logs?hours=721")
    assert response.status_code == 422


def test_graph_webhook_validation(client):
    token = "test-validation-token-123"
    response = client.post("/api/webhooks/graph?validationToken=" + token)
    assert response.status_code == 200
    assert response.text == token


def test_graph_webhook_notification(client):
    payload = {"value": [{"changeType": "updated", "resource": "auditLogs/directoryAudits", "clientState": "secret"}]}
    response = client.post("/api/webhooks/graph", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_update_tags(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-tags",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Directory",
            "operation": "Add user",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
        }
    )
    response = client.patch("/api/events/evt-tags/tags", json={"tags": ["investigating", "urgent"]})
    assert response.status_code == 200
    assert response.json()["tags"] == ["investigating", "urgent"]


def test_add_comment(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-comment",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Directory",
            "operation": "Add user",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
        }
    )
    response = client.post("/api/events/evt-comment/comments", json={"text": "Looks suspicious"})
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Looks suspicious"


def test_source_health(client, mock_watermarks_collection):
    mock_watermarks_collection.insert_one({"source": "directory", "last_fetch_time": "2024-01-01T00:00:00Z"})
    response = client.get("/api/source-health")
    assert response.status_code == 200
    data = response.json()
    directory = next((x for x in data if x["source"] == "directory"), None)
    assert directory["status"] == "healthy"


def test_rules_crud(client):
    rule = {
        "name": "After-hours admin",
        "enabled": True,
        "severity": "high",
        "conditions": [{"field": "operation", "op": "eq", "value": "Add user"}],
        "message": "Admin action outside business hours",
    }
    res = client.post("/api/rules", json=rule)
    assert res.status_code == 200
    created = res.json()
    assert created["name"] == "After-hours admin"

    res2 = client.get("/api/rules")
    assert res2.status_code == 200
    assert len(res2.json()) == 1

    updated = {**rule, "name": "After-hours admin updated"}
    res3 = client.put(f"/api/rules/{created['id']}", json=updated)
    assert res3.status_code == 200
    assert res3.json()["name"] == "After-hours admin updated"

    res4 = client.delete(f"/api/rules/{created['id']}")
    assert res4.status_code == 200

    res5 = client.get("/api/rules")
    assert res5.status_code == 200
    assert len(res5.json()) == 0


def test_privacy_filtering_events_by_operation(client, mock_events_collection, monkeypatch):
    monkeypatch.setattr("config.PRIVACY_SENSITIVE_OPERATIONS", {"MailItemsAccessed", "Send"})
    monkeypatch.setattr("routes.events.PRIVACY_SENSITIVE_OPERATIONS", {"MailItemsAccessed", "Send"})
    monkeypatch.setattr("auth.PRIVACY_SERVICE_ROLES", {"SecurityAdmin"})
    monkeypatch.setattr("auth.user_can_access_privacy_services", lambda claims: False)
    monkeypatch.setattr("routes.events.user_can_access_privacy_services", lambda claims: False)

    mock_events_collection.insert_one(
        {
            "id": "evt-safe",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Exchange",
            "operation": "Add-MailboxPermission",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
        }
    )
    mock_events_collection.insert_one(
        {
            "id": "evt-priv",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Exchange",
            "operation": "Send",
            "result": "success",
            "actor_display": "Bob",
            "raw_text": "",
        }
    )

    response = client.get("/api/events")
    assert response.status_code == 200
    data = response.json()
    ids = [e["id"] for e in data["items"]]
    assert "evt-safe" in ids
    assert "evt-priv" not in ids


def test_saved_searches_crud(client, monkeypatch):
    monkeypatch.setattr("auth.AUTH_ENABLED", False)

    response = client.post(
        "/api/saved-searches", json={"name": "Test search", "filters": {"actor": "alice", "result": "success"}}
    )
    assert response.status_code == 200
    created = response.json()
    assert created["name"] == "Test search"
    search_id = created["id"]

    response2 = client.get("/api/saved-searches")
    assert response2.status_code == 200
    assert len(response2.json()) == 1

    response3 = client.delete(f"/api/saved-searches/{search_id}")
    assert response3.status_code == 200

    response4 = client.get("/api/saved-searches")
    assert response4.status_code == 200
    assert len(response4.json()) == 0


def test_bulk_tags_append(client, mock_events_collection):
    mock_events_collection.insert_one(
        {
            "id": "evt-bulk",
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "Exchange",
            "operation": "Update",
            "result": "success",
            "actor_display": "Alice",
            "raw_text": "",
            "tags": ["existing"],
        }
    )
    response = client.post("/api/events/bulk-tags?service=Exchange", json={"tags": ["backup"], "mode": "append"})
    assert response.status_code == 200
    data = response.json()
    assert data["matched"] == 1
