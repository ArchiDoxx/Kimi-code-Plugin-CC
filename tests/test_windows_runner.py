"""Tests for the Windows-aware subprocess runner."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kimi_code_plugin_cc.bridge.runner import (
    DEFAULT_MAX_DEPTH,
    DEPTH_ENV_VAR,
    RunResult,
    run_agent_process,
)


def _make_process(
    stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> AsyncMock:
    proc = AsyncMock(spec=asyncio.subprocess.Process)
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


class TestRunAgentProcess:
    async def test_runs_command_and_returns_result(self) -> None:
        proc = _make_process(stdout=b"hello", stderr=b"", returncode=0)
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            result = await run_agent_process(["kimi", "-p", "hi"], timeout=5.0)

        assert isinstance(result, RunResult)
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.args == ["kimi", "-p", "hi"]
        mock_exec.assert_awaited_once_with(
            "kimi",
            "-p",
            "hi",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=mock_exec.call_args.kwargs["env"],
            shell=False,
        )

    async def test_increments_bridge_depth_in_child_env(self) -> None:
        proc = _make_process()
        env = {DEPTH_ENV_VAR: "1"}
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            await run_agent_process(["kimi", "-p", "hi"], env=env, timeout=5.0)

        child_env = mock_exec.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "2"

    async def test_blocks_when_child_depth_exceeds_limit(self) -> None:
        env = {DEPTH_ENV_VAR: str(DEFAULT_MAX_DEPTH)}
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            await run_agent_process(["kimi", "-p", "hi"], env=env, timeout=5.0)

    async def test_respects_custom_max_depth(self) -> None:
        proc = _make_process()
        env = {DEPTH_ENV_VAR: "3"}
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            await run_agent_process(
                ["kimi", "-p", "hi"], env=env, timeout=5.0, max_depth=5
            )

        child_env = mock_exec.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "4"

    async def test_raises_on_empty_args(self) -> None:
        with pytest.raises(ValueError, match="args must not be empty"):
            await run_agent_process([], timeout=5.0)

    async def test_times_out_and_kills_hanging_process(self) -> None:
        async def _hang(*_args, **_kwargs) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        proc = _make_process()
        proc.communicate = AsyncMock(side_effect=_hang)
        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
            pytest.raises(TimeoutError, match="possible auth hang"),
        ):
            await run_agent_process(["kimi", "-p", "hi"], timeout=0.01)

        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_propagates_invalid_depth_as_zero(self) -> None:
        env = {DEPTH_ENV_VAR: "not-a-number"}
        proc = _make_process()
        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)
        ) as mock_exec:
            await run_agent_process(["kimi"], env=env, timeout=5.0)

        child_env = mock_exec.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "1"
