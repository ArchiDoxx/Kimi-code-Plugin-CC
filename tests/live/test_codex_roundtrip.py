"""Live (non-mocked) round-trip test for the Codex adapter.

This spawns the REAL, authenticated ``codex`` CLI — it is not a unit test. It
exists because three properties of the codex integration cannot be proven with
mocks:

1. ``codex exec`` really does exit on its own (kimi does not), so the adapter
   is correct to rely on natural exit instead of a completion sentinel.
2. The child survives the runner's minimal environment allowlist. A missing
   ``CODEX_HOME``/``OPENAI_*`` passthrough only shows up against the real
   process.
3. ``--ephemeral`` really keeps the user's session store clean.

Run explicitly with:

    uv run pytest -m live

Skipped by default so the normal unit-test suite stays hermetic.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.agent_registry.capabilities import supports_flag

live = pytest.mark.live

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("codex") is None,
        reason="codex CLI not on PATH; live tests require an authenticated codex",
    ),
]


def _session_snapshot() -> set[Path]:
    """List the codex session store (honouring ``CODEX_HOME``), read-only."""
    home = os.environ.get("CODEX_HOME")
    sessions = (Path(home) if home else Path.home() / ".codex") / "sessions"
    if not sessions.is_dir():
        return set()
    return set(sessions.rglob("*"))


@live
async def test_codex_round_trip_returns_the_agents_final_message() -> None:
    """Prompt in, text out, through the real CLI and the shared runner."""
    adapter = get("codex")
    before = _session_snapshot()

    message = await adapter.run(
        "Reply with exactly the word BANANA and nothing else.",
        {"depth": 0, "bridge_id": "live-test", "approval_policy": "read-only"},
    )

    assert "BANANA" in message.payload.upper(), (
        f"codex did not return its final message. Payload: {message.payload!r}"
    )
    assert message.approval_policy == "read-only"

    # --ephemeral must leave the user's session store untouched. If the
    # installed CLI is too old to support it the adapter omits the flag, so
    # only assert when the capability is actually there.
    codex = shutil.which("codex")
    assert codex is not None
    if supports_flag([codex, "exec"], "--ephemeral"):
        assert _session_snapshot() == before, (
            "--ephemeral was requested but codex still wrote session files"
        )


@live
async def test_codex_multiline_prompt_reaches_the_agent() -> None:
    """The prompt travels as one argv element; newlines must survive.

    This is the codex counterpart of the kimi ``.cmd``-shim truncation test —
    codex ships a native binary, so the failure mode differs, but the property
    the loops depend on is the same.
    """
    adapter = get("codex")
    message = await adapter.run(
        "Name the bug on the next line in one short sentence:\n"
        "def sub(a, b): return a + b   # named sub but adds",
        {"depth": 0, "bridge_id": "live-test", "approval_policy": "read-only"},
    )
    lowered = message.payload.lower()
    assert any(token in lowered for token in ("sub", "add", "mismatch", "name")), (
        f"codex did not reference the line-2 bug. Payload: {message.payload!r}"
    )
