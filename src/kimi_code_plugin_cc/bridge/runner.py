"""Async subprocess runner for headless CLI agents."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_DEPTH = 2
DEPTH_ENV_VAR = "KIMI_BRIDGE_DEPTH"


@dataclass(frozen=True)
class RunResult:
    """Result of a spawned agent process."""

    returncode: int
    stdout: str
    stderr: str
    args: list[str]
    env: dict[str, str]


def _get_current_depth(env: dict[str, str] | None = None) -> int:
    """Return the current bridge depth from the environment, defaulting to 0."""
    source = os.environ if env is None else env
    raw = source.get(DEPTH_ENV_VAR, "0")
    try:
        return int(raw)
    except ValueError:
        return 0


async def run_agent_process(
    args: list[str],
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_depth: int | None = None,
) -> RunResult:
    """Run a CLI agent asynchronously with depth-guard and timeout.

    The child environment receives ``KIMI_BRIDGE_DEPTH`` set to the current
    depth + 1. If that exceeds *max_depth* (default ``DEFAULT_MAX_DEPTH``),
    the call fails fast without spawning a process.
    """
    if not args:
        raise ValueError("args must not be empty")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    current_depth = _get_current_depth(merged_env)
    child_depth = current_depth + 1
    limit = DEFAULT_MAX_DEPTH if max_depth is None else max_depth
    if not (0 <= child_depth <= limit):
        raise RuntimeError(
            "Depth guard blocked spawn: "
            f"child depth {child_depth} exceeds limit {limit}"
        )
    merged_env[DEPTH_ENV_VAR] = str(child_depth)

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
        shell=False,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise TimeoutError(
            f"Agent process timed out after {timeout}s (possible auth hang): "
            f"{' '.join(args)}"
        ) from None

    return RunResult(
        returncode=process.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        args=args,
        env=merged_env,
    )
