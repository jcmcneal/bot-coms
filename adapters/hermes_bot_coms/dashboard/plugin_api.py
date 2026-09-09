"""Mounted only when the shared Hermes instance explicitly enables bot-coms."""
from bot_coms_runtime.backend import create_router

router = create_router()
