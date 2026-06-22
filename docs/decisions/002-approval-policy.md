# ADR-002: Approval Policy and Capability Restriction

## Status

Accepted.

## Context

Headless agents can edit files, run commands, and make autonomous decisions. We need a capability model that defaults to the safest posture and can only be escalated by the host, not by the spawned agent or model.

## Decision

Introduce three ordered approval policies in the protocol data model:

- `read-only`: agent may only read and report.
- `accept-edits`: agent may propose or apply edits within an isolated worktree.
- `explicit`: agent may request broader capabilities, subject to host confirmation.

The effective policy is capped by `KIMI_MAX_POLICY` (default `read-only`). The requested policy is never allowed to exceed this ceiling.

### v0.5 scope note

In v0.5 the plugin is **read-only in practice**. `accept-edits` and `explicit` are declared in the `AgentMessage` schema so the protocol can carry them, but no v0.5 code path translates them into Kimi CLI flags or an edit mode. The spawned agent always runs as if the policy were `read-only`; any materialisation of edits back to the host requires an explicit, user-approved step outside the plugin. This ADR documents the intended future capability model, not a v0.5 feature switch.

## Real Kimi Flags

Kimi Code 0.18.0 exposes:

- `-y` / `--yolo`: auto-approve tool calls.
- `--auto`: run without further confirmation.

Both are **opt-in** and default to `false`. The plugin **never** passes these flags automatically.

## Worktree Isolation

Every agent invocation creates a temporary directory via `create_isolated_worktree()`. The host repository is not directly exposed. Edits are only materialised back to the host through explicit, user-approved mounts or patches.

## Consequences

- Safe default behaviour for unattended or model-driven invocations.
- Explicit escalation path via `KIMI_MAX_POLICY` and host approval.
- Easy to audit: effective policy is part of every `AgentMessage`.
