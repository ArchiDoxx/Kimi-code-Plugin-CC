---
name: bridge
description: Spawn a headless CLI agent (Kimi Code, Codex, ...) and return its parsed output via the bridge MCP server. Use for a one-off answer from an external agent, not an iterative loop.
---

# bridge

Use this skill to run a single headless CLI agent as a subagent inside Claude Code.

## When to use

- You need a one-off answer from Kimi Code or another registered CLI agent.
- You do not need an iterative review or planning loop (use `review-loop`,
  `santa-loop`, or `planning-loop` for those).

## How to use

Two equivalent entry points:

- Slash command: `/kimi-run [agent-name] "<prompt>"`
- MCP tool directly: call `mcp__kimi-code-plugin-cc__run_agent` with
  `agent_name` (default `kimi`), `prompt`, and optional `approval_policy`.

Registered agents in v0.5: `kimi` (working), `codex` (skeleton, raises
NotImplementedError). The adapter resolves the executable via PATH (so Windows
`kimi.CMD` is found), spawns `kimi -p "<prompt>" --output-format stream-json`,
and parses assistant content out of the stream.

## Safety defaults

- Default approval policy is `read-only`; the server caps any request at
  `KIMI_MAX_POLICY`.
- `--yolo`, `-y`, `--auto`, `--afk` are never passed automatically.
- Each spawn runs in an isolated temporary worktree (under the system temp dir,
  not the host repo). Override the base with `KIMI_WORKTREE_BASE`.
- Recursion depth is tracked via `KIMI_BRIDGE_DEPTH`; spawning beyond
  `DEFAULT_MAX_DEPTH` (2) is blocked before any process starts.

## Example

```
/kimi-run kimi "Explain the bridge module in src/kimi_code_plugin_cc/bridge"
```
