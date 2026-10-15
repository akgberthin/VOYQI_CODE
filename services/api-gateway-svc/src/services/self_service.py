"""Object-level access for self-scoped roles (members).

A role granted "<module>.self" instead of "<module>.read"/"write" may only
call the endpoints listed here, and only on records it owns. Ownership is
checked against the owning service before the request is forwarded:

  catalog       no ownership (e.g. the public plan list)
  path_user     the {user_id} in the path is the caller
  member        the member profile belongs to the caller
  subscription  the subscription's member profile belongs to the caller
  upgrade_case  the upgrade case's member profile belongs to the caller
  charge        the payment charge belongs to the caller
  body_user     the JSON body's user_id is the caller
  body_member   the JSON body's member_profile_id belongs to the caller

Anything not listed is refused. A failed lookup is a refusal too (and
the same refusal as "not yours", so record ids can't be probed).
"""
import json
import re
import time

import httpx

from src.core.config import get_settings
from src.services.routing import resolve_route

_ID = r"(?P<id>[^/]+)"
SELF_ROUTES: list[tuple[str, re.Pattern, str]] = [
    (m, re.compile(p), rule) for m, p, rule in [
        ("GET", r"^/api/v1/subscription/plans$", "catalog"),
        ("GET", rf"^/api/v1/subscription/members/by-user-id/{_ID}$", "path_user"),
        ("POST", r"^/api/v1/subscription/members$", "body_user"),
        ("GET", rf"^/api/v1/subscription/members/{_ID}$", "member"),
        ("PATCH", rf"^/api/v1/subscription/members/{_ID}$", "member"),
        ("GET", rf"^/api/v1/subscription/members/{_ID}/(gu-ledger|invoices|pause-count|travel-declarations|upgrade-pattern)$", "member"),
        ("POST", rf"^/api/v1/subscription/members/{_ID}/request-upgrade-evaluation$", "member"),
        ("POST", r"^/api/v1/subscription/subscriptions$", "body_member"),
        ("GET", rf"^/api/v1/subscription/subscriptions/{_ID}$", "subscription"),
        ("POST", rf"^/api/v1/subscription/subscriptions/{_ID}/(pause|resume|initiate-cancellation|finalize-cancellation|accept-winback)$", "subscription"),
        ("POST", r"^/api/v1/subscription/(above-class-requests|travel-declarations|swap-requests|gu/redeem)$", "body_member"),
        ("GET", rf"^/api/v1/subscription/upgrade-cases/{_ID}$", "upgrade_case"),
        ("POST", rf"^/api/v1/subscription/upgrade-cases/{_ID}/(accept|decline)$", "upgrade_case"),
        ("POST", r"^/api/v1/payment/payment-methods$", "body_user"),
        ("POST", r"^/api/v1/payment/charges$", "body_user"),
        ("GET", rf"^/api/v1/payment/charges/{_ID}$", "charge"),
    ]
]

# rule -> (lookup path template, field holding the owner, follow-up rule)
LOOKUPS = {
    "member": ("/api/v1/subscription/members/{id}", "user_id", None),
    "subscription": ("/api/v1/subscription/subscriptions/{id}", "member_profile_id", "member"),
    "upgrade_case": ("/api/v1/subscription/upgrade-cases/{id}", "member_profile_id", "member"),
    "charge": ("/api/v1/payment/charges/{id}", "user_id", None),
}
OWNER_CACHE_SECONDS = 300.0
_owner_cache: dict[tuple[str, str, str], tuple[float, str | None]] = {}


class NotOwnerError(Exception):
    pass


def reset_cache() -> None:
    _owner_cache.clear()


def match(method: str, path: str) -> tuple[str, str | None] | None:
    for route_method, pattern, rule in SELF_ROUTES:
        found = pattern.match(path)
        if route_method == method.upper() and found:
            return rule, found.groupdict().get("id")
    return None


async def fetch_owner(tenant_id: str, kind: str, record_id: str) -> str | None:
    """The user (or, for subscription/upgrade_case, the member profile)
    that owns a record, from the owning service."""
    path, field, _next = LOOKUPS[kind]
    path = path.format(id=record_id)
    base_url = resolve_route(path).target_base_url
    try:
        async with httpx.AsyncClient(timeout=get_settings().request_timeout_seconds) as client:
            resp = await client.get(f"{base_url}{path}", headers={"x-tenant-id": tenant_id})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    value = resp.json().get(field)
    return str(value) if value is not None else None


async def _owner(tenant_id: str, kind: str, record_id: str) -> str | None:
    key = (tenant_id, kind, record_id)
    cached = _owner_cache.get(key)
    if cached and time.monotonic() - cached[0] < OWNER_CACHE_SECONDS:
        return cached[1]
    owner = await fetch_owner(tenant_id, kind, record_id)
    if owner is not None:  # never cache a miss — the record may appear
        _owner_cache[key] = (time.monotonic(), owner)
    return owner


async def owning_user(tenant_id: str, kind: str, record_id: str) -> str | None:
    owner = await _owner(tenant_id, kind, record_id)
    follow_up = LOOKUPS[kind][2]
    if owner is not None and follow_up:
        return await owning_user(tenant_id, follow_up, owner)
    return owner


def _body_field(body: bytes, field: str) -> str | None:
    try:
        value = json.loads(body or b"{}").get(field)
    except (ValueError, AttributeError):
        return None
    return str(value) if value is not None else None


async def check(claims: dict, method: str, path: str, body: bytes) -> None:
    """Raises NotOwnerError unless this self-scoped request is on a
    listed endpoint and on the caller's own record."""
    matched = match(method, path)
    if matched is None:
        raise NotOwnerError("this endpoint is not available to your role")
    rule, record_id = matched
    caller, tenant = claims["user_id"], claims["tenant_id"]
    if rule == "catalog":
        return
    if rule == "path_user":
        owner = record_id
    elif rule == "body_user":
        owner = _body_field(body, "user_id")
    elif rule == "body_member":
        member = _body_field(body, "member_profile_id")
        owner = await owning_user(tenant, "member", member) if member else None
    else:
        owner = await owning_user(tenant, rule, record_id)
    if owner != caller:
        raise NotOwnerError("you can only access your own records")
