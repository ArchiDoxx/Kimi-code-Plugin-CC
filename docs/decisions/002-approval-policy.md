# ADR-002: Approval Policy and Capability Restriction

## Status

Accepted.

## Context

Headless agents can edit files, run commands, and make autonomous decisions. We need a capability model that defaults to the safest posture and can only be escalated by the host, not by the spawned agent or model.

## Decision

Introduce three ordered approval policies:

- `read-only`: agent may only read and report.
- `accept-edits`: agent may propose or apply edits within an isolated worktree.
- `explicit`: agent may request broader capabilities, subject to host confirmation.

The effective policy is capped by `KIMI_MAX_POLICY` (default `read-only`). The requested policy is never allowed to exceed this ceiling.

## Real Kimi Flags

Kimi Code 0.17.1 exposes:

- `-y` / `--yolo`: auto-approve tool calls.
- `--auto`: run without further confirmation.

Both are **opt-in** and default to `false`. The plugin **never** passes these flags automatically.

## Worktree Isolation

Every agent invocation creates a temporary directory via `create_isolated_worktree()`. The host repository is not directly exposed. Edits are only materialised back to the host through explicit, user-approved mounts or patches.

## Consequences

- Safe default behaviour for unattended or model-driven invocations.
- Explicit escalation path via `KIMI_MAX_POLICY` and host approval.
- Easy to audit: effective policy is part of every `AgentMessage`.
