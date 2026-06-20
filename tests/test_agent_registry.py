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
from kimi_code_plugin_cc.protocol.messages import DEFAULT_MAX_DEPTH, AgentMessage


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

            def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
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

    def test_run_calls_subprocess_with_pinned_command(self) -> None:
        adapter = KimiCodeAdapter()
        stdout_lines = [
            json.dumps({"type": "text", "content": "hello"}),
            json.dumps({"type": "text", "content": "world"}),
        ]
        with (
            mock.patch(
                "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
            ) as mock_run,
            mock.patch(
                "kimi_code_plugin_cc.agent_registry.kimi.shutil.which"
            ) as mock_which,
        ):
            mock_which.return_value = "/usr/bin/kimi"
            mock_run.return_value = mock.MagicMock(
                stdout="\n".join(stdout_lines),
                stderr="",
                returncode=0,
            )
            result = adapter.run("say hi", {"bridge_id": "b1", "depth": 1})

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
        assert env["KIMI_BRIDGE_DEPTH"] == "2"

    def test_run_raises_on_nonzero_exit(self) -> None:
        adapter = KimiCodeAdapter()
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
        ) as mock_run:
            mock_run.return_value = mock.MagicMock(
                stdout="",
                stderr="auth required",
                returncode=1,
            )
            with pytest.raises(RuntimeError, match="auth required"):
                adapter.run("prompt", {})

    def test_run_uses_isolated_worktree_by_default(self) -> None:
        adapter = KimiCodeAdapter()
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
        ) as mock_run:
            mock_run.return_value = mock.MagicMock(
                stdout=json.dumps({"content": "ok"}),
                stderr="",
                returncode=0,
            )
            adapter.run("prompt", {})
        assert "cwd" in mock_run.call_args.kwargs
        cwd = mock_run.call_args.kwargs["cwd"]
        assert "kimi_worktree_" in str(cwd)

    def test_run_can_disable_worktree(self) -> None:
        adapter = KimiCodeAdapter(use_isolated_worktree=False)
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
        ) as mock_run:
            mock_run.return_value = mock.MagicMock(
                stdout=json.dumps({"content": "ok"}),
                stderr="",
                returncode=0,
            )
            adapter.run("prompt", {})
        assert "cwd" not in mock_run.call_args.kwargs

    def test_run_respects_approval_policy(self) -> None:
        adapter = KimiCodeAdapter()
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
        ) as mock_run, mock.patch.dict(os.environ, {"KIMI_MAX_POLICY": "accept-edits"}):
            mock_run.return_value = mock.MagicMock(
                stdout=json.dumps({"content": "ok"}),
                stderr="",
                returncode=0,
            )
            result = adapter.run(
                "prompt",
                {"bridge_id": "b1", "depth": 0, "approval_policy": "accept-edits"},
            )
        assert result.approval_policy == "accept-edits"

    def test_run_caps_approval_policy(self) -> None:
        adapter = KimiCodeAdapter()
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.kimi.subprocess.run"
        ) as mock_run:
            mock_run.return_value = mock.MagicMock(
                stdout=json.dumps({"content": "ok"}),
                stderr="",
                returncode=0,
            )
            result = adapter.run(
                "prompt",
                {"bridge_id": "b1", "depth": 0, "approval_policy": "accept-edits"},
            )
        assert result.approval_policy == "read-only"

    def test_run_blocks_excessive_depth(self) -> None:
        adapter = KimiCodeAdapter()
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            adapter.run("prompt", {"depth": DEFAULT_MAX_DEPTH})


class TestCodexAdapter:
    def test_name(self) -> None:
        adapter = CodexAdapter()
        assert adapter.name == "codex"

    def test_run_raises_not_implemented(self) -> None:
        adapter = CodexAdapter()
        with pytest.raises(NotImplementedError):
            adapter.run("prompt", {})
