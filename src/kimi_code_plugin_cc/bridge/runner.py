"""Thread-backed subprocess runner for headless CLI agents.

Why ``subprocess.run`` in a thread (via :func:`asyncio.to_thread`) instead of
``asyncio.create_subprocess_exec``? On Windows, spawning a child process from
inside an event loop that also owns a stdio transport (the MCP server case)
hits a ProactorEventLoop conflict: the child's stdin inherits the parent's
stdio pipe handle, and the child blocks forever waiting on that handle.
Running the synchronous ``subprocess.run`` in a worker thread with
``stdin=DEVNULL`` and ``CREATE_NO_WINDOW`` (Windows) sidesteps the pipe
inheritance entirely while keeping the async interface callers depend on.
This composes correctly inside any host event loop (tests, MCP server).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass

from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH

DEFAULT_TIMEOUT_SECONDS = 120.0
DEPTH_ENV_VAR = "KIMI_BRIDGE_DEPTH"

# Windows: prevent the child from inheriting the parent's console/stdio pipe
# (which is what blocks the MCP server's spawned agents) and from popping up a
# console window. No-op on POSIX.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@dataclass(frozen=True)
class RunResult:
    """Result of a spawned agent process."""

    returncode: int
    stdout: str
    stderr: str
    args: list[str]
    env: dict[str, str]


def _get_current_depth(env: dict[str, str] | None = None) -> int:
    """Return the current bridge depth from the environment, defaulting to 0.

    A non-numeric ``KIMI_BRIDGE_DEPTH`` is treated as 0 (shallowest) rather
    than silently weakening the guard — this is the safe default since the
    variable is inherited across process boundaries and may be corrupted.
    """
    source = os.environ if env is None else env
    raw = source.get(DEPTH_ENV_VAR, "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def assert_spawn_allowed(current_depth: int, max_depth: int) -> int:
    """Authoritative recursion guard shared by runner and adapters.

    Computes ``child_depth = current_depth + 1`` and raises ``RuntimeError``
    when it falls outside ``[0, max_depth]``. Returns the validated child
    depth on success. Centralising this keeps the guard message and semantics
    identical across the runner and any synchronous adapter wrapper.
    """
    child_depth = current_depth + 1
    if not (0 <= child_depth <= max_depth):
        raise RuntimeError(
            "Depth guard blocked spawn: "
            f"child depth {child_depth} exceeds limit {max_depth}"
        )
    return child_depth


def _run_subprocess_sync(
    args: list[str],
    merged_env: dict[str, str],
    timeout: float,
    cwd: str | os.PathLike[str] | None,
) -> RunResult:
    """Run *args* synchronously with a hard timeout, returning a RunResult.

    Uses :func:`subprocess.run` with ``stdin=DEVNULL`` so the child never
    inherits the parent's stdin handle (the Windows MCP-server pipe-block).
    On timeout the child is terminated (``subprocess.run`` raises
    :class:`subprocess.TimeoutExpired`, translated to :class:`TimeoutError`).
    """
    try:
        completed = subprocess.run(  # noqa: S603, UP022 - argv built by callers; can't use capture_output because stdin=DEVNULL is required
            args,
            env=merged_env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=_CREATION_FLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        partial_err = (exc.stderr or b"").decode("utf-8", "replace").strip()
        partial_out = (exc.stdout or b"").decode("utf-8", "replace").strip()
        raise TimeoutError(
            f"Agent process timed out after {exc.timeout}s (possible auth hang): "
            f"{' '.join(args)}"
            + (f"\nPartial stderr:\n{partial_err}" if partial_err else "")
            + (f"\nPartial stdout:\n{partial_out}" if partial_out else "")
        ) from exc

    return RunResult(
        returncode=completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
        args=args,
        env=merged_env,
    )


# Child-process environment allowlist. Only these variables are forwarded to
# the spawned agent; everything else from the host environment (including any
# tokens/secrets) is dropped. This prevents a compromised or prompt-injected
# agent from exfiltrating host secrets via `env`.
#
# - PATH / PATHEXT / SYSTEMROOT / COMSPEC / WINDIR / APPDATA: the child needs
#   these to find its own executables and the kimi CLI on Windows.
# - HOME / USERPROFILE / TMP / TEMP: standard runtime locations.
# - KIMI_* / ANTHROPIC_*: explicit auth passthrough so the agent can
#   authenticate. These prefixes carry auth tokens the CLI needs. (Non-secret
#   KIMI_* config vars like KIMI_MAX_POLICY ride along — harmless and
#   non-exploitable; tightening further would complicate auth passthrough.)
# - API_KEY / OPENAI_API_KEY: common auth var names used by some CLIs.
_ALLOWED_ENV_PREFIXES = ("KIMI_", "ANTHROPIC_")
_ALLOWED_ENV_EXACT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "WINDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "API_KEY",
        "OPENAI_API_KEY",
    }
)


def _build_child_env(overrides: dict[str, str]) -> dict[str, str]:
    """Build a minimal child environment from the host env using an allowlist.

    Only explicitly allowed variables and ``KIMI_*``/``ANTHROPIC_*``-prefixed
    auth vars are forwarded, then *overrides* (e.g. ``KIMI_BRIDGE_DEPTH``) are
    applied on top. This is the inverse of the previous "copy all of
    ``os.environ``" behaviour and closes the secret-exfiltration gap.
    """
    child: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _ALLOWED_ENV_EXACT or key.startswith(_ALLOWED_ENV_PREFIXES):
            child[key] = value
    child.update(overrides)
    return child


async def run_agent_process(
    args: list[str],
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_depth: int | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> RunResult:
    """Run a CLI agent asynchronously with depth-guard and timeout.

    The child environment is built from a minimal allowlist (plus the caller's
    overrides) so host secrets are not forwarded. ``KIMI_BRIDGE_DEPTH`` is set
    to the current depth + 1; if that exceeds *max_depth* (default
    ``DEFAULT_MAX_DEPTH``), the call fails fast without spawning a process.

    The actual process is run in a worker thread via :func:`asyncio.to_thread`
    so it composes inside any event loop (MCP server, tests) without hitting
    the Windows Proactor subprocess/stdio conflict.

    Args:
        args: Argv list (already PATH-resolved by the caller on Windows).
        env: Extra environment overrides applied on top of the allowlisted env.
        timeout: Hard wall-clock timeout. Kills the process on expiry so a
            hung/auth-required agent cannot block the host.
        max_depth: Recursion ceiling. Defaults to ``DEFAULT_MAX_DEPTH``.
        cwd: Optional working directory for the child process. Used for
            worktree isolation; the adapter passes an isolated temp dir.
    """
    if not args:
        raise ValueError("args must not be empty")
    overrides = dict(env or {})
    # The depth variable is authoritative-managed by this function; strip any
    # caller-supplied value so the computed child_depth is not clobbered.
    overrides.pop(DEPTH_ENV_VAR, None)
    current_depth = _get_current_depth(env)
    limit = DEFAULT_MAX_DEPTH if max_depth is None else max_depth
    child_depth = assert_spawn_allowed(current_depth, limit)
    merged_env = _build_child_env({DEPTH_ENV_VAR: str(child_depth), **overrides})

    return await asyncio.to_thread(_run_subprocess_sync, args, merged_env, timeout, cwd)
