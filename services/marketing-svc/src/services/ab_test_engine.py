"""
A/B test engine — fully autonomous conclusion. There is no human
"declare the winner" click: evaluate_test runs a real two-proportion
z-test against the configured significance threshold, and if (and only
if) both arms have reached the minimum sample size AND the result is
statistically significant, it writes a winner and concludes the test
in the same call. Below the sample-size floor, it reports
INSUFFICIENT_DATA and takes no action — it doesn't force an early,
statistically meaningless call just to produce an answer.
"""
import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import ABTest, ABTestStatus, ContentDraft, ContentStatus


async def create_ab_test(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, variant_a_draft_id: uuid.UUID, variant_b_draft_id: uuid.UUID, metric: str = "click_rate") -> ABTest:
    test = ABTest(tenant_id=tenant_id, campaign_id=campaign_id, variant_a_draft_id=variant_a_draft_id, variant_b_draft_id=variant_b_draft_id, metric=metric)
    db.add(test)
    await db.commit()
    await db.refresh(test)
    return test


async def get_ab_test(db: AsyncSession, tenant_id: str, test_id: uuid.UUID) -> ABTest | None:
    result = await db.execute(select(ABTest).where(ABTest.id == test_id, ABTest.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_ab_tests(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None) -> list[ABTest]:
    query = select(ABTest).where(ABTest.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(ABTest.campaign_id == campaign_id)
    return list((await db.execute(query)).scalars().all())


async def record_result(db: AsyncSession, tenant_id: str, test_id: uuid.UUID, variant: str, sent_delta: int, converted_delta: int) -> ABTest | None:
    test = await get_ab_test(db, tenant_id, test_id)
    if test is None:
        return None
    if variant == "a":
        test.variant_a_sent += sent_delta
        test.variant_a_converted += converted_delta
    else:
        test.variant_b_sent += sent_delta
        test.variant_b_converted += converted_delta
    await db.commit()
    await db.refresh(test)
    return test


def _two_proportion_z_test(sent_a: int, conv_a: int, sent_b: int, conv_b: int) -> float:
    """Returns a p-value. Pure and deterministic — the same
    statistics function whether called from the engine or a unit
    test in isolation."""
    if sent_a == 0 or sent_b == 0:
        return 1.0
    p_a, p_b = conv_a / sent_a, conv_b / sent_b
    p_pool = (conv_a + conv_b) / (sent_a + sent_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / sent_a + 1 / sent_b))
    if se == 0:
        return 1.0
    z = abs(p_a - p_b) / se
    # Two-tailed p-value from the standard normal survival function,
    # via the complementary error function (no scipy dependency needed).
    p_value = math.erfc(z / math.sqrt(2))
    return p_value


async def evaluate_test(db: AsyncSession, tenant_id: str, test_id: uuid.UUID) -> ABTest | None:
    """Fully autonomous: concludes the test and writes a winner in
    the same call, if and only if both arms have enough data and the
    result is statistically significant. No human declares a winner
    for a test this function is able to resolve on its own.

    INSUFFICIENT_DATA is a transient status, not a terminal one — a
    test flagged that way is re-evaluated normally on the next call,
    since more data may have arrived since. Only CONCLUDED stops
    further evaluation; treating INSUFFICIENT_DATA as equally final
    would mean a test that didn't have enough data on day one could
    never be re-checked once it did."""
    settings = get_settings()
    test = await get_ab_test(db, tenant_id, test_id)
    if test is None or test.status == ABTestStatus.CONCLUDED:
        return test

    if test.variant_a_sent < settings.ab_test_min_sample_per_arm or test.variant_b_sent < settings.ab_test_min_sample_per_arm:
        test.status = ABTestStatus.INSUFFICIENT_DATA
        await db.commit()
        await db.refresh(test)
        return test

    p_value = _two_proportion_z_test(test.variant_a_sent, test.variant_a_converted, test.variant_b_sent, test.variant_b_converted)
    if p_value > settings.ab_test_significance_threshold:
        test.status = ABTestStatus.INSUFFICIENT_DATA  # enough volume, but not a significant difference yet
        await db.commit()
        await db.refresh(test)
        return test

    rate_a = test.variant_a_converted / test.variant_a_sent
    rate_b = test.variant_b_converted / test.variant_b_sent
    winner_id = test.variant_a_draft_id if rate_a >= rate_b else test.variant_b_draft_id

    test.winner_draft_id = winner_id
    test.status = ABTestStatus.CONCLUDED
    from datetime import datetime, timezone
    test.concluded_at = datetime.now(timezone.utc)

    loser_id = test.variant_b_draft_id if winner_id == test.variant_a_draft_id else test.variant_a_draft_id
    loser_result = await db.execute(select(ContentDraft).where(ContentDraft.id == loser_id))
    loser_draft = loser_result.scalar_one_or_none()
    if loser_draft and loser_draft.status not in (ContentStatus.SENT,):
        loser_draft.status = ContentStatus.REJECTED

    await db.commit()
    await db.refresh(test)
    return test
