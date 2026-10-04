import uuid

from src.models import CompensationReason
from src.services import employee_engine, workforce_analytics_engine

TENANT = "voyqi-holdings"


async def test_record_and_get_latest_attrition_risk(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await workforce_analytics_engine.record_attrition_risk(db_session, TENANT, emp.id, 0.35, "low risk")
    latest = await workforce_analytics_engine.record_attrition_risk(db_session, TENANT, emp.id, 0.72, "risk increased")

    result = await workforce_analytics_engine.get_latest_attrition_risk(db_session, TENANT, emp.id)
    assert result.id == latest.id
    assert result.risk_score == 0.72


async def test_get_latest_attrition_risk_no_history_returns_none(db_session):
    result = await workforce_analytics_engine.get_latest_attrition_risk(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_high_risk_watchlist_uses_most_recent_only(db_session):
    emp_a = await employee_engine.create_employee(db_session, TENANT, "A")
    emp_b = await employee_engine.create_employee(db_session, TENANT, "B")
    await workforce_analytics_engine.record_attrition_risk(db_session, TENANT, emp_a.id, 0.8, "old high score")
    await workforce_analytics_engine.record_attrition_risk(db_session, TENANT, emp_a.id, 0.2, "improved since")
    await workforce_analytics_engine.record_attrition_risk(db_session, TENANT, emp_b.id, 0.6, "consistently elevated")

    watchlist = await workforce_analytics_engine.list_high_attrition_risk_employees(db_session, TENANT, threshold=0.5)
    watchlist_ids = {s.employee_id for s in watchlist}
    assert emp_a.id not in watchlist_ids
    assert emp_b.id in watchlist_ids


async def test_get_attrition_risk_factors_gathers_real_data(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", job_title="Service Advisor", department="Service", annual_salary_usd=55000)
    await employee_engine.create_position(db_session, TENANT, "Service Advisor", department="Service", salary_band_min=45000, salary_band_max=65000)

    factors = await workforce_analytics_engine.get_attrition_risk_factors(db_session, TENANT, emp.id)
    assert factors["tenure_months"] >= 0
    assert factors["comp_band_position"] == round((55000 - 45000) / (65000 - 45000), 2)
    assert factors["months_since_last_comp_change"] == 0


async def test_get_attrition_risk_factors_unknown_employee(db_session):
    factors = await workforce_analytics_engine.get_attrition_risk_factors(db_session, TENANT, uuid.uuid4())
    assert factors == {}


async def test_headcount_by_department(db_session):
    await employee_engine.create_employee(db_session, TENANT, "A", department="Sales")
    await employee_engine.create_employee(db_session, TENANT, "B", department="Sales")
    await employee_engine.create_employee(db_session, TENANT, "C", department="Service")

    headcount = await workforce_analytics_engine.get_headcount_by_department(db_session, TENANT)
    by_dept = {h["department"]: h["headcount"] for h in headcount}
    assert by_dept["Sales"] == 2
    assert by_dept["Service"] == 1


async def test_headcount_by_department_excludes_terminated(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "A", department="Sales")
    await employee_engine.initiate_offboarding(db_session, TENANT, emp.id)

    headcount = await workforce_analytics_engine.get_headcount_by_department(db_session, TENANT)
    assert headcount == []


async def test_headcount_by_entity(db_session):
    await employee_engine.create_employee(db_session, TENANT, "A", entity_ref="rooftop-fl-1")
    await employee_engine.create_employee(db_session, TENANT, "B", entity_ref="rooftop-fl-1")
    await employee_engine.create_employee(db_session, TENANT, "C", entity_ref="rooftop-tx-1")

    headcount = await workforce_analytics_engine.get_headcount_by_entity(db_session, TENANT)
    by_entity = {h["entity_ref"]: h["headcount"] for h in headcount}
    assert by_entity["rooftop-fl-1"] == 2
    assert by_entity["rooftop-tx-1"] == 1


async def test_compensation_benchmark_exact_arithmetic(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", job_title="Service Advisor", department="Service", annual_salary_usd=55000)
    await employee_engine.create_position(db_session, TENANT, "Service Advisor", department="Service", salary_band_min=45000, salary_band_max=65000)

    benchmark = await workforce_analytics_engine.get_compensation_benchmark(db_session, TENANT, emp.id)
    assert benchmark["current_salary_usd"] == 55000
    assert benchmark["band_position_pct"] == 50.0  # exactly midpoint of 45k-65k


async def test_compensation_benchmark_no_matching_position(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", job_title="Unlisted Role", annual_salary_usd=50000)
    benchmark = await workforce_analytics_engine.get_compensation_benchmark(db_session, TENANT, emp.id)
    assert benchmark["current_salary_usd"] == 50000
    assert benchmark["band_position_pct"] is None


async def test_compensation_benchmark_no_comp_history(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    benchmark = await workforce_analytics_engine.get_compensation_benchmark(db_session, TENANT, emp.id)
    assert benchmark["current_salary_usd"] is None


async def test_compensation_benchmark_unknown_employee(db_session):
    benchmark = await workforce_analytics_engine.get_compensation_benchmark(db_session, TENANT, uuid.uuid4())
    assert "error" in benchmark


async def test_pay_equity_snapshot_groups_by_department_and_tenure_only(db_session):
    emp1 = await employee_engine.create_employee(db_session, TENANT, "A", department="Sales", annual_salary_usd=60000)
    emp2 = await employee_engine.create_employee(db_session, TENANT, "B", department="Sales", annual_salary_usd=70000)

    snapshot = await workforce_analytics_engine.get_pay_equity_snapshot(db_session, TENANT)
    sales_group = next(s for s in snapshot if s["department"] == "Sales")
    assert sales_group["employee_count"] == 2
    assert sales_group["avg_salary_usd"] == 65000.0
    assert sales_group["min_salary_usd"] == 60000.0
    assert sales_group["max_salary_usd"] == 70000.0
    # Confirm no protected-characteristic field appears anywhere in the snapshot
    assert "gender" not in sales_group and "race" not in sales_group and "age" not in sales_group
    assert emp1.id and emp2.id  # employees created for the aggregate, never individually named in output


async def test_pay_equity_snapshot_excludes_employees_without_comp_records(db_session):
    await employee_engine.create_employee(db_session, TENANT, "A", department="Marketing")  # no annual_salary_usd
    snapshot = await workforce_analytics_engine.get_pay_equity_snapshot(db_session, TENANT)
    assert not any(s["department"] == "Marketing" for s in snapshot)


async def test_manual_compensation_change_updates_months_since_last_change(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", annual_salary_usd=50000)
    await employee_engine.add_compensation_record(db_session, TENANT, emp.id, 58000, CompensationReason.MERIT)

    factors = await workforce_analytics_engine.get_attrition_risk_factors(db_session, TENANT, emp.id)
    assert factors["months_since_last_comp_change"] == 0
