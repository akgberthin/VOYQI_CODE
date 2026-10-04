"""
Real, unmocked tests against dms_tools.py's actual HTTP call sites.

Every existing test elsewhere in this suite monkeypatches dms_tools
functions directly, which is correct for testing the *agents* in
isolation but means the actual path strings these functions send to
dms-svc are never exercised anywhere else. This file exists
specifically to close that gap: it requires a real dms-svc instance
reachable at AI_ORCH_DMS_SVC_BASE_URL and calls the functions for
real, the same way a live integration check would, rather than
mocking the transport.

Skipped automatically if no dms-svc is reachable, so this doesn't
break a normal unit-test run — run it explicitly against a live
dms-svc to get its real value.
"""
import httpx
import pytest

from src.core.config import get_settings
from src.services import dms_tools


def _dms_svc_reachable() -> bool:
    settings = get_settings()
    try:
        httpx.get(settings.dms_svc_base_url + "/health", timeout=1.0)
        return True
    except httpx.HTTPError:
        return False


requires_live_dms_svc = pytest.mark.skipif(not _dms_svc_reachable(), reason="dms-svc is not reachable at the configured base URL — run this against a live instance")


@requires_live_dms_svc
def test_get_book_value_average_hits_the_real_registered_route():
    """Regression: this call used to 404 against the real dms-svc
    because it was missing the '/vehicles' path segment that
    inventory.py's router actually registers under — silently
    swallowed by the try/except and reported as no book value data,
    for every retail-pricing and trade-appraisal decision. Requires a
    VIN with a seeded quote to assert real values back; without one,
    this at minimum confirms the call reaches dms-svc as a 200/None
    rather than dying on a swallowed 404 for a wrong path."""
    settings = get_settings()
    tenant_id = "voyqi-holdings"
    vin = "TEST-VIN-DMS-TOOLS-REGRESSION"

    with httpx.Client(base_url=settings.dms_svc_base_url, timeout=10.0) as client:
        create_resp = client.post(
            "/api/v1/dms/vehicles/book-value-quotes",
            json={"vin": vin, "source": "kbb", "retail_usd": 48000, "wholesale_usd": 42000},
            headers={"X-Tenant-Id": tenant_id},
        )
        assert create_resp.status_code == 201

    result = dms_tools.get_book_value_average(tenant_id, vin)
    assert result["average_retail_value_usd"] == 48000.0
    assert result["average_wholesale_value_usd"] == 42000.0
