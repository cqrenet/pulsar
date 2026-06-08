from models.event_model import _make_dedupe_key, normalize_event


def test_make_dedupe_key_prefers_id_and_category():
    e = {"id": "evt-123", "category": "Directory"}
    assert _make_dedupe_key(e) == "evt-123|Directory"


def test_make_dedupe_key_fallback_without_id():
    e = {
        "activityDateTime": "2024-01-01T00:00:00Z",
        "category": "Exchange",
        "activityDisplayName": "Update setting",
    }
    key = _make_dedupe_key(e)
    assert "2024-01-01T00:00:00Z|Exchange|Update setting" in key


def test_normalize_event_basic():
    e = {
        "id": "abc",
        "activityDateTime": "2024-01-15T10:30:00Z",
        "category": "UserManagement",
        "activityDisplayName": "Add user",
        "result": "success",
        "initiatedBy": {
            "user": {
                "id": "u1",
                "displayName": "Alice",
                "userPrincipalName": "alice@example.com",
            }
        },
        "targetResources": [{"id": "t1", "displayName": "Bob", "type": "User"}],
    }
    out = normalize_event(e)
    assert out["id"] == "abc"
    assert out["timestamp"] == "2024-01-15T10:30:00Z"
    assert out["service"] == "UserManagement"
    assert out["operation"] == "Add user"
    assert out["result"] == "success"
    assert out["actor_display"] == "Alice (alice@example.com)"
    assert out["target_displays"] == ["Bob"]
    assert out["dedupe_key"] == "abc|UserManagement"
    assert "raw_text" in out


def test_normalize_event_with_resolved_actor():
    e = {
        "id": "def",
        "activityDateTime": "2024-01-15T11:00:00Z",
        "category": "ApplicationManagement",
        "activityDisplayName": "Add app",
        "result": "success",
        "initiatedBy": {"servicePrincipal": {"id": "sp1"}},
        "targetResources": [],
        "_resolvedActor": {"id": "sp1", "type": "servicePrincipal", "name": "MyApp"},
        "_resolvedActorOwners": ["Owner1"],
    }
    out = normalize_event(e)
    assert out["actor_display"] == "MyApp (owners: Owner1)"
    assert out["display_category"] == "Application"


def test_normalize_event_extracts_correlation_id_from_graph():
    e = {
        "id": "graph-evt",
        "correlationId": "aaaa-bbbb-cccc",
        "activityDateTime": "2024-02-01T12:00:00Z",
        "category": "UserManagement",
        "activityDisplayName": "Add user",
        "result": "success",
        "initiatedBy": {"user": {"id": "u1", "displayName": "Alice", "userPrincipalName": "alice@example.com"}},
        "targetResources": [],
    }
    out = normalize_event(e)
    assert out["correlation_id"] == "aaaa-bbbb-cccc"


def test_normalize_event_extracts_correlation_id_from_unified_audit_raw():
    e = {
        "id": "o365-evt",
        "activityDateTime": "2024-02-01T12:00:00Z",
        "category": "Exchange",
        "activityDisplayName": "Update",
        "result": "success",
        "initiatedBy": {"user": {"id": "u1", "displayName": "Alice", "userPrincipalName": "alice@example.com"}},
        "targetResources": [],
        "raw": {"CorrelationId": "dddd-eeee-ffff"},
    }
    out = normalize_event(e)
    assert out["correlation_id"] == "dddd-eeee-ffff"
