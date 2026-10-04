from datetime import datetime, timezone

import pytest

from src.services import ip_engine

TENANT = "voyqi-holdings"
TECH_ENTITY = "VOYQI Technology LLC"


@pytest.mark.asyncio
async def test_registered_ip_asset_starts_pending(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Class Equivalency Engine Method", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    assert asset.status == "pending"


@pytest.mark.asyncio
async def test_trade_secret_starts_protected_not_pending(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Continuity Routing Algorithm", owning_entity=TECH_ENTITY)
    await db_session.commit()
    assert asset.status == "protected"


@pytest.mark.asyncio
async def test_pending_patent_can_be_granted(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Fleet Staging Method", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    asset_id = asset.id

    granted = await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="granted", registration_number="US11,999,999")
    await db_session.commit()
    assert granted.status == "granted"
    assert granted.grant_date is not None
    assert granted.registration_number == "US11,999,999"


@pytest.mark.asyncio
async def test_pending_patent_can_be_abandoned(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Abandoned idea", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    asset_id = asset.id

    abandoned = await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="abandoned", reason="Prior art found")
    await db_session.commit()
    assert abandoned.status == "abandoned"


@pytest.mark.asyncio
async def test_cannot_transition_a_patent_directly_to_compromised(db_session):
    """compromised only makes sense for confidentiality-based assets —
    a patent's failure mode is abandonment or expiration, not disclosure."""
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Patent", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    asset_id = asset.id

    with pytest.raises(ip_engine.InvalidAssetStatusTransitionError):
        await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="compromised")


@pytest.mark.asyncio
async def test_protected_trade_secret_can_become_compromised(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret algorithm", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id

    compromised = await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="compromised", reason="Leaked in a former employee's laptop")
    await db_session.commit()
    assert compromised.status == "compromised"


@pytest.mark.asyncio
async def test_cannot_grant_a_trade_secret(db_session):
    """A trade secret was never filed — 'granted' is meaningless for it."""
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id

    with pytest.raises(ip_engine.InvalidAssetStatusTransitionError):
        await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="granted")


@pytest.mark.asyncio
async def test_terminal_status_blocks_any_further_transition(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="compromised")
    await db_session.commit()

    with pytest.raises(ip_engine.InvalidAssetStatusTransitionError):
        await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="compromised", reason="again")


