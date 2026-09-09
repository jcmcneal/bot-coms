"""Predictive turn-taking before expensive agent session admission.

Deterministic routing wins first. A Hermes plugin auxiliary structured call
runs only for ambiguous group turns (default-responder wake with no explicit
recipients). Membership and authority are validated outside the model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Protocol

POLICY_VERSION = "1"
AUX_TASK = "bot_coms_turn_taking"
MAX_VIEW_MESSAGES = 20
MAX_BODY_CHARS = 400

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reason"],
    "properties": {
        "action": {"enum": ["select", "yield"]},
        "speaker": {"type": "string"},
        "reason": {"enum": ["addressed", "relevant", "ack", "nothing_new", "human"]},
    },
}

INSTRUCTIONS = (
    "Pick at most one eligible member id from the roster, or yield. "
    "Never invent ids. Prefer yield when the human should answer or nothing "
    "new is useful. Prefer the default responder for brief group acknowledgements."
)


class LlmSelector(Protocol):
    async def acomplete_structured(self, **kwargs) -> Any: ...


@dataclass(frozen=True)
class TurnDecision:
    action: str  # select | yield
    speaker: Optional[str]
    reason: str
    model: str
    policy_version: str = POLICY_VERSION
    used_model: bool = False


def classify_route(
    *,
    conversation_kind: str,
    recipients: list,
    parent_dispatch: Optional[str],
) -> str:
    """Return keep | ambiguous.

    keep: admit the queued profile as-is (DM, explicit recipients, mention hop).
    ambiguous: may invoke the auxiliary selector.
    """
    if conversation_kind == "dm":
        return "keep"
    if parent_dispatch:
        return "keep"
    if recipients:
        return "keep"
    return "ambiguous"


def build_bounded_view(
    *,
    members: list[dict],
    default_responder: str,
    messages: list[dict],
    unanswered_human: bool,
    remaining_wakes: int,
) -> str:
    roster = [
        {
            "id": m["id"],
            "display_name": m.get("display_name") or m.get("name") or m["id"],
        }
        for m in members
        if isinstance(m, dict) and m.get("id")
    ]
    history = []
    for row in messages[-MAX_VIEW_MESSAGES:]:
        body = str(row.get("body") or "")
        if len(body) > MAX_BODY_CHARS:
            body = body[: MAX_BODY_CHARS - 1] + "…"
        history.append(
            {
                "id": row["id"],
                "sequence": row["sequence"],
                "author": row["author"],
                "body": body,
            }
        )
    return json.dumps(
        {
            "members": roster,
            "default_responder": default_responder,
            "unanswered_human_request": unanswered_human,
            "remaining_origin_wake_budget": remaining_wakes,
            "messages": history,
        },
        ensure_ascii=False,
    )


def validate_decision(
    parsed: Optional[dict],
    *,
    members: set[str],
    default_responder: str,
    unanswered_human: bool,
    model: str = "",
    used_model: bool = False,
) -> TurnDecision:
    """Coerce model/fallback output into a membership-safe decision."""
    if not isinstance(parsed, dict):
        return _fallback(default_responder, unanswered_human, model=model, used_model=used_model)
    action = parsed.get("action")
    reason = parsed.get("reason")
    if reason not in {"addressed", "relevant", "ack", "nothing_new", "human"}:
        reason = "relevant"
    if action == "yield":
        return TurnDecision(
            action="yield",
            speaker=None,
            reason=reason if reason in {"nothing_new", "human"} else "nothing_new",
            model=model,
            used_model=used_model,
        )
    if action == "select":
        speaker = parsed.get("speaker")
        if isinstance(speaker, str) and speaker in members:
            return TurnDecision(
                action="select",
                speaker=speaker,
                reason=reason if reason in {"addressed", "relevant", "ack"} else "relevant",
                model=model,
                used_model=used_model,
            )
    return _fallback(default_responder, unanswered_human, model=model, used_model=used_model)


def _fallback(
    default_responder: str,
    unanswered_human: bool,
    *,
    model: str = "",
    used_model: bool = False,
) -> TurnDecision:
    if unanswered_human:
        return TurnDecision(
            action="select",
            speaker=default_responder,
            reason="ack",
            model=model,
            used_model=used_model,
        )
    return TurnDecision(
        action="yield",
        speaker=None,
        reason="nothing_new",
        model=model,
        used_model=used_model,
    )


async def select_speaker(
    llm: Optional[LlmSelector],
    *,
    members: list[dict],
    member_ids: set[str],
    default_responder: str,
    messages: list[dict],
    unanswered_human: bool,
    remaining_wakes: int,
) -> TurnDecision:
    """Run one structured aux call, or fall back when llm is missing/fails."""
    if llm is None:
        return _fallback(default_responder, unanswered_human)
    view = build_bounded_view(
        members=members,
        default_responder=default_responder,
        messages=messages,
        unanswered_human=unanswered_human,
        remaining_wakes=remaining_wakes,
    )
    try:
        result = await llm.acomplete_structured(
            task=AUX_TASK,
            purpose="bot-coms.turn-taking",
            temperature=0,
            max_tokens=64,
            json_schema=DECISION_SCHEMA,
            instructions=INSTRUCTIONS,
            input=[{"type": "text", "text": view}],
        )
    except Exception:
        return _fallback(default_responder, unanswered_human)
    model = getattr(result, "model", "") or ""
    parsed = getattr(result, "parsed", None)
    return validate_decision(
        parsed,
        members=member_ids,
        default_responder=default_responder,
        unanswered_human=unanswered_human,
        model=model if isinstance(model, str) else "",
        used_model=True,
    )
