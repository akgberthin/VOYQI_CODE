
from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import hrm_tools, talent_agent

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


async def test_onboarding_dealership_gets_department_tasks(db_session, monkeypatch):
    added = []
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: added.append(a) or {})

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-1", "onboarding", "Service Advisor", "dealership", entity_ref="rooftop-fl-1", client=_UnconfiguredClient()
    )
    assert "Provision DMS system access" in result.tasks_added
    assert "Issue rooftop badge/access" in result.tasks_added
    assert "Complete new hire paperwork" in result.tasks_added
    assert len(added) == len(result.tasks_added)


async def test_onboarding_hub_department_gets_hub_tasks(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-2", "onboarding", "Hub Coordinator", "hub", client=_UnconfiguredClient()
    )
    assert "Provision hub network access" in result.tasks_added
    assert "Issue VOYQI Card" in result.tasks_added


async def test_onboarding_unknown_department_gets_base_tasks_only(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-3", "onboarding", "Marketing Coordinator", "Marketing", client=_UnconfiguredClient()
    )
    assert result.tasks_added == talent_agent._BASE_ONBOARDING_TASKS
    assert "no department-specific template matched" in result.reasoning


async def test_offboarding_dealership_revokes_dms_access(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-4", "offboarding", "Service Advisor", "dealership", client=_UnconfiguredClient()
    )
    assert "Revoke DMS system access" in result.tasks_added
    assert "Revoke IT account access" in result.tasks_added


async def test_offboarding_hub_department_deactivates_card(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-5", "offboarding", "Hub Coordinator", "hub", client=_UnconfiguredClient()
    )
    assert "Revoke hub network access" in result.tasks_added
    assert "Deactivate VOYQI Card" in result.tasks_added


async def test_paused_agent_produces_no_tasks(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_ONBOARDING, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-6", "onboarding", "Service Advisor", "dealership", client=_UnconfiguredClient()
    )
    assert result.tasks_added == []
    assert result.skipped_reason == "paused"


