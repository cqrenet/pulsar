from datetime import UTC, datetime


def test_matches_equals():
    rule = {"conditions": [{"field": "operation", "op": "eq", "value": "Add user"}]}
    event = {"operation": "Add user", "timestamp": datetime.now(UTC).isoformat()}
    from rules import _matches

    assert _matches(rule, event) is True


def test_matches_not_equals():
    rule = {"conditions": [{"field": "operation", "op": "neq", "value": "Delete user"}]}
    event = {"operation": "Add user", "timestamp": datetime.now(UTC).isoformat()}
    from rules import _matches

    assert _matches(rule, event) is True


def test_matches_contains():
    rule = {"conditions": [{"field": "actor_display", "op": "contains", "value": "Admin"}]}
    event = {"actor_display": "Admin (admin@example.com)", "timestamp": datetime.now(UTC).isoformat()}
    from rules import _matches

    assert _matches(rule, event) is True


def test_matches_after_hours():
    rule = {"conditions": [{"field": "timestamp", "op": "after_hours", "value": None}]}
    event = {"timestamp": "2024-01-01T22:00:00Z"}
    from rules import _matches

    assert _matches(rule, event) is True

    event2 = {"timestamp": "2024-01-01T10:00:00Z"}
    assert _matches(rule, event2) is False


def test_evaluate_event_creates_alert(monkeypatch):
    from rules import alerts_collection, evaluate_event

    monkeypatch.setattr(
        "rules.load_rules",
        lambda: [
            {
                "_id": "r1",
                "name": "Test rule",
                "enabled": True,
                "severity": "high",
                "conditions": [{"field": "operation", "op": "eq", "value": "Add user"}],
                "message": "Alert!",
            }
        ],
    )

    inserted = {}

    def mock_insert(doc):
        inserted["doc"] = doc

    monkeypatch.setattr(alerts_collection, "insert_one", mock_insert)
    monkeypatch.setattr(alerts_collection, "count_documents", lambda *args, **kwargs: 0)

    event = {"id": "e1", "operation": "Add user", "timestamp": datetime.now(UTC).isoformat(), "dedupe_key": "dk1"}
    triggered = evaluate_event(event)
    assert len(triggered) == 1
    assert inserted["doc"]["rule_name"] == "Test rule"
