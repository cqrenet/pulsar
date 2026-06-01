"""Optional Azure Key Vault integration for secrets storage.

If AZURE_KEY_VAULT_NAME is configured, sensitive secrets are fetched from
Azure Key Vault at startup and injected into the environment so that
pydantic-settings can read them. Falls back to .env / environment variables
when Key Vault is not configured.

Secret naming convention in Key Vault:
    pulsar-client-secret         → CLIENT_SECRET
    pulsar-mongo-uri             → MONGO_URI
    pulsar-webhook-client-secret → WEBHOOK_CLIENT_SECRET
"""

import os

import structlog

logger = structlog.get_logger("pulsar.secrets")

_KEY_VAULT_SECRET_MAP = {
    "pulsar-client-secret": "CLIENT_SECRET",
    "pulsar-mongo-uri": "MONGO_URI",
    "pulsar-webhook-client-secret": "WEBHOOK_CLIENT_SECRET",
}


def _load_from_key_vault(vault_name: str) -> dict[str, str]:
    """Fetch secrets from Azure Key Vault and return as {env_name: value}."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure Key Vault libraries are not installed. Run: pip install azure-identity azure-keyvault-secrets"
        ) from exc

    vault_url = f"https://{vault_name}.vault.azure.net/"
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)

    loaded = {}
    for kv_name, env_name in _KEY_VAULT_SECRET_MAP.items():
        try:
            secret = client.get_secret(kv_name)
            if secret.value:
                loaded[env_name] = secret.value
                logger.info("Loaded secret from Key Vault", secret_name=kv_name)
        except Exception as exc:
            logger.warning(
                "Failed to load secret from Key Vault",
                secret_name=kv_name,
                error=str(exc),
            )
    return loaded


def load_key_vault_secrets(vault_name: str | None = None):
    """Load secrets from Azure Key Vault into os.environ if configured.

    This should be called BEFORE pydantic-settings parses configuration.
    """
    vault = vault_name or os.environ.get("AZURE_KEY_VAULT_NAME", "")
    if not vault:
        return

    logger.info("Loading secrets from Azure Key Vault", vault_name=vault)
    secrets = _load_from_key_vault(vault)
    for env_name, value in secrets.items():
        os.environ[env_name] = value
    logger.info(
        "Key Vault secrets loaded",
        count=len(secrets),
        keys=list(secrets.keys()),
    )
