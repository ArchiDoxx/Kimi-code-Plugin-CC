---
description: Focused single-pass external code review of a file or inline code by Kimi Code. Daily-workhorse review command — concrete, prioritised findings in one shot.
argument-hint: <target file path or inline code>
allowed-tools: mcp__kimi-code-plugin-cc__run_agent, Read
---

Review a target with a focused single-pass external review.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Resolve the target:
   - If `$ARGUMENTS` looks like a file path that exists, **Read the file** and
     use its full contents as the code to review.
   - Otherwise treat `$ARGUMENTS` as inline code/text to review.
2. Build the review prompt by wrapping the target in this brief:

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
   <target contents>
   ---
   ```

3. Call `mcp__kimi-code-plugin-cc__run_agent` with:
   - `agent_name`: `kimi`
   - `prompt`: the review brief from step 2
   - `approval_policy`: `read-only`
4. Return the agent's review verbatim, then add a short prioritised summary:
   list the BLOCKER and MAJOR items first (quote the concrete fix), then a count
   of MINOR/NIT. If the agent returned the no-output sentinel, say so plainly
   — do NOT treat it as approval.

Safety:
- This is a single pass, not a loop. For iterative refinement use
  `/kimi-review <target> --loop review`. For adversarial dual-review use
  `/kimi-review <target> --loop santa`.
- Default policy is `read-only`; never escalate without explicit user approval.
