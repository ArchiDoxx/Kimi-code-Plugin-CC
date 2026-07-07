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
import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH

# 600s: agentic Kimi runs (skill loads, tool calls) routinely exceed the old
# 120s deadline on real payloads. With sentinel-based early exit this is a
# pure backstop, not the expected completion path.
DEFAULT_TIMEOUT_SECONDS = 600.0
DEPTH_ENV_VAR = "KIMI_BRIDGE_DEPTH"

# Streaming mode: how long to wait after the completion sentinel for the child
# to exit on its own before killing its process tree, how often the wait loop
# wakes up, and how long to wait for reader threads to drain after the child
# is gone.
_SENTINEL_GRACE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.05
_READER_JOIN_SECONDS = 5.0

# Windows: prevent the child from inheriting the parent's console/stdio pipe
# (which is what blocks the MCP server's spawned agents) and from popping up a
# console window. No-op on POSIX.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@dataclass(frozen=True)
class RunResult:
    """Result of a spawned agent process.

    ``early_exit`` is True when the run was completed via the caller's
    completion sentinel (see ``run_agent_process(early_exit_check=...)``)
    while the child process was still alive. In that case the output is
    complete but ``returncode`` reflects a process that was reaped by the
    bridge — callers must not treat a non-zero code as failure then.
    """

    returncode: int
    stdout: str
    stderr: str
    args: list[str]
    env: dict[str, str]
    early_exit: bool = False


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


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate *proc* and all of its descendants.

    The Kimi CLI spawns MCP-server child processes from the user's global
    config. Killing only the direct child would orphan them — and on Windows
    they inherit the stdout handle, so the reader threads would never see
    EOF. ``taskkill /T`` (Windows) / ``killpg`` (POSIX, requires the child to
    be its own session leader) take the whole tree down.
    """
    if os.name == "nt":
        subprocess.run(  # noqa: S603, S607 - fixed argv, pid is an int
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    # Defensive: taskkill/killpg above is expected to reap the tree; a stuck
    # wait must not turn cleanup into a second hang.
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=_READER_JOIN_SECONDS)


def _run_subprocess_streaming(
    args: list[str],
    merged_env: dict[str, str],
    timeout: float,
    cwd: str | os.PathLike[str] | None,
    early_exit_check: Callable[[str], bool],
) -> RunResult:
    """Run *args*, returning as soon as *early_exit_check* matches a stdout line.

    Why this exists: ``kimi -p`` (with the user's global MCP servers/hooks
    configured) prints its complete answer and then never exits, so waiting
    for process exit turns every successful run into a timeout that discards
    the finished answer. Here stdout is consumed line by line in a reader
    thread; when *early_exit_check* recognises the completion event (for Kimi:
    the ``session.resume_hint`` meta line) the child gets a short grace period
    to exit on its own and is then killed — with its whole process tree, so
    spawned MCP servers do not accumulate as orphans.

    Falls back to normal semantics when the sentinel never appears: natural
    exit returns a regular result, deadline expiry raises ``TimeoutError``
    carrying the partial output.
    """
    proc = subprocess.Popen(  # noqa: S603 - argv built by callers, shell=False
        args,
        env=merged_env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=_CREATION_FLAGS,
        start_new_session=(os.name != "nt"),
    )
    stdout_lines: list[str] = []
    stderr_chunks: list[bytes] = []
    sentinel_seen = threading.Event()

    def _drain_stdout() -> None:
        stream = proc.stdout
        assert stream is not None  # PIPE above guarantees this
        for raw_line in stream:
            line = raw_line.decode("utf-8", errors="replace")
            stdout_lines.append(line)
            if not sentinel_seen.is_set() and early_exit_check(line):
                sentinel_seen.set()

    def _drain_stderr() -> None:
        stream = proc.stderr
        assert stream is not None  # PIPE above guarantees this
        stderr_chunks.append(stream.read())

    readers = [
        threading.Thread(target=_drain_stdout, daemon=True),
        threading.Thread(target=_drain_stderr, daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout
    early_exit = False
    while True:
        if sentinel_seen.is_set():
            early_exit = True
            try:
                proc.wait(timeout=_SENTINEL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc)
            break
        if proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            _kill_process_tree(proc)
            for reader in readers:
                reader.join(timeout=_READER_JOIN_SECONDS)
            partial_out = "".join(stdout_lines).strip()
            partial_err = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
            raise TimeoutError(
                f"Agent process timed out after {timeout}s without emitting a "
                f"completion event: {' '.join(args)}"
                + (f"\nPartial stderr:\n{partial_err}" if partial_err else "")
                + (f"\nPartial stdout:\n{partial_out}" if partial_out else "")
            )
        sentinel_seen.wait(_POLL_INTERVAL_SECONDS)

    for reader in readers:
        reader.join(timeout=_READER_JOIN_SECONDS)
    returncode = proc.poll()
    if returncode is None:  # pragma: no cover - kill/wait above makes this rare
        returncode = -1
    return RunResult(
        returncode=returncode,
        stdout="".join(stdout_lines),
        stderr=b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        args=args,
        env=merged_env,
        early_exit=early_exit,
    )


# Child-process environment allowlist. Only these variables are forwarded to
# the spawned agent; everything else from the host environment (including any
# tokens/secrets) is dropped. This prevents a compromised or prompt-injected
# agent from exfiltrating host secrets via `env`.
#
# - PATH / PATHEXT / SYSTEMROOT / COMSPEC / WINDIR / APPDATA: the child needs
#   these to find its own executables and the kimi CLI on Windows.
# - HOME / USERPROFILE / TMP / TEMP: standard runtime locations.
# - KIMI_* / ANTHROPIC_* / MOONSHOT_*: explicit auth passthrough so the agent
#   can authenticate (Kimi Code is Moonshot's CLI; API-key deployments use
#   MOONSHOT_API_KEY). These prefixes carry auth tokens the CLI needs.
#   (Non-secret KIMI_* config vars like KIMI_MAX_POLICY ride along — harmless
#   and non-exploitable; tightening further would complicate auth passthrough.)
# - API_KEY / OPENAI_API_KEY: common auth var names used by some CLIs.
_ALLOWED_ENV_PREFIXES = ("KIMI_", "ANTHROPIC_", "MOONSHOT_")
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
    early_exit_check: Callable[[str], bool] | None = None,
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
        early_exit_check: Optional per-line stdout predicate. When it matches,
            the run is considered complete: the child gets a short grace
            period to exit, is then killed (whole process tree), and the
            collected output is returned with ``early_exit=True``. Required
            for agents like Kimi that print their answer but never exit when
            long-lived MCP servers are configured. ``None`` keeps the plain
            wait-for-exit behaviour.
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

    if early_exit_check is None:
        return await asyncio.to_thread(
            _run_subprocess_sync, args, merged_env, timeout, cwd
        )
    return await asyncio.to_thread(
        _run_subprocess_streaming, args, merged_env, timeout, cwd, early_exit_check
    )
