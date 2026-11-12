"""Regression tests for the pre-pentest security fixes."""

import asyncio
import base64
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import audit_trail
import auth
import jwt
import pytest
from config import settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from middleware import client_ip
from starlette.requests import Request

USERS = {
    "admin": {"sub": "admin-sub", "roles": ["PulsarAdmin", "PrivacyReader"]},
    "reader": {"sub": "reader-sub", "roles": ["PulsarReader"]},
}


@pytest.fixture
def signed_in(monkeypatch):
    """Enable auth; the bearer token value picks the user from USERS."""

    async def no_jwks():
        return []

    monkeypatch.setattr("config.settings.AUTH_ENABLED", True)
    monkeypatch.setattr("auth._get_jwks_async", no_jwks)
    monkeypatch.setattr("auth._decode_token", lambda token, jwks: USERS[token])
    return lambda name: {"Authorization": f"Bearer {name}"}


@pytest.fixture
def privacy(monkeypatch, mock_events_collection):
    monkeypatch.setattr("config.settings.PRIVACY_SERVICES", {"Exchange"})
    monkeypatch.setattr("config.settings.PRIVACY_SERVICE_ROLES", {"PrivacyReader"})
    now = datetime.now(UTC).isoformat()
    mock_events_collection.insert_many(
        [
            {"id": "pub", "timestamp": now, "service": "Directory", "operation": "Add user", "actor_display": "Alice"},
            {"id": "hid", "timestamp": now, "service": "Exchange", "operation": "Send", "actor_display": "Mallory"},
        ]
    )


RULE = {"name": "r1", "enabled": True, "severity": "low", "conditions": [], "message": "m"}


# --- Authorization and audit trail -------------------------------------------------------------


def test_audit_trail_records_the_real_user(client, signed_in):
    assert client.post("/api/rules", json=RULE, headers=signed_in("admin")).status_code == 200
    users = {d["user"] for d in audit_trail.audit_collection.find({"resource": "/api/rules"})}
    assert users == {"admin-sub"}


def test_one_audit_record_per_mutation_with_action_detail_and_request_id(client, signed_in):
    headers = {**signed_in("admin"), "x-request-id": "req-12345678"}
    assert client.post("/api/rules", json=RULE, headers=headers).status_code == 200
    records = list(audit_trail.audit_collection.find({"resource": "/api/rules"}))
    assert len(records) == 1
    rec = records[0]
    assert rec["action"] == "create_rule" and rec["user"] == "admin-sub" and rec["request_id"] == "req-12345678"
    assert rec["details"] == {"name": "r1", "enabled": True, "method": "POST", "status_code": 200}


