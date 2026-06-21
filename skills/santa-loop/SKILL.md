---
name: santa-loop
description: Adversarial dual-review loop. Fail-closed - returns green only when two independent reviewers both approve. Use for security-critical or safety-critical reviews where false negatives are costly.
---

# santa-loop

Use this skill for high-stakes reviews where two independent reviewers must agree before declaring something safe.

## When to use

- Security-critical code paths.
- Safety-critical logic (e.g. an alarm or assessment module).
- Any review where a false "looks fine" is costly.

## How it works

The primary external agent reviews the target. A second, independent reviewer
also reviews it. The loop returns `green` only if BOTH approve; on disagreement
the primary gets up to `max_iterations` revision rounds, then it fails closed to
`red`.

Two ways to supply reviewer #2:

1. **External adversary (MCP tool):** call
   `mcp__kimi-code-plugin-cc__run_santa_loop` with `primary_agent`, `target`,
   `max_iterations`. Reviewer #2 is an independent, adversarially-framed re-run
   of the external agent. This is fully automated but homogeneous.
2. **Claude as heterogeneous reviewer #2 (recommended, via `/kimi-review
   <target> --loop santa`):** run the tool for the external verdict, then
   independently review the same target YOURSELF and report `green` only if both
   your verdict and the tool's verdict approve. This is the true heterogeneous
   dual-review and best protects against shared blind spots.

## Returns

JSON: `verdict` (`green`/`red`), `primary_review`, `secondary_review`,
`iterations`, `explanation`.

## Safety rule

**Fail-closed.** If reviewers disagree or do not converge within
`max_iterations`, the verdict is `red`, never `green`. The no-output sentinel
(`needs_discussion: ...`) also never counts as approval.

## Example

```
/kimi-review src/kimi_code_plugin_cc/security/policy.py --loop santa
```
