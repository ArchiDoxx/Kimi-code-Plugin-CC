# Security policy

This plugin spawns a third-party CLI agent as a child process and feeds it text
you supply. That makes its security posture worth stating explicitly rather than
leaving it implied by the code.

## What the plugin guarantees

| Guarantee | How it is enforced |
|---|---|
| No auto-approve flags | `--yolo`, `-y`, `--auto`, `--afk` are never constructed. They cannot be reached through configuration or a model-supplied value. |
| Read-only by default | `KIMI_MAX_POLICY` caps every request. A policy above `read-only` is refused with an error rather than recorded as granted but never enforced. |
| Filesystem isolation | Each turn runs in a fresh directory under the system temp dir (`KIMI_WORKTREE_BASE` to relocate). The agent never gets the host repository as its working directory. |
| No secret forwarding | The child environment is built from an allowlist, not inherited wholesale. Only `PATH`-class variables and `KIMI_*` / `ANTHROPIC_*` / `MOONSHOT_*` auth variables are passed through. |
| No flag injection | A `model` value is validated against `[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}`, so it can never begin with `-` or contain whitespace, and cannot smuggle a flag into the argv. |
| Bounded input | Prompts are capped (`KIMI_MAX_PROMPT_CHARS`, default 30000) so an oversized paste fails with a clear message instead of an opaque spawn error. |
| Bounded recursion | `KIMI_BRIDGE_DEPTH` (default 2) caps nested agent spawns, so a prompt cannot fan out into an unbounded swarm. |
| No process orphans | On completion or timeout the whole child process tree is terminated (`taskkill /T` on Windows, `killpg` on POSIX), so MCP servers the agent started do not survive it. |
| Fail-closed verdicts | Unparseable, ambiguous, empty, or crashed reviews resolve to a non-approval. No failure path can produce `approve` or `green`. |

Run `kimi-code-plugin doctor` to see the effective values on your machine.

## What it does not guarantee

- **The agent's own behaviour.** Once spawned, the CLI runs under its own
  configuration, credentials, and network access. This plugin constrains how it
  is *invoked*, not what the vendor's binary does.
- **Prompt confidentiality.** Whatever you pass as a target is sent to the
  agent's provider. Do not paste secrets into a review.
- **Trust in review output.** A review is advice from a third-party model.
  `santa-loop` exists precisely because a single "looks fine" is not evidence.

## Reporting a vulnerability

Open a [security advisory](https://github.com/ArchiDoxx/Kimi-code-Plugin-CC/security/advisories/new)
rather than a public issue. Include the version (`kimi-code-plugin doctor`), the
platform, and a reproduction.

Please report privately if the issue would let an attacker escape the worktree,
bypass the policy ceiling, reach host secrets, or turn a failed review into an
approval.
