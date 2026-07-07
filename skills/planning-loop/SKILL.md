---
name: planning-loop
description: Iteratively build or refine a plan with an external agent, refining up to a max-iteration budget. Use when a complex task needs a structured plan from a second perspective before implementation.
---

# planning-loop — iterative external planning

Use this when a task needs a **structured plan** and you want an external agent
to build and refine it before you implement. The first iteration creates a
plan; later iterations refine it. Stops early if a refinement round returns the
same plan (convergence).

## When to use

- Breaking down a complex feature before coding.
- Comparing implementation options with a second perspective.
- Getting an external agent's step-by-step execution plan.

For a quick design opinion (not a full plan), use `second-opinion`.

## How to use

**MCP tool:**

```
mcp__kimi-code-plugin-cc__run_planning_loop(
  agent_name="kimi",
  prompt="<task description>",
  max_iterations=3
)
```

There is no dedicated slash command for planning — call the MCP tool directly,
or use `/kimi-run` with a planning-style prompt for a single-pass plan.

If the task references files, include their **contents** in the prompt — the
agent runs in an isolated worktree and cannot open host paths.

## Returns

JSON: `plan`, `iterations`, `status` (`complete` | `max_iterations`),
`final_message`.

- `complete` — the plan converged (a refinement round returned the same plan).
- `max_iterations` — the budget was exhausted; the last plan is returned.

## Example

```
mcp__kimi-code-plugin-cc__run_planning_loop(
  agent_name="kimi",
  prompt="Design the storage module for sensor readings with a repository pattern and raw SQL. The schema must support stale detection.",
  max_iterations=3
)
```
