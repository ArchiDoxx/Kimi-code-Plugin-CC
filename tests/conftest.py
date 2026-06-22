"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from kimi_code_plugin_cc.agent_registry import _REGISTRY


@pytest.fixture(autouse=True)
def _isolate_registry() -> None:
    """Snapshot and restore the global adapter registry around every test.

    Prevents tests that register temporary adapters from polluting the shared
    global registry for later tests.
    """
    snapshot = _REGISTRY.copy()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)
