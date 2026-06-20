# /kimi-review

Run a review loop or adversarial dual-review on a target artifact.

## Usage

```
/kimi-review <target> [--loop review|santa] [--agent <agent-name>]
```

## Options

- `target`: file path, code block, or description to review.
- `--loop`: `review` for a single-agent loop, `santa` for adversarial dual-review (default: `review`).
- `--agent`: primary reviewer agent (default: `kimi`).

## Examples

```
/kimi-review src/kimi_code_plugin_cc/security/policy.py
/kimi-review "the alarm threshold logic" --loop santa
```

## Safety notes

- `santa-loop` is fail-closed: it never returns `green` unless both reviewers agree.
- Default approval policy is `read-only`.
