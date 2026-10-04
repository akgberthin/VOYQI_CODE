from typing import Any

import jwt

from src.core.config import get_settings

settings = get_settings()


class TokenInvalidError(Exception):
    pass


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalidError("access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError(f"invalid access token: {exc}") from exc
    if payload.get("type") != "access":
        raise TokenInvalidError("not an access token")
    return payload
