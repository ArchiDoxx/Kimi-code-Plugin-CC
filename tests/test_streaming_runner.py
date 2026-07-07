"""Tests for the sentinel-based early-exit path of the subprocess runner.

These tests spawn real child processes (``sys.executable``) instead of mocking
``subprocess`` because the streaming path's whole point is real pipe/thread
behaviour: a child that answers on stdout but never exits (the Kimi
MCP-server hang, see memory ``kimi-review-timeout-non-exit``).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from kimi_code_plugin_cc.bridge.runner import RunResult, run_agent_process

SENTINEL_EVENT = json.dumps(
    {"role": "meta", "type": "session.resume_hint", "content": "resume"}
)

# Generous wall-clock bound for "returned early": far below the child's sleep
# and the runner timeout, far above process-spawn overhead on slow CI.
EARLY_RETURN_BOUND_SECONDS = 30.0


def _is_sentinel(line: str) -> bool:
    if "session.resume_hint" not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and obj.get("type") == "session.resume_hint"


def _child_argv(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "child.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


ANSWER_THEN_HANG = f"""
import time
print('{{"role": "assistant", "content": "OK"}}', flush=True)
print('{SENTINEL_EVENT}', flush=True)
time.sleep(300)
"""

ANSWER_THEN_EXIT = """
print("hello from child", flush=True)
"""

PARTIAL_THEN_HANG = """
import time
print("partial-line", flush=True)
time.sleep(300)
"""


class TestEarlyExitOnSentinel:
    async def test_returns_on_sentinel_while_child_still_running(
        self, tmp_path: Path
    ) -> None:
        args = _child_argv(tmp_path, ANSWER_THEN_HANG)
        start = time.monotonic()
        result = await run_agent_process(
            args, timeout=120.0, early_exit_check=_is_sentinel
        )
        elapsed = time.monotonic() - start

        assert elapsed < EARLY_RETURN_BOUND_SECONDS
        assert result.early_exit is True
        assert '"content": "OK"' in result.stdout
        assert "session.resume_hint" in result.stdout

    async def test_natural_exit_without_sentinel_keeps_normal_semantics(
        self, tmp_path: Path
    ) -> None:
        args = _child_argv(tmp_path, ANSWER_THEN_EXIT)
        result = await run_agent_process(
            args, timeout=60.0, early_exit_check=_is_sentinel
        )

        assert result.returncode == 0
        assert result.early_exit is False
        assert "hello from child" in result.stdout

    async def test_timeout_without_sentinel_raises_with_partial_output(
        self, tmp_path: Path
    ) -> None:
        args = _child_argv(tmp_path, PARTIAL_THEN_HANG)
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="partial-line"):
            await run_agent_process(args, timeout=3.0, early_exit_check=_is_sentinel)
        assert time.monotonic() - start < EARLY_RETURN_BOUND_SECONDS

    async def test_sentinel_in_first_line_still_captures_it(
        self, tmp_path: Path
    ) -> None:
        body = f"""
import time
print('{SENTINEL_EVENT}', flush=True)
time.sleep(300)
"""
        args = _child_argv(tmp_path, body)
        result = await run_agent_process(
            args, timeout=120.0, early_exit_check=_is_sentinel
        )
        assert result.early_exit is True
        assert "session.resume_hint" in result.stdout


class TestRunResultCompat:
    def test_early_exit_defaults_to_false(self) -> None:
        result = RunResult(returncode=0, stdout="", stderr="", args=[], env={})
        assert result.early_exit is False
