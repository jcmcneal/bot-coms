"""Unit tests for deterministic turn-taking policy and structured validation."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot_coms_messaging import turn_taking


def test_classify_route_dm_explicit_and_mention_keep():
    assert turn_taking.classify_route(conversation_kind='dm', recipients=[], parent_dispatch=None) == 'keep'
    assert turn_taking.classify_route(
        conversation_kind='group', recipients=['swe-id'], parent_dispatch=None
    ) == 'keep'
    assert turn_taking.classify_route(
        conversation_kind='group', recipients=[], parent_dispatch='parent-1'
    ) == 'keep'


def test_classify_route_ambiguous_group_default_wake():
    assert turn_taking.classify_route(
        conversation_kind='group', recipients=[], parent_dispatch=None
    ) == 'ambiguous'


def test_validate_select_rejects_unknown_speaker():
    decision = turn_taking.validate_decision(
        {'action': 'select', 'speaker': 'stranger', 'reason': 'relevant'},
        members={'swe-id', 'designer-id'},
        default_responder='swe-id',
        unanswered_human=True,
    )
    assert decision.action == 'select'
    assert decision.speaker == 'swe-id'
    assert decision.reason == 'ack'


def test_validate_yield_and_select_member():
    yielded = turn_taking.validate_decision(
        {'action': 'yield', 'reason': 'human'},
        members={'swe-id'},
        default_responder='swe-id',
        unanswered_human=True,
    )
    assert yielded.action == 'yield'
    assert yielded.speaker is None
    assert yielded.reason == 'human'

    selected = turn_taking.validate_decision(
        {'action': 'select', 'speaker': 'designer-id', 'reason': 'addressed'},
        members={'swe-id', 'designer-id'},
        default_responder='swe-id',
        unanswered_human=True,
    )
    assert selected.action == 'select'
    assert selected.speaker == 'designer-id'


def test_fallback_without_unanswered_human_yields():
    decision = turn_taking.validate_decision(
        None,
        members={'swe-id'},
        default_responder='swe-id',
        unanswered_human=False,
    )
    assert decision.action == 'yield'


def test_bounded_view_truncates_body():
    long_body = 'x' * 1000
    view = turn_taking.build_bounded_view(
        members=[{'id': 'swe-id', 'display_name': 'SWE'}],
        default_responder='swe-id',
        messages=[{'id': 'm1', 'sequence': 1, 'author': 'user', 'body': long_body}],
        unanswered_human=True,
        remaining_wakes=3,
    )
    assert '…' in view
    assert len(long_body) not in [len(view)]  # truncated content present as short body


class StubLlm:
    def __init__(self, parsed=None, raise_error=False):
        self.parsed = parsed
        self.raise_error = raise_error
        self.calls = []

    async def acomplete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_error:
            raise TimeoutError('aux timeout')
        return SimpleNamespace(parsed=self.parsed, model='stub-model')


def test_select_speaker_uses_stub_and_persists_shape():
    llm = StubLlm(parsed={'action': 'select', 'speaker': 'designer-id', 'reason': 'relevant'})
    decision = asyncio.run(
        turn_taking.select_speaker(
            llm,
            members=[{'id': 'swe-id'}, {'id': 'designer-id'}],
            member_ids={'swe-id', 'designer-id'},
            default_responder='swe-id',
            messages=[{'id': 'm', 'sequence': 1, 'author': 'user', 'body': 'hi'}],
            unanswered_human=True,
            remaining_wakes=2,
            auxiliary={},
        )
    )
    assert decision.speaker == 'designer-id'
    assert decision.used_model is True
    assert llm.calls[0]['task'] == turn_taking.TITLE_FALLBACK_TASK
    assert llm.calls[0]['max_tokens'] == 64


def test_resolve_selector_task_pin_vs_title_fallback():
    assert turn_taking.resolve_selector_task({}) == turn_taking.TITLE_FALLBACK_TASK
    assert turn_taking.resolve_selector_task(
        {'bot_coms_turn_taking': {'provider': 'auto', 'model': ''}}
    ) == turn_taking.TITLE_FALLBACK_TASK
    assert turn_taking.resolve_selector_task(
        {'bot_coms_turn_taking': {'provider': 'openai-codex', 'model': 'gpt-5.6-luna'}}
    ) == turn_taking.AUX_TASK
    assert turn_taking.resolve_selector_task(
        {'bot_coms_turn_taking': {'provider': 'auto', 'model': 'cheap-model'}}
    ) == turn_taking.AUX_TASK
    assert turn_taking.resolve_selector_task(
        {'bot_coms_turn_taking': {'base_url': 'http://127.0.0.1:1234'}}
    ) == turn_taking.AUX_TASK


def test_select_speaker_uses_pinned_turn_taking_task():
    llm = StubLlm(parsed={'action': 'yield', 'reason': 'nothing_new'})
    asyncio.run(
        turn_taking.select_speaker(
            llm,
            members=[{'id': 'swe-id'}],
            member_ids={'swe-id'},
            default_responder='swe-id',
            messages=[],
            unanswered_human=False,
            remaining_wakes=1,
            auxiliary={'bot_coms_turn_taking': {'provider': 'openai-codex', 'model': 'gpt-5.6-luna'}},
        )
    )
    assert llm.calls[0]['task'] == turn_taking.AUX_TASK


def test_select_speaker_timeout_falls_back():
    llm = StubLlm(raise_error=True)
    decision = asyncio.run(
        turn_taking.select_speaker(
            llm,
            members=[{'id': 'swe-id'}],
            member_ids={'swe-id'},
            default_responder='swe-id',
            messages=[],
            unanswered_human=True,
            remaining_wakes=1,
            auxiliary={},
        )
    )
    assert decision.action == 'select'
    assert decision.speaker == 'swe-id'


def test_select_speaker_missing_llm_falls_back():
    decision = asyncio.run(
        turn_taking.select_speaker(
            None,
            members=[{'id': 'swe-id'}],
            member_ids={'swe-id'},
            default_responder='swe-id',
            messages=[],
            unanswered_human=False,
            remaining_wakes=1,
        )
    )
    assert decision.action == 'yield'


def test_select_speaker_missing_llm_unanswered_wakes_default():
    decision = asyncio.run(
        turn_taking.select_speaker(
            None,
            members=[{'id': 'swe-id'}],
            member_ids={'swe-id'},
            default_responder='swe-id',
            messages=[],
            unanswered_human=True,
            remaining_wakes=1,
        )
    )
    assert decision.action == 'select'
    assert decision.speaker == 'swe-id'
