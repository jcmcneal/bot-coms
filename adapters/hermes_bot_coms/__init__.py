"""Hermes tools plugin for bot-coms.

Loadable beside the legacy agent-to-agent platform plugin. Imports only
bot_coms plus the Hermes PluginContext passed into register(). Never reads
the team coordination file or product-tree paths.
"""

from __future__ import annotations

from hermes_bot_coms.tools import (
    ACK_SCHEMA,
    CLAIM_SCHEMA,
    EMIT_SCHEMA,
    NACK_SCHEMA,
    RECLAIM_SCHEMA,
    REQUEST_SCHEMA,
    SEND_SCHEMA,
    STATUS_SCHEMA,
    bot_coms_ack,
    bot_coms_claim,
    bot_coms_emit,
    bot_coms_nack,
    bot_coms_reclaim,
    bot_coms_request,
    bot_coms_send,
    bot_coms_status,
)

# Host-owned PluginLlm from the last successful register(ctx). Messaging uses
# this for turn-taking without constructing PluginLlm itself.
_plugin_llm = None

TURN_TAKING_TASK = "bot_coms_turn_taking"


def get_plugin_llm():
    """Return the PluginLlm bound at plugin register time, or None."""
    return _plugin_llm


def register(ctx) -> None:
    """Register bot-coms tools and the turn-taking auxiliary LLM task."""
    global _plugin_llm
    _plugin_llm = ctx.llm
    ctx.register_auxiliary_task(
        TURN_TAKING_TASK,
        display_name="Turn taking",
        description="Select next messaging speaker or yield",
        defaults={"provider": "auto", "model": "", "timeout": 8},
    )
    for name, schema, handler, desc in (
        ("bot_coms_request", REQUEST_SCHEMA, bot_coms_request, "Request/wait path for agent callers"),
        ("bot_coms_emit", EMIT_SCHEMA, bot_coms_emit, "Fire-and-forget enqueue for agent callers"),
        ("bot_coms_send", SEND_SCHEMA, bot_coms_send, "Enqueue a bot-coms message to a peer inbox"),
        ("bot_coms_claim", CLAIM_SCHEMA, bot_coms_claim, "Claim the next eligible bot-coms inbox message"),
        ("bot_coms_reclaim", RECLAIM_SCHEMA, bot_coms_reclaim, "Reclaim stale bot-coms processing messages"),
        ("bot_coms_ack", ACK_SCHEMA, bot_coms_ack, "Ack a claimed bot-coms message"),
        ("bot_coms_nack", NACK_SCHEMA, bot_coms_nack, "Nack a claimed bot-coms message"),
        ("bot_coms_status", STATUS_SCHEMA, bot_coms_status, "Inspect bot-coms message or peer spool status"),
    ):
        ctx.register_tool(
            name=name,
            toolset="bot_coms",
            schema=schema,
            handler=handler,
            description=desc,
        )
