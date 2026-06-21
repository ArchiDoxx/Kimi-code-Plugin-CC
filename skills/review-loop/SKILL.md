---
name: review-loop
description: Run an external agent in an iterative review loop that returns a verdict (approve / request_changes / needs_discussion) and stops early on approval. Use for code or design review by a headless CLI agent.
---

# review-loop

Use this skill to get structured feedback on code, a design, or any target artifact from an external CLI agent.

## When to use

- Code review before committing.
- Design review for a module or API.
- Checking a document or plan for issues.

For high-stakes paths where two independent reviewers must agree, use
`santa-loop` instead.

## How to use

- Slash command: `/kimi-review <target> --loop review [--agent <name>]`
- MCP tool directly: call `mcp__kimi-code-plugin-cc__run_review_loop` with
  `agent_name` (default `kimi`), `target`, and `max_iterations` (default 3).

If the target is a file, pass its **contents** (not just the path): the agent
runs in an isolated worktree and cannot open arbitrary host paths. The
`/kimi-review` command reads the file for you.

The loop asks for a verdict + comments and stops early on `approve`; otherwise
it returns the last review, defaulting to `needs_discussion` when no verdict can
be parsed.

## Returns

JSON: `review`, `verdict`, `iterations`, `final_message`.

## Example

```
/kimi-review src/kimi_code_plugin_cc/bridge/runner.py --loop review
```
