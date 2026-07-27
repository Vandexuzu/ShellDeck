"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ShellDeck"
    secret_key: str = "change-me-to-a-long-random-string"
    access_token_expire_minutes: int = 1440

    database_url: str = "sqlite:///./shelldeck.db"

    host: str = "0.0.0.0"
    port: int = 8000

    # Skip SSH host key checking. Convenient for homelab, but weakens MITM
    # protection. Set to False in production and manage known_hosts instead.
    ssh_ignore_known_hosts: bool = True

    # Optional OIDC single sign-on. Leave unset to disable.
    oidc_enabled: bool = False
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_discovery_url: str = ""   # e.g. https://accounts.google.com/.well-known/openid-configuration
    oidc_scopes: str = "openid email profile"


settings = Settings()

# Derive a stable 32-byte url-safe Fernet key from the app secret.
_FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
_fernet = Fernet(_FERNET_KEY)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret string (e.g. SSH password / key) for storage."""
    if not plaintext:
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a value produced by :func:`encrypt`."""
    if not token:
        return ""
    return _fernet.decrypt(token.encode()).decode()
