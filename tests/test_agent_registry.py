"""Tests for the agent registry and adapters."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from kimi_code_plugin_cc.agent_registry import (
    KimiCodeAdapter,
    get,
    list_adapters,
    register,
)
from kimi_code_plugin_cc.agent_registry.base import AgentAdapter
from kimi_code_plugin_cc.agent_registry.codex import CodexAdapter
from kimi_code_plugin_cc.agent_registry.codex_contract import (
    BANNED_SANDBOX_MODE,
    ENV_ISOLATE_SESSION,
    LAST_MESSAGE_FILENAME,
    NEVER_FLAGS,
    OUTPUT_FLAG,
)
from kimi_code_plugin_cc.agent_registry.common import DEFAULT_MAX_PROMPT_CHARS
from kimi_code_plugin_cc.agent_registry.kimi import is_resume_hint_event
from kimi_code_plugin_cc.bridge.runner import RunResult
from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH, AgentMessage

KIMI_MODULE = "kimi_code_plugin_cc.agent_registry.kimi"
CODEX_MODULE = "kimi_code_plugin_cc.agent_registry.codex"


def _run_result(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    early_exit: bool = False,
) -> RunResult:
    return RunResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        args=[],
        env={},
        early_exit=early_exit,
    )


class TestRegistry:
    def test_default_registry_has_kimi_and_codex(self) -> None:
        assert "kimi" in list_adapters()
        assert "codex" in list_adapters()

    def test_get_existing(self) -> None:
        adapter = get("kimi")
        assert isinstance(adapter, KimiCodeAdapter)

    def test_get_missing_raises(self) -> None:
        with pytest.raises(KeyError, match="No adapter registered"):
            get("nonexistent")

    def test_register_custom_adapter(self) -> None:
        class DummyAdapter(AgentAdapter):
            @property
            def name(self) -> str:
                return "dummy"

            async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
                return AgentMessage(
                    bridge_id="",
                    payload="dummy",
                )

        adapter = DummyAdapter()
        register("dummy", adapter)
        assert get("dummy") is adapter

    def test_register_non_adapter_raises(self) -> None:
        with pytest.raises(TypeError):
            register("bad", "not-an-adapter")  # type: ignore[arg-type]


class TestKimiCodeAdapter:
    def test_name(self) -> None:
        adapter = KimiCodeAdapter()
        assert adapter.name == "kimi"

    def test_custom_name(self) -> None:
        adapter = KimiCodeAdapter(name="kimi-local")
        assert adapter.name == "kimi-local"

    async def test_run_delegates_to_runner_with_pinned_command(self) -> None:
        adapter = KimiCodeAdapter()
        stdout_lines = [
            json.dumps({"type": "text", "content": "hello"}),
            json.dumps({"type": "text", "content": "world"}),
        ]
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout="\n".join(stdout_lines))
            result = await adapter.run("say hi", {"bridge_id": "b1", "depth": 1})

        assert isinstance(result, AgentMessage)
        assert result.bridge_id == "b1"
        assert result.depth == 1
        assert result.approval_policy == "read-only"
        assert "hello" in result.payload
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.args[0] == [
            "/usr/bin/kimi",
            "-p",
            "say hi",
            "--output-format",
            "stream-json",
        ]
        env = call_args.kwargs["env"]
        # Adapter passes its current depth; the runner increments it.
        assert env["KIMI_BRIDGE_DEPTH"] == "1"
        assert call_args.kwargs["max_depth"] == DEFAULT_MAX_DEPTH

    async def test_run_never_emits_auto_approve_flags(self) -> None:
        """No read-only policy may cause -y/--yolo/--auto/--afk to be injected.

        v1.0 enforces read-only at the adapter boundary; higher policies raise
        PermissionError rather than running, so we only exercise read-only here.
        The escalation-refusal is covered by test_run_refuses_policy_escalation.
        """
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {"depth": 0, "approval_policy": "read-only"})
        argv = mock_run.call_args.args[0]
        assert not any(flag in argv for flag in ("--yolo", "-y", "--auto", "--afk")), (
            f"read-only policy leaked an auto-approve flag: {argv}"
        )

    async def test_run_refuses_policy_escalation(self) -> None:
        """v1.0 refuses any effective policy above read-only (enforcement gap)."""
        adapter = KimiCodeAdapter()
        for policy in ("explicit", "accept-edits"):
            with (
                mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
                pytest.raises(PermissionError, match="not supported in v1.0"),
            ):
                await adapter.run("prompt", {"depth": 0, "approval_policy": policy})

    async def test_run_raises_on_nonzero_exit(self) -> None:
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stderr="auth required", returncode=1)
            with pytest.raises(RuntimeError, match="auth required"):
                await adapter.run("prompt", {})

    async def test_run_uses_isolated_worktree_by_default(self) -> None:
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cwd is not None
        assert "kimi_worktree_" in str(cwd)

    async def test_run_passes_completion_check_to_runner(self) -> None:
        """Kimi prints its answer but may never exit (global MCP servers keep
        the event loop alive), so the adapter must hand the runner a completion
        sentinel instead of relying on process exit."""
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert mock_run.call_args.kwargs["early_exit_check"] is is_resume_hint_event

    async def test_early_exit_result_with_nonzero_code_is_not_failure(self) -> None:
        """When the sentinel completed the run, the child was reaped by the
        bridge — its exit code is meaningless and must not raise."""
        adapter = KimiCodeAdapter()
        stdout = json.dumps({"role": "assistant", "content": "OK"})
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(
                stdout=stdout, returncode=1, early_exit=True
            )
            result = await adapter.run("prompt", {})
        assert result.payload == "OK"


class TestResumeHintEvent:
    def test_matches_real_resume_hint_event(self) -> None:
        line = json.dumps(
            {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": "session_abc",
                "content": "To resume this session: kimi -r session_abc",
            }
        )
        assert is_resume_hint_event(line) is True

    def test_ignores_assistant_content_mentioning_the_hint(self) -> None:
        line = json.dumps(
            {"role": "assistant", "content": 'docs about "session.resume_hint"'}
        )
        assert is_resume_hint_event(line) is False

    def test_ignores_plain_text_containing_marker(self) -> None:
        assert is_resume_hint_event("session.resume_hint but not json") is False

    def test_ignores_unrelated_json_cheaply(self) -> None:
        line = json.dumps({"role": "assistant", "content": "hi"})
        assert is_resume_hint_event(line) is False


class TestModelSelection:
    """The CLI's -m/--model alias must be pluggable per call (multi-provider
    setups route different providers through config.toml aliases) and must be
    structurally injection-safe (a model value can never become a flag)."""

    def _mocks(self):
        return (
            mock.patch(f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock),
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        )

    async def test_model_from_context_lands_in_argv(self) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with run_patch as mock_run, which_patch:
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {"model": "glm-4.6"})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("-m") + 1] == "glm-4.6"

    async def test_without_model_no_flag_is_emitted(self) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with run_patch as mock_run, which_patch:
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert "-m" not in mock_run.call_args.args[0]

    async def test_constructor_default_model_is_used(self) -> None:
        adapter = KimiCodeAdapter(model="kimi-for-coding")
        run_patch, which_patch = self._mocks()
        with run_patch as mock_run, which_patch:
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("-m") + 1] == "kimi-for-coding"

    async def test_context_model_overrides_constructor_default(self) -> None:
        adapter = KimiCodeAdapter(model="kimi-for-coding")
        run_patch, which_patch = self._mocks()
        with run_patch as mock_run, which_patch:
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {"model": "glm-4.6"})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("-m") + 1] == "glm-4.6"

    @pytest.mark.parametrize(
        "bad_model",
        ["--yolo", "-m", "glm 4.6", "", 'x"y', "a\nb", "-leading-dash"],
    )
    async def test_invalid_model_is_rejected_before_spawn(self, bad_model: str) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            pytest.raises(ValueError, match="model"),
        ):
            await adapter.run("prompt", {"model": bad_model})
        mock_run.assert_not_called()

    async def test_run_can_disable_worktree(self) -> None:
        adapter = KimiCodeAdapter(use_isolated_worktree=False)
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert mock_run.call_args.kwargs["cwd"] is None

    async def test_run_respects_approval_policy(self) -> None:
        """read-only is honored and recorded on the resulting message."""
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            result = await adapter.run(
                "prompt",
                {"bridge_id": "b1", "depth": 0, "approval_policy": "read-only"},
            )
        assert result.approval_policy == "read-only"

    async def test_run_caps_approval_policy(self) -> None:
        """An unknown policy string falls back to read-only (the only enforced one)."""
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            result = await adapter.run(
                "prompt",
                {"bridge_id": "b1", "depth": 0, "approval_policy": "totally-bogus"},
            )
        assert result.approval_policy == "read-only"

    async def test_run_blocks_excessive_depth(self) -> None:
        adapter = KimiCodeAdapter()
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            await adapter.run("prompt", {"depth": DEFAULT_MAX_DEPTH})

    async def test_run_plaintext_fallback_when_no_json(self) -> None:
        """Kimi sometimes emits plain prose instead of stream-json."""
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(
                stdout="I don't see a target attached. Please paste the code."
            )
            result = await adapter.run("review please", {})
        assert "target attached" in result.payload

    def test_parse_output_handles_tool_call_events(self) -> None:
        """Multi-event output with tool_calls keeps only assistant content."""
        adapter = KimiCodeAdapter()
        real = "\n".join(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "Checking with the review skill.",
                        "tool_calls": [{"type": "function", "id": "t1"}],
                    }
                ),
                json.dumps({"role": "tool", "tool_call_id": "t1", "content": "loaded"}),
                json.dumps(
                    {"role": "assistant", "content": "Verdict: request_changes"}
                ),
                json.dumps({"role": "meta", "type": "session.resume_hint"}),
            ]
        )
        out = adapter._parse_output(real)
        assert "request_changes" in out
        assert "loaded" not in out  # tool event ignored
        assert "resume" not in out  # meta event ignored

    async def test_run_empty_output_uses_failsafe_sentinel(self) -> None:
        """Empty/garbage output must never crash or read as approval."""
        from kimi_code_plugin_cc.agent_registry.kimi import EMPTY_RESPONSE_SENTINEL

        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        ):
            mock_run.return_value = _run_result(stdout="   \n  \n")
            result = await adapter.run("prompt", {})
        assert result.payload == EMPTY_RESPONSE_SENTINEL
        assert "approve" not in result.payload


class TestPromptSizeGuard:
    """The prompt travels as an argv element, so it has an OS-imposed ceiling."""

    def _mocks(self):
        return (
            mock.patch(f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock),
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        )

    async def test_oversized_prompt_is_rejected_before_spawn(self) -> None:
        adapter = KimiCodeAdapter()
        oversized = "x" * (DEFAULT_MAX_PROMPT_CHARS + 1)
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            pytest.raises(ValueError, match="above the"),
        ):
            await adapter.run(oversized, {})
        # Nothing was spawned: the user gets an actionable message instead of an
        # opaque OSError from CreateProcess.
        mock_run.assert_not_called()

    async def test_prompt_at_the_limit_is_accepted(self) -> None:
        adapter = KimiCodeAdapter()
        at_limit = "x" * DEFAULT_MAX_PROMPT_CHARS
        run_patch, which_patch = self._mocks()
        with run_patch as mock_run, which_patch:
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run(at_limit, {})
        mock_run.assert_called_once()

    async def test_limit_is_configurable(self) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_PROMPT_CHARS": "10"}, clear=False),
            run_patch as mock_run,
            which_patch,
            pytest.raises(ValueError, match="10-character limit"),
        ):
            await adapter.run("x" * 11, {})
        mock_run.assert_not_called()

    async def test_garbage_limit_falls_back_to_default(self) -> None:
        # A broken override must not silently disable the guard.
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            mock.patch.dict(
                os.environ, {"KIMI_MAX_PROMPT_CHARS": "not-a-number"}, clear=False
            ),
            run_patch as mock_run,
            which_patch,
            pytest.raises(ValueError, match=f"{DEFAULT_MAX_PROMPT_CHARS}-character"),
        ):
            await adapter.run("x" * (DEFAULT_MAX_PROMPT_CHARS + 1), {})
        mock_run.assert_not_called()


class TestSkillsIsolation:
    """Skills discovery makes reviews machine-dependent; isolation is opt-out."""

    def _mocks(self):
        return (
            mock.patch(f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock),
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
        )

    async def test_flag_is_added_when_cli_supports_it(self) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            mock.patch(f"{KIMI_MODULE}.supports_flag", return_value=True),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        argv = mock_run.call_args.args[0]
        assert "--skills-dir" in argv
        assert argv[argv.index("--skills-dir") + 1].endswith(".kimi-no-skills")

    async def test_flag_is_omitted_when_cli_does_not_support_it(self) -> None:
        # Fail-safe: an unknown flag would break every call on an older CLI.
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            mock.patch(f"{KIMI_MODULE}.supports_flag", return_value=False),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert "--skills-dir" not in mock_run.call_args.args[0]

    async def test_constructor_can_opt_out(self) -> None:
        adapter = KimiCodeAdapter(isolate_skills=False)
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            mock.patch(f"{KIMI_MODULE}.supports_flag", return_value=True) as probe,
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert "--skills-dir" not in mock_run.call_args.args[0]
        probe.assert_not_called()  # opting out must not cost a probe

    async def test_env_var_can_opt_out(self) -> None:
        adapter = KimiCodeAdapter()
        run_patch, which_patch = self._mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_ISOLATE_SKILLS": "0"}, clear=False),
            run_patch as mock_run,
            which_patch,
            mock.patch(f"{KIMI_MODULE}.supports_flag", return_value=True),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert "--skills-dir" not in mock_run.call_args.args[0]

    async def test_no_worktree_means_no_isolation_dir(self) -> None:
        # Without a working directory there is nowhere to put the empty dir.
        adapter = KimiCodeAdapter(use_isolated_worktree=False)
        run_patch, which_patch = self._mocks()
        with (
            run_patch as mock_run,
            which_patch,
            mock.patch(f"{KIMI_MODULE}.supports_flag", return_value=True),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            await adapter.run("prompt", {})
        assert "--skills-dir" not in mock_run.call_args.args[0]


def _codex_runner(
    payload: str = "REVIEW OK",
    returncode: int = 0,
    stderr: str = "",
    write_last_message: bool = True,
):
    """Build a fake runner that behaves like a real ``codex exec`` invocation.

    The real CLI writes its final answer to the ``-o`` file and exits, so the
    fake does the same: tests that assert on the payload exercise the same
    file-based path the adapter depends on, not a stdout shortcut.
    """

    async def _run(args: list[str], **kwargs: Any) -> RunResult:
        if write_last_message:
            out = Path(args[args.index(OUTPUT_FLAG) + 1])
            out.write_text(payload, encoding="utf-8")
        return RunResult(
            returncode=returncode,
            stdout="",
            stderr=stderr,
            args=list(args),
            env={},
        )

    return _run


def _codex_mocks(supports: bool = False):
    """Patches shared by the codex tests: runner, PATH resolution, probe.

    ``supports`` drives the capability probe; it defaults to False so the base
    command shape is asserted without optional flags unless a test opts in.
    """
    return (
        mock.patch(f"{CODEX_MODULE}.run_agent_process", new_callable=mock.AsyncMock),
        mock.patch(f"{CODEX_MODULE}.shutil.which", return_value="/usr/bin/codex"),
        mock.patch(f"{CODEX_MODULE}.supports_flag", return_value=supports),
    )


class TestCodexAdapter:
    def test_name(self) -> None:
        assert CodexAdapter().name == "codex"

    def test_custom_name(self) -> None:
        assert CodexAdapter(name="codex-local").name == "codex-local"

    async def test_run_uses_pinned_base_command_shape(self) -> None:
        """Base argv, verified against codex-cli 0.145.0.

        The prompt must be last and preceded by ``--``: without the separator
        clap parses a prompt that starts with a hyphen as an unknown flag and
        the run dies before it starts.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner(payload="hello")
            result = await adapter.run("say hi", {"bridge_id": "b1", "depth": 1})

        assert isinstance(result, AgentMessage)
        assert result.bridge_id == "b1"
        assert result.depth == 1
        assert result.approval_policy == "read-only"
        assert result.payload == "hello"

        argv = mock_run.call_args.args[0]
        assert argv[:2] == ["/usr/bin/codex", "exec"]
        assert argv[-2:] == ["--", "say hi"]
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--json" in argv
        assert argv[argv.index(OUTPUT_FLAG) + 1].endswith(LAST_MESSAGE_FILENAME)

        env = mock_run.call_args.kwargs["env"]
        assert env["KIMI_BRIDGE_DEPTH"] == "1"
        assert mock_run.call_args.kwargs["max_depth"] == DEFAULT_MAX_DEPTH

    async def test_run_waits_for_natural_exit(self) -> None:
        """``codex exec`` is batch and exits on its own.

        Unlike kimi it needs no completion sentinel; passing one would reap the
        process tree early. The timeout stays as a pure backstop.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        assert mock_run.call_args.kwargs.get("early_exit_check") is None

    async def test_run_uses_isolated_worktree_by_default(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        cwd = mock_run.call_args.kwargs["cwd"]
        assert cwd is not None
        assert "kimi_worktree_" in str(cwd)

    async def test_last_message_file_lives_outside_the_workspace(self) -> None:
        """The payload file must not sit inside the agent's writable workspace.

        Under ``workspace-write`` a model-generated command could otherwise
        overwrite the file the adapter treats as the authoritative answer.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        argv = mock_run.call_args.args[0]
        out_file = Path(argv[argv.index(OUTPUT_FLAG) + 1])
        workdir = Path(mock_run.call_args.kwargs["cwd"])
        assert workdir not in out_file.parents

    async def test_temp_artifacts_are_cleaned_up(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        argv = mock_run.call_args.args[0]
        out_file = Path(argv[argv.index(OUTPUT_FLAG) + 1])
        assert not out_file.parent.exists()
        assert not Path(mock_run.call_args.kwargs["cwd"]).exists()

    async def test_run_blocks_excessive_depth(self) -> None:
        adapter = CodexAdapter()
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            await adapter.run("prompt", {"depth": DEFAULT_MAX_DEPTH})

    async def test_missing_cli_names_the_install_channel(self) -> None:
        adapter = CodexAdapter()
        with (
            mock.patch(f"{CODEX_MODULE}.shutil.which", return_value=None),
            pytest.raises(FileNotFoundError, match="@openai/codex"),
        ):
            await adapter.run("prompt", {})


class TestCodexPolicyMapping:
    """Plugin policy -> ``--sandbox`` value. The cap is the whole point."""

    async def _sandbox_for(self, policy: str) -> str:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"approval_policy": policy})
        argv = mock_run.call_args.args[0]
        return argv[argv.index("--sandbox") + 1]

    async def test_read_only_is_the_default(self) -> None:
        assert await self._sandbox_for("read-only") == "read-only"

    async def test_accept_edits_is_capped_to_read_only_by_default(self) -> None:
        """KIMI_MAX_POLICY defaults to read-only, so escalation must not pass."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KIMI_MAX_POLICY", None)
            assert await self._sandbox_for("accept-edits") == "read-only"

    async def test_accept_edits_maps_to_workspace_write_when_ceiling_allows(
        self,
    ) -> None:
        with mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}):
            assert await self._sandbox_for("accept-edits") == "workspace-write"

    async def test_explicit_is_refused_rather_than_faked(self) -> None:
        """``codex exec`` is non-interactive: nobody can approve each action.

        Recording a policy the CLI cannot enact is the defect this repo already
        refuses for kimi; the same honesty rule applies here.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "explicit"}),
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(PermissionError, match="non-interactive"),
        ):
            await adapter.run("prompt", {"approval_policy": "explicit"})
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "smuggled",
        ["danger-full-access", "DANGER-FULL-ACCESS", "danger_full_access", "yolo"],
    )
    async def test_danger_full_access_is_unreachable(self, smuggled: str) -> None:
        """No caller-supplied policy string may reach the dangerous sandbox."""
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(ValueError, match="Unknown approval policy"),
        ):
            await adapter.run("prompt", {"approval_policy": smuggled})
        mock_run.assert_not_called()

    async def test_writable_sandbox_requires_a_working_directory(self) -> None:
        """A writable sandbox with cwd=None would make the HOST dir writable.

        `--sandbox workspace-write` scopes writes to the process's working
        directory. With worktree isolation off and no explicit worktree that is
        the host's current directory, so a prompt-injected agent could edit the
        code under review. The policy cap alone does not prevent it.
        """
        adapter = CodexAdapter(use_isolated_worktree=False)
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(PermissionError, match="workspace-write"),
        ):
            await adapter.run("prompt", {"approval_policy": "accept-edits"})
        mock_run.assert_not_called()

    async def test_writable_sandbox_is_allowed_inside_an_explicit_worktree(
        self, tmp_path
    ) -> None:
        # A caller-supplied directory contains the writes, so it is permitted.
        adapter = CodexAdapter(worktree=tmp_path)
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
            run_patch as mock_run,
            which_patch,
            probe_patch,
        ):
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"approval_policy": "accept-edits"})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"

    async def test_read_only_still_runs_without_a_working_directory(self) -> None:
        # The containment rule must only bite for writable sandboxes.
        adapter = CodexAdapter(use_isolated_worktree=False)
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"approval_policy": "read-only"})
        assert mock_run.call_args.kwargs["cwd"] is None

    async def test_sandbox_allowlist_is_enforced_structurally(self) -> None:
        """Defense in depth: the argv builder refuses an off-list sandbox mode.

        Even if a future policy-mapping edit produced an unexpected value, the
        command must not be built rather than silently widening the sandbox.
        """
        adapter = CodexAdapter()
        with pytest.raises(ValueError, match="sandbox"):
            adapter._build_base_command(
                ["/usr/bin/codex"], BANNED_SANDBOX_MODE, Path("out.txt")
            )


