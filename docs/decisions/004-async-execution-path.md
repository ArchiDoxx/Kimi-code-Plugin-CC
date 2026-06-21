# ADR-004: Single Async Execution Path

## Status

Accepted — supersedes the v0.5 adapter's `asyncio.run` approach.

## Context

The MCP server (FastMCP) executes tool functions **inside its own running event
loop**. The first v0.5 adapter called `asyncio.run(run_agent_process(...))`
synchronously inside `KimiCodeAdapter.run`. That works in unit tests but raises
`RuntimeError: asyncio.run() cannot be called from a running event loop` the
moment a loop tool (`run_review_loop`, `run_santa_loop`, `run_planning_loop`) is
invoked through MCP — i.e. exactly when the headline features are used.

There were also two execution code paths: the async `run_agent_process` in
`bridge/runner.py` (depth-guarded, timeout-capped) and a separate synchronous
`subprocess.run` reimplementation in the adapter. They could drift.

## Decision

Make the whole call chain async and collapse to **one** execution path:

- `AgentAdapter.run` is `async def`.
- `KimiCodeAdapter.run` `await`s `bridge.runner.run_agent_process` directly
  (no `asyncio.run`). The runner is the single place that spawns a process,
  injects `KIMI_BRIDGE_DEPTH`, enforces the depth guard, applies the timeout,
  and sets the isolated `cwd`.
- `review_loop`, `santa_loop`, `planning_loop` are `async def` and `await` the
  adapter.
- The MCP tools are `async def` and `await` the loops.
- `santa_loop`'s `host_reviewer` callback may be sync or async; it is awaited
  via `inspect.isawaitable`.

## Consequences

- Loops are callable through MCP without event-loop conflicts (verified live:
  `run_agent` and `run_review_loop` complete a real Kimi round-trip in-process).
- One spawn implementation; the depth guard (`assert_spawn_allowed`) is shared
  between the runner and the adapter's cheap pre-check.
- Tests use `pytest-asyncio` (`asyncio_mode = "auto"`); adapter stubs are
  `async def run`, and `run_agent_process` is patched with `AsyncMock`.

## Related

- Robustness: the stream-json parser tolerates single-event, multi-event
  `tool_calls`, and plain-prose output; empty output yields a fail-safe
  `needs_discussion` sentinel rather than crashing or reading as approval.
- ADR-001 (per-turn spawn), ADR-002 (approval policy), ADR-003 (recursion guard).