@pytest.mark.asyncio
async def test_cannot_license_an_asset_still_pending(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Pending patent", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    asset_id = asset.id

    with pytest.raises(ip_engine.AssetNotLicensableError):
        await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive")


@pytest.mark.asyncio
async def test_can_license_a_granted_patent(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Patent", owning_entity=TECH_ENTITY, jurisdiction="US")
    await db_session.commit()
    asset_id = asset.id
    await ip_engine.update_asset_status(db_session, tenant_id=TENANT, asset_id=asset_id, new_status="granted", registration_number="US1")
    await db_session.commit()

    license_agreement = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive")
    await db_session.commit()
    assert license_agreement.status == "active"


@pytest.mark.asyncio
async def test_can_license_a_protected_trade_secret(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id

    license_agreement = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Mobility LLC", license_type="exclusive")
    await db_session.commit()
    assert license_agreement.license_type == "exclusive"


@pytest.mark.asyncio
async def test_exclusive_license_blocks_any_further_license_on_same_asset(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Mobility LLC", license_type="exclusive")
    await db_session.commit()

    with pytest.raises(ip_engine.ExclusiveLicenseConflictError):
        await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive")


@pytest.mark.asyncio
async def test_multiple_non_exclusive_licenses_can_coexist(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id

    await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive")
    await db_session.commit()
    second = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Mobility LLC", license_type="non_exclusive")
    await db_session.commit()
    assert second.licensee_entity == "VOYQI Mobility LLC"

    active = await ip_engine.list_active_licenses_for_asset(db_session, tenant_id=TENANT, ip_asset_id=asset_id)
    assert len(active) == 2


@pytest.mark.asyncio
async def test_cannot_grant_exclusive_license_while_non_exclusive_ones_are_active(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive")
    await db_session.commit()

    with pytest.raises(ip_engine.ExclusiveLicenseConflictError):
        await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Auto Group LLC", license_type="exclusive")


@pytest.mark.asyncio
async def test_terminate_license(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="exclusive")
    await db_session.commit()
    license_id = license_agreement.id

    terminated = await ip_engine.terminate_license(db_session, tenant_id=TENANT, license_id=license_id, reason="Business unit divested")
    await db_session.commit()
    assert terminated.status == "terminated"
    assert terminated.terminated_at is not None


@pytest.mark.asyncio
async def test_terminating_an_already_terminated_license_fails(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="exclusive")
    await db_session.commit()
    license_id = license_agreement.id
    await ip_engine.terminate_license(db_session, tenant_id=TENANT, license_id=license_id)
    await db_session.commit()

    with pytest.raises(ip_engine.InvalidAssetStatusTransitionError):
        await ip_engine.terminate_license(db_session, tenant_id=TENANT, license_id=license_id)


@pytest.mark.asyncio
async def test_terminated_license_frees_up_asset_for_a_new_exclusive_license(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    first_license = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="exclusive")
    await db_session.commit()
    first_license_id = first_license.id
    await ip_engine.terminate_license(db_session, tenant_id=TENANT, license_id=first_license_id)
    await db_session.commit()

    second_license = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Mobility LLC", license_type="exclusive")
    await db_session.commit()
    assert second_license.licensee_entity == "VOYQI Mobility LLC"


@pytest.mark.asyncio
async def test_royalty_statement_percentage_of_revenue_calculation(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(
        db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive",
        royalty_type="percentage_of_revenue", royalty_rate=500,  # 5.00%
    )
    await db_session.commit()
    license_id = license_agreement.id

    statement = await ip_engine.create_royalty_statement(
        db_session, tenant_id=TENANT, license_agreement_id=license_id,
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        licensee_reported_revenue_cents=100000,
    )
    await db_session.commit()
    assert statement.royalty_owed_cents == 5000  # 5% of $1000.00


@pytest.mark.asyncio
async def test_royalty_statement_percentage_requires_revenue_report(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(
        db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive",
        royalty_type="percentage_of_revenue", royalty_rate=500,
    )
    await db_session.commit()
    license_id = license_agreement.id

    with pytest.raises(ip_engine.MissingRevenueReportError):
        await ip_engine.create_royalty_statement(
            db_session, tenant_id=TENANT, license_agreement_id=license_id,
            period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_royalty_statement_fixed_fee_ignores_revenue(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(
        db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive",
        royalty_type="fixed_fee", royalty_rate=250000,  # $2,500.00 flat
    )
    await db_session.commit()
    license_id = license_agreement.id

    statement = await ip_engine.create_royalty_statement(
        db_session, tenant_id=TENANT, license_agreement_id=license_id,
        period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )
    await db_session.commit()
    assert statement.royalty_owed_cents == 250000


@pytest.mark.asyncio
async def test_finalize_then_pay_statement_lifecycle(db_session):
    asset = await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret", owning_entity=TECH_ENTITY)
    await db_session.commit()
    asset_id = asset.id
    license_agreement = await ip_engine.create_license(db_session, tenant_id=TENANT, ip_asset_id=asset_id, licensee_entity="VOYQI Fleet LLC", license_type="non_exclusive", royalty_type="fixed_fee", royalty_rate=10000)
    await db_session.commit()
    license_id = license_agreement.id
    statement = await ip_engine.create_royalty_statement(db_session, tenant_id=TENANT, license_agreement_id=license_id, period_start=datetime(2026, 1, 1, tzinfo=timezone.utc), period_end=datetime(2026, 1, 31, tzinfo=timezone.utc))
    await db_session.commit()
    statement_id = statement.id

    with pytest.raises(ip_engine.StatementAlreadyProcessedError):
        await ip_engine.record_payment(db_session, tenant_id=TENANT, statement_id=statement_id)  # can't pay before finalizing

    finalized = await ip_engine.finalize_statement(db_session, tenant_id=TENANT, statement_id=statement_id)
    await db_session.commit()
    assert finalized.status == "finalized"

    with pytest.raises(ip_engine.StatementAlreadyProcessedError):
        await ip_engine.finalize_statement(db_session, tenant_id=TENANT, statement_id=statement_id)  # can't finalize twice

    paid = await ip_engine.record_payment(db_session, tenant_id=TENANT, statement_id=statement_id)
    await db_session.commit()
    assert paid.status == "paid"
    assert paid.paid_at is not None


@pytest.mark.asyncio
async def test_list_assets_by_owning_entity(db_session):
    await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trade_secret", name="Secret A", owning_entity=TECH_ENTITY)
    await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="patent", name="Patent B", owning_entity=TECH_ENTITY, jurisdiction="US")
    await ip_engine.register_asset(db_session, tenant_id=TENANT, asset_type="trademark", name="Other Corp Mark", owning_entity="Some Other Entity LLC", jurisdiction="US")
    await db_session.commit()

    tech_assets = await ip_engine.list_assets_by_owning_entity(db_session, tenant_id=TENANT, owning_entity=TECH_ENTITY)
    assert len(tech_assets) == 2
