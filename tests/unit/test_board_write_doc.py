"""Tests for team_bus write_doc and doc_write helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot_coms_board import doc_write as dw
from bot_coms_board import tool as tbt


@pytest.fixture
def team_env(tmp_path: Path, monkeypatch):
    team_root = tmp_path / ".hermes" / "team"
    team_root.mkdir(parents=True)
    monkeypatch.setenv("BOT_COMS_TEAM_ROOT", str(team_root))
    return {
        "team_root": team_root,
        "team_md": team_root.parent / "TEAM.md",
        "standing_md": team_root / "STANDING.md",
    }


class TestDocWrite:
    def test_allowed_paths_respect_team_root(self, team_env):
        allow = dw.allowed_doc_paths(team_root=team_env["team_root"])
        assert team_env["team_md"].resolve() in allow
        assert team_env["standing_md"].resolve() in allow
        assert len(allow) == 2

    def test_resolve_rejects_outside_allowlist(self, team_env):
        with pytest.raises(ValueError, match="not allowed"):
            dw.resolve_allowed_path("/etc/passwd", team_root=team_env["team_root"])
        with pytest.raises(ValueError, match="not allowed"):
            dw.resolve_allowed_path(
                str(team_env["team_root"] / "context" / "S9.md"),
                team_root=team_env["team_root"],
            )

    def test_replace_atomic_write(self, team_env):
        path = str(team_env["team_md"])
        dw.write_doc(path=path, content="# Team\n", mode="replace", team_root=team_env["team_root"])
        assert team_env["team_md"].read_text(encoding="utf-8") == "# Team\n"
        assert list(team_env["team_md"].parent.glob("*.partial")) == []

    def test_atomic_write_cleans_partial_on_failure(self, team_env, monkeypatch):
        path = str(team_env["team_md"])

        def boom(stage: str) -> None:
            if stage == "before_rename":
                raise OSError("injected")

        dw.set_fault_hook(boom)
        try:
            with pytest.raises(OSError):
                dw.write_doc(
                    path=path,
                    content="nope\n",
                    mode="replace",
                    team_root=team_env["team_root"],
                )
        finally:
            dw.set_fault_hook(None)
        assert not team_env["team_md"].exists()
        assert list(team_env["team_md"].parent.glob("*.partial")) == []

    def test_append(self, team_env):
        path = str(team_env["standing_md"])
        dw.write_doc(path=path, content="line1\n", mode="replace", team_root=team_env["team_root"])
        dw.write_doc(path=path, content="line2\n", mode="append", team_root=team_env["team_root"])
        assert team_env["standing_md"].read_text(encoding="utf-8") == "line1\nline2\n"

    def test_upsert_section_creates(self, team_env):
        path = str(team_env["team_md"])
        dw.write_doc(
            path=path,
            content="- no force-push to main\n",
            mode="upsert_section",
            section="Standing rules",
            team_root=team_env["team_root"],
        )
        text = team_env["team_md"].read_text(encoding="utf-8")
        assert "## Standing rules\n- no force-push to main\n" in text

    def test_upsert_section_replaces_idempotently(self, team_env):
        path = str(team_env["team_md"])
        team_env["team_md"].write_text(
            "# Team\n\n## Standing rules\n- old rule\n\n## Other\nkeep\n",
            encoding="utf-8",
        )
        dw.write_doc(
            path=path,
            content="- new rule\n",
            mode="upsert_section",
            section="Standing rules",
            team_root=team_env["team_root"],
        )
        text = team_env["team_md"].read_text(encoding="utf-8")
        assert "- new rule\n" in text
        assert "- old rule" not in text
        assert "## Other\nkeep\n" in text

    def test_upsert_section_rejects_standing_md(self, team_env):
        with pytest.raises(ValueError, match="TEAM.md"):
            dw.write_doc(
                path=str(team_env["standing_md"]),
                content="x\n",
                mode="upsert_section",
                section="Rules",
                team_root=team_env["team_root"],
            )


class TestTeamBusWriteDoc:
    def test_pm_replace_team_md(self, team_env):
        raw = tbt.handle_team_bus(
            {
                "action": "write_doc",
                "path": str(team_env["team_md"]),
                "content": "# Constitution\n",
                "mode": "replace",
            },
            actor="project-manager",
        )
        out = json.loads(raw)
        assert out["success"] is True
        assert out["action"] == "write_doc"
        assert team_env["team_md"].read_text(encoding="utf-8") == "# Constitution\n"

    def test_non_pm_rejected(self, team_env):
        raw = tbt.handle_team_bus(
            {
                "action": "write_doc",
                "path": str(team_env["team_md"]),
                "content": "nope\n",
            },
            actor="software-engineer",
        )
        out = json.loads(raw)
        assert out["success"] is False
        assert "PM-only" in out["error"]

    def test_path_jail_via_tool(self, team_env, tmp_path: Path):
        rogue = tmp_path / "AGENTS.md"
        rogue.write_text("secrets", encoding="utf-8")
        raw = tbt.handle_team_bus(
            {
                "action": "write_doc",
                "path": str(rogue),
                "content": "pwn\n",
            },
            actor="project-manager",
        )
        out = json.loads(raw)
        assert out["success"] is False
        assert "not allowed" in out["error"]
        assert rogue.read_text(encoding="utf-8") == "secrets"

    def test_standing_rule_upsert_via_tool(self, team_env):
        raw = tbt.handle_team_bus(
            {
                "action": "write_doc",
                "path": str(team_env["team_md"]),
                "mode": "upsert_section",
                "section": "Standing rules",
                "content": "- BUS notes via team_bus log only\n",
            },
            actor="project-manager",
        )
        out = json.loads(raw)
        assert out["success"] is True
        text = team_env["team_md"].read_text(encoding="utf-8")
        assert "BUS notes via team_bus log only" in text
