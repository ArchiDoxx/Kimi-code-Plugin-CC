"""Live (non-mocked) transport-integrity smoke tests for the Kimi adapter.

These tests spawn the REAL, authenticated ``kimi`` CLI — they are NOT unit
tests. They exist specifically to catch the Windows ``.cmd``-shim multi-line
truncation bug (see LIVE_TEST.md §3 and ADR-005): mock-based tests cannot
find it because the failure is in the real subprocess/cmd.exe path.

Run them explicitly with:

    uv run pytest -m live

They are skipped by default (no ``kimi`` auth, CI sandbox, etc.) so the
normal unit-test suite stays hermetic.
"""

from __future__ import annotations

import shutil

import pytest

from kimi_code_plugin_cc.agent_registry import get
from kimi_code_plugin_cc.agent_registry.kimi import _deshim_cmd_wrapper

# Opt-in marker. Register it so ``-m live`` works without extra config.
live = pytest.mark.live


def _kimi_available() -> bool:
    """True iff a real ``kimi`` executable is on PATH."""
    return shutil.which("kimi") is not None


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _kimi_available(),
        reason="kimi CLI not on PATH; live tests require an authenticated kimi",
    ),
]


@live
def test_deshim_resolves_cmd_shim_to_node_entry() -> None:
    """The Windows ``kimi.CMD`` shim must resolve to ``node <main.mjs>``.

    This is the static half of the fix: if it regresses, the dynamic multi-line
    test below will also fail, but this gives a fast, deterministic signal.
    """
    shim = shutil.which("kimi")
    assert shim is not None
    deshimed = _deshim_cmd_wrapper(__import__("pathlib").Path(shim))
    if deshimed is None:
        # On POSIX there is no .cmd shim; nothing to de-shim. Skip gracefully.
        pytest.skip("not a .cmd/.bat shim (POSIX)")
    assert deshimed[0] in ("node", "node.exe") or deshimed[0].endswith("node.exe")
    entry = deshimed[1]
    assert entry.endswith((".mjs", ".js", ".cjs"))
    assert __import__("pathlib").Path(entry).exists(), (
        f"resolved node entry-point does not exist: {entry}"
    )


@live
async def test_multiline_prompt_reaches_agent() -> None:
    """A multi-line prompt must reach kimi intact (line 2 must be seen).

    Before the de-shim fix this failed on Windows: ``cmd.exe`` truncated the
    ``kimi.CMD`` argument at the first ``\\n``, so only line 1 arrived and
    kimi reported the message as cut off.
    """
    adapter = get("kimi")
    prompt = "Reply with the word on the next line and nothing else.\nBANANA"
    msg = await adapter.run(
        prompt,
        {"depth": 0, "bridge_id": "live-test", "approval_policy": "read-only"},
    )
    assert "BANANA" in msg.payload.upper(), (
        f"Multi-line prompt was truncated; line 2 never reached kimi. "
        f"Payload: {msg.payload!r}"
    )


@live
async def test_multiline_bug_review_reaches_agent() -> None:
    """A realistic multi-line code-review brief must be seen by kimi.

    The bug is on line 2 of the prompt; kimi must name it. This is the
    §3.2 transport-integrity test from LIVE_TEST.md, automated.
    """
    adapter = get("kimi")
    prompt = (
        "Name the bug on the next line in one sentence:\n"
        "def sub(a,b): return a+b   # named sub but adds"
    )
    msg = await adapter.run(
        prompt,
        {"depth": 0, "bridge_id": "live-test", "approval_policy": "read-only"},
    )
    lowered = msg.payload.lower()
    # kimi should mention the name/operation mismatch (sub vs add).
    assert any(token in lowered for token in ("sub", "add", "mismatch", "name")), (
        f"kimi did not reference the line-2 bug. Payload: {msg.payload!r}"
    )
