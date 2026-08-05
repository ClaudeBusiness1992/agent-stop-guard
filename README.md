# Agent Stop Guard

A portable completion guard for Claude Code, Codex and OpenCode. It prevents an
agent from ending a work turn immediately after announcing more work or while
structured tasks are still open.

The guard does **not** grant permissions, expand the original task or execute
commands on its own. It only rejects a premature stop and asks the current
agent to continue within the already authorized scope.

## What it checks

- pending or in-progress Claude tasks;
- pending or in-progress Codex `update_plan` items;
- a Codex turn ending on a commentary/intermediate message;
- explicit promises such as “I will check that now” without execution;
- provider-native pending or in-progress tasks where the platform exposes them;
- a claimed completion that contradicts text saying work remains.

The guard recognizes these explicit final attestations where a structured
task is deliberately parked:

```text
AUFTRAG VOLLSTÄNDIG ERLEDIGT
```

or, when only the user can unblock the remaining work. A generic placeholder
such as “later” is rejected; the line must name concrete required input:

```text
BLOCKED_ON_USER: <specific required input and evidence>
```

Recommendations and optional future improvements do not authorize new work and
are not treated as unfinished implementation by themselves.

## Requirements

- Linux or macOS
- Python 3.10 or newer
- a hook-capable Claude Code or Codex installation, and/or OpenCode with local
  plugin loading

The reference installation was tested with Claude Code 2.1.220, Codex CLI
0.146.0 and OpenCode 1.18.11. Hook APIs can change; run the smoke test after an
agent upgrade.

## Install

Clone the repository and preview the changes first:

```bash
python3 install.py --claude --codex --opencode --dry-run
```

Install for the desired agents:

```bash
python3 install.py --claude --codex --opencode
```

Optionally add the stricter Claude `AskUserQuestion` guard. It makes Claude
finish independent tasks before asking the user about a blocked task:

```bash
python3 install.py --claude --ask-user-guard
```

The installer:

- stores shared scripts under `~/.local/share/agent-stop-guard/`;
- keeps an identical Claude compatibility copy for already-running sessions
  that cached the former direct hook path;
- merges hooks into `~/.claude/settings.json` and `~/.codex/hooks.json`;
- installs the OpenCode plugin under `~/.config/opencode/plugins/`;
- migrates an older direct Claude/Codex hook path to the shared installed copy;
- creates timestamped backups before changing an existing JSON file;
- never copies credentials, provider settings or unrelated configuration.

Restart the selected agent sessions after installation.

## Test

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_repository.py
```

A useful smoke test is to ask an agent to perform a harmless tool action and
then end with “I will verify the rest now.” The guard should reject that stop.
A normal chat-only response should remain unaffected.

## Emergency disable

Disable the completion guard for the current user:

```bash
mkdir -p ~/.local/state/agent-stop-guard
touch ~/.local/state/agent-stop-guard/disabled
```

Re-enable it:

```bash
rm ~/.local/state/agent-stop-guard/disabled
```

The guard uses separate retry budgets to avoid infinite loops: eight text-rule
blocks and up to 25 structured-task or plan blocks per session. The optional
Ask-user guard lets an unchanged question through after three identical
rejections to avoid deadlocks.

## OpenCode behavior

OpenCode exposes an idle event rather than the same transcript-oriented Stop
hook. Its plugin reads the latest assistant message and the native session Todo
list on `session.idle`. It applies the same scope-safe threshold as Claude and
Codex: actionable structured work and concrete first-person work promises
continue; optional recommendations and tool use by themselves do not.

## Not included

This repository intentionally does not include:

- router or worker kill switches;
- provider credentials or model configuration;
- host, server, container or network permissions;
- project-specific UI, deployment or verification rules;
- personal paths, names, transcripts or task data.

## Security notes

Review hook code before installing it. The scripts parse local agent
transcripts and task metadata but do not send them over the network. State and
logs are stored under `~/.local/state/agent-stop-guard/`.

Report security issues privately through GitHub's security advisory feature;
do not publish sensitive transcripts in an issue.

## License

MIT
