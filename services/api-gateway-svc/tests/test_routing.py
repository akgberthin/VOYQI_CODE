from src.services.routing import is_public_path, resolve_route


def test_resolves_known_prefix():
    route = resolve_route("/api/v1/auth/login")
    assert route is not None
    assert route.target_base_url == "http://auth-svc:8024"


def test_unknown_path_returns_none():
    assert resolve_route("/api/v1/does-not-exist") is None


def test_login_is_public():
    route = resolve_route("/api/v1/auth/login")
    assert is_public_path(route, "/api/v1/auth/login") is True


def test_me_is_not_public():
    route = resolve_route("/api/v1/auth/me")
    assert is_public_path(route, "/api/v1/auth/me") is False


def test_user_register_is_public_but_other_user_paths_are_not():
    register_route = resolve_route("/api/v1/user/users/register")
    assert is_public_path(register_route, "/api/v1/user/users/register") is True

    get_user_route = resolve_route("/api/v1/user/users/some-id")
    assert is_public_path(get_user_route, "/api/v1/user/users/some-id") is False


def test_every_downstream_prefix_is_distinct_and_non_overlapping_in_a_confusing_way():
    """Guards against accidentally adding a route prefix that's a
    substring of another in a way that would misroute (e.g. adding
    /api/v1/hr as well as /api/v1/hrm would make longest-prefix-match
    essential, not incidental)."""
    hrm_route = resolve_route("/api/v1/hrm/employees")
    assert hrm_route.target_base_url == "http://hrm-svc:8023"


def test_ip_asset_svc_route_resolves():
    route = resolve_route("/api/v1/ip/assets")
    assert route is not None
    assert route.target_base_url == "http://ip-asset-svc:8028"


def test_admin_layer_services_are_routed():
    """The Layer 2 services built after the original route table — each
    must be reachable through the gateway, not only by direct port."""
    expected = {
        "/api/v1/sales/orders": "http://sales-service:8009",
        "/api/v1/insurance-risk/policies": "http://insurance-risk-svc:8029",
        "/api/v1/security-compliance/grants": "http://security-compliance-svc:8031",
        "/api/v1/executive/dashboard/summary": "http://executive-svc:8032",
        "/api/v1/management/cost-centers": "http://management-svc:8033",
        "/api/v1/real-estate/properties": "http://real-estate-svc:8034",
        "/api/v1/capital-finance/funding-sources": "http://capital-finance-svc:8035",
        "/api/v1/originations/credit-decisions": "http://originations-svc:8036",
    }
    for path, target in expected.items():
        route = resolve_route(path)
        assert route is not None, path
        assert route.target_base_url == target, path
        assert is_public_path(route, path) is False, path


def test_identity_and_org_route_to_the_services_that_own_them():
    assert resolve_route("/api/v1/identity/break-glass").target_base_url == "http://security-compliance-svc:8031"
    assert resolve_route("/api/v1/org/cases").target_base_url == "http://originations-svc:8036"


def test_no_route_points_at_a_service_that_does_not_exist():
    from src.services.routing import ROUTE_TABLE

    hosts = {r.target_base_url.split("//")[1].split(":")[0] for r in ROUTE_TABLE}
    assert not hosts & {"identity-svc", "org-svc", "cms-svc", "platform-config-svc"}


def test_landing_page_capture_is_public_but_listing_captures_is_not():
    route = resolve_route("/api/v1/marketing/public/lead-captures")
    assert is_public_path(route, "/api/v1/marketing/public/lead-captures") is True
    assert is_public_path(route, "/api/v1/marketing/lead-captures") is False


def test_hosted_marketing_media_is_public_but_nothing_else_under_marketing():
    from src.services.routing import is_public_path, resolve_route

    media = "/api/v1/marketing/public/media/5b2e0c6a-3f7e-4b1d-9a51-7d1f0a3c9e11"
    assert is_public_path(resolve_route(media), media)
    for private in ("/api/v1/marketing/media", "/api/v1/marketing/campaigns", "/api/v1/marketing/public/media/../campaigns"):
        assert not is_public_path(resolve_route(private), private)


def test_open_pixel_and_tracked_links_are_public():
    from src.services.routing import is_public_path, resolve_route

    for path in ("/api/v1/marketing/public/t/o/abc.def.gif", "/api/v1/marketing/public/t/c/abc.def"):
        assert is_public_path(resolve_route(path), path)
    for private in ("/api/v1/marketing/analytics/send-times", "/api/v1/marketing/public/t/../campaigns"):
        assert not is_public_path(resolve_route(private), private)
