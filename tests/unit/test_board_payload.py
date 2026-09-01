"""Tests for board payload validation."""

from __future__ import annotations

import pytest

from bot_coms_board.payload import (
    PayloadError,
    assignment_spec_text,
    parse_payload,
    sha256_assignment_spec,
    sha256_whole_file,
    verify_content_digest,
)


class TestParsePayload:
    def test_assign_minimal(self):
        p = parse_payload({"schema_version": "1.0", "intent": "assign", "slice": "S9"})
        assert p.intent == "assign"
        assert p.slice == "S9"

    def test_report_only(self):
        p = parse_payload({"intent": "report_only", "slice": "SMOKE-1"})
        assert p.intent == "report_only"

    def test_rejects_legacy_text(self):
        with pytest.raises(PayloadError) as exc:
            parse_payload({"text": "PING read /Users/jason/.hermes/team/BUS.md item S9"})
        assert exc.value.code == "LEGACY_PING"

    def test_rejects_extra_keys(self):
        with pytest.raises(PayloadError) as exc:
            parse_payload(
                {
                    "intent": "assign",
                    "slice": "S9",
                    "title": "should not be here",
                }
            )
        assert exc.value.code == "EXTRA_KEYS"

    def test_idempotency_key_includes_digest_prefix(self):
        p = parse_payload({"intent": "assign", "slice": "S9"})
        assert p.idempotency_key("abcdef0123456789").startswith("assign:S9:abcdef01")


class TestContentDigest:
    def test_verify_match(self, tmp_path):
        f = tmp_path / "ctx.md"
        f.write_text("hello", encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(b"hello").hexdigest()
        verify_content_digest(str(f), digest)

    def test_stale_raises(self, tmp_path):
        f = tmp_path / "ctx.md"
        f.write_text("hello", encoding="utf-8")
        with pytest.raises(PayloadError) as exc:
            verify_content_digest(str(f), "deadbeef")
        assert exc.value.code == "STALE_ASSIGNMENT"

    def test_spec_digest_excludes_notes_section(self, tmp_path):
        f = tmp_path / "ctx.md"
        base = "## Assignment\nwork\n\n## Notes\n"
        f.write_text(base, encoding="utf-8")
        digest = sha256_assignment_spec(f)
        f.write_text(base + "- landed\n", encoding="utf-8")
        verify_content_digest(str(f), digest)
        assert sha256_whole_file(f) != digest

    def test_legacy_whole_file_digest_still_valid(self, tmp_path):
        f = tmp_path / "ctx.md"
        body = "## Assignment\nwork\n\n## Notes\n"
        f.write_text(body, encoding="utf-8")
        legacy = sha256_whole_file(f)
        verify_content_digest(str(f), legacy)

    def test_assignment_spec_text(self):
        text = "## Assignment\nx\n\n## Notes\n-y\n"
        assert assignment_spec_text(text) == "## Assignment\nx"
