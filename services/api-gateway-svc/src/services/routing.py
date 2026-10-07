"""The gateway's routing table: every downstream service this platform
has, keyed by the path prefix a client sends. This is what makes
"API-first gateway-only ingress" (stated as non-negotiable since the
platform's earliest PRD) an actual enforced mechanism rather than a
documented intention — every one of these prefixes was previously only
reachable by hitting a service directly.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RouteConfig:
    prefix: str
    target_base_url: str
    # Exact paths under this prefix that skip JWT verification — login,
    # registration, refresh, password reset, and health checks. Every
    # other path under the prefix requires a valid access token.
    public_paths: frozenset[str] = field(default_factory=frozenset)


ROUTE_TABLE: list[RouteConfig] = [
    RouteConfig(
        prefix="/api/v1/auth",
        target_base_url="http://auth-svc:8024",
        public_paths=frozenset({
            "/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/refresh",
            "/api/v1/auth/mfa/verify", "/api/v1/auth/password-reset/request", "/api/v1/auth/password-reset/confirm",
        }),
    ),
    RouteConfig(prefix="/api/v1/user", target_base_url="http://user-svc:8025", public_paths=frozenset({"/api/v1/user/users/register"})),
    RouteConfig(prefix="/api/v1/notifications", target_base_url="http://notification-svc:8027"),
    RouteConfig(prefix="/api/v1/subscription", target_base_url="http://subscription-svc:8003"),
    RouteConfig(prefix="/api/v1/fleet", target_base_url="http://fleet-svc:8004"),
    RouteConfig(prefix="/api/v1/rental", target_base_url="http://rental-svc:8005"),
    RouteConfig(prefix="/api/v1/hub", target_base_url="http://hub-svc:8006"),
    RouteConfig(prefix="/api/v1/marketing", target_base_url="http://marketing-svc:8007"),
    RouteConfig(prefix="/api/v1/dms", target_base_url="http://dms-svc:8008"),
    RouteConfig(prefix="/api/v1/pricing", target_base_url="http://pricing-svc:8010"),
    RouteConfig(prefix="/api/v1/rbac", target_base_url="http://rbac-svc:8011"),
    RouteConfig(prefix="/api/v1/identity", target_base_url="http://identity-svc:8014"),
    RouteConfig(prefix="/api/v1/platform-config", target_base_url="http://platform-config-svc:8015"),
    RouteConfig(prefix="/api/v1/cms", target_base_url="http://cms-svc:8016"),
    RouteConfig(prefix="/api/v1/ai", target_base_url="http://ai-orchestration-svc:8020"),
    RouteConfig(prefix="/api/v1/crm", target_base_url="http://crm-svc:8021"),
    RouteConfig(prefix="/api/v1/org", target_base_url="http://org-svc:8022"),
    RouteConfig(prefix="/api/v1/hrm", target_base_url="http://hrm-svc:8023"),
    RouteConfig(prefix="/api/v1/payment", target_base_url="http://payment-svc:8026"),
    RouteConfig(prefix="/api/v1/ip", target_base_url="http://ip-asset-svc:8028"),
    RouteConfig(prefix="/api/v1/sales", target_base_url="http://sales-service:8009"),
    RouteConfig(prefix="/api/v1/insurance-risk", target_base_url="http://insurance-risk-svc:8029"),
    RouteConfig(prefix="/api/v1/security-compliance", target_base_url="http://security-compliance-svc:8031"),
    RouteConfig(prefix="/api/v1/executive", target_base_url="http://executive-svc:8032"),
    RouteConfig(prefix="/api/v1/management", target_base_url="http://management-svc:8033"),
    RouteConfig(prefix="/api/v1/real-estate", target_base_url="http://real-estate-svc:8034"),
    RouteConfig(prefix="/api/v1/capital-finance", target_base_url="http://capital-finance-svc:8035"),
    RouteConfig(prefix="/api/v1/originations", target_base_url="http://originations-svc:8036"),
]


def resolve_route(path: str) -> RouteConfig | None:
    """Longest-prefix match, so e.g. /api/v1/user/users/register (a more
    specific public path) still resolves under the /api/v1/user route."""
    matches = [r for r in ROUTE_TABLE if path.startswith(r.prefix)]
    if not matches:
        return None
    return max(matches, key=lambda r: len(r.prefix))


def is_public_path(route: RouteConfig, path: str) -> bool:
    return path in route.public_paths
