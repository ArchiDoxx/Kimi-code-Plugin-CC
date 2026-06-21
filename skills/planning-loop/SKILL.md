---
name: planning-loop
description: Iteratively build or refine a plan with an external CLI agent, refining up to a max-iteration budget. Use when a task needs a structured plan before implementation.
---

# planning-loop

Use this skill when a task needs a structured plan before implementation.

## When to use

- Breaking down a complex feature.
- Comparing implementation options.
- Creating a step-by-step execution plan.

## How to use

Call the MCP tool `mcp__kimi-code-plugin-cc__run_planning_loop` with:

- `agent_name`: registered agent to use (default `kimi`).
- `prompt`: the task description.
- `max_iterations`: maximum refinement rounds (default 3).

The first iteration asks the agent to create a plan; each later iteration asks
it to refine the previous plan. The loop always returns the final plan, with
`status` = `max_iterations` when the budget is exhausted.

## Returns

JSON: `plan`, `iterations`, `status`, `final_message`.

## Example

Call `mcp__kimi-code-plugin-cc__run_planning_loop` with
`agent_name="kimi"`, `prompt="Design the storage module for sensor readings"`,
`max_iterations=3`.
