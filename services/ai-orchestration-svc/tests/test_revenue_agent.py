
from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import crm_tools, revenue_agent

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


PROFILES = [
    {"id": "profile-1", "full_name": "Priya Sharma", "primary_email": "priya@example.com", "primary_phone": "305-555-0142"},
    {"id": "profile-2", "full_name": "Marcus Reyes", "primary_email": "marcus@example.com", "primary_phone": "512-555-0110"},
]


async def test_exact_email_match_creates_high_confidence_candidate(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    created = []
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: created.append(a) or {})

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-1", "Priya Sharma", "priya@example.com", "", client=_UnconfiguredClient()
    )
    assert result.candidate_person_profile_id == "profile-1"
    assert result.match_score == 0.95
    assert "exact email match" in result.reasoning
    assert len(created) == 1


async def test_exact_phone_match_when_no_email_given(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-2", "P. Sharma", "", "(305) 555-0142", client=_UnconfiguredClient()
    )
    assert result.candidate_person_profile_id == "profile-1"
    assert result.match_score == 0.85


async def test_name_only_match_below_threshold_produces_no_candidate(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    created = []
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: created.append(a) or {})

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-3", "Priya Sharma", "different@example.com", "999-555-9999", client=_UnconfiguredClient()
    )
    assert result.candidate_person_profile_id is None
    assert len(created) == 0
    assert "too weak" in result.reasoning


async def test_no_matching_profile_at_all(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-4", "Someone Else", "someone@example.com", "111-222-3333", client=_UnconfiguredClient()
    )
    assert result.candidate_person_profile_id is None


async def test_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-5", "Priya Sharma", "priya@example.com", "", client=_UnconfiguredClient()
    )
    assert result.candidate_person_profile_id is None
    assert result.skipped_reason == "paused"


