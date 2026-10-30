"""The SEO agents' only door into the SEO engine (marketing-svc).

Every call carries X-AI-Initiated with the agent's key, so marketing-svc
records the agent as the actor and applies every hard gate an agent
meets: it cannot approve held content, clear a held page, approve a fix
that needs a person, initiate sponsored links or change a location's
core details. Nothing here has a parameter that could skip those."""
from typing import Any

import httpx

from src.core.config import get_settings

BASE = "/api/v1/marketing/seo"


class SeoApiError(Exception):
    def __init__(self, status: int, detail: str, code: str = ""):
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code

    @property
    def needs_integration(self) -> bool:
        return self.code in ("provider_not_configured",) or (self.status == 409 and "not connected" in self.detail)


class SeoApi:
    """Calls made as one agent for one tenant; each is recorded so the
    decision log shows exactly what the agent did."""

    def __init__(self, tenant_id: str, agent_key: str, timeout: float = 600.0):
        self.tenant_id, self.agent_key, self.timeout = tenant_id, agent_key, timeout
        self.calls: list[dict] = []

    async def request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        settings = get_settings()
        headers = {"X-Tenant-Id": self.tenant_id, "X-AI-Initiated": self.agent_key}
        try:
            async with httpx.AsyncClient(base_url=settings.marketing_svc_base_url, timeout=self.timeout) as client:
                resp = await client.request(method, f"{BASE}{path}", json=body, params=params, headers=headers)
        except httpx.HTTPError as exc:
            self.calls.append({"tool": f"{method} {path}", "error": type(exc).__name__})
            raise SeoApiError(0, f"marketing-svc unreachable ({type(exc).__name__})") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:300]}
        entry = {"tool": f"{method} {path}", "status": resp.status_code}
        if body:
            entry["input"] = {k: (v if len(str(v)) < 200 else f"{str(v)[:200]}…") for k, v in body.items()}
        self.calls.append(entry)
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else str(data)
            raise SeoApiError(resp.status_code, str(detail), data.get("code", "") if isinstance(data, dict) else "")
        return data

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None} or None)

    async def post(self, path: str, body: dict | None = None) -> Any:
        return await self.request("POST", path, body or {})
