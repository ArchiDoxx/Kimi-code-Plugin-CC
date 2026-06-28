# ADR-005: Thread-backed subprocess runner (Windows MCP-server fix)

**Date:** 2026-06-28
**Status:** Accepted — applies in v1.0.0.

## Context

The v0.5 runner used `asyncio.create_subprocess_exec` directly inside the MCP
server's event loop. On Windows this deadlocks: the child process inherits the
parent's stdin pipe handle (owned by the MCP stdio transport) and blocks on it,
so `run_agent` never returns to the client. This was the single biggest blocker
to real integration readiness — the plugin validated and installed, but could
not actually spawn an agent.

## Decision

Spawn the agent via `subprocess.run` executed in a worker thread
(`asyncio.to_thread`), with:

- `stdin=subprocess.DEVNULL` — the child does not inherit the parent's stdin
  pipe handle, removing the deadlock.
- `stdout=subprocess.PIPE`, `stderr=subprocess.PIPE` — captured output.
- `creationflags=CREATE_NO_WINDOW` (Windows only) — no console window pops up
  for the spawned agent.
- A hard `timeout` translated to `TimeoutError` on expiry.

The async interface (`run_agent_process`) is preserved, so callers (adapters,
loops, MCP tools) are unchanged.

## Consequences

- Agents now run successfully inside the MCP server on Windows (verified
  end-to-end: official MCP client → server → `kimi -p` → `pong` in ~43s).
- The same code path works on POSIX (the `creationflags` are Windows-only and
  become `0`).
- Tests were updated from `create_subprocess_exec` mocks to `subprocess.run`
  mocks; the depth-guard and timeout semantics are unchanged.
- The previous `_UNUSED`/`_ANY` placeholder fields in the runner were removed.
