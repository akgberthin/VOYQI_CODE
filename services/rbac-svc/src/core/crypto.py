"""Encryption at rest for integration credentials (Fernet: AES-128-CBC +
HMAC-SHA256, from the `cryptography` package)."""
import json

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import get_settings


class EncryptionUnavailable(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().integrations_key
    if not key:
        raise EncryptionUnavailable("RBAC_INTEGRATIONS_KEY is not set — credentials can't be stored")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise EncryptionUnavailable("RBAC_INTEGRATIONS_KEY is not a valid Fernet key") from exc


def encrypt(config: dict) -> str:
    return _fernet().encrypt(json.dumps(config).encode()).decode()


def decrypt(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise EncryptionUnavailable("stored credentials can't be decrypted with the current RBAC_INTEGRATIONS_KEY") from exc