class TestCodexBannedFlags:
    """Invariant 2: auto-approve capabilities are never emitted, for any input."""

    @pytest.mark.parametrize("policy", ["read-only", "accept-edits"])
    @pytest.mark.parametrize("model", [None, "gpt-5-codex"])
    @pytest.mark.parametrize("supports", [True, False])
    async def test_never_flags_absent_for_every_input(
        self, policy: str, model: str | None, supports: bool
    ) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks(supports=supports)
        with (
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
            run_patch as mock_run,
            which_patch,
            probe_patch,
        ):
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"approval_policy": policy, "model": model})
        argv = mock_run.call_args.args[0]
        leaked = [f for f in (*NEVER_FLAGS, BANNED_SANDBOX_MODE) if f in argv]
        assert not leaked, f"banned codex capability leaked into argv: {leaked}"

    async def test_prompt_content_cannot_inject_a_banned_flag(self) -> None:
        """A prompt is a single argv element after ``--``; it stays inert."""
        adapter = CodexAdapter()
        hostile = "--dangerously-bypass-approvals-and-sandbox"
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run(hostile, {})
        argv = mock_run.call_args.args[0]
        assert argv[-1] == hostile
        assert argv[-2] == "--"
        # The banned string appears exactly once, as the inert prompt payload.
        assert argv.count(hostile) == 1


