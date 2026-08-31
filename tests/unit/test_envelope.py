from __future__ import annotations

import pytest

from bot_coms.envelope import envelope_from_dict, generate_ulid, new_envelope
from bot_coms.types import FakeClock, ValidationError


def test_ulid_length_and_alphabet() -> None:
    uid = generate_ulid()
    assert len(uid) == 26
    assert uid == uid.upper()


def test_new_envelope_defaults(clock: FakeClock) -> None:
    env = new_envelope(
        from_peer="a",
        to="b",
        msg_type="request",
        payload={"x": 1},
        clock=clock,
        ttl_s=60,
    )
    assert env.idempotency_key == env.id
    assert env.correlation_id == env.id
    assert env.attempt == 0
    parsed = envelope_from_dict(env.to_dict())
    assert parsed.from_peer == "a"


def test_rejects_bad_peer() -> None:
    with pytest.raises(ValidationError):
        envelope_from_dict(
            {
                "schema_version": "1.0",
                "id": generate_ulid(),
                "idempotency_key": "k",
                "correlation_id": "c",
                "from": "../etc",
                "to": "b",
                "type": "request",
                "payload": {},
                "created_at": "2026-08-31T18:00:00.000Z",
                "expires_at": "2026-09-01T18:00:00.000Z",
                "attempt": 0,
            }
        )


def test_rejects_major_2() -> None:
    with pytest.raises(ValidationError):
        envelope_from_dict(
            {
                "schema_version": "2.0",
                "id": generate_ulid(),
                "idempotency_key": "k",
                "correlation_id": "c",
                "from": "a",
                "to": "b",
                "type": "request",
                "payload": {},
                "created_at": "2026-08-31T18:00:00.000Z",
                "expires_at": "2026-09-01T18:00:00.000Z",
                "attempt": 0,
            }
        )


def test_ignores_unknown_optional_fields() -> None:
    data = new_envelope(
        from_peer="a",
        to="b",
        msg_type="event",
        payload={},
        clock=FakeClock(),
        ttl_s=10,
    ).to_dict()
    data["future_field"] = "ok"
    env = envelope_from_dict(data)
    assert env.to == "b"


def test_payload_must_be_object() -> None:
    data = new_envelope(
        from_peer="a",
        to="b",
        msg_type="event",
        payload={},
        clock=FakeClock(),
        ttl_s=10,
    ).to_dict()
    data["payload"] = ["nope"]
    with pytest.raises(ValidationError):
        envelope_from_dict(data)
