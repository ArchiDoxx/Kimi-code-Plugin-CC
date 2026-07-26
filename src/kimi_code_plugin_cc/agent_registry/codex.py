"""Adapter for the OpenAI Codex CLI (``codex exec``).

Second real agent behind the bridge, and the proof that the ``AgentAdapter``
abstraction generalises: registry, shared runner, policy cap, error contract
and capability probing are all reused unchanged. Two things differ from kimi:

**Completion.** ``codex exec`` is batch — it prints, writes its final message,
and exits on its own, so there is no completion sentinel and no process tree to
reap (kimi needs both, because long-lived MCP servers keep it alive after it
has answered). The runner's timeout is a pure backstop.

**Payload.** The answer comes from the ``-o/--output-last-message`` file, not
from stdout. The ``--json`` event stream is diagnostics only; parsing it is
deliberately not load-bearing, so an upstream schema change cannot silently
corrupt a review verdict. A missing or empty file is a failure even on exit 0 —
an empty answer must never read as an approval.

Flag surface verified live against ``codex-cli 0.145.0`` (``codex exec
--help``, 2026-07-26). Everything beyond the base surface is capability-gated;
``tests/test_cli_contract.py`` pins the base surface against the real CLI.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import cast

from kimi_code_plugin_cc.agent_registry.capabilities import supports_flag
from kimi_code_plugin_cc.agent_registry.codex_contract import (
    ALLOWED_SANDBOX_MODES,
    ARGS_SEPARATOR,
    CODEX_EXECUTABLE,
    ENV_ISOLATE_SESSION,
    EXEC_SUBCOMMAND,
    INSTALL_HINT,
    JSON_FLAG,
    LAST_MESSAGE_FILENAME,
    MODEL_FLAG,
    OUTPUT_FLAG,
    POLICY_TO_SANDBOX,
    SANDBOX_FLAG,
    SESSION_ISOLATION_FLAGS,
    SKIP_GIT_REPO_CHECK_FLAG,
)
from kimi_code_plugin_cc.agent_registry.common import (
    assert_prompt_fits,
    env_flag_enabled,
    resolve_executable,
    validate_model_alias,
)
from kimi_code_plugin_cc.bridge.runner import (
    DEPTH_ENV_VAR,
    AuthEnv,
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
from kimi_code_plugin_cc.security.policy import (
    create_isolated_worktree,
    resolve_effective_policy,
)

from .base import AgentAdapter

DEFAULT_TIMEOUT_SECONDS = 600
_OUTBOX_PREFIX = "kimi_codex_out_"

# Host credentials this agent may receive. OPENAI_* carries the API key and any
# base-URL override; CODEX_HOME points at auth.json and the session store, and
# is named exactly rather than as a CODEX_* prefix so future variables are not
# forwarded blindly. No Moonshot/Anthropic variables: codex has no use for
# them, and a review is exactly the situation where a prompt-injected agent
# would be asked to read its own environment back to us.
AUTH_ENV = AuthEnv(prefixes=("OPENAI_",), exact=frozenset({"CODEX_HOME"}))

# How much stdout to quote when a run fails without writing to stderr.
_MAX_CONTEXT_CHARS = 2000


def _failure_context(result: RunResult) -> str:
    """Return the most useful diagnostic text from a failed run."""
    stderr = result.stderr.strip()
    if stderr:
        return stderr
    stdout = result.stdout.strip()
    if stdout:
        return f"(no stderr) last stdout: {stdout[-_MAX_CONTEXT_CHARS:]}"
    return "(no output on either stream)"


def session_isolation_enabled() -> bool:
    """Return True unless ``KIMI_CODEX_ISOLATE_SESSION`` is explicitly falsey.

    Module-level so ``doctor`` can report the configured setting without
    constructing an adapter; a per-instance override still wins over it.
    """
    return env_flag_enabled(ENV_ISOLATE_SESSION)


def _assert_write_sandbox_is_contained(sandbox: str, workdir: Path | None) -> None:
    """Refuse a writable sandbox that has no working directory to contain it.

    ``--sandbox workspace-write`` lets the agent write its *workspace*, and the
    workspace is the process's working directory. With ``cwd=None`` that is the
    host process's current directory — typically the repository under review —
    so a prompt-injected agent could edit the very code being reviewed.

    The policy cap alone does not prevent this: ``KIMI_MAX_POLICY=accept-edits``
    plus ``CodexAdapter(use_isolated_worktree=False)`` is a legitimate-looking
    combination that reaches it. Containment is therefore enforced here rather
    than merely asserted in a comment. Neither ingredient is reachable from
    per-call context, so this guards an embedding mistake, not a hostile prompt.
    """
    if sandbox == "workspace-write" and workdir is None:
        raise PermissionError(
            "Refusing to run codex with --sandbox workspace-write and no "
            "working directory: the agent's writable workspace would be the "
            "host process's current directory. Enable worktree isolation "
            "(the default) or pass an explicit 'worktree' to contain the "
            "writes, or keep the policy at 'read-only'."
        )


def read_final_message(path: Path, result: RunResult) -> str:
    """Return the agent's final message, or raise a classified failure.

    Fail-closed in three directions, each of which must resolve to a non-
    approval rather than to empty-but-successful output:

    * non-zero exit — reported with whatever diagnostic the CLI produced;
    * no last-message file — a clean exit that answered nothing;
    * a blank last-message file — same, but the CLI created the file.

    ``RuntimeError`` is what :mod:`kimi_code_plugin_cc.errors` maps to
    ``agent_failed``, which every loop already treats as a non-approval.
    """
    if result.returncode != 0:
        raise RuntimeError(
            f"Codex CLI exited with {result.returncode}: {_failure_context(result)}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError, so it must be
        # named or it escapes as 'invalid_input' — blaming the caller for
        # output the agent produced. Decoding stays strict: errors="replace"
        # would turn undecodable bytes into a non-empty payload that a review
        # loop might read as a real answer.
        raise RuntimeError(
            f"Codex CLI exited cleanly but wrote no final message to {path.name} "
            f"(missing or undecodable): {_failure_context(result)}"
        ) from exc
    if not text.strip():
        raise RuntimeError(
            "Codex CLI wrote an empty final message; treating the run as failed "
            f"rather than as an empty answer: {_failure_context(result)}"
        )
    return text.strip()


class CodexAdapter(AgentAdapter):
    """Spawn ``codex exec`` per turn and return its final message.

    Execution itself (subprocess spawn, timeout, depth guard, env allowlist) is
    delegated to :func:`run_agent_process`, so there is exactly one
    process-spawning code path in the plugin. This adapter owns only the
    codex-specific concerns: command shape, policy-to-sandbox mapping,
    capability gating, and reading the last-message file.

    Concurrency: by default every :meth:`run` gets its own worktree and output
    directory, so calls are independent. The two constructor opt-outs trade
    that away — ``worktree=<path>`` makes overlapping runs share one working
    directory, and ``use_isolated_worktree=False`` runs codex in the host's
    current directory. Neither is reachable from per-call context, so an
    untrusted prompt cannot select them.
    """

    def __init__(
        self,
        name: str = "codex",
        worktree: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        use_isolated_worktree: bool = True,
        model: str | None = None,
        isolate_session: bool | None = None,
    ) -> None:
        self._name = name
        self._worktree = worktree
        self._timeout = timeout
        self._use_isolated_worktree = use_isolated_worktree
        # Default model for every run; a per-call ``model`` context key wins.
        # None lets codex use its own configured default.
        self._model = model
        # None defers to KIMI_CODEX_ISOLATE_SESSION (default on); an explicit
        # bool wins, so an embedding caller can opt out without touching env.
        self._isolate_session = isolate_session

    @property
    def name(self) -> str:
        return self._name

    @property
    def _owns_worktree(self) -> bool:
        """True when each run creates (and must clean up) its own worktree."""
        return self._use_isolated_worktree and self._worktree is None

    async def run(self, prompt: str, context: dict | None = None) -> AgentMessage:
        """Run ``codex exec`` and return the message it wrote to ``-o``."""
        ctx = context or {}
        depth = int(ctx.get("depth", 0))
        bridge_id = str(ctx.get("bridge_id", self._name))
        policy = self._resolve_policy(ctx)
        model = ctx.get("model", self._model)
        if model is not None:
            model = validate_model_alias(model)
        assert_prompt_fits(prompt)

        payload = await self._execute(prompt, POLICY_TO_SANDBOX[policy], model, depth)
        return AgentMessage(
            bridge_id=bridge_id, depth=depth, approval_policy=policy, payload=payload
        )

    def _resolve_policy(self, context: dict) -> ApprovalPolicyLiteral:
        """Return the effective policy, or refuse if it cannot be enacted.

        The requested policy is capped against ``KIMI_MAX_POLICY`` by the
        shared resolver, which also rejects names outside the plugin's
        vocabulary — that is what makes codex's ``danger-full-access``
        unreachable: it is not a plugin policy, so no caller string maps to it.

        Unlike the kimi adapter this does *not* normalise an unknown policy to
        read-only. Here the policy is enacted as a real sandbox mode, so
        silently reinterpreting the caller's intent would hide a bug; refusing
        is equally fail-closed and far easier to diagnose.
        """
        requested = context.get("approval_policy", DEFAULT_POLICY)
        if not isinstance(requested, str):
            raise ValueError(
                f"approval_policy must be a string, got {type(requested).__name__}"
            )
        effective = resolve_effective_policy(requested)
        effective_str = cast(ApprovalPolicyLiteral, effective.to_string())
        if effective_str not in POLICY_TO_SANDBOX:
            raise PermissionError(
                f"Policy {effective_str!r} cannot be enacted by codex: "
                "'codex exec' is non-interactive, so there is no per-action "
                "approval step to honour. Use 'read-only', or 'accept-edits' "
                "with KIMI_MAX_POLICY raised accordingly."
            )
        return effective_str

    async def _execute(
        self, prompt: str, sandbox: str, model: str | None, depth: int
    ) -> str:
        """Build the command, run it, and extract the final message.

        A cheap depth pre-check runs first so a blocked spawn never creates
        temp directories; the runner re-checks authoritatively against the
        inherited environment. Both temp directories are removed in a
        ``finally`` so long-running MCP sessions cannot leak them.
        """
        assert_spawn_allowed(depth, DEFAULT_MAX_DEPTH)

        prefix = resolve_executable(CODEX_EXECUTABLE, INSTALL_HINT)
        # Both directories are created inside the try with their handles
        # pre-bound to None, so a failure creating the second still lets the
        # finally remove the first. Creating either before the try leaks it.
        workdir: Path | None = None
        outbox: Path | None = None
        try:
            outbox = Path(tempfile.mkdtemp(prefix=_OUTBOX_PREFIX))
            workdir = self._resolve_workdir()
            _assert_write_sandbox_is_contained(sandbox, workdir)
            last_message = outbox / LAST_MESSAGE_FILENAME
            command = await self._build_command(
                prefix, prompt, sandbox, model, last_message
            )
            # No early_exit_check: codex exec exits on its own, so the runner's
            # plain wait-for-exit path is correct and the timeout stays a
            # backstop.
            result = await run_agent_process(
                command,
                env={DEPTH_ENV_VAR: str(depth)},
                timeout=self._timeout,
                max_depth=DEFAULT_MAX_DEPTH,
                cwd=workdir,
                auth_env=AUTH_ENV,
            )
            return read_final_message(last_message, result)
        finally:
            if outbox is not None:
                shutil.rmtree(outbox, ignore_errors=True)
            # Only a worktree we created this turn is ours to remove; an
            # explicitly supplied one is caller-managed.
            if workdir is not None and self._owns_worktree:
                shutil.rmtree(workdir, ignore_errors=True)

    def _build_base_command(
        self, prefix: list[str], sandbox: str, last_message: Path
    ) -> list[str]:
        """Return the pinned base argv, refusing any off-list sandbox mode.

        The allowlist check is defense in depth: :meth:`_resolve_policy`
        already makes a dangerous mode unreachable, but a future edit to
        :data:`POLICY_TO_SANDBOX` must fail loudly here rather than widen the
        sandbox silently.
        """
        if sandbox not in ALLOWED_SANDBOX_MODES:
            raise ValueError(
                f"Refusing to build a codex command with sandbox mode "
                f"{sandbox!r}: only {sorted(ALLOWED_SANDBOX_MODES)} are "
                "reachable through the plugin's policy model."
            )
        return [
            *prefix,
            EXEC_SUBCOMMAND,
            SANDBOX_FLAG,
            sandbox,
            JSON_FLAG,
            OUTPUT_FLAG,
            str(last_message),
        ]

    async def _build_command(
        self,
        prefix: list[str],
        prompt: str,
        sandbox: str,
        model: str | None,
        last_message: Path,
    ) -> list[str]:
        """Assemble the full argv: base flags, gated flags, model, prompt.

        The prompt is the final element and is preceded by ``--``, so its
        content is always a positional value and can never be re-read as a
        flag. (codex can also take the prompt on stdin, which would sidestep
        the argv length cap — not usable here, because the runner pins
        ``stdin=DEVNULL`` to avoid the Windows Proactor pipe block.)
        """
        command = self._build_base_command(prefix, sandbox, last_message)
        command += await self._isolation_flags(prefix)
        if model is not None:
            command += [MODEL_FLAG, model]
        command += [ARGS_SEPARATOR, prompt]
        return command

    async def _isolation_flags(self, prefix: list[str]) -> list[str]:
        """Return the supported subset of the optional flags.

        Fail-safe: a flag the installed CLI does not advertise is omitted,
        because passing an unknown flag would make every call fail. The probe
        targets ``codex exec --help`` — these flags live on the subcommand, not
        on the top-level command. The three lookups below cost at most one
        subprocess in total: ``capabilities.help_text`` caches the output per
        argv prefix for the life of the process.
        """
        probe_prefix = [*prefix, EXEC_SUBCOMMAND]
        flags: list[str] = []
        if await self._supports(probe_prefix, SKIP_GIT_REPO_CHECK_FLAG):
            flags.append(SKIP_GIT_REPO_CHECK_FLAG)
        if not self._session_isolation_enabled():
            return flags
        for flag in SESSION_ISOLATION_FLAGS:
            if await self._supports(probe_prefix, flag):
                flags.append(flag)
        return flags

    async def _supports(self, probe_prefix: list[str], flag: str) -> bool:
        """Probe the installed CLI off the event loop (the probe is blocking)."""
        return await asyncio.to_thread(supports_flag, probe_prefix, flag)

    def _session_isolation_enabled(self) -> bool:
        """Return whether ``--ephemeral``/``--ignore-user-config`` are wanted."""
        if self._isolate_session is not None:
            return self._isolate_session
        return session_isolation_enabled()

    def _resolve_workdir(self) -> Path | None:
        """Return the working directory for the agent subprocess.

        An explicit worktree wins. Otherwise, when isolation is enabled, a
        fresh directory is created so the agent never implicitly reads or
        writes the host repository.
        """
        if self._worktree is not None:
            return self._worktree
        if self._use_isolated_worktree:
            return create_isolated_worktree()
        return None