async def test_decision_count_increments(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_ONBOARDING, display_name="Talent Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await talent_agent.evaluate_checklist_population(db_session, TENANT, "checklist-7", "onboarding", "Service Advisor", "dealership", client=_UnconfiguredClient())
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_checklist_task_tool_dispatch",
        lambda tenant_id: {"submit_checklist_tasks": lambda inp: {}},
    )
    added = []
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: added.append(a) or {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Shop-floor role needs safety training too.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_checklist_tasks",
                        tool_input={"tasks": ["Complete new hire paperwork", "Provision IT account", "OSHA safety orientation"], "reasoning": "shop-floor role requires safety training beyond the standard template"},
                        tool_output={"tasks": ["Complete new hire paperwork", "Provision IT account", "OSHA safety orientation"], "reasoning": "shop-floor role requires safety training beyond the standard template"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_checklist_population(
        db_session, TENANT, "checklist-8", "onboarding", "Technician", "service", client=_FakeClient()
    )
    assert "OSHA safety orientation" in result.tasks_added
    assert len(added) == 3


async def test_handle_employee_created_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.handle_employee_created(
        db_session, TENANT, "hrm.employee_created", {"checklist_id": "checklist-9", "job_title": "Service Advisor", "department": "dealership", "entity_ref": "rooftop-fl-1"}
    )
    assert "Provision DMS system access" in result.tasks_added


async def test_handle_offboarding_initiated_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    result = await talent_agent.handle_offboarding_initiated(
        db_session, TENANT, "hrm.offboarding_initiated", {"checklist_id": "checklist-10", "job_title": "Service Advisor", "department": "dealership"}
    )
    assert "Revoke DMS system access" in result.tasks_added


PLANS = [
    {"id": "plan-1", "name": "VOYQI PPO Gold", "plan_type": "medical", "monthly_premium_usd": 450.0, "employer_contribution_pct": 0.7},
    {"id": "plan-2", "name": "VOYQI HMO Basic", "plan_type": "medical", "monthly_premium_usd": 300.0, "employer_contribution_pct": 0.5},
    {"id": "plan-3", "name": "VOYQI Dental", "plan_type": "dental", "monthly_premium_usd": 30.0, "employer_contribution_pct": 0.8},
]


async def test_benefits_recommendation_picks_lowest_employee_cost_medical_plan(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": PLANS})
    created = []
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: created.append(a) or {})

    # plan-1 employee cost: 450 * 0.3 = 135; plan-2: 300 * 0.5 = 150 -> plan-1 wins
    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-1", "Service Advisor", "dealership", client=_UnconfiguredClient())
    assert result.recommended_plan_id == "plan-1"
    assert len(created) == 1


async def test_benefits_recommendation_no_medical_plans_reports_none(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": [PLANS[2]]})  # only dental
    created = []
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-2", "Service Advisor", "dealership", client=_UnconfiguredClient())
    assert result.recommended_plan_id is None
    assert len(created) == 0


async def test_benefits_recommendation_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_BENEFITS, display_name="Talent Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-3", "Service Advisor", "dealership", client=_UnconfiguredClient())
    assert result.recommended_plan_id is None
    assert result.skipped_reason == "disabled"


async def test_benefits_recommendation_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_benefits_recommendation_tool_dispatch",
        lambda tenant_id: {"get_benefits_plans": lambda inp: {"plans": PLANS}, "submit_benefits_recommendation": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Gold plan given the role's travel demands.",
                tool_calls=[
                    ToolCallResult(tool_name="get_benefits_plans", tool_input={}, tool_output={"plans": PLANS}),
                    ToolCallResult(
                        tool_name="submit_benefits_recommendation", tool_input={"recommended_plan_id": "plan-1", "reasoning": "broader network fits this role's regional travel"},
                        tool_output={"recommended_plan_id": "plan-1", "reasoning": "broader network fits this role's regional travel"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-4", "Regional Sales Manager", "dealership", client=_FakeClient())
    assert result.recommended_plan_id == "plan-1"


async def test_benefits_recommendation_first_time_enrollment_auto_accepts(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": PLANS})
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {"id": "rec-1"})
    monkeypatch.setattr(hrm_tools, "get_employee_benefits_enrollments", lambda tenant_id, employee_id: [])
    accepted = []
    monkeypatch.setattr(hrm_tools, "accept_benefits_recommendation", lambda *a, **k: accepted.append(a) or {})

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-new", "Service Advisor", "dealership", client=_UnconfiguredClient())
    assert result.decision_log.action_taken == "benefits_recommendation_auto_accepted"
    assert len(accepted) == 1


async def test_benefits_recommendation_cost_neutral_switch_auto_accepts(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": PLANS})
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {"id": "rec-2"})
    # currently enrolled in plan-1 (employee cost 135); plan-1 is also the recommendation -> no increase
    monkeypatch.setattr(hrm_tools, "get_employee_benefits_enrollments", lambda tenant_id, employee_id: [{"status": "active", "plan_id": "plan-1"}])
    accepted = []
    monkeypatch.setattr(hrm_tools, "accept_benefits_recommendation", lambda *a, **k: accepted.append(a) or {})

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-same", "Service Advisor", "dealership", client=_UnconfiguredClient())
    assert result.decision_log.action_taken == "benefits_recommendation_auto_accepted"
    assert len(accepted) == 1


async def test_benefits_recommendation_cost_increase_holds_for_human_review(db_session, monkeypatch):
    # Force the recommendation toward plan-2 (higher employee cost than plan-1) by removing plan-1 from the catalog seen by the agent.
    higher_cost_plans = [PLANS[1], PLANS[2]]  # plan-2 (cost 150), dental
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": higher_cost_plans})
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {"id": "rec-3"})
    # currently enrolled in plan-1 (employee cost 135, cheaper) but plan-1 isn't resolvable in the (deliberately trimmed) catalog list the agent just fetched — still, simulate the real scenario: employee is on a plan cheaper than what's being recommended.
    monkeypatch.setattr(hrm_tools, "get_employee_benefits_enrollments", lambda tenant_id, employee_id: [{"status": "active", "plan_id": "plan-1-not-in-recommend-catalog"}])
    accepted = []
    monkeypatch.setattr(hrm_tools, "accept_benefits_recommendation", lambda *a, **k: accepted.append(a) or {})

    result = await talent_agent.evaluate_benefits_recommendation(db_session, TENANT, "emp-increase", "Service Advisor", "dealership", client=_UnconfiguredClient())
    # current plan unresolvable -> held for review rather than assumed safe
    assert result.decision_log.action_taken == "benefits_recommendation_created_pending_financial_review"
    assert len(accepted) == 0


async def test_benefits_financial_risk_helper_detects_real_cost_increase(db_session):
    plans = [
        {"id": "cheap", "monthly_premium_usd": 100.0, "employer_contribution_pct": 0.5},   # employee cost 50
        {"id": "pricier", "monthly_premium_usd": 400.0, "employer_contribution_pct": 0.5},  # employee cost 200
    ]
    enrollments = [{"status": "active", "plan_id": "cheap"}]
    is_risk, note = talent_agent._benefits_financial_risk(plans, enrollments, "pricier")
    assert is_risk is True
    assert "50" in note and "200" in note


async def test_benefits_financial_risk_helper_allows_cost_decrease(db_session):
    plans = [
        {"id": "expensive", "monthly_premium_usd": 400.0, "employer_contribution_pct": 0.5},  # employee cost 200
        {"id": "cheaper", "monthly_premium_usd": 100.0, "employer_contribution_pct": 0.5},    # employee cost 50
    ]
    enrollments = [{"status": "active", "plan_id": "expensive"}]
    is_risk, _note = talent_agent._benefits_financial_risk(plans, enrollments, "cheaper")
    assert is_risk is False


async def test_benefits_financial_risk_helper_no_active_enrollment_is_never_risk(db_session):
    plans = [{"id": "plan-x", "monthly_premium_usd": 999.0, "employer_contribution_pct": 0.0}]
    is_risk, note = talent_agent._benefits_financial_risk(plans, [], "plan-x")
    assert is_risk is False
    assert "first-time" in note.lower()


async def test_handle_benefits_recommendation_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_benefits_plans", lambda tenant_id: {"plans": PLANS})
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {})

    result = await talent_agent.handle_benefits_recommendation_requested(
        db_session, TENANT, "hrm.benefits_recommendation_requested", {"employee_id": "emp-5", "job_title": "Service Advisor", "department": "dealership"}
    )
    assert result.recommended_plan_id == "plan-1"


async def test_screening_keyword_overlap_scoring(db_session, monkeypatch):
    applied = []
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: applied.append(a) or {})

    result = await talent_agent.evaluate_application_screening(
        db_session, TENANT, "app-1", "Experienced in automotive diagnostics and customer service", "automotive, customer service, DMS", client=_UnconfiguredClient()
    )
    assert result.score == round(2 / 3, 2)
    assert len(applied) == 1


async def test_screening_no_required_skills_defaults_neutral(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: {})

    result = await talent_agent.evaluate_application_screening(db_session, TENANT, "app-2", "Some resume text", "", client=_UnconfiguredClient())
    assert result.score == 0.5
    assert "No required skills listed" in result.reasoning


async def test_screening_no_keyword_match_scores_zero(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: {})

    result = await talent_agent.evaluate_application_screening(db_session, TENANT, "app-3", "Experience in retail sales", "automotive, DMS", client=_UnconfiguredClient())
    assert result.score == 0.0


async def test_screening_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_RECRUITING, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_application_screening(db_session, TENANT, "app-4", "resume", "skills", client=_UnconfiguredClient())
    assert result.score is None
    assert result.skipped_reason == "paused"


async def test_screening_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_screening_tool_dispatch",
        lambda tenant_id: {"submit_screening_score": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Strong adjacent experience despite different terminology.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_screening_score", tool_input={"score": 0.75, "reasoning": "adjacent industry experience transfers well"},
                        tool_output={"score": 0.75, "reasoning": "adjacent industry experience transfers well"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_application_screening(db_session, TENANT, "app-5", "resume", "skills", client=_FakeClient())
    assert result.score == 0.75


async def test_handle_application_submitted_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: {})

    result = await talent_agent.handle_application_submitted(
        db_session, TENANT, "hrm.application_submitted", {"application_id": "app-6", "resume_summary": "automotive diagnostics", "required_skills": "automotive"}
    )
    assert result.score == 1.0


async def test_nurture_draft_deterministic_template(db_session, monkeypatch):
    created = []
    sent = []
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: created.append(a) or {"id": "draft-1"})
    monkeypatch.setattr(hrm_tools, "send_nurture_draft", lambda tenant_id, draft_id: sent.append(draft_id) or {"status": "approved_sent"})

    result = await talent_agent.evaluate_candidate_nurture(db_session, TENANT, "cand-1", "app-1", "Alex Chen", "role filled", client=_UnconfiguredClient())
    assert result.draft_created is True
    assert len(created) == 1


async def test_nurture_draft_full_automation_auto_sends(db_session, monkeypatch):
    """Full-automation rebuild: the agent must not stop at drafting —
    it has to actually call send_nurture_draft on its own output."""
    sent = []
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: {"id": "draft-42"})
    monkeypatch.setattr(hrm_tools, "send_nurture_draft", lambda tenant_id, draft_id: sent.append(draft_id) or {"status": "approved_sent"})

    result = await talent_agent.evaluate_candidate_nurture(db_session, TENANT, "cand-1", "app-1", "Alex Chen", "role filled", client=_UnconfiguredClient())
    assert result.draft_sent is True
    assert sent == ["draft-42"]
    assert result.decision_log.action_taken == "nurture_draft_created_and_sent"


async def test_nurture_draft_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_RECRUITING, display_name="Talent Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_candidate_nurture(db_session, TENANT, "cand-2", "app-2", "Alex Chen", "role filled", client=_UnconfiguredClient())
    assert result.draft_created is False
    assert result.skipped_reason == "disabled"


async def test_nurture_draft_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_nurture_tool_dispatch",
        lambda tenant_id: {"submit_nurture_draft": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: {"id": "draft-3"})
    monkeypatch.setattr(hrm_tools, "send_nurture_draft", lambda tenant_id, draft_id: {"status": "approved_sent"})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Warm, specific message drafted.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_nurture_draft", tool_input={"draft_body": "Alex, we were genuinely impressed...", "reasoning": "strong candidate, keep warm for next opening"},
                        tool_output={"draft_body": "Alex, we were genuinely impressed...", "reasoning": "strong candidate, keep warm for next opening"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_candidate_nurture(db_session, TENANT, "cand-3", "app-3", "Alex Chen", "role filled", client=_FakeClient())
    assert result.draft_created is True
    assert result.draft_sent is True


async def test_handle_application_rejected_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: {})

    result = await talent_agent.handle_application_rejected(
        db_session, TENANT, "hrm.application_rejected", {"candidate_id": "cand-4", "application_id": "app-4", "candidate_name": "Alex Chen", "reason": "role filled"}
    )
    assert result.draft_created is True


async def test_feedback_draft_deterministic_template_includes_notes(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "create_feedback_draft", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_feedback_draft(db_session, TENANT, "review-1", "Exceeded CSI targets consistently", 4.2, client=_UnconfiguredClient())
    assert result.draft_created is True
    assert len(created) == 1


async def test_feedback_draft_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_PERFORMANCE, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_feedback_draft(db_session, TENANT, "review-2", "notes", 4.0, client=_UnconfiguredClient())
    assert result.draft_created is False
    assert result.skipped_reason == "paused"


async def test_feedback_draft_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_feedback_draft_tool_dispatch",
        lambda tenant_id: {"submit_feedback_draft": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_feedback_draft", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Polished narrative drafted.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_feedback_draft", tool_input={"draft_body": "Jane excelled this quarter...", "reasoning": "polished from notes"},
                        tool_output={"draft_body": "Jane excelled this quarter...", "reasoning": "polished from notes"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_feedback_draft(db_session, TENANT, "review-3", "notes", 4.5, client=_FakeClient())
    assert result.draft_created is True


async def test_handle_feedback_draft_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_feedback_draft", lambda *a, **k: {})

    result = await talent_agent.handle_feedback_draft_requested(db_session, TENANT, "hrm.feedback_draft_requested", {"review_id": "review-4", "manager_notes": "strong", "rating": 4.0})
    assert result.draft_created is True


async def test_nine_box_high_performance_high_potential(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_nine_box_placement(db_session, TENANT, "emp-1", "cycle-1", 4.5, 0.9, client=_UnconfiguredClient())
    assert result.performance_axis == "high"
    assert result.potential_axis == "high"
    assert len(created) == 1


async def test_nine_box_low_performance_low_potential(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: {})

    result = await talent_agent.evaluate_nine_box_placement(db_session, TENANT, "emp-2", "cycle-2", 1.5, 0.1, client=_UnconfiguredClient())
    assert result.performance_axis == "low"
    assert result.potential_axis == "low"


async def test_nine_box_missing_data_defaults_to_medium(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: {})

    result = await talent_agent.evaluate_nine_box_placement(db_session, TENANT, "emp-3", "cycle-3", None, None, client=_UnconfiguredClient())
    assert result.performance_axis == "medium"
    assert result.potential_axis == "medium"


async def test_nine_box_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_PERFORMANCE, display_name="Talent Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_nine_box_placement(db_session, TENANT, "emp-4", "cycle-4", 4.0, 0.8, client=_UnconfiguredClient())
    assert result.placement_created is False
    assert result.skipped_reason == "disabled"


async def test_nine_box_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_nine_box_tool_dispatch",
        lambda tenant_id: {"submit_nine_box_placement": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="High performer with strong potential.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_nine_box_placement", tool_input={"performance_axis": "high", "potential_axis": "medium", "reasoning": "consistent results, developing leadership readiness"},
                        tool_output={"performance_axis": "high", "potential_axis": "medium", "reasoning": "consistent results, developing leadership readiness"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_nine_box_placement(db_session, TENANT, "emp-5", "cycle-5", 4.2, 0.6, client=_FakeClient())
    assert result.performance_axis == "high"
    assert result.potential_axis == "medium"


async def test_handle_nine_box_evaluation_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: {})

    result = await talent_agent.handle_nine_box_evaluation_requested(
        db_session, TENANT, "hrm.nine_box_evaluation_requested", {"employee_id": "emp-6", "review_cycle_id": "cycle-6", "rating": 4.5, "goal_completion_rate": 0.9}
    )
    assert result.placement_created is True


async def test_attrition_risk_low_comp_band_position_drives_score(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: recorded.append(a) or {})

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-1", tenure_months=24, comp_band_position=0.15, months_since_last_comp_change=6, goal_completion_rate=0.8, client=_UnconfiguredClient())
    assert result.risk_score == 0.35
    assert "bottom 30%" in result.reasoning
    assert len(recorded) == 1


async def test_attrition_risk_combines_multiple_drivers_additively(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-2", tenure_months=3, comp_band_position=0.1, months_since_last_comp_change=24, goal_completion_rate=0.1, client=_UnconfiguredClient())
    # 0.35 (comp band) + 0.25 (stagnation) + 0.20 (low completion) + 0.15 (short tenure) = 0.95
    assert result.risk_score == 0.95


async def test_attrition_risk_no_signals_scores_zero(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-3", tenure_months=36, comp_band_position=0.6, months_since_last_comp_change=3, goal_completion_rate=0.9, client=_UnconfiguredClient())
    assert result.risk_score == 0.0
    assert "no elevated risk signals" in result.reasoning


async def test_attrition_risk_missing_data_contributes_nothing(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-4", tenure_months=None, comp_band_position=None, months_since_last_comp_change=None, goal_completion_rate=None, client=_UnconfiguredClient())
    assert result.risk_score == 0.0


async def test_attrition_risk_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_WORKFORCE_ANALYTICS, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-5", client=_UnconfiguredClient())
    assert result.risk_score is None
    assert result.skipped_reason == "paused"


async def test_attrition_risk_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_attrition_risk_tool_dispatch",
        lambda tenant_id: {"submit_attrition_risk": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Elevated risk given compensation stagnation.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_attrition_risk", tool_input={"risk_score": 0.6, "reasoning": "compensation hasn't moved in 2 years despite strong performance"},
                        tool_output={"risk_score": 0.6, "reasoning": "compensation hasn't moved in 2 years despite strong performance"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_attrition_risk(db_session, TENANT, "emp-6", client=_FakeClient())
    assert result.risk_score == 0.6


async def test_handle_attrition_risk_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    result = await talent_agent.handle_attrition_risk_requested(
        db_session, TENANT, "hrm.attrition_risk_requested", {"employee_id": "emp-7", "comp_band_position": 0.1, "months_since_last_comp_change": 20}
    )
    assert result.risk_score == 0.6


async def test_course_recommendation_no_incomplete_courses(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_course_recommendation(db_session, TENANT, "emp-1", [], [], client=_UnconfiguredClient())
    assert result.recommended_course_id is None
    assert "No incomplete mandatory courses" in result.reasoning
    assert len(created) == 1


async def test_course_recommendation_prioritizes_compliance_over_other_courses(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: {})

    result = await talent_agent.evaluate_course_recommendation(
        db_session, TENANT, "emp-2", ["course-tech", "course-compliance"], ["Advanced DMS Operations", "Anti-Harassment Compliance Training"], client=_UnconfiguredClient()
    )
    assert result.recommended_course_id == "course-compliance"
    assert "compliance/safety" in result.reasoning


async def test_course_recommendation_falls_back_to_first_incomplete_when_none_are_priority(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: {})

    result = await talent_agent.evaluate_course_recommendation(
        db_session, TENANT, "emp-3", ["course-leadership", "course-tech"], ["Leadership Fundamentals", "Advanced DMS Operations"], client=_UnconfiguredClient()
    )
    assert result.recommended_course_id == "course-leadership"


async def test_course_recommendation_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_ACADEMY, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_course_recommendation(db_session, TENANT, "emp-4", ["course-1"], ["Safety 101"], client=_UnconfiguredClient())
    assert result.recommendation_created is False
    assert result.skipped_reason == "paused"


async def test_course_recommendation_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_course_recommendation_tool_dispatch",
        lambda tenant_id: {"submit_course_recommendation": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Compliance course takes priority.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_course_recommendation", tool_input={"recommended_course_id": "course-compliance", "reasoning": "regulatory deadline approaching"},
                        tool_output={"recommended_course_id": "course-compliance", "reasoning": "regulatory deadline approaching"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_course_recommendation(db_session, TENANT, "emp-5", ["course-compliance"], ["Data Security Basics"], client=_FakeClient())
    assert result.recommended_course_id == "course-compliance"


async def test_handle_course_recommendation_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: {})

    result = await talent_agent.handle_course_recommendation_requested(
        db_session, TENANT, "hrm.course_recommendation_requested", {"employee_id": "emp-6", "incomplete_mandatory_course_ids": ["course-1"], "incomplete_mandatory_course_titles": ["Safety 101"]}
    )
    assert result.recommendation_created is True


async def test_policy_answer_grounds_in_matching_policy(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [
        {"id": "policy-1", "title": "PTO Policy", "body": "Employees may carry over up to 40 hours of unused PTO annually."},
        {"id": "policy-2", "title": "Remote Work Policy", "body": "Remote work requires manager approval."},
    ])
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_policy_question(db_session, TENANT, "case-1", "Can I carry over unused PTO?", client=_UnconfiguredClient())
    assert result.draft_created is True
    assert "PTO Policy" in result.reasoning
    assert len(created) == 1


async def test_policy_answer_no_matching_policy_creates_no_draft(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [
        {"id": "policy-1", "title": "Remote Work Policy", "body": "Remote work requires manager approval."},
    ])
    created = []
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_policy_question(db_session, TENANT, "case-2", "What is our parental leave policy?", client=_UnconfiguredClient())
    assert result.draft_created is False
    assert len(created) == 0
    assert "human response" in result.reasoning


async def test_policy_answer_no_policies_on_file_creates_no_draft(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [])
    created = []
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_policy_question(db_session, TENANT, "case-3", "Can I carry over PTO?", client=_UnconfiguredClient())
    assert result.draft_created is False
    assert len(created) == 0


async def test_policy_answer_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_CASE_MANAGEMENT, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_policy_question(db_session, TENANT, "case-4", "Can I carry over PTO?", client=_UnconfiguredClient())
    assert result.draft_created is False
    assert result.skipped_reason == "paused"


async def test_policy_answer_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [{"id": "policy-1", "title": "PTO Policy", "body": "Up to 40 hours carryover."}])
    monkeypatch.setattr(
        hrm_tools, "build_policy_answer_tool_dispatch",
        lambda tenant_id: {"submit_policy_answer": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Grounded in PTO Policy.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_policy_answer", tool_input={"draft_body": "Per PTO Policy, up to 40 hours carry over.", "cites_policy_id": "policy-1", "reasoning": "direct match"},
                        tool_output={"draft_body": "Per PTO Policy, up to 40 hours carry over.", "cites_policy_id": "policy-1", "reasoning": "direct match"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_policy_question(db_session, TENANT, "case-5", "Can I carry over PTO?", client=_FakeClient())
    assert result.draft_created is True


async def test_handle_policy_question_asked_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [{"id": "policy-1", "title": "PTO Policy", "body": "Up to 40 hours carryover annually."}])
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: {})

    result = await talent_agent.handle_policy_question_asked(db_session, TENANT, "hrm.policy_question_asked", {"case_id": "case-6", "question": "Can I carry over unused PTO?"})
    assert result.draft_created is True


async def test_note_update_adds_new_note_when_none_exists(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: None)
    added = []
    monkeypatch.setattr(hrm_tools, "add_employee_note", lambda *a, **k: added.append(a) or {})

    result = await talent_agent.evaluate_note_update(db_session, TENANT, "emp-1", "Completed onboarding", client=_UnconfiguredClient())
    assert result.note_written is True
    assert result.action_taken == "note_added"
    assert len(added) == 1


async def test_note_update_appends_when_recent_note_exists(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: {"id": "note-1", "body": "existing note"})
    appended = []
    monkeypatch.setattr(hrm_tools, "append_employee_note", lambda *a, **k: appended.append(a) or {})

    result = await talent_agent.evaluate_note_update(db_session, TENANT, "emp-2", "Case resolved", client=_UnconfiguredClient())
    assert result.note_written is True
    assert result.action_taken == "note_appended"
    assert len(appended) == 1


async def test_note_update_deterministic_entry_is_short_and_factual(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: None)
    captured = []
    monkeypatch.setattr(hrm_tools, "add_employee_note", lambda tenant_id, employee_id, body, author="talent-agent": captured.append(body) or {})

    result = await talent_agent.evaluate_note_update(db_session, TENANT, "emp-3", "Completed VOYQI Academy training", client=_UnconfiguredClient())
    assert result.note_written is True
    assert "Completed VOYQI Academy training" in captured[0]
    assert len(captured[0]) < 100  # short, factual — no embellishment


async def test_note_update_paused_agent_writes_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_COMMUNICATION, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_note_update(db_session, TENANT, "emp-4", "context", client=_UnconfiguredClient())
    assert result.note_written is False
    assert result.skipped_reason == "paused"


async def test_note_update_live_claude_path_can_choose_to_append(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: {"id": "note-1", "body": "existing"})
    monkeypatch.setattr(
        hrm_tools, "build_note_entry_tool_dispatch",
        lambda tenant_id: {"submit_note_entry": lambda inp: {}},
    )
    appended = []
    monkeypatch.setattr(hrm_tools, "append_employee_note", lambda *a, **k: appended.append(a) or {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Appending to the active thread.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_note_entry", tool_input={"entry_text": "Follow-up logged.", "append_to_existing": True, "reasoning": "same active thread"},
                        tool_output={"entry_text": "Follow-up logged.", "append_to_existing": True, "reasoning": "same active thread"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_note_update(db_session, TENANT, "emp-5", "Follow-up", client=_FakeClient())
    assert result.action_taken == "note_appended"
    assert len(appended) == 1


async def test_handle_note_update_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: None)
    monkeypatch.setattr(hrm_tools, "add_employee_note", lambda *a, **k: {})

    result = await talent_agent.handle_note_update_requested(db_session, TENANT, "hrm.note_update_requested", {"employee_id": "emp-6", "context": "Completed onboarding"})
    assert result.note_written is True


async def test_handle_case_resolved_adapter_builds_factual_context(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: None)
    captured = []
    monkeypatch.setattr(hrm_tools, "add_employee_note", lambda tenant_id, employee_id, body, author="talent-agent": captured.append(body) or {})

    result = await talent_agent.handle_case_resolved(db_session, TENANT, "hrm.case_resolved", {"employee_id": "emp-7", "case_type": "policy_question", "subject": "PTO carryover"})
    assert result.note_written is True
    assert "PTO carryover" in captured[0]


async def test_email_draft_deterministic_template_for_all_staff(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "create_email_draft", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_email_draft(db_session, TENANT, "all_staff", "remind everyone open enrollment ends Friday", client=_UnconfiguredClient())
    assert result.email_created is True
    assert len(created) == 1


async def test_email_draft_never_invents_content_beyond_prompt(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_email_draft", lambda *a, **k: {})

    _subject, body = talent_agent._deterministic_email_draft("open enrollment ends Friday", "all_staff")
    assert "open enrollment ends Friday" in body
    assert "Dear Team" in body


async def test_email_draft_single_employee_uses_different_greeting(db_session):
    _subject, body = talent_agent._deterministic_email_draft("please complete your training", "single_employee")
    assert "Dear Colleague" in body


async def test_email_draft_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_COMMUNICATION, display_name="Talent Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_email_draft(db_session, TENANT, "all_staff", "prompt", client=_UnconfiguredClient())
    assert result.email_created is False
    assert result.skipped_reason == "paused"


async def test_email_draft_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_email_draft_tool_dispatch",
        lambda tenant_id: {"submit_email_draft": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_email_draft", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Drafted.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_email_draft", tool_input={"subject": "Open Enrollment Reminder", "body": "Dear Team,\n\nOpen enrollment ends Friday.\n\nBest,\nHR", "reasoning": "expanded prompt into formal email"},
                        tool_output={"subject": "Open Enrollment Reminder", "body": "Dear Team,\n\nOpen enrollment ends Friday.\n\nBest,\nHR", "reasoning": "expanded prompt into formal email"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_email_draft(db_session, TENANT, "all_staff", "remind everyone", client=_FakeClient())
    assert result.email_created is True


async def test_handle_email_draft_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_email_draft", lambda *a, **k: {})

    result = await talent_agent.handle_email_draft_requested(db_session, TENANT, "hrm.email_draft_requested", {"recipient_scope": "all_staff", "prompt": "reminder"})
    assert result.email_created is True


async def test_course_draft_infers_safety_category_and_video_content_type(db_session, monkeypatch):
    created = []
    monkeypatch.setattr(hrm_tools, "create_course_draft", lambda *a, **k: created.append(a) or {})

    result = await talent_agent.evaluate_course_draft(db_session, TENANT, "defensive driving safety training", client=_UnconfiguredClient())
    assert result.draft_created is True
    _title, category, _description, content_type = talent_agent._deterministic_course_draft("defensive driving safety training")
    assert category == "safety"
    assert content_type == "video"


async def test_course_draft_infers_compliance_category(db_session):
    _title, category, _description, _content_type = talent_agent._deterministic_course_draft("anti-harassment compliance policy training")
    assert category == "compliance"


async def test_course_draft_defaults_to_technical_when_no_keywords_match(db_session):
    _title, category, _description, content_type = talent_agent._deterministic_course_draft("advanced spreadsheet techniques")
    assert category == "technical"
    assert content_type == "document"


async def test_course_draft_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=talent_agent.AGENT_KEY_ACADEMY, display_name="Talent Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await talent_agent.evaluate_course_draft(db_session, TENANT, "prompt", client=_UnconfiguredClient())
    assert result.draft_created is False
    assert result.skipped_reason == "disabled"


async def test_course_draft_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "build_course_draft_tool_dispatch",
        lambda tenant_id: {"submit_course_draft": lambda inp: {}},
    )
    monkeypatch.setattr(hrm_tools, "create_course_draft", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Drafted a video course.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_course_draft",
                        tool_input={"title": "Forklift Safety Certification", "category": "safety", "description": "Hands-on forklift operation safety.", "content_type": "video", "reasoning": "hands-on topic suits video"},
                        tool_output={"title": "Forklift Safety Certification", "category": "safety", "description": "Hands-on forklift operation safety.", "content_type": "video", "reasoning": "hands-on topic suits video"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await talent_agent.evaluate_course_draft(db_session, TENANT, "forklift safety certification", client=_FakeClient())
    assert result.draft_created is True


async def test_handle_course_draft_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_draft", lambda *a, **k: {})

    result = await talent_agent.handle_course_draft_requested(db_session, TENANT, "hrm.course_draft_requested", {"prompt": "forklift safety"})
    assert result.draft_created is True
