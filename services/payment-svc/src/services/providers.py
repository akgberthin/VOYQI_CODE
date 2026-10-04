"""Payment provider abstraction. Sandbox mode is not a no-op stub — it
simulates realistic decline behavior using the SAME test-card last4
conventions Stripe itself documents (e.g. last4 "0002" always declines),
so integration testing against this sandbox actually exercises the
failure paths a real integration would need to handle, not just the
happy path.
"""
import uuid

from src.core.config import get_settings
from src.models import ProviderMode

settings = get_settings()

# Mirrors Stripe's own published test-card last4 -> behavior mapping.
SANDBOX_DECLINE_LAST4 = {
    "0002": "card_declined",
    "0069": "expired_card",
    "0127": "incorrect_cvc",
    "0119": "processing_error",
}


def current_mode() -> str:
    return ProviderMode.REAL_PROVIDER.value if settings.stripe_api_key else ProviderMode.SANDBOX.value


async def vault_payment_method(*, provider_token: str, last4: str) -> tuple[str, str]:
    """Returns (provider_payment_method_id, provider_mode). In real mode
    this would call the processor's API to attach/confirm the token; in
    sandbox mode it generates a realistic-shaped fake id so downstream
    code never has to branch on which mode created a record."""
    if settings.stripe_api_key:
        raise NotImplementedError("Real Stripe integration requires stripe_api_key to be wired to an actual HTTP call — not implemented in this environment.")
    return f"pm_sandbox_{uuid.uuid4().hex[:24]}", ProviderMode.SANDBOX.value


async def create_charge(*, amount_cents: int, currency: str, last4: str, description: str | None) -> tuple[bool, str, str | None, str | None]:
    """Returns (success, provider_mode, provider_charge_id, failure_reason)."""
    if settings.stripe_api_key:
        raise NotImplementedError("Real Stripe integration requires stripe_api_key to be wired to an actual HTTP call — not implemented in this environment.")

    if last4 in SANDBOX_DECLINE_LAST4:
        return False, ProviderMode.SANDBOX.value, None, SANDBOX_DECLINE_LAST4[last4]
    return True, ProviderMode.SANDBOX.value, f"ch_sandbox_{uuid.uuid4().hex[:24]}", None


async def create_refund(*, provider_charge_id: str, amount_cents: int) -> tuple[bool, str, str | None, str | None]:
    """Returns (success, provider_mode, provider_refund_id, failure_reason)."""
    if settings.stripe_api_key:
        raise NotImplementedError("Real Stripe integration requires stripe_api_key to be wired to an actual HTTP call — not implemented in this environment.")
    return True, ProviderMode.SANDBOX.value, f"re_sandbox_{uuid.uuid4().hex[:24]}", None
