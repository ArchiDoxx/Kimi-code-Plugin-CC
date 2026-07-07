"""Tests for the thread-backed subprocess runner."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from kimi_code_plugin_cc.bridge.runner import (
    DEFAULT_MAX_DEPTH,
    DEPTH_ENV_VAR,
    RunResult,
    run_agent_process,
)


def _make_completed(
    stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> MagicMock:
    """Build a MagicMock mimicking subprocess.CompletedProcess."""
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = stdout
    completed.stderr = stderr
    completed.returncode = returncode
    return completed


class TestRunAgentProcess:
    async def test_runs_command_and_returns_result(self) -> None:
        completed = _make_completed(stdout=b"hello", stderr=b"", returncode=0)
        with patch(
            "kimi_code_plugin_cc.bridge.runner.subprocess.run",
            new=MagicMock(return_value=completed),
        ) as mock_run:
            result = await run_agent_process(["kimi", "-p", "hi"], timeout=5.0)

        assert isinstance(result, RunResult)
        assert result.returncode == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.args == ["kimi", "-p", "hi"]
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.args == (["kimi", "-p", "hi"],)
        assert call_args.kwargs["shell"] is False
        assert call_args.kwargs["stdin"] == subprocess.DEVNULL
        assert call_args.kwargs["stdout"] == subprocess.PIPE
        assert call_args.kwargs["stderr"] == subprocess.PIPE
        assert call_args.kwargs["timeout"] == 5.0

    async def test_increments_bridge_depth_in_child_env(self) -> None:
        completed = _make_completed()
        env = {DEPTH_ENV_VAR: "1"}
        with patch(
            "kimi_code_plugin_cc.bridge.runner.subprocess.run",
            new=MagicMock(return_value=completed),
        ) as mock_run:
            await run_agent_process(["kimi", "-p", "hi"], env=env, timeout=5.0)

        child_env = mock_run.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "2"

    async def test_blocks_when_child_depth_exceeds_limit(self) -> None:
        env = {DEPTH_ENV_VAR: str(DEFAULT_MAX_DEPTH)}
        with pytest.raises(RuntimeError, match="Depth guard blocked spawn"):
            await run_agent_process(["kimi", "-p", "hi"], env=env, timeout=5.0)

    async def test_respects_custom_max_depth(self) -> None:
        completed = _make_completed()
        env = {DEPTH_ENV_VAR: "3"}
        with patch(
            "kimi_code_plugin_cc.bridge.runner.subprocess.run",
            new=MagicMock(return_value=completed),
        ) as mock_run:
            await run_agent_process(
                ["kimi", "-p", "hi"], env=env, timeout=5.0, max_depth=5
            )

        child_env = mock_run.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "4"

    async def test_raises_on_empty_args(self) -> None:
        with pytest.raises(ValueError, match="args must not be empty"):
            await run_agent_process([], timeout=5.0)

    async def test_times_out_and_raises_timeouterror(self) -> None:
        # subprocess.run raises TimeoutExpired on timeout; the runner translates
        # it to TimeoutError so callers see a uniform signal.
        with (
            patch(
                "kimi_code_plugin_cc.bridge.runner.subprocess.run",
                new=MagicMock(
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["kimi", "-p", "hi"], timeout=0.01
                    )
                ),
            ),
            pytest.raises(TimeoutError, match="possible auth hang"),
        ):
            await run_agent_process(["kimi", "-p", "hi"], timeout=0.01)

    async def test_propagates_invalid_depth_as_zero(self) -> None:
        env = {DEPTH_ENV_VAR: "not-a-number"}
        completed = _make_completed()
        with patch(
            "kimi_code_plugin_cc.bridge.runner.subprocess.run",
            new=MagicMock(return_value=completed),
        ) as mock_run:
            await run_agent_process(["kimi"], env=env, timeout=5.0)

        child_env = mock_run.call_args.kwargs["env"]
        assert child_env[DEPTH_ENV_VAR] == "1"
