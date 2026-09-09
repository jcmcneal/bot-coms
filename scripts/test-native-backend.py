"""Run against a matching Hermes source checkout supplied through PYTHONPATH."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

# Imports may initialize Hermes runtime paths before fixtures scope individual
# profiles. Keep those imports away from the developer's installed home too.
with TemporaryDirectory(prefix="bot-coms-native-") as test_home:
    os.environ["HERMES_HOME"] = test_home
    # Both repositories have a tests tree. Load Hermes's real-handler fixtures
    # before pytest names bot-coms's importlib-mode test directory.
    import tests.tui_gateway.test_plugin_sessions  # noqa: F401
    import pytest

    raise SystemExit(pytest.main([
        str(Path(__file__).resolve().parents[1] / "tests" / "native"),
        "--import-mode=importlib", "-q",
    ]))
