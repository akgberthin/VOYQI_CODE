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
