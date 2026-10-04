import uuid

from src.services import notice_engine

TENANT = "voyqi-holdings"


async def test_create_and_list_notices(db_session):
    await notice_engine.create_notice(db_session, TENANT, "Q3 Travel Policy Update", "Business travel over 200 miles now requires pre-approval.", posted_by="hr-ops-1", channels="app,email")
    notices = await notice_engine.list_notices(db_session, TENANT)
    assert len(notices) == 1
    assert notices[0].channels == "app,email"


async def test_notice_defaults_to_app_channel_only(db_session):
    notice = await notice_engine.create_notice(db_session, TENANT, "Office closed Friday", "Enjoy the long weekend.")
    assert notice.channels == "app"


async def test_add_and_list_notice_comments_in_order(db_session):
    notice = await notice_engine.create_notice(db_session, TENANT, "New hub opening", "Austin hub opens next month.")
    employee_id = uuid.uuid4()
    await notice_engine.add_notice_comment(db_session, TENANT, notice.id, employee_id, "Excited about this!")
    await notice_engine.add_notice_comment(db_session, TENANT, notice.id, employee_id, "Will there be an opening event?")

    comments = await notice_engine.list_notice_comments(db_session, TENANT, notice.id)
    assert len(comments) == 2
    assert comments[0].body == "Excited about this!"


async def test_add_note_human_authored(db_session):
    employee_id = uuid.uuid4()
    note = await notice_engine.add_note(db_session, TENANT, employee_id, "Discussed career development goals in 1:1.", author="manager-jane", is_ai_generated=False)
    assert note.is_ai_generated is False
    assert note.author == "manager-jane"


async def test_add_note_ai_generated(db_session):
    employee_id = uuid.uuid4()
    note = await notice_engine.add_note(db_session, TENANT, employee_id, "Case resolved: PTO carryover question.", author="talent-agent", is_ai_generated=True)
    assert note.is_ai_generated is True


async def test_list_notes_for_employee_most_recent_first(db_session):
    employee_id = uuid.uuid4()
    await notice_engine.add_note(db_session, TENANT, employee_id, "First note")
    await notice_engine.add_note(db_session, TENANT, employee_id, "Second note")

    notes = await notice_engine.list_notes_for_employee(db_session, TENANT, employee_id)
    assert len(notes) == 2
    assert notes[0].body == "Second note"


async def test_append_to_note_preserves_original_text_exactly(db_session):
    """The core safety guarantee: appending never destroys the
    original human-authored content."""
    employee_id = uuid.uuid4()
    note = await notice_engine.add_note(db_session, TENANT, employee_id, "Original human note about performance.", author="manager-jane")
    original_body = note.body

    updated = await notice_engine.append_to_note(db_session, TENANT, note.id, "Case resolved — see resolution details.", author="talent-agent")
    assert updated.body.startswith(original_body)
    assert original_body in updated.body  # the original text is fully intact, not edited or truncated
    assert "Case resolved" in updated.body
    assert "talent-agent" in updated.body


async def test_append_to_note_can_be_called_multiple_times_without_losing_history(db_session):
    employee_id = uuid.uuid4()
    note = await notice_engine.add_note(db_session, TENANT, employee_id, "Original note.")
    await notice_engine.append_to_note(db_session, TENANT, note.id, "First update.", author="talent-agent")
    twice_updated = await notice_engine.append_to_note(db_session, TENANT, note.id, "Second update.", author="talent-agent")

    assert "Original note." in twice_updated.body
    assert "First update." in twice_updated.body
    assert "Second update." in twice_updated.body


async def test_append_to_note_updates_updated_at_timestamp(db_session):
    employee_id = uuid.uuid4()
    note = await notice_engine.add_note(db_session, TENANT, employee_id, "Original note.")
    original_updated_at = note.updated_at

    updated = await notice_engine.append_to_note(db_session, TENANT, note.id, "New context.")
    assert updated.updated_at >= original_updated_at


async def test_append_to_unknown_note_returns_none(db_session):
    result = await notice_engine.append_to_note(db_session, TENANT, uuid.uuid4(), "text")
    assert result is None


async def test_get_most_recent_note(db_session):
    employee_id = uuid.uuid4()
    await notice_engine.add_note(db_session, TENANT, employee_id, "Older note")
    newest = await notice_engine.add_note(db_session, TENANT, employee_id, "Newest note")

    result = await notice_engine.get_most_recent_note(db_session, TENANT, employee_id)
    assert result.id == newest.id


async def test_get_most_recent_note_no_notes_returns_none(db_session):
    result = await notice_engine.get_most_recent_note(db_session, TENANT, uuid.uuid4())
    assert result is None
