"""Hermes plugin adapter for bot-coms board (team coordination lane)."""

from __future__ import annotations

from bot_coms_board.tool import TEAM_BUS_SCHEMA
from hermes_bot_coms_board.tools import (
    TEAM_WORKFLOW_SCHEMA,
    team_workflow,
    TEAM_ASSIGN_SCHEMA,
    TEAM_INBOX_SCHEMA,
    TEAM_REPORT_SCHEMA,
    team_assign,
    team_bus,
    team_inbox,
    team_report,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="team_bus",
        toolset="team_bus",
        schema=TEAM_BUS_SCHEMA,
        handler=lambda args, **kw: team_bus(args, **kw),
        description=TEAM_BUS_SCHEMA.get("description", ""),
        emoji="📋",
    )
    ctx.register_tool(
        name="team_assign",
        toolset="team_bus",
        schema=TEAM_ASSIGN_SCHEMA,
        handler=lambda args, **kw: team_assign(args, **kw),
        description=TEAM_ASSIGN_SCHEMA.get("description", ""),
        emoji="📤",
    )
    ctx.register_tool(
        name="team_inbox",
        toolset="team_bus",
        schema=TEAM_INBOX_SCHEMA,
        handler=lambda args, **kw: team_inbox(args, **kw),
        description=TEAM_INBOX_SCHEMA.get("description", ""),
        emoji="📥",
    )
    ctx.register_tool(
        name="team_report",
        toolset="team_bus",
        schema=TEAM_REPORT_SCHEMA,
        handler=lambda args, **kw: team_report(args, **kw),
        description=TEAM_REPORT_SCHEMA.get("description", ""),
        emoji="📣",
    )

    ctx.register_tool(name="team_workflow", toolset="team_bus", schema=TEAM_WORKFLOW_SCHEMA,
                      handler=lambda args, **kw: team_workflow(args, **kw),
                      description=TEAM_WORKFLOW_SCHEMA['description'], emoji="📋")
