---
name: bridge
description: Spawn a headless CLI agent (Kimi Code, Codex, ...) and return its stream-json output via the protocol layer.
---

# bridge

Use this skill to run a single headless CLI agent as a subagent inside Claude Code.

## When to use

- You need a one-off answer from Kimi Code or another registered CLI agent.
- You do not need an iterative review or planning loop.

## How to use

1. Choose a registered agent name (run `/kimi-run --list` to see options).
2. Use the slash command `/kimi-run <agent-name> "<prompt>"`.
3. The agent is spawned with `--output-format stream-json`, parsed, and returned.

## Safety defaults

- Default approval policy is `read-only`.
- `--yolo` and `--auto` are never passed automatically.
- Each spawn runs in an isolated temporary worktree.
- Recursion depth is tracked; self-spawning beyond the configured limit is blocked.

## Example

```
/kimi-run kimi "Explain the bridge module in src/kimi_code_plugin_cc/bridge"
```
