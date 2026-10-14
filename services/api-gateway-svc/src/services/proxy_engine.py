"""Request forwarding. For any authenticated path, the tenant/user
identity injected into the downstream request comes from the verified
JWT, never from a client-supplied header — a client cannot claim to be
a different tenant or user than what their own token says, which is a
real security property the platform didn't have when every service
trusted whatever X-Tenant-Id header showed up.
"""
import httpx

from src.core.config import get_settings
from src.core.tokens import TokenInvalidError, decode_access_token
from src.services.authorization import AuthorizationError, authorize
from src.services.rate_limiter import check_rate_limit
from src.services.routing import is_public_path, resolve_route

settings = get_settings()

# Headers that must never be forwarded as-is between hops.
HOP_BY_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length"}


class GatewayError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class ForwardResult:
    def __init__(self, status_code: int, headers: dict, content: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = content


def _clean_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


async def handle_request(
    *, method: str, path: str, query_string: str, headers: dict, body: bytes,
    access_token_cookie: str | None, client_host: str | None,
) -> ForwardResult:
    route = resolve_route(path)
    if route is None:
        raise GatewayError(404, f"No route configured for path: {path}")

    public = is_public_path(route, path)
    outgoing_headers = _clean_headers(headers)

    if public:
        tenant_id_for_rate_limit = headers.get("x-tenant-id", client_host or "unknown")
    else:
        if not access_token_cookie:
            raise GatewayError(401, "Not authenticated")
        try:
            claims = decode_access_token(access_token_cookie)
        except TokenInvalidError as exc:
            raise GatewayError(401, str(exc)) from exc
        try:
            await authorize(claims, route, method, path)
        except AuthorizationError as exc:
            raise GatewayError(exc.status_code, exc.detail) from exc
        # Enforced from the verified token — a client cannot override these via headers.
        outgoing_headers["x-tenant-id"] = claims["tenant_id"]
        outgoing_headers["x-user-id"] = claims["user_id"]
        outgoing_headers["x-user-credential-id"] = claims["sub"]
        tenant_id_for_rate_limit = claims["tenant_id"]

    allowed, remaining = check_rate_limit(tenant_id=tenant_id_for_rate_limit, path_prefix=route.prefix)
    if not allowed:
        raise GatewayError(429, f"Rate limit exceeded for {route.prefix}")

    target_url = f"{route.target_base_url}{path}"
    if query_string:
        target_url = f"{target_url}?{query_string}"

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        try:
            response = await client.request(method, target_url, headers=outgoing_headers, content=body)
        except httpx.HTTPError as exc:
            raise GatewayError(502, f"Upstream service unreachable: {route.target_base_url} ({exc})") from exc

    response_headers = _clean_headers(dict(response.headers))
    response_headers["x-ratelimit-remaining"] = str(remaining)
    return ForwardResult(status_code=response.status_code, headers=response_headers, content=response.content)
