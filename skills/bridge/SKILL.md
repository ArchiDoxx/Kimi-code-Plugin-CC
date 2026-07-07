---
name: bridge
description: One-shot call to a headless CLI agent (Kimi Code by default) via the bridge MCP server. Use for a single question or task that does NOT need an iterative loop. Lowest-overhead way to get an external agent's answer.
---

# bridge — one-shot external agent call

Use this when you need a **single** answer from Kimi Code (or another registered
CLI agent) — no iteration, no verdict, no convergence. Just: ask once, get the
answer back.

## When to use

- You want a quick explanation or task done by an external agent.
- You do NOT need a review verdict (use `code-review` / `review-loop`).
- You do NOT need an iterative refinement (use `review-loop` / `planning-loop`).

## How to use

Two equivalent entry points:

- **Slash command:** `/kimi-run [agent-name] "<prompt>"`
- **MCP tool directly:** `mcp__kimi-code-plugin-cc__run_agent` with
  `agent_name` (default `kimi`), `prompt`, and optional
  `approval_policy` (default `read-only`).

If the prompt references a file, **Read the file first** and include its
**contents** in the prompt. The agent runs in an isolated worktree and cannot
open arbitrary host paths.

## Registered agents

- `kimi` — working adapter (Kimi Code CLI).
- `codex` — **skeleton** in v1.0, raises `NotImplementedError`. Do not call
  unless you intentionally want the error.

## Safety defaults (always-on)

- `approval_policy` defaults to `read-only` and is capped by `KIMI_MAX_POLICY`.
- `--yolo`, `-y`, `--auto`, `--afk` are structurally never injected.
- Each spawn runs in an isolated worktree under the system temp dir.
- Recursion beyond `KIMI_BRIDGE_DEPTH` (default 2) is blocked before spawn.

## Example

```
/kimi-run kimi "Summarise what src/kimi_code_plugin_cc/security/policy.py does"
```

Via MCP tool:

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="Explain the depth-guard in this code:\n\n<contents of runner.py>"
)
```

With an explicit model alias (multi-provider setups; alias must exist in the
agent CLI's own config, e.g. `~/.kimi-code/config.toml`):

```
mcp__kimi-code-plugin-cc__run_agent(
  agent_name="kimi",
  prompt="...",
  model="zai-coding-plan/glm-5.2"
)
```

The loop tools (`run_review_loop`, `run_santa_loop`, `run_planning_loop`)
accept the same optional `model` parameter.