def test_denied_and_unnamed_mutations_are_still_audited(client, signed_in, monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_ADMIN_ROLES", {"PulsarAdmin"})
    assert client.post("/api/rules", json=RULE, headers=signed_in("reader")).status_code == 403
    denied = audit_trail.audit_collection.find_one({"resource": "/api/rules"})
    assert denied["action"] == "post" and denied["details"]["status_code"] == 403  # handler never ran

    client.post("/api/saved-searches", json={"name": "s", "filters": {}}, headers=signed_in("reader"))
    saved = audit_trail.audit_collection.find_one({"action": "create_saved_search"})
    assert saved["user"] == "reader-sub" and saved["details"]["name"] == "s"


def test_rule_changes_and_fetch_require_admin_role(client, signed_in, monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_ADMIN_ROLES", {"PulsarAdmin"})
    assert client.get("/api/rules", headers=signed_in("reader")).status_code == 200
    assert client.post("/api/rules", json=RULE, headers=signed_in("reader")).status_code == 403
    assert client.get("/api/fetch-audit-logs", headers=signed_in("reader")).status_code == 403
    assert client.post("/api/rules", json=RULE, headers=signed_in("admin")).status_code == 200


def test_invalid_object_ids_are_client_errors(client):
    assert client.delete("/api/rules/not-an-id").status_code == 404
    assert client.patch("/api/alerts/not-an-id/status", json={"status": "resolved"}).status_code == 404
    bad_cursor = base64.b64encode(b"2026-01-01T00:00:00Z|not-an-oid").decode()
    assert client.get(f"/api/events?cursor={bad_cursor}").status_code == 400


def _token(key, kid, **claims):
    base = {"aud": "api-guid", "tid": "tenant", "iss": "https://login.microsoftonline.com/tenant/v2.0"}
    return jwt.encode({**base, "exp": int(time.time()) + 600, **claims}, key, algorithm="RS256", headers={"kid": kid})


def test_token_must_have_exact_tenant_and_issuer(monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_CLIENT_ID", "api-guid")
    monkeypatch.setattr("config.settings.AUTH_TENANT_ID", "tenant")
    monkeypatch.setattr("config.settings.AUTH_SCOPE", "api://api-guid/user_impersonation")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = [{**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "k1"}]

    with pytest.raises(HTTPException) as exc:
        auth._decode_token(
            _token(
                key,
                "k1",
                tid="tenant",
                iss="https://login.microsoftonline.com/tenant.attacker/v2.0",
                azp="spa",
                scp="user_impersonation",
            ),
            jwks,
        )
    assert exc.value.status_code == 401


def test_id_tokens_are_not_accepted_as_api_credentials(monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_CLIENT_ID", "api-guid")
    monkeypatch.setattr("config.settings.AUTH_TENANT_ID", "tenant")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = [{**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "k1"}]

    with pytest.raises(HTTPException) as exc:
        auth._decode_token(_token(key, "k1", sub="u"), jwks)  # ID token: no azp/appid
    assert exc.value.status_code == 401
    assert auth._decode_token(_token(key, "k1", sub="u", azp="spa", scp="user_impersonation"), jwks)["sub"] == "u"


def test_access_tokens_must_contain_configured_scope(monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_CLIENT_ID", "api-guid")
    monkeypatch.setattr("config.settings.AUTH_TENANT_ID", "tenant")
    monkeypatch.setattr("config.settings.AUTH_SCOPE", "api://api-guid/user_impersonation")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = [{**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "k1"}]

    with pytest.raises(HTTPException) as exc:
        auth._decode_token(_token(key, "k1", sub="u", azp="spa", roles=["PulsarReader"]), jwks)
    assert exc.value.status_code == 401


# --- Client IP / X-Forwarded-For -------------------------------------------------------------


def _request(peer, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_client_ip_cannot_be_spoofed():
    assert client_ip(_request("203.0.113.9", "10.0.0.1")) == "203.0.113.9"  # direct client, header ignored
    assert client_ip(_request("172.18.0.2", "10.0.0.1, 203.0.113.9")) == "203.0.113.9"  # via proxy, rightmost
    assert client_ip(_request("172.18.0.2", "192.168.1.5")) == "192.168.1.5"  # internal user via proxy
    assert client_ip(_request("172.18.0.2")) == "172.18.0.2"  # scraper hitting backend directly


def test_metrics_rejects_spoofed_forwarded_for(client):
    from main import app

    external = TestClient(app, client=("203.0.113.9", 50000))
    assert external.get("/metrics", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 403


# --- Privacy filtering on every read path ----------------------------------------------------


def test_privacy_hidden_from_rest_reads(client, privacy):
    ids = [e["id"] for e in client.get("/api/events").json()["items"]]
    assert ids == ["pub"]
    services = [s["name"] for s in client.get("/api/summary").json()["by_service"]]
    assert services == ["Directory"]
    options = client.get("/api/filter-options").json()
    assert "Mallory" not in options["actors"] and "Exchange" not in options["services"]
    assert client.post("/api/events/hid/comments", json={"text": "probe"}).status_code == 404


def test_privacy_operations_only_config_is_enforced(client, privacy, monkeypatch):
    monkeypatch.setattr("config.settings.PRIVACY_SERVICES", set())
    monkeypatch.setattr("config.settings.PRIVACY_SENSITIVE_OPERATIONS", {"Send"})
    assert [e["id"] for e in client.get("/api/events").json()["items"]] == ["pub"]


def test_privacy_hidden_from_mcp(client, privacy):
    from mcp_common import handle_get_event, handle_get_summary, handle_search_events

    def run(coro):
        return asyncio.run(coro)[0].text

    nobody = {"sub": "mcp-api-key"}
    assert "Mallory" not in run(handle_search_events({"services": ["Exchange"]}, nobody))
    assert "not found" in run(handle_get_event({"event_id": "hid"}, nobody))
    assert "Exchange" not in run(handle_get_summary({}, nobody))
    assert "Mallory" in run(handle_search_events({}, USERS["admin"]))


def test_privacy_hidden_from_alerts(client, privacy):
    from rules import _create_alert

    _create_alert({"_id": "r", "name": "r"}, {"id": "hid", "service": "Exchange", "operation": "Send"})
    assert client.get("/api/alerts?status=open").json()["items"] == []


# --- MCP input handling and identity ----------------------------------------------------------


def test_mcp_arguments_are_literal_and_bounded(client, mock_events_collection):
    from mcp_common import handle_search_events

    now = datetime.now(UTC).isoformat()
    mock_events_collection.insert_many([{"id": str(i), "timestamp": now, "operation": "AddUser"} for i in range(150)])
    text = asyncio.run(handle_search_events({"operation": "Add.*"}, None))[0].text
    assert text == "No matching events found."  # regex metacharacters matched literally
    # limit=0 means "no limit" to MongoDB: it must be clamped, as must oversized limits and bad days.
    assert asyncio.run(handle_search_events({"limit": 0, "days": -5}, None))[0].text.startswith("Found 1 event(s)")
    assert asyncio.run(handle_search_events({"limit": 10_000}, None))[0].text.startswith("Found 100 event(s)")


def test_mcp_api_key_caller_is_identified_and_audited(client, monkeypatch):
    from mcp_tools import _caller
    from routes.mcp import _McpAuthMiddleware

    monkeypatch.setattr("config.settings.MCP_API_KEY", "k" * 32)
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope["state"])

    async def send(message):
        seen.setdefault("status", message.get("status"))

    gate = _McpAuthMiddleware(app)
    asyncio.run(gate({"type": "http", "headers": [(b"x-api-key", b"wrong")]}, None, send))
    assert seen == {"status": 401}
    seen.clear()
    asyncio.run(gate({"type": "http", "headers": [(b"x-api-key", b"k" * 32)]}, None, send))
    assert seen["user"]["sub"] == "mcp-api-key"

    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(**seen))))
    assert _caller(ctx, "get_event", {"event_id": "x"})["sub"] == "mcp-api-key"
    assert audit_trail.audit_collection.find_one({"action": "mcp_get_event"})["user"] == "mcp-api-key"


# --- Alert channels -------------------------------------------------------------------------


def test_alert_cards_defang_tenant_controlled_text():
    import json

    from notifications import _build_slack_payload, _build_teams_payload

    event = {
        "actor_display": "[Verify your account](https://evil.example) www.evil.example",
        "target_displays": ["<!channel> <https://evil.example|click>"],
    }
    teams = json.dumps(_build_teams_payload("r", "high", "m", event))
    assert "https://" not in teams and "](" not in teams and "www.evil" not in teams
    slack = json.dumps(_build_slack_payload("r", "high", "m", event))
    assert "<!channel>" not in slack and "&lt;!channel&gt;" in slack and "https://" not in slack


# --- Startup configuration ------------------------------------------------------------------


def test_startup_refuses_incomplete_auth_config(monkeypatch):

    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_TENANT_ID", "t")
    monkeypatch.setattr(settings, "AUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "AUTH_SCOPE", "")
    assert settings.missing_auth_settings() == ["AUTH_CLIENT_ID", "AUTH_SCOPE"]
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    assert settings.missing_auth_settings() == []


# --- Ingestion credential -------------------------------------------------------------------


def test_ingestion_uses_signed_certificate_assertion_not_secret(tmp_path, monkeypatch):
    import datetime as dt
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    from graph import auth as graph_auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pulsar-test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ) + cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "ingest.pem").write_bytes(pem)
    monkeypatch.setattr(settings, "CLIENT_CERTIFICATE_PATH", str(tmp_path / "ingest.pem"))
    monkeypatch.setattr(settings, "CLIENT_ID", "ingest-app")
    monkeypatch.setattr(settings, "TENANT_ID", "tenant")
    graph_auth._TOKEN_CACHE.clear()

    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "tok", "expires_in": 3600}

    monkeypatch.setattr(graph_auth.requests, "post", lambda url, data, timeout: sent.update(data) or Resp())
    assert graph_auth.get_access_token() == "tok"

    assert "client_secret" not in sent
    url = "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
    claims = jwt.decode(sent["client_assertion"], key.public_key(), algorithms=["PS256"], audience=url)
    assert claims["iss"] == claims["sub"] == "ingest-app"
    header = jwt.get_unverified_header(sent["client_assertion"])
    der = cert.public_bytes(serialization.Encoding.DER)
    expected = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).rstrip(b"=").decode()
    assert header["x5t#S256"] == expected and "x5t" in header


