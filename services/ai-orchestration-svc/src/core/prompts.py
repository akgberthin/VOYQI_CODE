"""AI-003 Prompt & Policy Manager — the runtime side.

- registry(): every agent system prompt in the code, keyed
  "<agent module>.<CONSTANT>" (e.g. "concierge_agent.MESSAGE_ASSESSMENT_SYSTEM_PROMPT").
  The code's text is the default each tenant starts from.
- evaluate(): the server-side check a new prompt version must pass before
  it can be promoted. The agents' code depends on the model calling
  specific tools (submit_valuation, get_lead_score, ...); a prompt that no
  longer mentions them, or drops "Always finish by calling ...", breaks
  the agent even though it reads fine to a person.
- resolve(): what AnthropicAgentClient actually sends — the tenant's
  promoted version of that prompt if there is one, else the default.
  Active versions are cached in-process and refreshed periodically, so a
  promotion reaches every replica within PROMPT_REFRESH_SECONDS.
"""
import contextvars
import importlib
import pkgutil
import re
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PROMPT_REFRESH_SECONDS = 60
MAX_PROMPT_CHARS = 20_000
FINISH_RULE = "Always finish by calling"

# The tenant the current request or event belongs to; set by
# security.get_tenant_id and supervisor.route_event.
current_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_tenant", default=None)

# (tenant_id, prompt_key) -> promoted prompt text
_active: dict[tuple[str, str], str] = {}


@lru_cache(maxsize=1)
def registry() -> dict[str, str]:
    import src.services as services

    prompts = {}
    for module_info in pkgutil.iter_modules(services.__path__):
        if not module_info.name.endswith("_agent"):
            continue
        module = importlib.import_module(f"src.services.{module_info.name}")
        for name, value in vars(module).items():
            if name.endswith("SYSTEM_PROMPT") and isinstance(value, str):
                prompts[f"{module_info.name}.{name}"] = value
    return prompts


@lru_cache(maxsize=1)
def _key_by_text() -> dict[str, str]:
    return {text: key for key, text in registry().items()}


@lru_cache(maxsize=1)
def tool_names() -> frozenset[str]:
    """Every tool name any agent can call (from the *_TOOL_SCHEMAS lists)."""
    import src.services as services

    names = set()
    for module_info in pkgutil.iter_modules(services.__path__):
        module = importlib.import_module(f"src.services.{module_info.name}")
        for name, value in vars(module).items():
            if name.endswith("TOOL_SCHEMAS") and isinstance(value, list):
                names |= {schema["name"] for schema in value if isinstance(schema, dict) and "name" in schema}
    return frozenset(names)


def required_markers(prompt_key: str) -> list[str]:
    default = registry()[prompt_key]
    markers = sorted(name for name in tool_names() if re.search(rf"\b{re.escape(name)}\b", default))
    if FINISH_RULE in default:
        markers.append(FINISH_RULE)
    return markers


def evaluate(prompt_key: str, text: str) -> tuple[bool, list[str]]:
    """Returns (passed, findings)."""
    if prompt_key not in registry():
        return False, [f"unknown prompt_key {prompt_key!r}"]
    findings = []
    if not text.strip():
        findings.append("prompt is empty")
    if len(text) > MAX_PROMPT_CHARS:
        findings.append(f"prompt is {len(text)} characters; the limit is {MAX_PROMPT_CHARS}")
    findings += [f"missing {marker!r}, which the agent's code relies on" for marker in required_markers(prompt_key) if marker not in text]
    return not findings, findings


def resolve(system_prompt: str) -> str:
    key = _key_by_text().get(system_prompt)
    tenant = current_tenant.get()
    if key is None or tenant is None:
        return system_prompt
    return _active.get((tenant, key), system_prompt)


def set_active(tenant_id: str, prompt_key: str, text: str | None) -> None:
    if text is None:
        _active.pop((tenant_id, prompt_key), None)
    else:
        _active[(tenant_id, prompt_key)] = text


async def refresh(db: AsyncSession) -> int:
    """Reloads every tenant's active versions — picks up promotions made
    on other replicas."""
    from src.models import PolicyStatus, PromptPolicy

    result = await db.execute(
        select(PromptPolicy.tenant_id, PromptPolicy.prompt_key, PromptPolicy.system_prompt).where(
            PromptPolicy.status == PolicyStatus.ACTIVE, PromptPolicy.prompt_key.is_not(None),
        )
    )
    fresh = {(tenant, key): text for tenant, key, text in result.all()}
    _active.clear()
    _active.update(fresh)
    return len(fresh)
