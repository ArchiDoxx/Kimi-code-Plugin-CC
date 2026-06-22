"""Adapter for the Kimi Code CLI."""

from __future__ import annotations

import json
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
# Flags confirmed live (kimi --help, kimi 0.18.0):
#   -p, --prompt <prompt>            non-interactive single prompt
#   --output-format {text|stream-json}
#   -y, --yolo / --auto              OPT-IN auto-approve (never added by us)
# Non-existent flags ruled out earlier: --print, --final-message-only, --afk.
DEFAULT_TIMEOUT_SECONDS = 120
STREAM_OUTPUT_FORMAT = "stream-json"
KIMI_EXECUTABLE = "kimi"

# Returned when the CLI produces no usable text. Worded so a review loop reads
# it as "needs discussion" (fail-safe), never as an approval.
EMPTY_RESPONSE_SENTINEL = "needs_discussion: agent returned no parseable output"

# v0.5 capability posture: these auto-approve flags are NEVER injected. The
# policy is enforced structurally (no flag) plus worktree isolation plus the
# KIMI_MAX_POLICY ceiling; see ADR-002.
NEVER_FLAGS = ("--yolo", "-y", "--auto", "--afk")


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
        """
        assert_spawn_allowed(depth, DEFAULT_MAX_DEPTH)

        resolved = self._resolve_executable(command)
        cwd = self._resolve_workdir()
        result = await run_agent_process(
            resolved,
            env={DEPTH_ENV_VAR: str(depth)},
            timeout=self._timeout,
            max_depth=DEFAULT_MAX_DEPTH,
            cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Kimi CLI exited with {result.returncode}: {result.stderr.strip()}"
            )
        return result

    def _resolve_executable(self, command: list[str]) -> list[str]:
        """Resolve the first argument via PATH so Windows finds .cmd wrappers."""
        if not command:
            return command
        executable = shutil.which(command[0])
        if executable is None:
            raise FileNotFoundError(
                f"Could not find executable for {command[0]!r} in PATH"
            )
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
        """Return the requested policy capped against KIMI_MAX_POLICY."""
        requested = context.get("approval_policy", DEFAULT_POLICY)
        if requested not in ("read-only", "accept-edits", "explicit"):
            requested = DEFAULT_POLICY
        from kimi_code_plugin_cc.security.policy import resolve_effective_policy

        effective = resolve_effective_policy(requested)
        return cast(ApprovalPolicyLiteral, effective.to_string())
