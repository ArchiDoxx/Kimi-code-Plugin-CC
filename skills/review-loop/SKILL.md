---
name: review-loop
description: Multi-round external code review that iterates up to a max-iteration budget, stopping early on 'approve'. Use when one review pass is not enough and you want the reviewer to refine its findings against the same target.
---

# review-loop — iterative external review

Use this when a **single** review pass (`code-review`) is not enough and you
want the external reviewer to **refine** its findings over multiple rounds
against the same target. The loop stops early on `approve`; otherwise it
returns the last review.

## When to use

- The target is subtle and one pass may miss issues.
- You want the reviewer to reconsider after seeing its own first draft.
- You do NOT need two independent reviewers (that's `santa-loop`).

For a single fast pass, use `code-review`. For safety-critical paths where two
reviewers must agree, use `santa-loop`.

## How to use

**Slash command:**

```
/kimi-review <target> --loop review [--agent <name>] [<model-alias>]
```

A trailing bracketed token like `[glm-4.6]` routes **every round** of the loop
to that model alias from the agent CLI's own config; omitted = the CLI's
default model.

**MCP tool:**

```
mcp__kimi-code-plugin-cc__run_review_loop(
  agent_name="kimi",     # default reviewer
  target="<code or description>",  # pass CONTENTS, not a path
  max_iterations=3,      # default
  model="glm-4.6"        # optional; omitted = CLI default
)
```

If the target is a file, **Read it first** and pass its **contents** as
`target`. The agent runs in an isolated worktree and cannot open host paths.
The `/kimi-review` command does this for you.

## Verdict semantics (fail-closed)

Each round, the reviewer's text is parsed for a verdict:

- `approve` → loop stops, returns `approve`.
- `request_changes` → another round (if budget left).
- `needs_discussion` → another round (if budget left).
- **nothing parseable** → defaults to `needs_discussion` (never `approve`).

Non-approve verdicts take precedence — an ambiguous review never reads as
approval.

## Returns

JSON: `review`, `verdict`, `iterations`, `final_message`.

## Example

```
/kimi-review src/kimi_code_plugin_cc/security/policy.py --loop review
```
