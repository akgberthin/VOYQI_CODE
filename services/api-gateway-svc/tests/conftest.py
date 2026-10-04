import os

os.environ.setdefault("GATEWAY_ALLOW_INSECURE_DEV_SECRET", "true")
os.environ.setdefault("GATEWAY_JWT_SECRET", "test-secret-shared-with-auth-svc-minimum-32-bytes")

import uuid
from datetime import datetime, timedelta, timezone

import jwt


def make_test_token(*, tenant_id="voyqi-holdings", user_id=None, user_credential_id=None, expired=False, token_type="access"):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_credential_id or uuid.uuid4()),
        "user_id": str(user_id or uuid.uuid4()),
        "tenant_id": tenant_id,
        "type": token_type,
        "iat": now,
        "exp": now + (timedelta(minutes=-5) if expired else timedelta(minutes=15)),
    }
    return jwt.encode(payload, os.environ["GATEWAY_JWT_SECRET"], algorithm="HS256")
