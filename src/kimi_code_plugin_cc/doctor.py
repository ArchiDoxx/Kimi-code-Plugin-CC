"""Preflight diagnostics for the agent bridge.

Almost every first-run failure has the same small set of causes: the agent CLI
is not installed or not on PATH, it is installed but not responding, or the
installed version no longer exposes a flag the adapter depends on. Each of
those surfaces at review time as a confusing error in the middle of unrelated
work.

``doctor`` checks them up front and reports them together, so the answer to
"why did my review fail" is one command rather than a debugging session.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kimi_code_plugin_cc.agent_registry.capabilities import cli_version, help_text
from kimi_code_plugin_cc.agent_registry.kimi import (
    DEFAULT_MAX_PROMPT_CHARS,
    KIMI_EXECUTABLE,
    _deshim_cmd_wrapper,
    _max_prompt_chars,
    _skills_isolation_enabled,
)
from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH
from kimi_code_plugin_cc.security.policy import (
    create_isolated_worktree,
    read_max_policy_from_env,
)

Status = Literal["ok", "warn", "fail"]

# The flag surface the adapter actually depends on. Losing any of these breaks
# every run, so their absence is a failure rather than a warning.
REQUIRED_FLAGS = ("-p", "--output-format", "-m")

_SYMBOLS: dict[Status, str] = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}


@dataclass(frozen=True)
class Check:
    """One diagnostic result."""

    name: str
    status: Status
    detail: str


def _resolve_prefix() -> list[str] | None:
    """Return the argv prefix used to invoke the agent CLI, or None if absent."""
    executable = shutil.which(KIMI_EXECUTABLE)
    if executable is None:
        return None
    deshimed = _deshim_cmd_wrapper(Path(executable))
    return deshimed if deshimed is not None else [executable]


def _check_worktree_base() -> Check:
    """Verify the isolated-worktree base directory is actually usable."""
    configured = os.environ.get("KIMI_WORKTREE_BASE")
    base = configured or tempfile.gettempdir()
    try:
        worktree = create_isolated_worktree()
    except OSError as exc:
        return Check("worktree base", "fail", f"{base} is not writable: {exc}")
    shutil.rmtree(worktree, ignore_errors=True)
    source = "KIMI_WORKTREE_BASE" if configured else "system temp"
    return Check("worktree base", "ok", f"{base} (from {source}) is writable")


def _cli_checks(prefix: list[str]) -> list[Check]:
    """Version and flag-surface checks that need a resolved executable."""
    checks = [Check("agent CLI", "ok", f"found at {prefix[-1]}")]

    version = cli_version(prefix)
    checks.append(
        Check("CLI version", "ok", version)
        if version
        else Check(
            "CLI version",
            "warn",
            "could not be determined; the CLI may not be responding",
        )
    )

    help_output = help_text(prefix)
    if not help_output:
        checks.append(
            Check(
                "flag surface",
                "fail",
                "'--help' produced no output, so the required flags could not be "
                "verified. Check that the CLI runs and is authenticated.",
            )
        )
        return checks

    missing = [flag for flag in REQUIRED_FLAGS if flag not in help_output]
    present = ", ".join(REQUIRED_FLAGS)
    checks.append(
        Check("flag surface", "fail", f"missing required flags: {', '.join(missing)}")
        if missing
        else Check("flag surface", "ok", f"all required flags present: {present}")
    )

    if "--skills-dir" in help_output:
        active = "active" if _skills_isolation_enabled() else "available but disabled"
        checks.append(Check("skills isolation", "ok", f"--skills-dir {active}"))
    else:
        checks.append(
            Check(
                "skills isolation",
                "warn",
                "this CLI has no --skills-dir, so reviews inherit the host's "
                "skills and may differ between machines",
            )
        )
    return checks


def run_checks() -> list[Check]:
    """Run every diagnostic and return the results in report order."""
    checks: list[Check] = []
    prefix = _resolve_prefix()
    if prefix is None:
        checks.append(
            Check(
                "agent CLI",
                "fail",
                f"'{KIMI_EXECUTABLE}' is not on PATH. Install it with "
                "'npm i -g @moonshot-ai/kimi-code', then check 'kimi --version'.",
            )
        )
    else:
        checks.extend(_cli_checks(prefix))

    policy = read_max_policy_from_env()
    checks.append(
        Check("policy ceiling", "ok", f"KIMI_MAX_POLICY={policy.to_string()}")
    )
    checks.append(
        Check("depth guard", "ok", f"max nested spawns = {DEFAULT_MAX_DEPTH}")
    )

    limit = _max_prompt_chars()
    suffix = "" if limit == DEFAULT_MAX_PROMPT_CHARS else " (overridden)"
    checks.append(Check("prompt limit", "ok", f"{limit} characters{suffix}"))
    checks.append(_check_worktree_base())
    return checks


def has_failure(checks: list[Check]) -> bool:
    """Return True when any check failed (warnings do not count)."""
    return any(check.status == "fail" for check in checks)


def format_report(checks: list[Check]) -> str:
    """Render *checks* as a plain-text report with a one-line summary."""
    lines = [f"{_SYMBOLS[c.status]} {c.name}: {c.detail}" for c in checks]
    if has_failure(checks):
        lines.append("\nNot ready: fix the [FAIL] items above, then re-run doctor.")
    else:
        warnings = sum(1 for c in checks if c.status == "warn")
        summary = "Ready." if not warnings else f"Ready, with {warnings} warning(s)."
        lines.append(f"\n{summary}")
    return "\n".join(lines)