class TestCodexPayload:
    """Invariant 1: the last-message file is the only source of truth."""

    async def test_payload_comes_from_the_last_message_file(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner(payload="  verdict: approve  ")
            result = await adapter.run("prompt", {})
        assert result.payload == "verdict: approve"

    async def test_missing_file_fails_even_on_exit_zero(self) -> None:
        """Exit 0 with no answer is a failure, not an empty approval."""
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="no final message"),
        ):
            mock_run.side_effect = _codex_runner(write_last_message=False)
            await adapter.run("prompt", {})

    async def test_empty_file_fails_closed(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="empty"),
        ):
            mock_run.side_effect = _codex_runner(payload="   \n  \n")
            await adapter.run("prompt", {})

    @pytest.mark.parametrize("failure_payload", ["", "   \n", "approve"])
    async def test_failures_never_resolve_to_an_approval(
        self, failure_payload: str
    ) -> None:
        """Even a file saying "approve" must not surface when the run failed."""
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError) as excinfo,
        ):
            mock_run.side_effect = _codex_runner(
                payload=failure_payload, returncode=1, stderr="not logged in"
            )
            await adapter.run("prompt", {})
        assert "not logged in" in str(excinfo.value)

    async def test_nonzero_exit_carries_stderr_context(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="stream disconnected"),
        ):
            mock_run.side_effect = _codex_runner(
                returncode=1, stderr="stream disconnected"
            )
            await adapter.run("prompt", {})

    async def test_utf8_payload_survives_the_round_trip(self) -> None:
        """The file is read with explicit UTF-8, not the Windows ANSI default."""
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner(payload="Prüfung — 完了 ✓")
            result = await adapter.run("prompt", {})
        assert result.payload == "Prüfung — 完了 ✓"


