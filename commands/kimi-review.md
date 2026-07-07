---
description: Review a target with a single-agent loop, or an adversarial dual-review (santa) where Claude is the heterogeneous second reviewer. For a single fast pass use /kimi-code-review instead.
argument-hint: <target> [--loop review|santa] [--agent <name>] [<model-alias>]
allowed-tools: mcp__kimi-code-plugin-cc__run_review_loop, mcp__kimi-code-plugin-cc__run_santa_loop, Read
---

Review a target artifact through an iterative or adversarial loop.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Parse arguments:
   - Model selector (optional): a trailing standalone bracketed token like
     `[glm-4.6]` or `[zai-coding-plan/glm-5.2]` selects a model alias from the
     agent CLI's own config (for kimi: `~/.kimi-code/config.toml`) for every
     round of the loop. Strip it from the target. Aliases must match
     `[A-Za-z0-9][A-Za-z0-9._:/-]*` (no whitespace); normalize loose names
     like `[GLM 4.6]` → `glm-4.6` and state which alias you passed. Do NOT
     treat brackets that are part of inline code (e.g. `x[i]`) as a model
     selector. `--model <alias>` is equivalent.
   - `target`: everything that is not a flag. If it looks like a file path,
     **Read the file** and use its contents as the target text (so the external
     agent gets the code, not just a path it cannot open in its worktree).
   - `--loop`: `review` (default) or `santa`.
   - `--agent`: primary reviewer (default `kimi`).
2. Dispatch (pass `model` to the tool only when a model selector was given;
   omitted = the CLI's default model):
   - For `review`: call `mcp__kimi-code-plugin-cc__run_review_loop` with
     `agent_name`, `target`, `max_iterations=3`.
   - For `santa`: call `mcp__kimi-code-plugin-cc__run_santa_loop` with
     `primary_agent`, `target`, `max_iterations=3`. The model applies to the
     external primary AND the external adversary — not to your own
     (Claude's) independent review. Note: this MCP tool uses an
     external adversary as reviewer #2. For a TRUE heterogeneous dual-review,
     after the tool returns, **independently review the same target YOURSELF
     (Claude)** and only report an overall `green` if BOTH your verdict and the
     tool's verdict are approve. Otherwise report fail-closed (`red`).
3. Parse the returned JSON and summarise: verdict, iterations, and the key
   findings. Quote concrete issues.

Examples:
- `/kimi-review src/foo.py --loop review` — default model.
- `/kimi-review src/foo.py --loop santa [zai-coding-plan/glm-5.2]` — both
  external reviewers run on the `zai-coding-plan/glm-5.2` alias.

Safety:
- `santa` is fail-closed: never report `green` unless both reviewers approve.
- Default approval policy is `read-only`.
- If the agent returns the no-output sentinel (`needs_discussion: agent
  returned no parseable output`), treat it as fail-safe, not approval.
- For a single fast review pass (no loop), use `/kimi-code-review` instead.
