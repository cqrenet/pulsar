import mongomock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="function")
def mock_events_collection():
    client = mongomock.MongoClient()
    db = client["micro_soc"]
    coll = db["events"]
    return coll


@pytest.fixture(scope="function")
def mock_watermarks_collection():
    client = mongomock.MongoClient()
    db = client["micro_soc"]
    coll = db["watermarks"]
    return coll


@pytest.fixture(scope="function")
def client(mock_events_collection, mock_watermarks_collection, monkeypatch):
    monkeypatch.setattr("database.events_collection", mock_events_collection)
    monkeypatch.setattr("database.saved_searches_collection", mock_events_collection)
    monkeypatch.setattr("routes.fetch.events_collection", mock_events_collection)
    monkeypatch.setattr("routes.events.events_collection", mock_events_collection)
    monkeypatch.setattr("routes.summary.events_collection", mock_events_collection)
    monkeypatch.setattr("mcp_common.events_collection", mock_events_collection)
    monkeypatch.setattr("routes.saved_searches.saved_searches_collection", mock_events_collection)
    monkeypatch.setattr("watermark.watermarks_collection", mock_watermarks_collection)
    monkeypatch.setattr("routes.health.watermarks_collection", mock_watermarks_collection)
    monkeypatch.setattr("routes.fetch.get_watermark", lambda source: None)
    monkeypatch.setattr("routes.fetch.set_watermark", lambda source, ts: None)
    monkeypatch.setattr("auth.AUTH_ENABLED", False)
    monkeypatch.setattr("routes.mcp.AUTH_ENABLED", False)
    monkeypatch.setattr("config.PRIVACY_SERVICES", set())
    monkeypatch.setattr("config.PRIVACY_SENSITIVE_OPERATIONS", set())
    monkeypatch.setattr("routes.events.PRIVACY_SERVICES", set())
    monkeypatch.setattr("routes.events.PRIVACY_SENSITIVE_OPERATIONS", set())
    monkeypatch.setattr("database.db.command", lambda cmd: {"ok": 1} if cmd == "ping" else {})

    # Mock audit trail and rules collections
    audit_client = mongomock.MongoClient()
    audit_db = audit_client["micro_soc"]
    monkeypatch.setattr("audit_trail.audit_collection", audit_db["pulsar_audit"])
    monkeypatch.setattr("rules.alerts_collection", audit_db["alerts"])
    monkeypatch.setattr("rules.rules_collection", audit_db["alert_rules"])
    monkeypatch.setattr("routes.rules.rules_collection", audit_db["alert_rules"])

    # Mock Redis so tests don't require a running Redis server
    class FakeRedis:
        _store = {}

        async def get(self, key):
            return self._store.get(key)

        async def setex(self, key, ttl, value):
            self._store[key] = value

        async def incr(self, key):
            self._store[key] = self._store.get(key, 0) + 1
            return self._store[key]

        async def expire(self, key, ttl):
            pass

    async def fake_get_arq_pool():
        return FakeRedis()

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr("redis_client.get_arq_pool", fake_get_arq_pool)
    monkeypatch.setattr("redis_client.get_redis", fake_get_redis)
    monkeypatch.setattr("rate_limiter.get_redis", fake_get_redis)

    from main import app

    return TestClient(app)
