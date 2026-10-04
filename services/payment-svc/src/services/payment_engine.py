"""Payment engine.

The idempotency guarantee on charge() is deliberately implemented as
"insert first, catch the constraint violation" rather than "check for an
existing row, then insert" — the latter has a race window between the
check and the insert that two concurrent requests (a genuine retry
storm, or two workers processing the same client retry) can both pass
through, each creating its own charge. The unique constraint at the
database layer is the only thing that actually closes that window.
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Charge,
    ChargeStatus,
    PaymentMethod,
    PaymentMethodStatus,
    Refund,
    WebhookEvent,
)
from src.services import providers
from src.services.providers import current_mode


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentMethodNotFoundError(Exception):
    pass


class ChargeNotFoundError(Exception):
    pass


class RefundAmountExceedsChargeError(Exception):
    pass


class ChargeNotRefundableError(Exception):
    pass


async def add_payment_method(
    db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, provider_token: str, method_type: str,
    last4: str, brand: str | None = None, exp_month: int | None = None, exp_year: int | None = None, is_default: bool = False,
) -> PaymentMethod:
    provider_payment_method_id, provider_mode = await providers.vault_payment_method(provider_token=provider_token, last4=last4)

    if is_default:
        await db.execute(
            update(PaymentMethod).where(PaymentMethod.user_id == user_id, PaymentMethod.is_default.is_(True)).values(is_default=False)
        )

    method = PaymentMethod(
        tenant_id=tenant_id, user_id=user_id, provider_mode=provider_mode, provider_payment_method_id=provider_payment_method_id,
        method_type=method_type, last4=last4, brand=brand, exp_month=exp_month, exp_year=exp_year, is_default=is_default,
    )
    db.add(method)
    await db.flush()
    return method


async def get_payment_method(db: AsyncSession, *, tenant_id: str, payment_method_id: uuid.UUID) -> PaymentMethod:
    result = await db.execute(select(PaymentMethod).where(PaymentMethod.id == payment_method_id, PaymentMethod.tenant_id == tenant_id))
    method = result.scalar_one_or_none()
    if method is None:
        raise PaymentMethodNotFoundError(f"payment method {payment_method_id} not found")
    return method


async def charge(
    db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, payment_method_id: uuid.UUID,
    amount_cents: int, idempotency_key: str, currency: str = "usd", description: str | None = None,
) -> Charge:
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")

    method = await get_payment_method(db, tenant_id=tenant_id, payment_method_id=payment_method_id)
    if method.status != PaymentMethodStatus.ACTIVE.value:
        raise ValueError(f"payment method is {method.status}, not active")

    new_charge = Charge(
        tenant_id=tenant_id, user_id=user_id, payment_method_id=payment_method_id,
        idempotency_key=idempotency_key, amount_cents=amount_cents, currency=currency,
        status=ChargeStatus.PENDING.value, provider_mode=current_mode(), description=description,
    )
    db.add(new_charge)
    try:
        await db.flush()
    except IntegrityError:
        # Someone already created a charge with this idempotency key —
        # this is the expected, correct outcome of a retry, not an
        # error. In real Postgres, a unique-constraint violation only
        # ever happens against already-committed data (the conflicting
        # insert blocks until the first transaction resolves), so this
        # row is guaranteed visible here without needing to retry.
        await db.rollback()
        result = await db.execute(select(Charge).where(Charge.tenant_id == tenant_id, Charge.idempotency_key == idempotency_key))
        existing = result.scalar_one_or_none()
        if existing is None:
            raise RuntimeError(
                f"idempotency key {idempotency_key} hit a constraint conflict but no row was visible — "
                "this would indicate the database backend does not provide the read-your-writes "
                "guarantee this design relies on for concurrent inserts"
            ) from None
        return existing

    success, provider_mode, provider_charge_id, failure_reason = await providers.create_charge(
        amount_cents=amount_cents, currency=currency, last4=method.last4, description=description,
    )
    new_charge.provider_mode = provider_mode
    new_charge.provider_charge_id = provider_charge_id
    new_charge.failure_reason = failure_reason
    new_charge.status = ChargeStatus.SUCCEEDED.value if success else ChargeStatus.FAILED.value
    if success:
        new_charge.succeeded_at = utcnow()
    await db.flush()
    return new_charge


async def get_charge(db: AsyncSession, *, tenant_id: str, charge_id: uuid.UUID) -> Charge:
    result = await db.execute(select(Charge).where(Charge.id == charge_id, Charge.tenant_id == tenant_id))
    found = result.scalar_one_or_none()
    if found is None:
        raise ChargeNotFoundError(f"charge {charge_id} not found")
    return found


async def refund(db: AsyncSession, *, tenant_id: str, charge_id: uuid.UUID, amount_cents: int | None = None, reason: str | None = None) -> Refund:
    target_charge = await get_charge(db, tenant_id=tenant_id, charge_id=charge_id)

    if target_charge.status not in (ChargeStatus.SUCCEEDED.value, ChargeStatus.PARTIALLY_REFUNDED.value):
        raise ChargeNotRefundableError(f"charge is {target_charge.status}, not refundable")

    remaining = target_charge.amount_cents - target_charge.refunded_amount_cents
    effective_amount = amount_cents if amount_cents is not None else remaining
    if effective_amount <= 0 or effective_amount > remaining:
        raise RefundAmountExceedsChargeError(f"requested {effective_amount} exceeds remaining refundable amount {remaining}")

    success, provider_mode, provider_refund_id, failure_reason = await providers.create_refund(
        provider_charge_id=target_charge.provider_charge_id, amount_cents=effective_amount,
    )

    refund_row = Refund(
        tenant_id=tenant_id, charge_id=charge_id, amount_cents=effective_amount, reason=reason,
        status="succeeded" if success else "failed", provider_mode=provider_mode, provider_refund_id=provider_refund_id,
        failure_reason=failure_reason,
    )
    db.add(refund_row)

    if success:
        target_charge.refunded_amount_cents += effective_amount
        target_charge.status = ChargeStatus.REFUNDED.value if target_charge.refunded_amount_cents >= target_charge.amount_cents else ChargeStatus.PARTIALLY_REFUNDED.value

    await db.flush()
    return refund_row


async def process_webhook(db: AsyncSession, *, tenant_id: str, provider: str, provider_event_id: str, event_type: str, payload: dict) -> WebhookEvent | None:
    """Returns None if this event_id was already processed (idempotent
    no-op) — the caller should treat that as success, not an error;
    webhook delivery is at-least-once by design in every real processor."""
    event = WebhookEvent(
        tenant_id=tenant_id, provider=provider, provider_event_id=provider_event_id,
        event_type=event_type, payload_json=json.dumps(payload),
    )
    db.add(event)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None

    event.processed_at = utcnow()
    await db.flush()
    return event
