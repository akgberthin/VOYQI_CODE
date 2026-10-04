import uuid
from datetime import datetime, timezone

from src.models import GarnishmentType
from src.services import employee_engine, payroll_engine

TENANT = "voyqi-holdings"


def test_compute_pay_stub_amounts_florida_no_state_tax():
    amounts = payroll_engine.compute_pay_stub_amounts(2000.0, "FL", 0.0, 0.0)
    assert amounts["fica_usd"] == round(2000.0 * 0.0765, 2)
    assert amounts["federal_tax_usd"] == round(2000.0 * 0.12, 2)
    assert amounts["state_tax_usd"] == 0.0  # FL has no state income tax
    assert amounts["net_pay_usd"] == round(2000.0 - amounts["fica_usd"] - amounts["federal_tax_usd"], 2)


def test_compute_pay_stub_amounts_texas_no_state_tax():
    amounts = payroll_engine.compute_pay_stub_amounts(2000.0, "TX", 0.0, 0.0)
    assert amounts["state_tax_usd"] == 0.0  # TX has no state income tax


def test_compute_pay_stub_amounts_other_state_uses_default_rate():
    amounts = payroll_engine.compute_pay_stub_amounts(2000.0, "CA", 0.0, 0.0)
    assert amounts["state_tax_usd"] == round(2000.0 * 0.05, 2)


def test_compute_pay_stub_amounts_with_garnishment_and_benefits():
    amounts = payroll_engine.compute_pay_stub_amounts(2000.0, "FL", 150.0, 75.0)
    assert amounts["garnishment_usd"] == 150.0
    assert amounts["benefits_premium_usd"] == 75.0
    expected_net = 2000.0 - amounts["fica_usd"] - amounts["federal_tax_usd"] - 150.0 - 75.0
    assert amounts["net_pay_usd"] == round(expected_net, 2)


async def test_add_pay_stub_reads_real_garnishments_and_benefits(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await payroll_engine.set_tax_status(db_session, TENANT, emp.id, "FL", "FL")
    await payroll_engine.create_garnishment_order(db_session, TENANT, emp.id, GarnishmentType.CHILD_SUPPORT, 200.0)

    run = await payroll_engine.create_pay_run(db_session, TENANT, "rooftop-fl-1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    stub = await payroll_engine.add_pay_stub(db_session, TENANT, run.id, emp.id, 2500.0)
    assert stub.garnishment_usd == 200.0
    assert stub.state_tax_usd == 0.0


async def test_inactive_garnishment_not_applied(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    order = await payroll_engine.create_garnishment_order(db_session, TENANT, emp.id, GarnishmentType.CREDITOR_GARNISHMENT, 100.0)
    await payroll_engine.deactivate_garnishment_order(db_session, TENANT, order.id)

    run = await payroll_engine.create_pay_run(db_session, TENANT, "rooftop-fl-1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    stub = await payroll_engine.add_pay_stub(db_session, TENANT, run.id, emp.id, 2000.0)
    assert stub.garnishment_usd == 0.0


async def test_multi_state_flag_set_correctly(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    status = await payroll_engine.set_tax_status(db_session, TENANT, emp.id, "TX", "FL")
    assert status.is_multi_state is True

    status2 = await payroll_engine.set_tax_status(db_session, TENANT, emp.id, "FL", "FL")
    assert status2.is_multi_state is False


async def test_process_pay_run_aggregates_totals(db_session):
    emp1 = await employee_engine.create_employee(db_session, TENANT, "A")
    emp2 = await employee_engine.create_employee(db_session, TENANT, "B")
    run = await payroll_engine.create_pay_run(db_session, TENANT, "rooftop-fl-1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    stub1 = await payroll_engine.add_pay_stub(db_session, TENANT, run.id, emp1.id, 2000.0)
    stub2 = await payroll_engine.add_pay_stub(db_session, TENANT, run.id, emp2.id, 3000.0)

    processed = await payroll_engine.process_pay_run(db_session, TENANT, run.id)
    assert processed.status.value == "completed"
    assert processed.total_gross_usd == 5000.0
    assert processed.total_net_usd == round(stub1.net_pay_usd + stub2.net_pay_usd, 2)
    assert processed.processed_at is not None


async def test_process_unknown_pay_run_returns_none(db_session):
    result = await payroll_engine.process_pay_run(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_list_pay_runs_filters_by_entity(db_session):
    await payroll_engine.create_pay_run(db_session, TENANT, "rooftop-fl-1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    await payroll_engine.create_pay_run(db_session, TENANT, "rooftop-tx-1", datetime.now(timezone.utc), datetime.now(timezone.utc))

    fl_runs = await payroll_engine.list_pay_runs(db_session, TENANT, entity_ref="rooftop-fl-1")
    assert len(fl_runs) == 1


async def test_list_garnishment_orders(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await payroll_engine.create_garnishment_order(db_session, TENANT, emp.id, GarnishmentType.STUDENT_LOAN, 50.0)
    orders = await payroll_engine.list_garnishment_orders(db_session, TENANT, emp.id)
    assert len(orders) == 1
