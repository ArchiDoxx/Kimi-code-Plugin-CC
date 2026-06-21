---
description: Run a single prompt through a registered headless CLI agent (Kimi by default) via the bridge MCP server.
argument-hint: [agent-name] "<prompt>"
allowed-tools: mcp__kimi-code-plugin-cc__run_agent
---

Run one prompt through a headless CLI agent using the bridge.

Arguments (raw): `$ARGUMENTS`

Do the following:

1. Parse the arguments:
   - If the first whitespace-delimited token is a bare word with no spaces and
     is followed by a quoted string, treat it as the agent name and the rest as
     the prompt. Otherwise the agent name is `kimi` and the whole of
     `$ARGUMENTS` is the prompt.
2. Call the MCP tool `mcp__kimi-code-plugin-cc__run_agent` with:
   - `agent_name`: the resolved agent (default `kimi`)
   - `prompt`: the resolved prompt
   - `approval_policy`: `read-only` (do NOT raise this unless the user
     explicitly asked and is authorized; the server caps it at `KIMI_MAX_POLICY`)
3. Return the agent's payload verbatim, then add a one-line note of which agent
   and policy were used.

Safety:
- Never pass `--yolo`, `--auto`, or any auto-approve flag; the adapter forbids
  them structurally.
- The agent runs in an isolated worktree; it cannot implicitly write the repo.
- If the tool errors (unknown agent, auth hang/timeout), report the error
  plainly. Do not retry with a higher policy.
