---
name: santa-loop
description: Adversarial dual-review loop with heterogeneous reviewers. Fail-closed: never returns green unless both reviewers agree.
---

# santa-loop

Use this skill for high-stakes reviews where two independent reviewers must agree before declaring something safe.

## When to use

- Security-critical code paths.
- Bewertungslogik or alarm logic.
- Any review where false negatives are costly.

## How to use

1. Provide the target artifact.
2. The primary external agent reviews it.
3. Claude itself acts as the second, heterogeneous reviewer.
4. The loop only returns `green` if both reviewers agree; otherwise it fails closed.

## Parameters

- `primary_agent`: registered agent that performs the primary review (default `kimi`).
- `target`: the artifact to review.
- `max_iterations`: maximum rounds (default `3`).

## Example

```
/santa-loop primary_agent=kimi target="src/kimi_code_plugin_cc/security/policy.py" max_iterations=3
```

## Safety rule

`santa-loop` is **fail-closed**. If reviewers disagree or do not converge within `max_iterations`, the result is `block` or `yellow`, never `green`.
