"""CLI contract tests: pin the flags the adapters depend on.

Both agent CLIs update frequently. The plugin is deliberately NOT pinned to a
CLI version — it only depends on a small public flag surface. These tests run
``--help`` (no auth, no API call, exits on its own) and fail loudly if any
pinned flag disappears, so drift is caught by a test run instead of by the
first broken review.

Each class skips automatically on machines without that CLI on PATH (e.g. CI).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kimi_code_plugin_cc.agent_registry.codex_contract import (
    BANNED_SANDBOX_MODE,
    NEVER_FLAGS,
    REQUIRED_EXEC_FLAGS,
)

KIMI_PATH = shutil.which("kimi")
CODEX_PATH = shutil.which("codex")

# The complete flag surface the kimi adapter uses (see agent_registry/kimi.py):
# -p for the prompt, --output-format stream-json for parseable output,
# -m/--model for per-call model aliases (multi-provider setups).
REQUIRED_KIMI_FRAGMENTS = (
    "-p, --prompt",
    "--output-format",
    "stream-json",
    "-m, --model",
)

# Sandbox values the codex adapter maps plugin policies onto. Losing either
# would silently change what "read-only" means at the CLI boundary.
REQUIRED_SANDBOX_MODES = ("read-only", "workspace-write")


def _help(argv: list[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, resolved path
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        shell=False,
    )
    return completed.stdout + completed.stderr


@pytest.mark.skipif(KIMI_PATH is None, reason="kimi CLI not installed on PATH")
def test_kimi_help_still_pins_required_flags() -> None:
    assert KIMI_PATH is not None
    help_text = _help([KIMI_PATH, "--help"])
    missing = [f for f in REQUIRED_KIMI_FRAGMENTS if f not in help_text]
    assert not missing, (
        f"kimi CLI help no longer documents {missing} — the CLI interface the "
        f"adapter depends on has drifted. Re-verify agent_registry/kimi.py "
        f"against `kimi --help` and update the pinned flags."
    )


@pytest.fixture(scope="module")
def exec_help() -> str:
    """``codex exec --help`` text, fetched once for the contract assertions."""
    assert CODEX_PATH is not None
    return _help([CODEX_PATH, "exec", "--help"])


@pytest.mark.skipif(CODEX_PATH is None, reason="codex CLI not installed on PATH")
class TestCodexCliContract:
    """Pins ``codex exec``'s flag surface (verified against codex-cli 0.145.0)."""

    def test_required_flags_are_documented(self, exec_help: str) -> None:
        missing = [f for f in REQUIRED_EXEC_FLAGS if f not in exec_help]
        assert not missing, (
            f"`codex exec --help` no longer documents {missing} — the CLI "
            f"interface the adapter depends on has drifted. Re-verify "
            f"agent_registry/codex.py against the installed CLI."
        )

    def test_sandbox_modes_are_documented(self, exec_help: str) -> None:
        missing = [m for m in REQUIRED_SANDBOX_MODES if m not in exec_help]
        assert not missing, (
            f"`codex exec --help` no longer offers sandbox mode(s) {missing}; "
            f"the policy mapping in agent_registry/codex.py is invalid."
        )

    def test_banned_flags_still_exist_under_pinned_names(self, exec_help: str) -> None:
        """A rename must break the build, not silently unenforce the ban.

        The adapter's guarantee is "these flags are never emitted". That
        guarantee is only meaningful while the flags still carry these names —
        if upstream renames one, our never-list stops covering the dangerous
        capability and the ban has to be re-derived from the new help output.
        """
        missing = [f for f in (*NEVER_FLAGS, BANNED_SANDBOX_MODE) if f not in exec_help]
        assert not missing, (
            f"Banned codex capabilities {missing} are no longer named that way "
            f"in `codex exec --help`. The structural auto-approve ban in "
            f"agent_registry/codex.py must be re-derived from the current CLI "
            f"before this test is updated."
        )
