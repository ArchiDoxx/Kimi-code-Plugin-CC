"""Tests for the MCP error contract and CLI capability detection."""

from __future__ import annotations

import json
from typing import Any

import pytest

from kimi_code_plugin_cc.agent_registry import register
from kimi_code_plugin_cc.agent_registry.base import (
    AdapterNotImplementedError,
    AgentAdapter,
)
from kimi_code_plugin_cc.agent_registry.capabilities import (
    cli_version,
    reset_cache,
    supports_flag,
)
from kimi_code_plugin_cc.errors import (
    ERROR_PREFIX,
    AgentNotInstalledError,
    ErrorKind,
    as_json,
    as_text,
    classify,
)
from kimi_code_plugin_cc.mcp_server import create_server
from kimi_code_plugin_cc.protocol.messages import AgentMessage

# A binary that cannot exist, used to prove capability detection fails safe.
MISSING_EXECUTABLE = ["kimi-code-plugin-cc-no-such-binary"]


class FailingAdapter(AgentAdapter):
    """Adapter that always raises a configured exception."""

    def __init__(self, name: str, exc: Exception) -> None:
        self._name = name
        self._exc = exc

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt: str, context: dict[str, Any]) -> AgentMessage:
        raise self._exc


class TestClassify:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (FileNotFoundError("kimi not found"), ErrorKind.NOT_INSTALLED),
            (KeyError("No adapter registered for 'nope'"), ErrorKind.UNKNOWN_AGENT),
            (AdapterNotImplementedError("skeleton"), ErrorKind.NOT_IMPLEMENTED),
            (PermissionError("policy escalation"), ErrorKind.POLICY_REFUSED),
            (TimeoutError("deadline"), ErrorKind.TIMEOUT),
            (ValueError("bad alias"), ErrorKind.INVALID_INPUT),
            (RuntimeError("exit 1"), ErrorKind.AGENT_FAILED),
            (ZeroDivisionError("boom"), ErrorKind.INTERNAL),
        ],
    )
    def test_maps_exception_to_kind(self, exc: Exception, expected: ErrorKind) -> None:
        kind, _message = classify(exc)
        assert kind is expected

    def test_not_implemented_wins_over_runtime_error(self) -> None:
        # AdapterNotImplementedError subclasses NotImplementedError; the more
        # specific branch must be reached first or a skeleton adapter would be
        # reported as a generic agent failure.
        kind, _ = classify(AdapterNotImplementedError("codex is a skeleton"))
        assert kind is ErrorKind.NOT_IMPLEMENTED

    def test_permission_error_is_not_swallowed_by_oserror(self) -> None:
        # PermissionError subclasses OSError, which the tool tuple also catches.
        kind, _ = classify(PermissionError("refused"))
        assert kind is ErrorKind.POLICY_REFUSED

    def test_key_error_message_is_prose_not_repr(self) -> None:
        _kind, message = classify(KeyError("No adapter registered for 'nope'"))
        assert message.startswith("No adapter registered")
        assert not message.startswith('"')

    def test_missing_cli_message_carries_install_hint(self) -> None:
        _kind, message = classify(FileNotFoundError("kimi not on PATH"))
        assert "npm i -g" in message

    def test_agent_specific_install_hint_wins_over_the_default(self) -> None:
        """A second agent must not be told to install the first agent's CLI."""
        kind, message = classify(
            AgentNotInstalledError(
                "codex", "Install it with 'npm install -g @openai/codex'."
            )
        )
        assert kind is ErrorKind.NOT_INSTALLED
        assert "@openai/codex" in message
        assert "moonshot" not in message.lower()

    def test_agent_not_installed_is_a_file_not_found_error(self) -> None:
        # Callers (and the runner) already treat a missing binary as OSError;
        # narrowing the type must not change that contract.
        assert issubclass(AgentNotInstalledError, FileNotFoundError)


class TestRendering:
    def test_as_text_is_unmistakably_an_error(self) -> None:
        text = as_text(ValueError("bad alias"))
        assert text.startswith(f"{ERROR_PREFIX} [{ErrorKind.INVALID_INPUT.value}]")

    def test_as_json_keeps_fail_closed_verdict(self) -> None:
        payload = json.loads(as_json(RuntimeError("exit 1"), verdict="red"))
        assert payload["status"] == "error"
        assert payload["error_kind"] == ErrorKind.AGENT_FAILED.value
        assert payload["verdict"] == "red"