class TestCodexModelAndPromptGuards:
    """Reused kimi guards must apply to codex identically."""

    async def test_model_from_context_lands_in_argv(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"model": "gpt-5-codex"})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("-m") + 1] == "gpt-5-codex"
        # The model flag must stay in front of the argument separator.
        assert argv.index("-m") < argv.index("--")

    async def test_without_model_no_flag_is_emitted(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        assert "-m" not in mock_run.call_args.args[0]

    async def test_context_model_overrides_constructor_default(self) -> None:
        adapter = CodexAdapter(model="gpt-5-codex")
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {"model": "o3"})
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("-m") + 1] == "o3"

    @pytest.mark.parametrize(
        "bad_model",
        [
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            "gpt 5",
            "",
            'x"y',
            "a\nb",
        ],
    )
    async def test_invalid_model_is_rejected_before_spawn(self, bad_model: str) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(ValueError, match="model"),
        ):
            await adapter.run("prompt", {"model": bad_model})
        mock_run.assert_not_called()

    async def test_oversized_prompt_is_rejected_before_spawn(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(ValueError, match="above the"),
        ):
            await adapter.run("x" * (DEFAULT_MAX_PROMPT_CHARS + 1), {})
        mock_run.assert_not_called()


class TestCodexIsolation:
    """Capability-gated flags: never emitted against a CLI that lacks them."""

    async def _argv(self, adapter: CodexAdapter, supports: bool) -> list[str]:
        run_patch, which_patch, probe_patch = _codex_mocks(supports=supports)
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        return mock_run.call_args.args[0]

    async def test_flags_are_added_when_the_cli_supports_them(self) -> None:
        argv = await self._argv(CodexAdapter(), supports=True)
        assert "--ephemeral" in argv
        assert "--ignore-user-config" in argv
        assert "--skip-git-repo-check" in argv

    async def test_flags_are_omitted_when_the_cli_lacks_them(self) -> None:
        # Fail-safe: an unknown flag would break every call on an older CLI.
        argv = await self._argv(CodexAdapter(), supports=False)
        assert "--ephemeral" not in argv
        assert "--ignore-user-config" not in argv
        assert "--skip-git-repo-check" not in argv

    async def test_constructor_can_opt_out_of_session_isolation(self) -> None:
        argv = await self._argv(CodexAdapter(isolate_session=False), supports=True)
        assert "--ephemeral" not in argv
        assert "--ignore-user-config" not in argv
        # The git-repo check is not part of the opt-out: the isolated worktree
        # is never a git repository, so the run would fail without it.
        assert "--skip-git-repo-check" in argv

    async def test_env_var_can_opt_out_of_session_isolation(self) -> None:
        with mock.patch.dict(os.environ, {ENV_ISOLATE_SESSION: "0"}, clear=False):
            argv = await self._argv(CodexAdapter(), supports=True)
        assert "--ephemeral" not in argv
        assert "--ignore-user-config" not in argv

    async def test_constructor_wins_over_the_env_opt_out(self) -> None:
        with mock.patch.dict(os.environ, {ENV_ISOLATE_SESSION: "0"}, clear=False):
            argv = await self._argv(CodexAdapter(isolate_session=True), supports=True)
        assert "--ephemeral" in argv


