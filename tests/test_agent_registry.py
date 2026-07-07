"""Tests for the agent registry and adapters."""

from __future__ import annotations

import json
import os
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
from kimi_code_plugin_cc.agent_registry.kimi import is_resume_hint_event
from kimi_code_plugin_cc.bridge.runner import RunResult
from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH, AgentMessage

KIMI_MODULE = "kimi_code_plugin_cc.agent_registry.kimi"


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


class TestCodexAdapter:
    def test_name(self) -> None:
        adapter = CodexAdapter()
        assert adapter.name == "codex"

    async def test_run_raises_not_implemented(self) -> None:
        adapter = CodexAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.run("prompt", {})