class TestMcpToolsFailClosed:
    """A crash must still surface as a non-approval, never as a missing verdict."""

    async def test_santa_loop_returns_red_on_adapter_failure(self) -> None:
        register("boom-santa", FailingAdapter("boom-santa", RuntimeError("exit 1")))
        server = create_server()
        content, _meta = await server.call_tool(
            "run_santa_loop",
            {"primary_agent": "boom-santa", "target": "code"},
        )
        payload = json.loads(content[0].text)
        assert payload["verdict"] == "red"
        assert payload["error_kind"] == ErrorKind.AGENT_FAILED.value

    async def test_santa_loop_returns_red_when_cli_is_missing(self) -> None:
        register(
            "gone-santa", FailingAdapter("gone-santa", FileNotFoundError("no kimi"))
        )
        server = create_server()
        content, _meta = await server.call_tool(
            "run_santa_loop",
            {"primary_agent": "gone-santa", "target": "code"},
        )
        payload = json.loads(content[0].text)
        assert payload["verdict"] == "red"
        assert payload["error_kind"] == ErrorKind.NOT_INSTALLED.value

    async def test_review_loop_returns_needs_discussion_on_failure(self) -> None:
        register("boom-review", FailingAdapter("boom-review", TimeoutError("slow")))
        server = create_server()
        content, _meta = await server.call_tool(
            "run_review_loop",
            {"agent_name": "boom-review", "target": "code"},
        )
        payload = json.loads(content[0].text)
        assert payload["verdict"] == "needs_discussion"
        assert payload["error_kind"] == ErrorKind.TIMEOUT.value

    async def test_planning_loop_reports_error_instead_of_crashing(self) -> None:
        register("boom-plan", FailingAdapter("boom-plan", RuntimeError("exit 1")))
        server = create_server()
        content, _meta = await server.call_tool(
            "run_planning_loop",
            {"agent_name": "boom-plan", "prompt": "task"},
        )
        payload = json.loads(content[0].text)
        assert payload["status"] == "error"

    async def test_loop_tools_classify_unknown_agent(self) -> None:
        server = create_server()
        content, _meta = await server.call_tool(
            "run_santa_loop",
            {"primary_agent": "not-registered", "target": "code"},
        )
        payload = json.loads(content[0].text)
        assert payload["error_kind"] == ErrorKind.UNKNOWN_AGENT.value
        assert payload["verdict"] == "red"

    async def test_skeleton_adapter_is_reported_not_raised(self) -> None:
        register(
            "skeleton",
            FailingAdapter("skeleton", AdapterNotImplementedError("not built yet")),
        )
        server = create_server()
        content, _meta = await server.call_tool(
            "run_agent",
            {"agent_name": "skeleton", "prompt": "hi"},
        )
        assert content[0].text.startswith(
            f"{ERROR_PREFIX} [{ErrorKind.NOT_IMPLEMENTED.value}]"
        )

    async def test_missing_codex_cli_is_reported_with_its_own_hint(self) -> None:
        from unittest import mock

        server = create_server()
        with mock.patch(
            "kimi_code_plugin_cc.agent_registry.codex.shutil.which", return_value=None
        ):
            content, _meta = await server.call_tool(
                "run_agent",
                {"agent_name": "codex", "prompt": "hi"},
            )
        text = content[0].text
        assert text.startswith(f"{ERROR_PREFIX} [{ErrorKind.NOT_INSTALLED.value}]")
        assert "@openai/codex" in text


class TestCapabilityDetection:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_missing_executable_reports_no_support(self) -> None:
        # Fail-safe: an unreadable CLI must never claim to support a flag, or
        # the adapter would pass an unknown flag and break every call.
        assert supports_flag(MISSING_EXECUTABLE, "--skills-dir") is False

    def test_missing_executable_reports_no_version(self) -> None:
        assert cli_version(MISSING_EXECUTABLE) is None

    def test_help_output_is_probed_only_once(self, monkeypatch: Any) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_probe(argv_prefix: tuple[str, ...], flag: str) -> str:
            calls.append(argv_prefix)
            return "--skills-dir <dir>  Load skills from this directory"

        monkeypatch.setattr(
            "kimi_code_plugin_cc.agent_registry.capabilities._probe", fake_probe
        )
        assert supports_flag(["fake-cli"], "--skills-dir") is True
        assert supports_flag(["fake-cli"], "--skills-dir") is True
        assert len(calls) == 1