class TestCodexWorkdirAndDiagnostics:
    """Worktree ownership and the diagnostics attached to a failed run."""

    async def test_explicit_worktree_is_used_and_left_alone(self, tmp_path) -> None:
        # A caller-supplied worktree is caller-managed: using it must not
        # delete the caller's directory when the run finishes.
        adapter = CodexAdapter(worktree=tmp_path)
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        assert mock_run.call_args.kwargs["cwd"] == tmp_path
        assert tmp_path.exists()

    async def test_worktree_isolation_can_be_disabled(self) -> None:
        adapter = CodexAdapter(use_isolated_worktree=False)
        run_patch, which_patch, probe_patch = _codex_mocks()
        with run_patch as mock_run, which_patch, probe_patch:
            mock_run.side_effect = _codex_runner()
            await adapter.run("prompt", {})
        assert mock_run.call_args.kwargs["cwd"] is None

    async def test_failure_without_stderr_falls_back_to_stdout(self) -> None:
        """codex reports some errors only on the JSONL stream, not on stderr."""
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()

        async def _stdout_only(args: list[str], **kwargs: Any) -> RunResult:
            return RunResult(
                returncode=1,
                stdout='{"type":"error","message":"model not found"}',
                stderr="",
                args=list(args),
                env={},
            )

        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="model not found"),
        ):
            mock_run.side_effect = _stdout_only
            await adapter.run("prompt", {})

    async def test_silent_failure_still_reports_something_actionable(self) -> None:
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="no output on either stream"),
        ):
            mock_run.side_effect = _codex_runner(returncode=1, write_last_message=False)
            await adapter.run("prompt", {})

    async def test_no_temp_dir_leaks_when_setup_fails_midway(self) -> None:
        """Regression: a failure between the two mkdtemp calls leaked a dir.

        The output directory used to be created after the worktree and outside
        the try, so an error in between skipped the cleanup entirely. In a
        long-running MCP session those leaks accumulate silently.
        """
        adapter = CodexAdapter()
        created: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def _tracking_mkdtemp(*args: Any, **kwargs: Any) -> str:
            path = real_mkdtemp(*args, **kwargs)
            created.append(Path(path))
            return path

        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            mock.patch(
                f"{CODEX_MODULE}.tempfile.mkdtemp", side_effect=_tracking_mkdtemp
            ),
            mock.patch(
                f"{CODEX_MODULE}.create_isolated_worktree",
                side_effect=OSError("no space left on device"),
            ),
            pytest.raises(OSError, match="no space left"),
        ):
            await adapter.run("prompt", {})
        mock_run.assert_not_called()
        assert created, "expected the output directory to have been created"
        assert not any(path.exists() for path in created), (
            f"temp directories leaked after a failed setup: {created}"
        )

    async def test_cleanup_survives_a_failure_before_anything_was_created(
        self,
    ) -> None:
        """The finally block must not itself blow up on the earliest failure.

        If the very first mkdtemp fails, both directory handles are still None;
        the cleanup has to tolerate that and let the original error surface.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            mock.patch(
                f"{CODEX_MODULE}.tempfile.mkdtemp",
                side_effect=OSError("too many open files"),
            ),
            pytest.raises(OSError, match="too many open files"),
        ):
            await adapter.run("prompt", {})
        mock_run.assert_not_called()

    async def test_undecodable_final_message_is_an_agent_failure(self) -> None:
        """Regression: UnicodeDecodeError is a ValueError, not an OSError.

        Left unnamed it escaped the handler and was classified as
        'invalid_input' — blaming the caller for bytes the agent wrote.
        """
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()

        async def _writes_binary(args: list[str], **kwargs: Any) -> RunResult:
            Path(args[args.index(OUTPUT_FLAG) + 1]).write_bytes(b"\xff\xfe\x00bad")
            return RunResult(
                returncode=0, stdout="", stderr="", args=list(args), env={}
            )

        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(RuntimeError, match="no final message"),
        ):
            mock_run.side_effect = _writes_binary
            await adapter.run("prompt", {})

    async def test_non_string_policy_is_rejected_before_spawn(self) -> None:
        # A caller passing e.g. an enum member or None must get a clear
        # invalid-input error, not an AttributeError from deep in the resolver.
        adapter = CodexAdapter()
        run_patch, which_patch, probe_patch = _codex_mocks()
        with (
            run_patch as mock_run,
            which_patch,
            probe_patch,
            pytest.raises(ValueError, match="must be a string"),
        ):
            await adapter.run("prompt", {"approval_policy": 0})
        mock_run.assert_not_called()
