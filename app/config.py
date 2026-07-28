"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ShellDeck"
    version: str = "0.1.0"
    author: str = "Vandexuzu"
    repo_url: str = "https://github.com/Vandexuzu/ShellDeck"
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
    # Auto-create a viewer account on first OIDC login. When False (default),
    # the IdP email must already map to an existing ShellDeck user, otherwise
    # login is rejected — prevents unknown IdP users from gaining access.
    oidc_auto_provision: bool = False


settings = Settings()

# Read the version from the VERSION file at the repo root (falls back to the
# default above if the file is missing, e.g. in some deploy layouts).
try:
    _version_file = Path(__file__).resolve().parent.parent / "VERSION"
    if _version_file.exists():
        settings.version = _version_file.read_text(encoding="utf-8").strip() or settings.version
except Exception:
    pass

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
