"""Adapter for the Kimi Code CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from kimi_code_plugin_cc.protocol.messages import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_POLICY,
    AgentMessage,
    ApprovalPolicyLiteral,
)
from kimi_code_plugin_cc.security.policy import resolve_effective_policy

from .base import AgentAdapter

DEFAULT_TIMEOUT_SECONDS = 120
STREAM_OUTPUT_FORMAT = "stream-json"


class KimiCodeAdapter(AgentAdapter):
    """Spawn Kimi Code per turn and translate its stream-json output."""

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

    def run(self, prompt: str, context: dict | None = None) -> AgentMessage:
        """Run ``kimi -p <prompt> --output-format stream-json`` and parse stdout."""
        ctx = context or {}
        depth = int(ctx.get("depth", 0))
        bridge_id = str(ctx.get("bridge_id", self._name))
        policy = self._resolve_policy(ctx)

        command = self._build_command(prompt)
        result = self._execute(command, depth)
        payload = self._parse_output(result.stdout)

        return AgentMessage(
            bridge_id=bridge_id,
            depth=depth,
            approval_policy=policy,
            payload=payload,
        )

    def _build_command(self, prompt: str) -> list[str]:
        """Construct the Kimi Code argv list without auto-approve flags."""
        return [
            "kimi",
            "-p",
            prompt,
            "--output-format",
            STREAM_OUTPUT_FORMAT,
        ]

    def _execute(self, command: list[str], depth: int) -> subprocess.CompletedProcess:
        """Run the subprocess with the inherited environment plus depth marker."""
        child_depth = depth + 1
        if child_depth > DEFAULT_MAX_DEPTH:
            raise RuntimeError(
                f"Depth guard blocked spawn: child depth {child_depth} "
                f"exceeds limit {DEFAULT_MAX_DEPTH}"
            )

        env = os.environ.copy()
        env["KIMI_BRIDGE_DEPTH"] = str(child_depth)

        resolved_command = self._resolve_executable(command)

        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "env": env,
            "timeout": self._timeout,
        }
        cwd = self._resolve_workdir()
        if cwd is not None:
            kwargs["cwd"] = str(cwd)

        result = subprocess.run(resolved_command, **kwargs)
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

            return create_isolated_worktree(".")
        return None

    def _parse_output(self, raw_stream: str) -> str:
        """Parse newline-delimited stream-json into a single payload string."""
        contents: list[str] = []
        for line in raw_stream.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = self._extract_text(obj)
            if text:
                contents.append(text)
        return "\n".join(contents)

    def _extract_text(self, obj: Any) -> str | None:
        """Extract a text payload from an assistant stream-json event."""
        if not isinstance(obj, dict):
            return None
        # Ignore meta/control events (session hints, usage, etc.).
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
        effective = resolve_effective_policy(requested)
        return effective.to_string()  # type: ignore[return-value]
