"""
DMS tool adapters — used by the Pricing Intelligence Agent's trade
appraisal and retail-pricing capabilities. Vehicle valuation is one
domain that agent already covers (rental dynamic pricing), so this
reuses it rather than standing up a sixth agent.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_market_comps(tenant_id: str, vin: str) -> dict[str, Any]:
    """Placeholder for a real market-comp data source (e.g. a vehicle
    valuation API) — dms-svc doesn't have one built yet for arbitrary
    trade-in VINs, so this always returns an empty comp set today, and
    the deterministic fallback below is honest about that rather than
    inventing numbers."""
    return {"comps": []}


def get_appraisal(tenant_id: str, appraisal_id: str) -> dict[str, Any]:
    """Checked before valuing — an appraisal already under
    SECURITY_HOLD (a stolen-vehicle registry match, set by dms-svc
    itself, never by this agent) must never be valued or offered on."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.dms_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/dms/appraisals/{appraisal_id}", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def apply_trade_valuation(tenant_id: str, appraisal_id: str, valuation_usd: float) -> dict[str, Any]:
    """Fully autonomous: writes valuation AND offer in the same
    dms-svc call — there is no separate acceptance step for the
    agent's own output. Fails loudly (lets the HTTPError propagate)
    if the appraisal is under security hold, since that's dms-svc
    refusing an action that should never have been attempted."""
    settings = get_settings()
    with httpx.Client(base_url=settings.dms_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/dms/appraisals/{appraisal_id}/valuation-and-offer",
            json={"valuation_usd": valuation_usd}, headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def get_book_value_average(tenant_id: str, vin: str) -> dict[str, Any]:
    """Unlike get_market_comps, this is real: dms-svc's book_value_quotes
    table holds actual KBB/Black Book/JD Power/Manheim MMR quotes per
    VIN. Used by the retail-pricing capability, where — unlike trade
    appraisal — real market data genuinely exists to read."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.dms_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/dms/vehicles/book-value-quotes/{vin}/average", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {"average_retail_value_usd": None, "average_wholesale_value_usd": None}


def apply_retail_price(tenant_id: str, vehicle_id: str, price_usd: float, reasoning: str) -> dict[str, Any]:
    """Fully autonomous: the terminal action for the retail-pricing
    capability — applies immediately, no acceptance step. This is the
    same dms-svc endpoint the aging sweep itself calls, so a manual
    re-price request and an autonomous sweep both leave an identical
    audit trail."""
    settings = get_settings()
    with httpx.Client(base_url=settings.dms_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/dms/vehicles/{vehicle_id}/price",
            json={"price_usd": price_usd, "reasoning": reasoning}, headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def _post(tenant_id: str, path: str, json: dict | None = None) -> Any:
    settings = get_settings()
    with httpx.Client(base_url=settings.dms_svc_base_url, timeout=15.0) as client:
        resp = client.post(path, json=json or {}, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def evaluate_license_renewals(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/rooftops/evaluate-renewals")


def evaluate_aging_sweep(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/vehicles/evaluate-aging-sweep")


def desk_and_approve_deal(tenant_id: str, deal_id: str) -> dict:
    return _post(tenant_id, f"/api/v1/dms/deals/{deal_id}/desk-and-approve")


def pull_bureau_and_route(tenant_id: str, app_id: str, bureau: str, fico: int, ofac_match: bool) -> dict:
    return _post(tenant_id, f"/api/v1/dms/lending/credit-applications/{app_id}/pull-bureau", {"bureau": bureau, "fico": fico, "ofac_match": ofac_match})


def dispatch_repair_order(tenant_id: str, rooftop_id: str, vin: str, customer_name: str, description: str, warranty: bool) -> dict:
    return _post(tenant_id, "/api/v1/dms/service/repair-orders", {"rooftop_id": rooftop_id, "vin": vin, "customer_name": customer_name, "description": description, "warranty": warranty})


def evaluate_parts_reorder(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/parts/evaluate-reorder")


def evaluate_loaner_overdue(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/loaners/evaluate-overdue")


def evaluate_franchise_remediation(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/franchise/evaluate-remediation")


def evaluate_dealer_performance(tenant_id: str) -> list[dict]:
    return _post(tenant_id, "/api/v1/dms/performance/evaluate-and-intervene")
    """Not a remote call — this tool's only job is to force the agent
    to report a structured number and its own justification rather than
    leaving the valuation buried in free-form prose."""
    return {"valuation_usd": valuation_usd, "reasoning": reasoning}


def submit_price_recommendation(recommended_price_usd: float, reasoning: str) -> dict[str, Any]:
    """Same structured-output discipline as submit_valuation, for the
    retail-pricing capability specifically — kept as a distinct tool so
    the DecisionLog's tools_called list unambiguously shows which
    capability produced which number."""
    return {"recommended_price_usd": recommended_price_usd, "reasoning": reasoning}


DMS_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_market_comps",
        "description": "Fetch market comparable sales for a VIN's make/model/year, if available.",
        "input_schema": {
            "type": "object",
            "properties": {"vin": {"type": "string"}},
            "required": ["vin"],
        },
    },
    {
        "name": "submit_valuation",
        "description": (
            "Report your trade-in valuation estimate in USD along with the reasoning behind it. "
            "Always call this once you've reached a conclusion — don't only describe it in prose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "valuation_usd": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["valuation_usd", "reasoning"],
        },
    },
]

RETAIL_PRICING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_book_value_average",
        "description": "Fetch the real average KBB/Black Book/JD Power/Manheim MMR book value for a VIN, if any quotes are on file.",
        "input_schema": {
            "type": "object",
            "properties": {"vin": {"type": "string"}},
            "required": ["vin"],
        },
    },
    {
        "name": "submit_price_recommendation",
        "description": (
            "Report your recommended retail asking price in USD along with the reasoning behind it. "
            "Always call this once you've reached a conclusion — don't only describe it in prose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_price_usd": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["recommended_price_usd", "reasoning"],
        },
    },
]


def build_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_market_comps": lambda inp: get_market_comps(tenant_id, inp["vin"]),
        "submit_valuation": lambda inp: submit_valuation(inp["valuation_usd"], inp["reasoning"]),
    }


def build_retail_pricing_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_book_value_average": lambda inp: get_book_value_average(tenant_id, inp["vin"]),
        "submit_price_recommendation": lambda inp: submit_price_recommendation(inp["recommended_price_usd"], inp["reasoning"]),
    }


def submit_max_bid(max_bid_usd: float | None, reasoning: str) -> dict[str, Any]:
    """Structured-output discipline for the max-bid capability. Accepts
    None explicitly: 'I cannot recommend a ceiling' is itself a
    structured answer, not a failure to answer."""
    return {"max_bid_usd": max_bid_usd, "reasoning": reasoning}


MAX_BID_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_book_value_average",
        "description": "Fetch the real average wholesale/retail book value for a VIN, if any quotes are on file.",
        "input_schema": {
            "type": "object",
            "properties": {"vin": {"type": "string"}},
            "required": ["vin"],
        },
    },
    {
        "name": "submit_max_bid",
        "description": (
            "Report your recommended maximum bid ceiling in USD with reasoning, or pass max_bid_usd=null "
            "with the reason you cannot recommend one. Always call this to conclude — don't only describe it in prose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_bid_usd": {"type": ["number", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["max_bid_usd", "reasoning"],
        },
    },
]


def build_max_bid_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_book_value_average": lambda inp: get_book_value_average(tenant_id, inp["vin"]),
        "submit_max_bid": lambda inp: submit_max_bid(inp["max_bid_usd"], inp["reasoning"]),
    }
