---
name: review-loop
description: Run an external agent in a review loop with a verdict (green/yellow/red) and iteration limit.
---

# review-loop

Use this skill to get structured feedback on code, a design, or any target artifact.

## When to use

- Code review before committing.
- Design review for a module or API.
- Checking a document or plan for issues.

## How to use

1. Provide the target (file path, code block, or description).
2. The loop asks the reviewer agent for a verdict and comments.
3. Iteration stops on `green` or after `max_iterations`.

## Parameters

- `agent_name`: registered reviewer agent (default `kimi`).
- `target`: the artifact to review.
- `max_iterations`: maximum review rounds (default `3`).

## Example

```
/review-loop agent_name=kimi target="src/kimi_code_plugin_cc/bridge/runner.py" max_iterations=2
```
