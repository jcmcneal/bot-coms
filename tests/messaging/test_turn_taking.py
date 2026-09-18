"""Auto / empty-To must elect a speaker on an unanswered human send."""
from bot_coms_messaging.turn_taking import validate_decision


def test_unanswered_human_yield_elects_default_responder():
    decision = validate_decision(
        {"action": "yield", "reason": "human"},
        members={"pm", "ux"},
        default_responder="pm",
        unanswered_human=True,
        model="gpt-5.6-luna",
        used_model=True,
    )
    assert decision.action == "select"
    assert decision.speaker == "pm"
    assert decision.reason == "ack"


def test_nothing_new_still_yields_when_not_unanswered():
    decision = validate_decision(
        {"action": "yield", "reason": "nothing_new"},
        members={"pm"},
        default_responder="pm",
        unanswered_human=False,
        model="",
    )
    assert decision.action == "yield"
    assert decision.speaker is None
    assert decision.reason == "nothing_new"
