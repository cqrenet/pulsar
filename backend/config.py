from secrets_manager import load_key_vault_secrets

# Pre-load Azure Key Vault secrets into os.environ before pydantic-settings reads them.
# This is a no-op if AZURE_KEY_VAULT_NAME is not set.
load_key_vault_secrets()

from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Microsoft Graph / App credentials
    TENANT_ID: str = ""
    CLIENT_ID: str = ""
    CLIENT_SECRET: str = ""

    # MongoDB
    MONGO_URI: str = ""
    DB_NAME: str = "micro_soc"

    # Periodic fetch
    ENABLE_PERIODIC_FETCH: bool = False
    FETCH_INTERVAL_MINUTES: int = 60

    # Auth (OIDC/Bearer) settings
    AUTH_ENABLED: bool = False
    AUTH_TENANT_ID: str = ""
    AUTH_CLIENT_ID: str = ""
    AUTH_SCOPE: str = ""
    AUTH_ALLOWED_ROLES: str = ""
    AUTH_ALLOWED_GROUPS: str = ""

    # Data retention (0 = disabled)
    RETENTION_DAYS: int = 0

    # CORS
    CORS_ORIGINS: str = "*"

    # SIEM export
    SIEM_ENABLED: bool = False
    SIEM_WEBHOOK_URL: str = ""

    # Alerting
    ALERTS_ENABLED: bool = False

    # Privacy / access control
    # Entire services can be hidden, or specific operations can be gated.
    PRIVACY_SERVICES: str = ""  # comma-separated, e.g. "Exchange,Teams"
    PRIVACY_SENSITIVE_OPERATIONS: str = ""  # comma-separated, e.g. "MailItemsAccessed,Search-Mailbox,Send"
    PRIVACY_SERVICE_ROLES: str = ""  # comma-separated, e.g. "SecurityAdministrator,ComplianceAdministrator"

    # Redis (caching + async job queue)
    REDIS_URL: str = "redis://localhost:6379/0"

    # UI defaults
    DEFAULT_PAGE_SIZE: int = 24

    # Alert notifications
    ALERT_WEBHOOK_URL: str = ""
    ALERT_WEBHOOK_FORMAT: str = "generic"  # generic | slack | teams
    ALERT_DEDUPE_MINUTES: int = 15

    # Webhook security
    WEBHOOK_CLIENT_SECRET: str = ""

    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 120
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # MCP API key (lightweight alternative / complement to OIDC for MCP SSE access)
    # Accepts the key via Authorization: Bearer <key> or x-api-key header.
    # Works alongside AUTH_ENABLED — either valid Entra token OR matching key is accepted.
    MCP_API_KEY: str = ""

    # OAuth discovery (RFC 8414) — enables Claude Desktop / AURORA to discover Entra auth.
    # Set both to expose /.well-known/oauth-authorization-server.
    MCP_CLIENT_ID: str = ""  # Entra app registration client ID for the MCP scope

    # Security / docs exposure
    DOCS_ENABLED: bool = False
    METRICS_ALLOWED_IPS: str = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # SIEM webhook restriction (comma-separated domains)
    SIEM_ALLOWED_DOMAINS: str = ""

    # Optional Azure Key Vault integration for secrets
    AZURE_KEY_VAULT_NAME: str = ""


_settings = Settings()

# Backward-compatible module-level exports
TENANT_ID = _settings.TENANT_ID
CLIENT_ID = _settings.CLIENT_ID
CLIENT_SECRET = _settings.CLIENT_SECRET
MONGO_URI = _settings.MONGO_URI
DB_NAME = _settings.DB_NAME

ENABLE_PERIODIC_FETCH = _settings.ENABLE_PERIODIC_FETCH
FETCH_INTERVAL_MINUTES = _settings.FETCH_INTERVAL_MINUTES

AUTH_ENABLED = _settings.AUTH_ENABLED
AUTH_TENANT_ID = _settings.AUTH_TENANT_ID or _settings.TENANT_ID or ""
AUTH_CLIENT_ID = _settings.AUTH_CLIENT_ID or _settings.CLIENT_ID or ""
AUTH_SCOPE = _settings.AUTH_SCOPE
AUTH_ALLOWED_ROLES = {r.strip() for r in _settings.AUTH_ALLOWED_ROLES.split(",") if r.strip()}
AUTH_ALLOWED_GROUPS = {g.strip() for g in _settings.AUTH_ALLOWED_GROUPS.split(",") if g.strip()}

RETENTION_DAYS = _settings.RETENTION_DAYS
CORS_ORIGINS = [o.strip() for o in _settings.CORS_ORIGINS.split(",") if o.strip()]

SIEM_ENABLED = _settings.SIEM_ENABLED
SIEM_WEBHOOK_URL = _settings.SIEM_WEBHOOK_URL
ALERTS_ENABLED = _settings.ALERTS_ENABLED

PRIVACY_SERVICES = {s.strip() for s in _settings.PRIVACY_SERVICES.split(",") if s.strip()}
PRIVACY_SENSITIVE_OPERATIONS = {o.strip() for o in _settings.PRIVACY_SENSITIVE_OPERATIONS.split(",") if o.strip()}
PRIVACY_SERVICE_ROLES = {r.strip() for r in _settings.PRIVACY_SERVICE_ROLES.split(",") if r.strip()}

REDIS_URL = _settings.REDIS_URL
DEFAULT_PAGE_SIZE = _settings.DEFAULT_PAGE_SIZE

ALERT_WEBHOOK_URL = _settings.ALERT_WEBHOOK_URL
ALERT_WEBHOOK_FORMAT = _settings.ALERT_WEBHOOK_FORMAT
ALERT_DEDUPE_MINUTES = _settings.ALERT_DEDUPE_MINUTES

WEBHOOK_CLIENT_SECRET = _settings.WEBHOOK_CLIENT_SECRET

RATE_LIMIT_ENABLED = _settings.RATE_LIMIT_ENABLED
RATE_LIMIT_REQUESTS = _settings.RATE_LIMIT_REQUESTS
RATE_LIMIT_WINDOW_SECONDS = _settings.RATE_LIMIT_WINDOW_SECONDS

MCP_API_KEY = _settings.MCP_API_KEY.strip()
MCP_CLIENT_ID = _settings.MCP_CLIENT_ID.strip()

DOCS_ENABLED = _settings.DOCS_ENABLED
METRICS_ALLOWED_IPS = _settings.METRICS_ALLOWED_IPS

SIEM_ALLOWED_DOMAINS = [d.strip().lower() for d in _settings.SIEM_ALLOWED_DOMAINS.split(",") if d.strip()]

AZURE_KEY_VAULT_NAME = _settings.AZURE_KEY_VAULT_NAME
