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
from kimi_code_plugin_cc.bridge.runner import RunResult
from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH, AgentMessage

KIMI_MODULE = "kimi_code_plugin_cc.agent_registry.kimi"


def _run_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> RunResult:
    return RunResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        args=[],
        env={},
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
        """No policy may cause -y/--yolo/--auto/--afk to be injected."""
        adapter = KimiCodeAdapter()
        for policy in ("read-only", "explicit", "accept-edits"):
            with (
                mock.patch(
                    f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
                ) as mock_run,
                mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
                mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
            ):
                mock_run.return_value = _run_result(
                    stdout=json.dumps({"content": "ok"})
                )
                await adapter.run("prompt", {"depth": 0, "approval_policy": policy})
            argv = mock_run.call_args.args[0]
            assert not any(
                flag in argv for flag in ("--yolo", "-y", "--auto", "--afk")
            ), f"policy {policy} leaked an auto-approve flag: {argv}"

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
        adapter = KimiCodeAdapter()
        with (
            mock.patch(
                f"{KIMI_MODULE}.run_agent_process", new_callable=mock.AsyncMock
            ) as mock_run,
            mock.patch(f"{KIMI_MODULE}.shutil.which", return_value="/usr/bin/kimi"),
            mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}),
        ):
            mock_run.return_value = _run_result(stdout=json.dumps({"content": "ok"}))
            result = await adapter.run(
                "prompt",
                {"bridge_id": "b1", "depth": 0, "approval_policy": "accept-edits"},
            )
        assert result.approval_policy == "accept-edits"

    async def test_run_caps_approval_policy(self) -> None:
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
                {"bridge_id": "b1", "depth": 0, "approval_policy": "accept-edits"},
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
