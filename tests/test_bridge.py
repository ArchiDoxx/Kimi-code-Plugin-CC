"""Tests for the bridge layer."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from unittest import mock

import pytest

from kimi_code_plugin_cc.agent_registry.codex import AUTH_ENV as CODEX_AUTH_ENV
from kimi_code_plugin_cc.agent_registry.kimi import AUTH_ENV as KIMI_AUTH_ENV
from kimi_code_plugin_cc.bridge.parser import (
    parse_stream_json,
    parse_stream_json_async,
)
from kimi_code_plugin_cc.bridge.runner import (
    DEPTH_ENV_VAR,
    AuthEnv,
    RunResult,
    _build_child_env,
    run_agent_process,
)


class TestParser:
    async def test_parse_simple_stream(self) -> None:
        raw = json.dumps({"type": "text", "content": "hello"})
        result = await parse_stream_json(raw)
        assert result == [{"type": "text", "content": "hello"}]

    async def test_parse_multiline_stream(self) -> None:
        lines = [
            json.dumps({"type": "text", "content": "line1"}),
            json.dumps({"type": "text", "content": "line2"}),
        ]
        result = await parse_stream_json("\n".join(lines))
        assert len(result) == 2
        assert result[0]["content"] == "line1"

    async def test_parse_ignores_empty_lines(self) -> None:
        result = await parse_stream_json("\n\n")
        assert result == []

    async def test_parse_malformed_line(self) -> None:
        result = await parse_stream_json("not-json")
        assert result == [{"raw": "not-json"}]

    async def test_parse_non_dict_value(self) -> None:
        result = await parse_stream_json("42")
        assert result == [{"value": 42}]

    async def test_parse_async_chunk_boundaries(self) -> None:
        async def chunks() -> AsyncIterator[str]:
            yield '{"type": "te'
            yield 'xt", "content": "a"}\n{"type": "tex'
            yield 't", "content": "b"}'

        result = await parse_stream_json_async(chunks())
        assert len(result) == 2
        assert result[0] == {"type": "text", "content": "a"}
        assert result[1] == {"type": "text", "content": "b"}

    async def test_parse_async_trailing_partial(self) -> None:
        async def chunks() -> AsyncIterator[str]:
            yield '{"type": "text", "content": "x"}\n{"partial": tr'

        result = await parse_stream_json_async(chunks())
        assert result[0] == {"type": "text", "content": "x"}
        assert result[1] == {"raw": '{"partial": tr'}


class TestRunner:
    async def test_run_agent_process_success(self) -> None:
        result = await run_agent_process(
            ["python", "-c", "print('hello')"],
            env={},
            timeout=10.0,
            max_depth=5,
        )
        assert isinstance(result, RunResult)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.env[DEPTH_ENV_VAR] == "1"

    async def test_run_agent_process_empty_args_raises(self) -> None:
        with pytest.raises(ValueError, match="args must not be empty"):
            await run_agent_process([], env={}, timeout=10.0, max_depth=5)

    async def test_run_agent_process_inherits_and_increments_depth(self) -> None:
        result = await run_agent_process(
            ["python", "-c", "import os; print(os.environ['KIMI_BRIDGE_DEPTH'])"],
            env={DEPTH_ENV_VAR: "2"},
            timeout=10.0,
            max_depth=5,
        )
        assert result.stdout.strip() == "3"

    async def test_run_agent_process_depth_guard_blocks(self) -> None:
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            await run_agent_process(
                ["python", "-c", "print('hello')"],
                env={DEPTH_ENV_VAR: "2"},
                timeout=10.0,
                max_depth=2,
            )

    async def test_run_agent_process_timeout(self) -> None:
        with pytest.raises(TimeoutError, match="possible auth hang"):
            await run_agent_process(
                ["python", "-c", "import time; time.sleep(10)"],
                env={},
                timeout=0.1,
                max_depth=5,
            )

    async def test_run_agent_process_invalid_executable(self) -> None:
        with pytest.raises(FileNotFoundError):
            await run_agent_process(
                ["this_command_definitely_does_not_exist_12345"],
                env={},
                timeout=5.0,
                max_depth=5,
            )


class TestAuthEnvIsolation:
    """Each agent may receive only its own vendor's credentials.

    A single global auth allowlist handed every agent every vendor's key. Since
    command execution is permitted even under a read-only sandbox, a
    prompt-injected agent could read another vendor's key out of its own
    environment and quote it into its answer, which the loops then persist to a
    transcript.
    """

    @staticmethod
    def _child_env(auth_env: AuthEnv | None, host: dict[str, str]) -> dict[str, str]:
        with mock.patch.dict(os.environ, host, clear=True):
            return _build_child_env({}, auth_env)

    def test_codex_does_not_receive_moonshot_or_anthropic_secrets(self) -> None:
        env = self._child_env(
            CODEX_AUTH_ENV,
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "sk-openai",
                "CODEX_HOME": "/home/u/.codex",
                "MOONSHOT_API_KEY": "sk-moonshot",
                "ANTHROPIC_API_KEY": "sk-anthropic",
                "KIMI_MAX_POLICY": "read-only",
            },
        )
        assert env["OPENAI_API_KEY"] == "sk-openai"
        assert env["CODEX_HOME"] == "/home/u/.codex"
        assert "MOONSHOT_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "KIMI_MAX_POLICY" not in env

    def test_kimi_does_not_receive_codex_specific_variables(self) -> None:
        env = self._child_env(
            KIMI_AUTH_ENV,
            {
                "PATH": "/usr/bin",
                "MOONSHOT_API_KEY": "sk-moonshot",
                "OPENAI_API_KEY": "sk-openai",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
                "CODEX_HOME": "/home/u/.codex",
            },
        )
        assert env["MOONSHOT_API_KEY"] == "sk-moonshot"
        # kimi keeps the OpenAI *key* it has had since v1.0.0 (it is
        # multi-provider and can route to an OpenAI-compatible endpoint), but
        # not codex's wider namespace.
        assert env["OPENAI_API_KEY"] == "sk-openai"
        assert "OPENAI_BASE_URL" not in env
        assert "CODEX_HOME" not in env

    def test_base_variables_are_forwarded_to_everyone(self) -> None:
        host = {"PATH": "/usr/bin", "TMP": "/tmp", "USERPROFILE": "C:/Users/u"}
        for auth_env in (KIMI_AUTH_ENV, CODEX_AUTH_ENV, None):
            env = self._child_env(auth_env, host)
            assert env["PATH"] == "/usr/bin"
            assert env["TMP"] == "/tmp"
            assert env["USERPROFILE"] == "C:/Users/u"

    def test_no_declaration_forwards_no_credentials(self) -> None:
        # Fail-safe default: a caller that has not said what its agent needs
        # gets nothing sensitive rather than everything.
        env = self._child_env(
            None,
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "sk-openai",
                "API_KEY": "sk-generic",
            },
        )
        assert "OPENAI_API_KEY" not in env
        assert "API_KEY" not in env
        assert env["PATH"] == "/usr/bin"

    def test_unrelated_host_secrets_are_never_forwarded(self) -> None:
        env = self._child_env(
            CODEX_AUTH_ENV,
            {"PATH": "/usr/bin", "AWS_SECRET_ACCESS_KEY": "aws", "GITHUB_TOKEN": "gh"},
        )
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "GITHUB_TOKEN" not in env

    def test_kimi_keeps_the_credentials_it_had_before_scoping(self) -> None:
        """Per-agent scoping must not silently narrow an existing agent.

        kimi has received these since v1.0.0, and the installed CLI bundles an
        OpenAI client that reads OPENAI_API_KEY — a multi-provider config routed
        through `-m` can authenticate from it. Dropping it under cover of a
        hardening change would break those runs mid-review.
        """
        assert KIMI_AUTH_ENV.allows("KIMI_ANYTHING")
        assert KIMI_AUTH_ENV.allows("ANTHROPIC_API_KEY")
        assert KIMI_AUTH_ENV.allows("MOONSHOT_API_KEY")
        assert KIMI_AUTH_ENV.allows("API_KEY")
        assert KIMI_AUTH_ENV.allows("OPENAI_API_KEY")
        # The exact name only — not codex's whole namespace.
        assert not KIMI_AUTH_ENV.allows("OPENAI_BASE_URL")
        assert not KIMI_AUTH_ENV.allows("CODEX_HOME")
