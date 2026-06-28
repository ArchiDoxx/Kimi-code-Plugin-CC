"""Tests for the bridge layer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from kimi_code_plugin_cc.bridge.parser import (
    parse_stream_json,
    parse_stream_json_async,
)
from kimi_code_plugin_cc.bridge.runner import (
    DEPTH_ENV_VAR,
    RunResult,
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
