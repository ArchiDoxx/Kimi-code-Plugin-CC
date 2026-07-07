---
name: santa-loop
description: Adversarial dual-review. Fail-closed — returns green ONLY when two independent reviewers both approve. Use for security-critical or safety-critical code where a false 'looks fine' is costly.
---

# santa-loop — adversarial dual-review (fail-closed)

Use this for **high-stakes** reviews where two independent reviewers must agree
before the target is declared safe. The loop returns `green` only when BOTH
approve; on disagreement or non-convergence it fails closed to `red`.

## When to use

- Security-critical code (auth, crypto, input validation, RBAC).
- Safety-critical logic (anything where a missed bug causes real harm).
- Any review where a false "looks fine" is more costly than a false alarm.

For a single pass use `code-review`; for multi-round single-reviewer use
`review-loop`.

## How it works

The primary external agent reviews the target. A second, **independent**
reviewer also reviews it. The loop returns `green` only if BOTH approve; on
disagreement the primary gets up to `max_iterations` revision rounds, then it
fails closed to `red`.

Two ways to supply reviewer #2:

1. **External adversary (MCP tool only):** call
   `mcp__kimi-code-plugin-cc__run_santa_loop`. Reviewer #2 is an independent,
   adversarially-framed re-run of the same external agent. Fully automated but
   **homogeneous** (Kimi reviewing Kimi) — weaker against shared blind spots.

2. **Claude as heterogeneous reviewer #2 (recommended):** run
   `/kimi-review <target> --loop santa`. The command calls the MCP tool for the
   external verdict, then **independently reviews the same target itself
   (Claude)** and reports `green` only if BOTH its own verdict AND the tool's
   verdict approve. This is the true heterogeneous dual-review.

## How to use

```
/kimi-review <target> --loop santa
```

`<target>` is a file path (the command reads it) or inline code.

## Returns

JSON: `verdict` (`green`/`red`), `primary_review`, `secondary_review`,
`iterations`, `explanation`.

## Safety rule (non-negotiable)

**Fail-closed.** If reviewers disagree, or do not converge within
`max_iterations`, or produce no output, the verdict is `red` — never `green`.
The no-output sentinel (`needs_discussion: ...`) never counts as approval.

## Example

```
/kimi-review src/kimi_code_plugin_cc/security/policy.py --loop santa
```
