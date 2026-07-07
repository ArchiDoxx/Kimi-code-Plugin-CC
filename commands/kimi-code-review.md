---
description: Focused single-pass external code review of a file or inline code by Kimi Code. Daily-workhorse review command — concrete, prioritised findings in one shot.
argument-hint: <target file path or inline code> [<model-alias>]
allowed-tools: mcp__kimi-code-plugin-cc__run_agent, Read
---

Review a target with a focused single-pass external review.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Extract the model selector (optional):
   - If `$ARGUMENTS` ends with a standalone bracketed token like `[glm-4.6]`
     or `[zai-coding-plan/glm-5.2]`, the bracket contents are a model alias
     from the agent CLI's own config (for kimi: `~/.kimi-code/config.toml`).
     Strip the token from the arguments and remember the alias.
   - Aliases must match `[A-Za-z0-9][A-Za-z0-9._:/-]*` (no whitespace, no
     leading `-`). If the user wrote a loose name like `[GLM 4.6]`, normalize
     it (lowercase, spaces → `-`, e.g. `glm-4.6`) and state which alias you
     passed. Do NOT treat brackets that are part of inline code (e.g. `x[i]`)
     as a model selector.
   - `--model <alias>` anywhere in the arguments is equivalent.
2. Resolve the target from the remaining arguments:
   - If they look like a file path that exists, **Read the file** and
     use its full contents as the code to review.
   - Otherwise treat them as inline code/text to review.
3. Build the review prompt by wrapping the target in this brief:

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

4. Call `mcp__kimi-code-plugin-cc__run_agent` with:
   - `agent_name`: `kimi`
   - `prompt`: the review brief from step 3
   - `approval_policy`: `read-only`
   - `model`: only when a model selector was given in step 1 (omitted = the
     CLI's default model)
5. Return the agent's review verbatim, then add a short prioritised summary:
   list the BLOCKER and MAJOR items first (quote the concrete fix), then a count
   of MINOR/NIT. If the agent returned the no-output sentinel, say so plainly
   — do NOT treat it as approval.

Examples:
- `/kimi-code-review src/foo.py` — review on the CLI's default model.
- `/kimi-code-review src/foo.py [glm-4.6]` — same review routed to the
  `glm-4.6` alias from the agent CLI's config.

Safety:
- This is a single pass, not a loop. For iterative refinement use
  `/kimi-review <target> --loop review`. For adversarial dual-review use
  `/kimi-review <target> --loop santa`.
- Default policy is `read-only`; never escalate without explicit user approval.
