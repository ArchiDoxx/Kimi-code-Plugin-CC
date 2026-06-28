---
name: code-review
description: Focused single-pass external code review. Sends code + a structured review brief to Kimi Code and returns concrete, prioritised findings. The daily-workhorse skill for reviewing a file, function, or diff. Use /kimi-code-review <target>.
---

# code-review — external single-pass code review

Use this for **one thorough review pass** of a file, function, or diff by an
external agent. Unlike `review-loop` (multi-round convergence) or `santa-loop`
(adversarial dual-review), this is a single focused call: fast, concrete,
opinionated. It is the skill behind `/kimi-code-review`.

## When to use

- Before committing or opening a PR — get a second pair of eyes.
- Reviewing a function or module you just wrote or refactored.
- You want concrete, prioritised findings in one shot (not a multi-round loop).

**Do NOT use** for safety-critical code where two independent reviewers must
agree — use `santa-loop` instead. For multi-round iterative refinement, use
`review-loop`.

## How to use

**Slash command (preferred):**

```
/kimi-code-review <target>
```

`<target>` is a file path (the command reads it for you) or inline code.

**MCP tool directly:** call `mcp__kimi-code-plugin-cc__run_agent` with
`agent_name="kimi"` and a prompt built from the template below.

## The review brief (what gets sent)

The skill/command wraps the target in a focused review brief so the external
agent returns structured, prioritised output rather than vague prose:

```
You are a senior code reviewer. Review the code below for real issues only —
no style nitpicks. Be concrete and prioritise.

For each finding, output:
  - Severity: BLOCKER / MAJOR / MINOR / NIT
  - Location: function/line reference
  - Issue: one sentence
  - Fix: one sentence or a snippet

Categories to check: correctness bugs, edge cases, error handling, security
(input validation, injection, secrets), resource leaks, concurrency, API
misuse. If you find nothing wrong, say so explicitly — do not invent issues.

Code:
---
<target contents here>
---
```

The external agent's raw response is the review. Summarise the BLOCKER/MAJOR
items for the user and quote the concrete fixes.

## Safety defaults

- `read-only` policy (agent cannot modify the repo).
- Agent runs in an isolated worktree — that is why you must pass **contents**,
  not a host path.
- Empty/no-output from the agent is surfaced as-is, never as "approved".

## Example

```
/kimi-code-review src/kimi_code_plugin_cc/bridge/runner.py
```
