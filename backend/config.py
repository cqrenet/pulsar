from secrets_manager import load_key_vault_secrets

# Pre-load Azure Key Vault secrets into os.environ before pydantic-settings reads them.
# This is a no-op if AZURE_KEY_VAULT_NAME is not set.
load_key_vault_secrets()

from typing import Annotated  # noqa: E402

from pydantic import Field, field_validator, model_validator  # noqa: E402
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict  # noqa: E402


def _csv(value):
    """Split a comma-separated setting, dropping blanks. Lists and sets pass through."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# NoDecode: pydantic-settings would otherwise try to parse these env values as JSON.
CsvList = Annotated[list[str], NoDecode]
CsvSet = Annotated[set[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ingestion app registration (app-only Graph / O365 permissions).
    # Authenticates with a certificate: a PEM file holding the private key and the certificate.
    TENANT_ID: str = ""
    CLIENT_ID: str = ""
    CLIENT_CERTIFICATE_PATH: str = ""

    # MongoDB
    MONGO_URI: str = ""
    DB_NAME: str = "micro_soc"
    # Separate database so MongoDB can make the audit trail insert-only for the app user
    AUDIT_DB_NAME: str = "micro_soc_audit"

    # Periodic fetch
    ENABLE_PERIODIC_FETCH: bool = False
    FETCH_INTERVAL_MINUTES: int = 60

    # Auth (OIDC/Bearer) settings
    AUTH_ENABLED: bool = False
    # Local development only. PULSAR refuses to start without auth unless this is set.
    ALLOW_UNAUTHENTICATED: bool = False
    AUTH_TENANT_ID: str = ""
    AUTH_CLIENT_ID: str = ""
    AUTH_SCOPE: str = ""
    AUTH_ALLOWED_ROLES: CsvSet = Field(default_factory=set)
    AUTH_ALLOWED_GROUPS: CsvSet = Field(default_factory=set)
    # Roles allowed to change alert rules and trigger fetches (empty = any signed-in user)
    AUTH_ADMIN_ROLES: CsvSet = Field(default_factory=set)

    # Data retention (0 = disabled)
    RETENTION_DAYS: int = 0

    # CORS
    CORS_ORIGINS: CsvList = Field(default_factory=list)  # empty = same-origin only

    # SIEM export
    SIEM_ENABLED: bool = False
    SIEM_WEBHOOK_URL: str = ""

    # Alerting
    ALERTS_ENABLED: bool = False

    # Privacy / access control
    # Entire services can be hidden, or specific operations can be gated.
    PRIVACY_SERVICES: CsvSet = Field(default_factory=set)  # comma-separated, e.g. "Exchange,Teams"
    PRIVACY_SENSITIVE_OPERATIONS: CsvSet = Field(
        default_factory=set
    )  # comma-separated, e.g. "MailItemsAccessed,Search-Mailbox,Send"
    PRIVACY_SERVICE_ROLES: CsvSet = Field(
        default_factory=set
    )  # comma-separated, e.g. "SecurityAdministrator,ComplianceAdministrator"

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

    # MCP allowed hosts for DNS rebinding protection.
    # Comma-separated list of host[:port] values that the MCP SSE transport will accept
    # in the Host header.  Add your public hostname here when running behind a reverse
    # proxy (e.g. "pulsar.cqre.net").  Loopback entries are always allowed.
    MCP_ALLOWED_HOSTS: CsvList = Field(default_factory=list)

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
    # Reverse proxies whose X-Forwarded-For is trusted (read right to left). Anything else is ignored.
    TRUSTED_PROXIES: str = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # SIEM webhook restriction (comma-separated domains)
    SIEM_ALLOWED_DOMAINS: CsvList = Field(default_factory=list)

    # Optional Azure Key Vault integration for secrets
    AZURE_KEY_VAULT_NAME: str = ""

    @field_validator(
        "AUTH_ALLOWED_ROLES",
        "AUTH_ALLOWED_GROUPS",
        "AUTH_ADMIN_ROLES",
        "CORS_ORIGINS",
        "PRIVACY_SERVICES",
        "PRIVACY_SENSITIVE_OPERATIONS",
        "PRIVACY_SERVICE_ROLES",
        "MCP_ALLOWED_HOSTS",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value):
        return _csv(value)

    @field_validator("SIEM_ALLOWED_DOMAINS", mode="before")
    @classmethod
    def _split_domains(cls, value):
        return [d.lower() for d in _csv(value)]

    @field_validator("MCP_API_KEY", "MCP_CLIENT_ID", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _default_auth_tenant(self):
        # The API app lives in the same tenant unless told otherwise. No fallback for AUTH_CLIENT_ID:
        # CLIENT_ID is the ingestion app, which exposes no API.
        self.AUTH_TENANT_ID = self.AUTH_TENANT_ID or self.TENANT_ID
        return self

    def startup_errors(self) -> list[str]:
        """Reasons PULSAR must refuse to start. Empty when the configuration is safe to serve data."""
        errors = []
        missing = self.missing_auth_settings()
        if missing:
            errors.append(f"AUTH_ENABLED=true but {', '.join(missing)} not set")
        if not self.AUTH_ENABLED and not self.ALLOW_UNAUTHENTICATED:
            errors.append(
                "AUTH_ENABLED=false (set AUTH_ENABLED=true, or ALLOW_UNAUTHENTICATED=true for local development only)"
            )
        return errors

    def missing_auth_settings(self) -> list[str]:
        """Settings that must be present when AUTH_ENABLED=true. Startup refuses to continue without them."""
        if not self.AUTH_ENABLED:
            return []
        required = {
            "AUTH_TENANT_ID": self.AUTH_TENANT_ID,
            "AUTH_CLIENT_ID": self.AUTH_CLIENT_ID,
            "AUTH_SCOPE": self.AUTH_SCOPE,
        }
        return [name for name, value in required.items() if not value]


# The one settings object. Read attributes at call time (settings.X), never copy them at import,
# so tests can override a value in one place.
settings = Settings()
