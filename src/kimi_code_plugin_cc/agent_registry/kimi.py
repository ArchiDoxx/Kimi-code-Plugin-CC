"""Adapter for the Kimi Code CLI."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

from kimi_code_plugin_cc.bridge.runner import (
    DEPTH_ENV_VAR,
    RunResult,
    assert_spawn_allowed,
    run_agent_process,
)
from kimi_code_plugin_cc.protocol.messages import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_POLICY,
    AgentMessage,
    ApprovalPolicyLiteral,
)

from .base import AgentAdapter

# Pinned, verified against the installed Kimi Code CLI.
# Flags confirmed live (kimi --help, @moonshot-ai/kimi-code 0.22.2, 2026-07-07):
#   -p, --prompt <prompt>            non-interactive single prompt
#   --output-format {text|stream-json}
#   -y, --yolo / --auto              OPT-IN auto-approve (never added by us)
# Non-existent flags ruled out earlier: --print, --final-message-only, --afk.
DEFAULT_TIMEOUT_SECONDS = 600
STREAM_OUTPUT_FORMAT = "stream-json"
KIMI_EXECUTABLE = "kimi"

# Returned when the CLI produces no usable text. Worded so a review loop reads
# it as "needs discussion" (fail-safe), never as an approval.
EMPTY_RESPONSE_SENTINEL = "needs_discussion: agent returned no parseable output"

# v1.0 capability posture: these auto-approve flags are NEVER injected. The
# policy is enforced structurally (no flag) plus worktree isolation plus the
# KIMI_MAX_POLICY ceiling; see ADR-002.
NEVER_FLAGS = ("--yolo", "-y", "--auto", "--afk")

# Terminal stream-json event: kimi emits {"role":"meta","type":"session.resume_hint"}
# as the last line of a -p run. It is the completion signal the runner keys on,
# because with long-lived MCP servers in the user's global ~/.kimi-code/mcp.json
# the process prints its answer and then never exits (verified 2026-07-07, see
# memory kimi-review-timeout-non-exit).
_RESUME_HINT_TYPE = "session.resume_hint"


def is_resume_hint_event(line: str) -> bool:
    """Return True when *line* is Kimi's terminal ``session.resume_hint`` event.

    Cheap substring pre-check first, then a JSON parse to confirm the top-level
    event type — assistant content that merely *mentions* the marker arrives
    JSON-escaped inside a string and therefore never matches the parsed check.
    """
    if _RESUME_HINT_TYPE not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(obj, dict)
        and obj.get("role") == "meta"
        and obj.get("type") == _RESUME_HINT_TYPE
    )


# Regex to find a node entry-point (.mjs/.js) referenced inside a .cmd/.bat
# npm shim. npm shims look like: ... "%dp0%\node_modules\<pkg>\dist\main.mjs" %*
# We capture the path after the leading quote (optional) up to the extension.
_SHIM_ENTRY_RE = re.compile(
    r'["\']?(?P<path>(?:[A-Za-z]:[\\/]|[^"\']*?[\\/])?'
    r'node_modules[\\/][^"\']+?\.(?:mjs|js|cjs))["\']?',
    re.IGNORECASE,
)


def _deshim_cmd_wrapper(shim_path: Path) -> list[str] | None:
    """Resolve a Windows ``.cmd``/``.bat`` npm shim to a direct ``node`` argv.

    npm installs ``kimi.CMD`` (and similar) as batch shims that ultimately run
    ``node <pkg>/dist/main.mjs %*`` through ``cmd.exe``. ``cmd.exe`` truncates
    arguments at the first newline and caps lines near 8191 chars, which
    silently breaks multi-line prompts.

    This function reads the shim, locates the node entry-point (a
    ``node_modules/.../*.mjs`` path, resolved relative to the shim's directory
    if it uses ``%dp0%`` / ``%~dp0``), and returns ``["node", <abs entry>]``
    so the caller can invoke node directly via ``CreateProcess``.

    Returns ``None`` if *shim_path* is not a ``.cmd``/``.bat`` file or no
    entry-point could be resolved — callers then fall back to running the
    shim as-is (single-line behaviour stays correct).
    """
    if shim_path.suffix.lower() not in (".cmd", ".bat"):
        return None
    try:
        text = shim_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = _SHIM_ENTRY_RE.search(text)
    if match is None:
        return None

    raw_path = match.group("path")
    # Resolve npm's %dp0% / %~dp0 (the shim's own directory) if the path is
    # relative to node_modules at the shim's location.
    entry = Path(raw_path)
    if not entry.is_absolute():
        # Strip a possible leading %dp0%-style prefix or backslash.
        cleaned = re.sub(r"^[%~dpDP0\\\/]+", "", raw_path)
        entry = (shim_path.parent / cleaned).resolve()
    else:
        entry = entry.resolve()

    if not entry.exists():
        return None

    # Prefer a node.exe next to the shim (npm layout), else rely on PATH.
    local_node = shim_path.parent / "node.exe"
    node_exec = str(local_node) if local_node.exists() else "node"
    return [node_exec, str(entry)]


class KimiCodeAdapter(AgentAdapter):
    """Spawn Kimi Code per turn and translate its stream-json output.

    Execution itself (subprocess spawn, timeout, depth-guard, env injection)
    is delegated to :func:`run_agent_process` so there is a single
    process-spawning code path in the plugin. This adapter is responsible only
    for Kimi-specific concerns: command shape, PATH resolution on Windows,
    isolated working directory, stream-json parsing, and policy bookkeeping.
    """

    def __init__(
        self,
        name: str = "kimi",
        worktree: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        use_isolated_worktree: bool = True,
    ) -> None:
        self._name = name
        self._worktree = worktree
        self._timeout = timeout
        self._use_isolated_worktree = use_isolated_worktree

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict | None = None) -> AgentMessage:
        """Run ``kimi -p <prompt> --output-format stream-json`` and parse stdout."""
        ctx = context or {}
        depth = int(ctx.get("depth", 0))
        bridge_id = str(ctx.get("bridge_id", self._name))
        policy = self._resolve_policy(ctx)

        command = self._build_command(prompt)
        result = await self._execute(command, depth)
        payload = self._parse_output(result.stdout)
        if not payload.strip():
            # Fail-safe: never emit an empty payload (it would crash the
            # message model and, in a review loop, must not read as approval).
            payload = EMPTY_RESPONSE_SENTINEL

        return AgentMessage(
            bridge_id=bridge_id,
            depth=depth,
            approval_policy=policy,
            payload=payload,
        )

    def _build_command(self, prompt: str) -> list[str]:
        """Construct the Kimi Code argv list without auto-approve flags."""
        return [
            KIMI_EXECUTABLE,
            "-p",
            prompt,
            "--output-format",
            STREAM_OUTPUT_FORMAT,
        ]

    async def _execute(self, command: list[str], depth: int) -> RunResult:
        """Delegate the actual spawn to the shared async runner.

        A cheap depth pre-check runs first so a blocked spawn never creates an
        isolated worktree. The runner re-checks authoritatively against the
        inherited environment (defense in depth, identical message). The call
        is awaited (not ``asyncio.run``) so it composes inside the MCP server's
        event loop.

        The isolated worktree (when created fresh for this turn) is removed in
        a ``finally`` so long-running MCP sessions do not leak temp dirs.
        """
        assert_spawn_allowed(depth, DEFAULT_MAX_DEPTH)

        resolved = self._resolve_executable(command)
        workdir = self._resolve_workdir()
        # We own the worktree only if we created it fresh this turn (no explicit
        # worktree was supplied). Explicit worktrees are caller-managed.
        own_workdir = (
            workdir
            if (self._use_isolated_worktree and self._worktree is None)
            else None
        )
        try:
            result = await run_agent_process(
                resolved,
                env={DEPTH_ENV_VAR: str(depth)},
                timeout=self._timeout,
                max_depth=DEFAULT_MAX_DEPTH,
                cwd=workdir,
                early_exit_check=is_resume_hint_event,
            )
        finally:
            if own_workdir is not None:
                shutil.rmtree(own_workdir, ignore_errors=True)
        # early_exit means the run completed via the resume-hint sentinel and
        # the child was reaped by the bridge; its exit code is meaningless.
        if result.returncode != 0 and not result.early_exit:
            raise RuntimeError(
                f"Kimi CLI exited with {result.returncode}: {result.stderr.strip()}"
            )
        return result

    def _resolve_executable(self, command: list[str]) -> list[str]:
        """Resolve the first argument, de-shimming Windows ``.cmd``/``.bat`` wrappers.

        ``shutil.which("kimi")`` returns ``kimi.CMD`` on Windows — an npm
        batch shim that ``cmd.exe`` executes. ``cmd.exe`` truncates every
        argument at the first ``\\n`` and caps lines near 8191 chars, which
        silently destroys multi-line prompts (every review/plan brief). The
        shim ultimately runs ``node <pkg>/dist/main.mjs %*``.

        To preserve newlines (and long prompts), we parse the shim, locate its
        node entry-point (a ``.mjs``/``.js`` path relative to the shim dir),
        and invoke ``node <entry> ...`` directly via ``CreateProcess`` (which
        handles ``\\n`` and ~32767-char args correctly). If anything fails, we
        fall back to the original resolved path so single-line behaviour keeps
        working.
        """
        if not command:
            return command
        executable = shutil.which(command[0])
        if executable is None:
            raise FileNotFoundError(
                f"Could not find executable for {command[0]!r} in PATH"
            )
        deshimed = _deshim_cmd_wrapper(Path(executable))
        if deshimed is not None:
            return [*deshimed, *command[1:]]
        return [executable, *command[1:]]

    def _resolve_workdir(self) -> Path | None:
        """Return the working directory for the agent subprocess.

        If an explicit worktree was supplied, use it. Otherwise, when
        ``use_isolated_worktree`` is enabled, create a fresh isolated directory
        so the agent never implicitly writes into the host repository.
        """
        if self._worktree is not None:
            return self._worktree
        if self._use_isolated_worktree:
            from kimi_code_plugin_cc.security.policy import create_isolated_worktree

            return create_isolated_worktree()
        return None

    def _parse_output(self, raw_stream: str) -> str:
        """Parse newline-delimited stream-json into a single payload string.

        Kimi normally emits one ``{"role":"assistant","content":...}`` event,
        but for some prompts (especially when run in an empty worktree) it
        falls back to plain prose on stdout instead of JSON. To stay robust we
        first collect assistant JSON content; if none is found we treat any
        non-JSON, non-meta text as the reply (plaintext fallback). The caller
        applies a fail-safe sentinel if even that is empty.
        """
        contents: list[str] = []
        plain_lines: list[str] = []
        for line in raw_stream.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                plain_lines.append(line)
                continue
            text = self._extract_text(obj)
            if text:
                contents.append(text)
        if contents:
            return "\n".join(contents)
        return "\n".join(plain_lines).strip()

    def _extract_text(self, obj: Any) -> str | None:
        """Extract a text payload from an assistant stream-json event.

        Live format (kimi 0.18.0) emits one ``{"role":"assistant","content":...}``
        event carrying the full reply, followed by a ``{"role":"meta",...}``
        session-resume hint. We keep only assistant content and ignore meta and
        control events (session hints, usage, etc.).
        """
        if not isinstance(obj, dict):
            return None
        if obj.get("role") not in ("assistant", None):
            return None
        for key in ("content", "text", "output", "message"):
            value = obj.get(key)
            if isinstance(value, str):
                return value
        return None

    def _resolve_policy(self, context: dict) -> ApprovalPolicyLiteral:
        """Return the effective policy, enforcing the read-only contract.

        The policy is capped against ``KIMI_MAX_POLICY``. In v1.0 the plugin
        enforces **only** ``read-only`` at the CLI boundary: kimi has no
        verified programmatic read-only/edit-accept flag in the pinned CLI
        version, so a policy above ``read-only`` would be *recorded* as granted
        but never *enacted* — a correctness and honesty defect. We therefore
        refuse any effective policy above ``read-only`` with a clear error
        instead of silently pretending it was honored. Worktree isolation
        remains the backstop for filesystem writes.
        """
        requested = context.get("approval_policy", DEFAULT_POLICY)
        if requested not in ("read-only", "accept-edits", "explicit"):
            requested = DEFAULT_POLICY
        from kimi_code_plugin_cc.security.policy import resolve_effective_policy

        effective = resolve_effective_policy(requested)
        effective_str = cast(ApprovalPolicyLiteral, effective.to_string())
        if effective_str != "read-only":
            raise PermissionError(
                "Policy escalation to "
                f"{effective_str!r} is not supported in v1.0: kimi has no "
                "verified CLI flag for non-read-only execution, so the policy "
                "would be recorded but not enacted. Use 'read-only' (the only "
                "enforced policy) and handle edits out-of-band."
            )
        return effective_str
