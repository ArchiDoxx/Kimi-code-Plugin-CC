"""Bridge layer for spawning and parsing headless agent output."""

from kimi_code_plugin_cc.bridge.parser import parse_stream_json
from kimi_code_plugin_cc.bridge.runner import (
    RunResult,
    assert_spawn_allowed,
    run_agent_process,
)
from kimi_code_plugin_cc.bridge.session import Session

__all__ = [
    "RunResult",
    "Session",
    "assert_spawn_allowed",
    "parse_stream_json",
    "run_agent_process",
]
