# ADR-003: Recursion Guard

## Status

Accepted.

## Context

A spawned agent could invoke the plugin again, which could spawn another agent, leading to unbounded recursion and resource exhaustion. We need a defense-in-depth mechanism.

## Decision

Track recursion depth through two layers:

1. **Protocol layer**: every `AgentMessage` carries a non-negative `depth` field.
2. **Environment layer**: the runner injects `KIMI_BRIDGE_DEPTH=depth+1` into the child process environment.

The default depth limit is `2`. A process whose inherited depth already equals the limit is blocked from spawning another agent. The check is performed before subprocess creation (fail-fast).

## Mechanism

- Parent sets `KIMI_BRIDGE_DEPTH` to its own depth + 1 in the child env.
- Child reads the env on startup; any further bridge call sees this value.
- `run_agent_process()` rejects the spawn if `child_depth > max_depth`.

## Consequences

- Accidental or malicious self-spawning is stopped early.
- Depth is observable in logs and messages for debugging.
- Default limit keeps resource usage bounded while still allowing one level of nested delegation.
