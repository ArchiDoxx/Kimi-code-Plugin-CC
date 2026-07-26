"""Adapter for the Kimi Code CLI."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, cast

from kimi_code_plugin_cc.agent_registry.capabilities import supports_flag
from kimi_code_plugin_cc.agent_registry.common import (
    assert_prompt_fits,
    env_flag_enabled,
    resolve_executable,
    validate_model_alias,
)
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
# Flags confirmed live (kimi --help, @moonshot-ai/kimi-code 0.29.1, 2026-07-25;
# unchanged since 0.22.2). Runtime detection for anything beyond this set lives
# in capabilities.py — do not add a flag here just because your CLI has it.
#   -p, --prompt <prompt>            non-interactive single prompt
#   --output-format {text|stream-json}
#   -y, --yolo / --auto              OPT-IN auto-approve (never added by us)
# Non-existent flags ruled out earlier: --print, --final-message-only, --afk.
DEFAULT_TIMEOUT_SECONDS = 600
STREAM_OUTPUT_FORMAT = "stream-json"
KIMI_EXECUTABLE = "kimi"

# Install channel, reported when the CLI is not on PATH.
INSTALL_HINT = (
    "Install it with 'npm i -g @moonshot-ai/kimi-code', then run 'kimi --version'."
)

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

# Skills isolation. Without it the spawned agent auto-discovers the user's
# global and project skill directories, so the same review can produce
# different output on two machines. `--skills-dir <empty dir>` replaces
# discovery with a directory we control, which makes runs reproducible.
# The flag does not exist in every CLI version, so it is only added when the
# installed CLI advertises it (see capabilities.supports_flag). Set
# KIMI_ISOLATE_SKILLS=0 to keep the host's skills.
_ENV_ISOLATE_SKILLS = "KIMI_ISOLATE_SKILLS"
_SKILLS_DIR_FLAG = "--skills-dir"
_EMPTY_SKILLS_DIRNAME = ".kimi-no-skills"


def _skills_isolation_enabled() -> bool:
    """Return True unless ``KIMI_ISOLATE_SKILLS`` is explicitly falsey."""
    return env_flag_enabled(_ENV_ISOLATE_SKILLS)


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
        model: str | None = None,
        isolate_skills: bool | None = None,
    ) -> None:
        self._name = name
        self._worktree = worktree
        self._timeout = timeout
        self._use_isolated_worktree = use_isolated_worktree
        # Default model alias for every run; a per-call ``model`` context key
        # overrides it. None lets kimi use default_model from config.toml.
        self._model = model
        # None defers to KIMI_ISOLATE_SKILLS (default on); an explicit bool
        # wins, so an embedding caller can opt out without touching the env.
        self._isolate_skills = isolate_skills

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict | None = None) -> AgentMessage:
        """Run ``kimi -p <prompt> --output-format stream-json`` and parse stdout."""
        ctx = context or {}
        depth = int(ctx.get("depth", 0))
        bridge_id = str(ctx.get("bridge_id", self._name))
        policy = self._resolve_policy(ctx)
        model = ctx.get("model", self._model)
        if model is not None:
            model = validate_model_alias(model)
        assert_prompt_fits(prompt)

        command = self._build_command(prompt, model)
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

    def _build_command(self, prompt: str, model: str | None = None) -> list[str]:
        """Construct the Kimi Code argv list without auto-approve flags.

        ``model`` must already be validated by :func:`~.common.validate_model_alias`;
        when set it is passed as ``-m <alias>`` so multi-provider setups can
        route a run to a specific provider/model from config.toml.
        """
        command = [
            KIMI_EXECUTABLE,
            "-p",
            prompt,
            "--output-format",
            STREAM_OUTPUT_FORMAT,
        ]
        if model is not None:
            command += ["-m", model]
        return command

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
        # The executable prefix is whatever _resolve_executable prepended: one
        # element for a direct binary, two for a de-shimmed ``node <entry>``.
        prefix = resolved[: len(resolved) - (len(command) - 1)]
        resolved = await self._with_skills_isolation(resolved, prefix, workdir)
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

    async def _with_skills_isolation(
        self,
        argv: list[str],
        prefix: list[str],
        workdir: Path | None,
    ) -> list[str]:
        """Append ``--skills-dir <empty dir>`` when it is safe and wanted.

        Returns *argv* unchanged when isolation is disabled, when there is no
        working directory to host the empty directory, or when the installed
        CLI does not advertise the flag — passing an unknown flag would make
        every call fail, so the fail-safe is the older command shape.

        The empty directory lives inside the run's working directory, so the
        existing cleanup removes it and no temp dirs leak.
        """
        enabled = (
            _skills_isolation_enabled()
            if self._isolate_skills is None
            else self._isolate_skills
        )
        if not enabled or workdir is None:
            return argv
        if not await asyncio.to_thread(supports_flag, prefix, _SKILLS_DIR_FLAG):
            return argv
        skills_dir = Path(workdir) / _EMPTY_SKILLS_DIRNAME
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Cannot create the sandbox: fall back rather than fail the run.
            return argv
        return [*argv, _SKILLS_DIR_FLAG, str(skills_dir)]

    def _resolve_executable(self, command: list[str]) -> list[str]:
        """Resolve the first argument, de-shimming Windows ``.cmd``/``.bat``.

        ``shutil.which("kimi")`` returns ``kimi.CMD`` on Windows — an npm batch
        shim that ``cmd.exe`` executes, truncating every argument at the first
        ``\\n``. See :func:`~.common.deshim_cmd_wrapper` for the full rationale
        and the fallback behaviour.
        """
        if not command:
            return command
        prefix = resolve_executable(command[0], INSTALL_HINT)
        return [*prefix, *command[1:]]

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

        Live format (confirmed on 0.18.0 through 0.29.1) emits one
        ``{"role":"assistant","content":...}``
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