# --- Hardening regressions ---------------------------------------------------------------------


def test_tokens_must_carry_exp_iss_aud_and_tid(monkeypatch):
    monkeypatch.setattr("config.settings.AUTH_CLIENT_ID", "api-guid")
    monkeypatch.setattr("config.settings.AUTH_TENANT_ID", "tenant")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = [{**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "k1"}]
    base = {
        "aud": "api-guid",
        "tid": "tenant",
        "iss": "https://login.microsoftonline.com/tenant/v2.0",
        "azp": "spa",
        "scp": "user_impersonation",
    }
    no_exp = jwt.encode(base, key, algorithm="RS256", headers={"kid": "k1"})
    no_tid = jwt.encode(
        {k: v for k, v in base.items() if k != "tid"} | {"exp": int(time.time()) + 600},
        key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    for token in (no_exp, no_tid):
        with pytest.raises(HTTPException) as exc:
            auth._decode_token(token, jwks)
        assert exc.value.status_code == 401
    ok = jwt.encode({**base, "exp": int(time.time()) + 600}, key, algorithm="RS256", headers={"kid": "k1"})
    assert auth._decode_token(ok, jwks)["tid"] == "tenant"


def test_client_supplied_request_ids_are_sanitised(client):
    for bad in ('aaaaaaaa"<script>', "x" * 200, "short"):
        got = client.get("/api/version", headers={"x-request-id": bad}).headers["x-request-id"]
        assert got != bad and len(got) == 32
    kept = client.get("/api/version", headers={"x-request-id": "trace-0123.abc_DEF"}).headers["x-request-id"]
    assert kept == "trace-0123.abc_DEF"


def test_privacy_filter_hides_documents_without_a_service(client, monkeypatch):
    import rules

    monkeypatch.setattr("config.settings.PRIVACY_SERVICES", {"Exchange"})
    monkeypatch.setattr("config.settings.PRIVACY_SERVICE_ROLES", {"PrivacyReader"})
    rules.alerts_collection.insert_many([{"status": "open", "severity": "low", "rule_name": "legacy"}])
    assert client.get("/api/alerts?status=open").json()["items"] == []


# Routes that are deliberately reachable without a token. Anything else under /api must require auth.
PUBLIC_API_ROUTES = {"/api/config/auth", "/api/config/features", "/api/version", "/api/webhooks/graph"}
ADMIN_ONLY_ROUTES = {
    ("GET", "/api/fetch-audit-logs"),
    ("POST", "/api/rules"),
    ("PUT", "/api/rules/{rule_id}"),
    ("DELETE", "/api/rules/{rule_id}"),
}


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_api_route_requires_auth_and_only_expected_ones_need_admin():
    from fastapi.routing import APIRoute
    from main import app

    unprotected, admin = [], set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        calls = set(_dependency_calls(route.dependant))
        if auth.require_admin in calls:
            admin |= {(m, route.path) for m in route.methods}
        if route.path not in PUBLIC_API_ROUTES and auth.require_auth not in calls:
            unprotected.append(route.path)
    assert not unprotected, f"routes without require_auth: {unprotected}"
    assert admin == ADMIN_ONLY_ROUTES


def test_startup_refuses_to_serve_without_auth_unless_explicitly_allowed(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED", False)
    assert any("ALLOW_UNAUTHENTICATED" in e for e in settings.startup_errors())
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED", True)
    assert settings.startup_errors() == []
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_TENANT_ID", "t")
    monkeypatch.setattr(settings, "AUTH_CLIENT_ID", "c")
    monkeypatch.setattr(settings, "AUTH_SCOPE", "s")
    assert settings.startup_errors() == []
