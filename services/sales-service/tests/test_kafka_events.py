from src.core.kafka_events import build_envelope


def test_build_envelope_matches_platform_shape():
    """ai-orchestration-svc's consumer reads {event_type, tenant_id,
    payload} — a raw payload would be skipped as malformed."""
    topic, envelope = build_envelope("sales.order_created", {"tenant_id": "tenant-1", "id": "abc"})

    assert topic == "sales.events"
    assert envelope["event_type"] == "sales.order_created"
    assert envelope["tenant_id"] == "tenant-1"
    assert envelope["payload"] == {"id": "abc"}
    assert "occurred_at" in envelope


def test_build_envelope_does_not_mutate_caller_payload():
    payload = {"tenant_id": "tenant-1", "id": "abc"}
    build_envelope("sales.order_created", payload)
    assert payload == {"tenant_id": "tenant-1", "id": "abc"}
