# /kimi-run

Run a single prompt through a registered headless CLI agent.

## Usage

```
/kimi-run [agent-name] "<prompt>"
```

## Options

- `agent-name`: registered agent identifier (default: `kimi`).
- `prompt`: the prompt to send to the agent.

## Examples

```
/kimi-run "Explain this codebase"
/kimi-run kimi "Refactor the bridge module"
```

## Safety notes

- Approval policy defaults to `read-only`.
- `--yolo` and `--auto` are never added automatically.
- The agent runs in an isolated worktree.