async def test_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "build_identity_resolution_tool_dispatch",
        lambda tenant_id: {"get_person_profiles": lambda inp: {"profiles": PROFILES}, "submit_match_result": lambda inp: {}},
    )
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Nickname variant of an existing profile, high confidence.",
                tool_calls=[
                    ToolCallResult(tool_name="get_person_profiles", tool_input={}, tool_output={"profiles": PROFILES}),
                    ToolCallResult(
                        tool_name="submit_match_result",
                        tool_input={"candidate_person_profile_id": "profile-1", "match_score": 0.88, "reasoning": "Priya vs P. Sharma, same phone area code and matching last name"},
                        tool_output={"candidate_person_profile_id": "profile-1", "match_score": 0.88, "reasoning": "Priya vs P. Sharma, same phone area code and matching last name"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-6", "P Sharma", "", "", client=_FakeClient()
    )
    assert result.candidate_person_profile_id == "profile-1"
    assert result.match_score == 0.88


async def test_live_claude_path_can_report_no_match(db_session, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "build_identity_resolution_tool_dispatch",
        lambda tenant_id: {"get_person_profiles": lambda inp: {"profiles": PROFILES}, "submit_match_result": lambda inp: {}},
    )
    create_calls = []
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: create_calls.append(a) or {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="No good match.",
                tool_calls=[
                    ToolCallResult(tool_name="get_person_profiles", tool_input={}, tool_output={"profiles": PROFILES}),
                    ToolCallResult(
                        tool_name="submit_match_result",
                        tool_input={"candidate_person_profile_id": None, "match_score": 0.1, "reasoning": "no plausible match in existing profiles"},
                        tool_output={"candidate_person_profile_id": None, "match_score": 0.1, "reasoning": "no plausible match in existing profiles"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-7", "Totally New Person", "new@example.com", "", client=_FakeClient()
    )
    assert result.candidate_person_profile_id is None
    assert len(create_calls) == 0


async def test_decision_count_increments(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await revenue_agent.evaluate_identity_resolution(
        db_session, TENANT, "record-8", "Priya Sharma", "priya@example.com", "", client=_UnconfiguredClient()
    )
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_handle_source_record_created_adapter(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_person_profiles", lambda tenant_id: {"profiles": PROFILES})
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})

    result = await revenue_agent.handle_source_record_created(
        db_session, TENANT, "crm.source_record_created",
        {"source_record_id": "record-9", "full_name": "Priya Sharma", "email": "priya@example.com", "phone": ""},
    )
    assert result.candidate_person_profile_id == "profile-1"


async def test_lead_scoring_deterministic_full_contact_corporate_domain(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "check_duplicate_leads", lambda tenant_id, email, phone: {"duplicates": []})
    updates = []
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: updates.append(a) or {})

    result = await revenue_agent.evaluate_lead_scoring(
        db_session, TENANT, "lead-1", "dealer", "Jane Doe", "jane@acmecorp.com", "305-555-0199", client=_UnconfiguredClient()
    )
    # base 0.1 + both contact (0.3) + corporate domain (0.3) + dealer weight (0.3) = 1.0
    assert result.score == 1.0
    assert result.routed_to_team == "enterprise_sales"
    assert len(updates) == 1


async def test_lead_scoring_deterministic_free_email_web_source(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "check_duplicate_leads", lambda tenant_id, email, phone: {"duplicates": []})
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: {})

    result = await revenue_agent.evaluate_lead_scoring(
        db_session, TENANT, "lead-2", "web_landing_page", "Jane Doe", "jane@gmail.com", "", client=_UnconfiguredClient()
    )
    # base 0.1 + no phone (no +0.3) + free domain (no +0.3) + web weight 0.15 = 0.25
    assert round(result.score, 2) == 0.25
    assert result.routed_to_team == "general_sales"


async def test_lead_scoring_notes_duplicates_in_reasoning(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "check_duplicate_leads", lambda tenant_id, email, phone: {"duplicates": [{"id": "existing-lead"}]})
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: {})

    result = await revenue_agent.evaluate_lead_scoring(
        db_session, TENANT, "lead-3", "web_landing_page", "Jane Doe", "jane@example.com", "", client=_UnconfiguredClient()
    )
    assert "potential duplicate" in result.reasoning


async def test_lead_scoring_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_lead_scoring(
        db_session, TENANT, "lead-4", "dealer", "Jane Doe", "jane@example.com", "", client=_UnconfiguredClient()
    )
    assert result.score is None
    assert result.skipped_reason == "disabled"


async def test_lead_scoring_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "build_lead_scoring_tool_dispatch",
        lambda tenant_id: {"check_duplicate_leads": lambda inp: {"duplicates": []}, "submit_lead_score": lambda inp: {}},
    )
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Strong enterprise signal.",
                tool_calls=[
                    ToolCallResult(tool_name="check_duplicate_leads", tool_input={"email": "jane@acmecorp.com"}, tool_output={"duplicates": []}),
                    ToolCallResult(
                        tool_name="submit_lead_score", tool_input={"score": 0.91, "routed_to_team": "enterprise_sales", "reasoning": "corporate domain, dealer channel, complete contact info"},
                        tool_output={"score": 0.91, "routed_to_team": "enterprise_sales", "reasoning": "corporate domain, dealer channel, complete contact info"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_lead_scoring(
        db_session, TENANT, "lead-5", "dealer", "Jane Doe", "jane@acmecorp.com", "305-555-0199", client=_FakeClient()
    )
    assert result.score == 0.91
    assert result.routed_to_team == "enterprise_sales"


async def test_handle_lead_captured_adapter_creates_lead_then_scores(db_session, monkeypatch):
    created_leads = []
    monkeypatch.setattr(crm_tools, "create_lead", lambda *a, **k: created_leads.append(a) or {"id": "new-lead-1"})
    monkeypatch.setattr(crm_tools, "check_duplicate_leads", lambda tenant_id, email, phone: {"duplicates": []})
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: {})

    result = await revenue_agent.handle_lead_captured(
        db_session, TENANT, "lead.captured",
        {"lead_id": "marketing-lead-1", "name": "Jane Doe", "email": "jane@acmecorp.com", "phone": "305-555-0199"},
    )
    assert len(created_leads) == 1
    assert result.lead_id == "new-lead-1"
    assert result.score is not None


PROFILES_FOR_DEDUP = [
    {"id": "profile-existing-1", "full_name": "Priya Sharma", "primary_email": "priya@example.com", "primary_phone": "305-555-0142"},
    {"id": "profile-existing-2", "full_name": "Marcus Reyes", "primary_email": "marcus@example.com", "primary_phone": "512-555-0110"},
]


async def test_profile_dedup_exact_email_match_creates_candidate(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_active_person_profiles", lambda tenant_id, exclude_profile_id="": {"profiles": PROFILES_FOR_DEDUP})
    created = []
    monkeypatch.setattr(crm_tools, "create_profile_duplicate_candidate", lambda *a, **k: created.append(a) or {})

    result = await revenue_agent.evaluate_profile_deduplication(
        db_session, TENANT, "profile-new-1", "P. Sharma", "priya@example.com", "", client=_UnconfiguredClient()
    )
    assert result.candidate_profile_id == "profile-existing-1"
    assert result.match_score == 0.95
    assert len(created) == 1


async def test_profile_dedup_no_match_creates_nothing(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_active_person_profiles", lambda tenant_id, exclude_profile_id="": {"profiles": PROFILES_FOR_DEDUP})
    created = []
    monkeypatch.setattr(crm_tools, "create_profile_duplicate_candidate", lambda *a, **k: created.append(a) or {})

    result = await revenue_agent.evaluate_profile_deduplication(
        db_session, TENANT, "profile-new-2", "Totally New Person", "new@example.com", "999-999-9999", client=_UnconfiguredClient()
    )
    assert result.candidate_profile_id is None
    assert len(created) == 0


async def test_profile_dedup_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_profile_deduplication(
        db_session, TENANT, "profile-new-3", "Priya Sharma", "priya@example.com", "", client=_UnconfiguredClient()
    )
    assert result.candidate_profile_id is None
    assert result.skipped_reason == "paused"


async def test_profile_dedup_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "build_profile_dedup_tool_dispatch",
        lambda tenant_id, exclude_profile_id: {"get_active_person_profiles": lambda inp: {"profiles": PROFILES_FOR_DEDUP}, "submit_profile_match_result": lambda inp: {}},
    )
    monkeypatch.setattr(crm_tools, "create_profile_duplicate_candidate", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Nickname match, high confidence.",
                tool_calls=[
                    ToolCallResult(tool_name="get_active_person_profiles", tool_input={}, tool_output={"profiles": PROFILES_FOR_DEDUP}),
                    ToolCallResult(
                        tool_name="submit_profile_match_result",
                        tool_input={"candidate_profile_id": "profile-existing-1", "match_score": 0.87, "reasoning": "Priya vs P. Sharma, matching phone area code"},
                        tool_output={"candidate_profile_id": "profile-existing-1", "match_score": 0.87, "reasoning": "Priya vs P. Sharma, matching phone area code"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_profile_deduplication(
        db_session, TENANT, "profile-new-4", "P Sharma", "", "305-555-0142", client=_FakeClient()
    )
    assert result.candidate_profile_id == "profile-existing-1"
    assert result.match_score == 0.87


async def test_handle_person_profile_created_adapter(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_active_person_profiles", lambda tenant_id, exclude_profile_id="": {"profiles": PROFILES_FOR_DEDUP})
    monkeypatch.setattr(crm_tools, "create_profile_duplicate_candidate", lambda *a, **k: {})

    result = await revenue_agent.handle_person_profile_created(
        db_session, TENANT, "crm.person_profile_created",
        {"person_profile_id": "profile-new-5", "full_name": "Priya Sharma", "email": "priya@example.com", "phone": ""},
    )
    assert result.candidate_profile_id == "profile-existing-1"


async def test_prequalification_uses_linked_lead_score(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.85, "routed_to_team": "enterprise_sales"}})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-1", "retail_finance", "Priya Sharma", "lead-1", client=_UnconfiguredClient()
    )
    assert result.verdict == "qualified"
    assert "0.85" in result.reasoning


async def test_prequalification_low_lead_score_needs_review(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.2, "routed_to_team": "general_sales"}})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-2", "subscription_enrollment", "Jane Doe", "lead-2", client=_UnconfiguredClient()
    )
    assert result.verdict == "needs_review"


async def test_prequalification_no_lead_but_has_external_ref(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": None})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-3", "rental_conversion", "Marcus R.", "rental-res-99", client=_UnconfiguredClient()
    )
    assert result.verdict == "qualified"
    assert "rental_conversion" in result.reasoning


async def test_prequalification_incomplete_with_no_signal(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": None})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-4", "retail_finance", "Nobody", "", client=_UnconfiguredClient()
    )
    assert result.verdict == "incomplete"


async def test_prequalification_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-5", "retail_finance", "Priya Sharma", "", client=_UnconfiguredClient()
    )
    assert result.verdict is None
    assert result.skipped_reason == "disabled"


async def test_prequalification_live_claude_path(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(
        org_tools, "build_prequalification_tool_dispatch",
        lambda tenant_id: {"get_lead_score": lambda inp: {"lead": None}, "submit_prequalification": lambda inp: {}},
    )
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Strong candidate.",
                tool_calls=[
                    ToolCallResult(tool_name="get_lead_score", tool_input={"external_ref_id": "lead-1"}, tool_output={"lead": None}),
                    ToolCallResult(
                        tool_name="submit_prequalification",
                        tool_input={"verdict": "qualified", "next_step_guidance": "proceed to decisioning", "reasoning": "complete profile, strong intake signals"},
                        tool_output={"verdict": "qualified", "next_step_guidance": "proceed to decisioning", "reasoning": "complete profile, strong intake signals"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_origination_prequalification(
        db_session, TENANT, "case-6", "retail_finance", "Priya Sharma", "lead-1", client=_FakeClient()
    )
    assert result.verdict == "qualified"
    assert result.next_step_guidance == "proceed to decisioning"


async def test_handle_case_created_adapter(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": None})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    result = await revenue_agent.handle_case_created(
        db_session, TENANT, "org.case_created",
        {"case_id": "case-7", "origination_type": "retail_finance", "full_name": "Priya Sharma", "external_ref_id": "dms-ca-1"},
    )
    assert result.verdict == "qualified"


async def test_risk_scoring_uses_linked_lead_score_directly(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.78, "routed_to_team": "member_experience"}})
    applied = []
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: applied.append(a) or {})

    result = await revenue_agent.evaluate_origination_decisioning(
        db_session, TENANT, "decision-1", "case-1", "subscription_enrollment", "Jane Doe", "lead-1", client=_UnconfiguredClient()
    )
    assert result.risk_score == 0.78
    assert len(applied) == 1


async def test_risk_scoring_no_lead_but_has_reference_gives_moderate_score(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": None})
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_decisioning(
        db_session, TENANT, "decision-2", "case-2", "rental_conversion", "Marcus R.", "rental-res-99", client=_UnconfiguredClient()
    )
    assert result.risk_score == 0.5


async def test_risk_scoring_no_signal_defaults_to_midpoint(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": None})
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: {})

    result = await revenue_agent.evaluate_origination_decisioning(
        db_session, TENANT, "decision-3", "case-3", "subscription_enrollment", "Nobody", "", client=_UnconfiguredClient()
    )
    assert result.risk_score == 0.5
    assert "insufficient signal" in result.reasoning


async def test_risk_scoring_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_origination_decisioning(
        db_session, TENANT, "decision-4", "case-4", "subscription_enrollment", "Jane Doe", "", client=_UnconfiguredClient()
    )
    assert result.risk_score is None
    assert result.skipped_reason == "paused"


async def test_risk_scoring_live_claude_path(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(
        org_tools, "build_risk_scoring_tool_dispatch",
        lambda tenant_id: {"get_lead_score": lambda inp: {"lead": None}, "submit_risk_score": lambda inp: {}},
    )
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Solid rental history.",
                tool_calls=[
                    ToolCallResult(tool_name="get_lead_score", tool_input={"external_ref_id": "rental-1"}, tool_output={"lead": None}),
                    ToolCallResult(
                        tool_name="submit_risk_score",
                        tool_input={"risk_score": 0.72, "reasoning": "consistent on-time rental payments over 8 months"},
                        tool_output={"risk_score": 0.72, "reasoning": "consistent on-time rental payments over 8 months"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_origination_decisioning(
        db_session, TENANT, "decision-5", "case-5", "rental_conversion", "Marcus R.", "rental-1", client=_FakeClient()
    )
    assert result.risk_score == 0.72


async def test_handle_decisioning_requested_adapter(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.9, "routed_to_team": "enterprise_sales"}})
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: {})

    result = await revenue_agent.handle_decisioning_requested(
        db_session, TENANT, "org.decisioning_requested",
        {"decision_id": "decision-6", "case_id": "case-6", "origination_type": "subscription_enrollment", "full_name": "Jane Doe", "external_ref_id": "lead-9"},
    )
    assert result.risk_score == 0.9


FUNDING_SOURCES = [
    {"id": "source-1", "name": "Lender A", "applicable_origination_type": "retail_finance", "min_risk_score": 0.3, "max_amount_usd": 50000.0, "reserve_participation_pct": 2.0},
    {"id": "source-2", "name": "Lender B", "applicable_origination_type": "retail_finance", "min_risk_score": 0.3, "max_amount_usd": 90000.0, "reserve_participation_pct": 3.5},
    {"id": "source-3", "name": "Lender C", "applicable_origination_type": "retail_finance", "min_risk_score": 0.8, "max_amount_usd": 90000.0, "reserve_participation_pct": 5.0},
]


async def test_funding_match_picks_highest_reserve_among_qualifying(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(org_tools, "get_funding_sources", lambda tenant_id, origination_type: {"sources": FUNDING_SOURCES})

    result = await revenue_agent.evaluate_funding_source_matching(
        db_session, TENANT, "case-1", "retail_finance", 0.6, 40000.0, client=_UnconfiguredClient()
    )
    assert result.candidate_funding_source_id == "source-2"  # source-3 disqualified (needs 0.8+), source-2 has higher reserve than source-1


async def test_funding_match_no_qualifying_source(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(org_tools, "get_funding_sources", lambda tenant_id, origination_type: {"sources": FUNDING_SOURCES})

    result = await revenue_agent.evaluate_funding_source_matching(
        db_session, TENANT, "case-2", "retail_finance", 0.1, 40000.0, client=_UnconfiguredClient()
    )
    assert result.candidate_funding_source_id is None


async def test_funding_match_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=revenue_agent.AGENT_KEY, display_name="Revenue Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await revenue_agent.evaluate_funding_source_matching(
        db_session, TENANT, "case-3", "retail_finance", 0.6, 40000.0, client=_UnconfiguredClient()
    )
    assert result.candidate_funding_source_id is None
    assert result.skipped_reason == "paused"


async def test_funding_match_live_claude_path(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(
        org_tools, "build_funding_match_tool_dispatch",
        lambda tenant_id: {"get_funding_sources": lambda inp: {"sources": FUNDING_SOURCES}, "submit_funding_match": lambda inp: {}},
    )

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Lender B is the best qualifying match.",
                tool_calls=[
                    ToolCallResult(tool_name="get_funding_sources", tool_input={"origination_type": "retail_finance"}, tool_output={"sources": FUNDING_SOURCES}),
                    ToolCallResult(
                        tool_name="submit_funding_match", tool_input={"candidate_funding_source_id": "source-2", "reasoning": "highest reserve among qualifying lenders"},
                        tool_output={"candidate_funding_source_id": "source-2", "reasoning": "highest reserve among qualifying lenders"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await revenue_agent.evaluate_funding_source_matching(
        db_session, TENANT, "case-4", "retail_finance", 0.6, 40000.0, client=_FakeClient()
    )
    assert result.candidate_funding_source_id == "source-2"


async def test_handle_funding_match_requested_adapter(db_session, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(org_tools, "get_funding_sources", lambda tenant_id, origination_type: {"sources": FUNDING_SOURCES})

    result = await revenue_agent.handle_funding_match_requested(
        db_session, TENANT, "org.funding_match_requested", {"case_id": "case-5", "origination_type": "retail_finance", "risk_score": 0.6, "amount_usd": 40000.0}
    )
    assert result.candidate_funding_source_id == "source-2"
