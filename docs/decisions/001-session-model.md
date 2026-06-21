# ADR-001: Session Model — Spawn vs ACP vs `kimi server`

## Status

Accepted — v0.5 uses per-turn spawn; ACP reserved for v0.6.

## Context

The plugin needs to communicate with the Kimi Code CLI. Several options exist:

1. **Per-turn spawn**: start `kimi -p "<prompt>" --output-format stream-json` for every request.
2. **Agent Communication Protocol (ACP)**: long-lived `kimi acp` session mediating tool calls.
3. **`kimi server`**: persistent server mode (if available).

## Decision

Use **per-turn spawn** for v0.5 and plan **ACP** for v0.6.

## Consequences

### Per-turn spawn

- **Pros**: simple, stateless, no long-lived process management, easy to test, no ACP/auth complexity.
- **Cons**: no interactive permission mediation during a turn, higher per-request overhead.

### ACP

- **Pros**: interactive tool-call approval, lower latency after warm-up, richer protocol.
- **Cons**: requires session lifecycle management, auth handling, and a stable ACP spec.

### `kimi server`

- **Pros**: potentially lowest latency.
- **Cons**: not verified as stable in Kimi 0.17.1; adds deployment complexity.

## Rationale

v0.5 priorities are correctness, testability, and security defaults. Per-turn spawn lets us pin the exact CLI command, enforce worktree isolation per process, and implement a depth guard via environment variables. ACP is the correct next step once the core protocol and security model are proven.

## Notes

- Command verified live with Kimi 0.18.0: `kimi -p "<prompt>" --output-format stream-json`.
- `--print`, `--final-message-only` do not exist; `-y`/`--yolo`/`--auto` exist
  but are auto-approve and are never injected by the bridge.
- 0.18.0 also ships `acp`, `server`, and `login` subcommands (ACP remains the
  v0.6 plan per the decision above).
- The per-turn spawn is implemented as a single **async** runner that adapters
  `await` (not `asyncio.run`), so it composes inside the MCP event loop. See
  ADR-004.
