from pathlib import Path

from bot_coms_messaging.config import profile_model_pin


def test_profile_model_pin_reads_named_and_default(tmp_path: Path):
    (tmp_path / 'config.yaml').write_text(
        'model:\n  provider: xai-oauth\n  default: grok-4.6\n'
    )
    named = tmp_path / 'profiles' / 'swe'
    named.mkdir(parents=True)
    (named / 'config.yaml').write_text(
        'model:\n  provider: openai-codex\n  default: gpt-5.6-luna\n'
    )
    assert profile_model_pin(tmp_path, 'default') == 'xai-oauth:grok-4.6'
    assert profile_model_pin(tmp_path, 'swe') == 'openai-codex:gpt-5.6-luna'
    assert profile_model_pin(tmp_path, 'missing') == ''
