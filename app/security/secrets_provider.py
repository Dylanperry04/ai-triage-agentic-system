"""
Secrets provider interface (Key Vault seam).

The app reads secrets (e.g. the pseudonymisation salt, Azure OpenAI keys) through
this interface so the SOURCE of secrets is swappable by config without touching
call sites. In production this is backed by Azure Key Vault via managed identity;
locally it falls back to environment variables.

This module does NOT itself talk to Key Vault (that dependency belongs in the
deployment image); it defines the interface and a local env-backed implementation,
plus a factory that selects the provider from SECRETS_PROVIDER. The Key Vault
implementation is a small documented adapter the deployment supplies.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol


class SecretsProvider(Protocol):
    def get_secret(self, name: str) -> Optional[str]:
        ...


class EnvSecretsProvider:
    """Local/demo: read secrets from environment variables."""
    def get_secret(self, name: str) -> Optional[str]:
        return os.environ.get(name)


class KeyVaultSecretsProvider:
    """
    Production seam for Azure Key Vault via managed identity.

    The Azure imports are intentionally lazy so the public/local demo does not
    require Azure credentials. In deployment, either pass a configured
    azure.keyvault.secrets.SecretClient, or set KEY_VAULT_URL / AZURE_KEY_VAULT_URL
    and let the factory build one with ManagedIdentityCredential.

    Example production wiring (in the deployment image, not here):
        from azure.identity import ManagedIdentityCredential
        from azure.keyvault.secrets import SecretClient
        client = SecretClient(vault_url=KV_URL, credential=ManagedIdentityCredential())
        provider = KeyVaultSecretsProvider(client=client)
    """
    def __init__(self, client=None):
        self.client = client

    def get_secret(self, name: str) -> Optional[str]:
        if self.client is None:
            # No real Key Vault wired. In patient-data mode, refuse rather than
            # silently fall back to a less-secure source.
            if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
                raise SecretsProviderNotConfiguredError(
                    f"SECRETS_PROVIDER=keyvault but no Key Vault client is wired "
                    f"(requested secret '{name}'). Wire KeyVaultSecretsProvider "
                    "with a Managed Identity client in patient-data mode."
                )
            return None  # fail closed: no real vault wired
        try:
            return self.client.get_secret(name).value
        except Exception:
            return None


class SecretsProviderNotConfiguredError(RuntimeError):
    """Raised when Key Vault is required but no client is wired."""


def get_secrets_provider() -> SecretsProvider:
    """Select the provider from SECRETS_PROVIDER (default: env for local/demo)."""
    kind = os.environ.get("SECRETS_PROVIDER", "env").lower()
    if kind == "keyvault":
        vault_url = (
            os.environ.get("KEY_VAULT_URL")
            or os.environ.get("AZURE_KEY_VAULT_URL")
            or ""
        ).strip()
        if vault_url:
            try:
                from azure.identity import ManagedIdentityCredential
                from azure.keyvault.secrets import SecretClient

                client_id = os.environ.get("AZURE_CLIENT_ID") or None
                credential = (
                    ManagedIdentityCredential(client_id=client_id)
                    if client_id else ManagedIdentityCredential()
                )
                return KeyVaultSecretsProvider(
                    client=SecretClient(vault_url=vault_url, credential=credential)
                )
            except Exception:
                # Return the fail-closed seam; patient-data mode will surface a
                # clear error when the secret is requested/probed.
                return KeyVaultSecretsProvider()
        # No vault URL/client wired. This fails closed when used in patient mode.
        return KeyVaultSecretsProvider()
    return EnvSecretsProvider()
