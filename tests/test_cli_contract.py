"""CLI contract test: pins the kimi flags the adapter depends on.

The Kimi Code CLI updates frequently. The plugin is deliberately NOT pinned to
a CLI version — it only depends on this small public flag surface. This test
runs ``kimi --help`` (no auth, no API call, exits on its own) and fails loudly
if any pinned flag disappears from the help text, so drift is caught by a test
run instead of by the first broken review.

Skipped automatically on machines without the kimi CLI on PATH (e.g. CI).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

KIMI_PATH = shutil.which("kimi")

pytestmark = pytest.mark.skipif(
    KIMI_PATH is None, reason="kimi CLI not installed on PATH"
)

# The complete flag surface the adapter uses (see agent_registry/kimi.py):
# -p for the prompt, --output-format stream-json for parseable output,
# -m/--model for per-call model aliases (multi-provider setups).
REQUIRED_HELP_FRAGMENTS = (
    "-p, --prompt",
    "--output-format",
    "stream-json",
    "-m, --model",
)


def test_kimi_help_still_pins_required_flags() -> None:
    completed = subprocess.run(  # noqa: S603 - fixed argv, resolved path
        [KIMI_PATH, "--help"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        shell=False,
    )
    help_text = completed.stdout + completed.stderr
    missing = [f for f in REQUIRED_HELP_FRAGMENTS if f not in help_text]
    assert not missing, (
        f"kimi CLI help no longer documents {missing} — the CLI interface the "
        f"adapter depends on has drifted. Re-verify agent_registry/kimi.py "
        f"against `kimi --help` and update the pinned flags."
    )
