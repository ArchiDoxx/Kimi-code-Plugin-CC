---
name: planning-loop
description: Iteratively build or refine a plan with an external agent, stopping when the plan converges or the iteration limit is reached.
---

# planning-loop

Use this skill when a task needs a structured plan before implementation.

## When to use

- Breaking down a complex feature.
- Comparing implementation options.
- Creating a step-by-step execution plan.

## How to use

1. Describe the task or goal.
2. The loop asks the registered planning agent for a plan.
3. The plan is refined up to `max_iterations` times or until it stops changing.

## Parameters

- `agent_name`: registered agent to use (default `kimi`).
- `prompt`: task description.
- `max_iterations`: maximum refinement rounds (default `3`).

## Example

```
/planning-loop agent_name=kimi prompt="Design the storage module for sensor readings" max_iterations=3
```
