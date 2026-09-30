# Configuration

Use this reference to install or update Agent Team, or to edit role files.
Ordinary delegation follows the main Skill.

## Install or update

`<codex-home>` is the active `CODEX_HOME`, or `~/.codex` when unset.

| Distribution source | Installed location |
| --- | --- |
| `skill/` | `<codex-home>/skills/agent-team/` |
| `roles/*.toml` | `<codex-home>/agents/` |
| `config.fragment.toml` | Merge into `<codex-home>/config.toml` |

Compare existing files before replacing them. Keep the user's `model` and
`model_reasoning_effort` in installed role files, and preserve unrelated
configuration keys. When updating an older installation, remove
`agents/sol-xhigh.toml`, `agents/worker-max.toml`, `agent-team-policy.toml`,
and the installed Skill's `scripts/` directory; Codex still discovers roles
left in `agents/`.

## Role files

Codex discovers every `.toml` file under `<codex-home>/agents/`, including
subdirectories. Keep backups and other TOML files outside that directory. A
role file takes effect through these fields:

| Field | Effect |
| --- | --- |
| `name` | Required. The role identity used in spawn requests; the file name does not replace it. |
| `description` | Required. Shown to the parent in the spawn tool; state when to choose the role. It may end with an effort line, ``Effort: `medium`; `high` when hard.``, naming the ordinary and hard efforts the parent chooses between. |
| `developer_instructions` | Required. Added to the child's instructions. |
| `model`, `model_reasoning_effort` | Optional. When set, fixed for the role and applied over any spawn request. When omitted, Codex uses the spawn request, then `[agents].default_subagent_model` and `[agents].default_subagent_reasoning_effort`, then the parent's current setting. |
| `model_reasoning_summary`, `model_verbosity`, `personality` | Optional output settings. |
| `nickname_candidates` | Optional. ASCII letters, digits, spaces, hyphens, and underscores only; an invalid entry makes Codex drop the whole role. |

Each distributed role ends its `developer_instructions` with the same child
contract: scope and escalation, no further spawning, reporting a reasoning
difficulty, the final's contents and size, and the messaging tools. Copy it
into a new role.

A role can disable some tools through `features`, or disable Skills; it cannot
enable them. Since openai/codex#39299 (August 2026), `sandbox_mode`,
`mcp_servers`, `service_tier`, and context or compaction settings in a role
file have no effect: children use the parent's permissions, MCP servers, and
service tier. Codex rejects symlinked role files.

After editing, run `codex doctor`. Malformed or duplicate roles appear among
its startup warnings. Confirm the model, effort, and behavior from an actual
delegation in a new session.
