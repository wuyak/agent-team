# Configuration and maintenance

Use this reference for installation, role or permission settings, model-profile or service-tier
changes, and configuration validation. Ordinary delegation follows the main Skill.

## Install or update

`<codex-home>` is the active `CODEX_HOME`, or `~/.codex` when unset.

| Distribution source | Installed location |
| --- | --- |
| `skill/` | `<codex-home>/skills/agent-team/` |
| `roles/*.toml` | `<codex-home>/agents/` |
| `agent-team-policy.toml` | `<codex-home>/agent-team-policy.toml` |
| `config.fragment.toml` | Merge into `<codex-home>/config.toml` |

Check the receiving client's role discovery, supported settings, and available models.
Compare existing files before replacing them; merge configuration keys and preserve unrelated
settings. Verify role recognition in a new session and actual model, permissions, and results
when delegation runs. Follow the user's selected installation or update scope.

The scripts require Python 3.11 or newer.

## Role and permission settings

Each role TOML supplies its name, selection description, behavior instructions, and actual model,
reasoning effort, and sandbox settings. The policy's `roles` table maps each role to its TOML
filename; its `models` table supplies model aliases and the current desired service tier. Read
the model, reasoning effort, and sandbox mode from the role TOML. Keep role names and filename
mappings consistent. Check support in the actual client and account; configuration alone does not
establish model availability.

Role `sandbox_mode` is a default, not a guaranteed hard permission boundary: live parent
permissions may override it. For enforced read-only work, the parent must itself be restrictive.
Verify effective permissions rather than inferring isolation from a role declaration.

Choose `fork_turns` in the spawn call, not in role TOMLs. Use the main Skill's context rules.
For a new recurring specialist, read [professional-agents.md](professional-agents.md).

## Change model profiles

Use the model controller to switch every managed subagent between supported model generations.
It updates only the model values in the installed `config.toml`, managed role TOMLs, and
`agent-team-policy.toml`; reasoning effort, permissions, service tiers, context settings, and
other configuration remain unchanged.

From the installed Skill directory, run the controller without arguments to inspect the current
profile and choose a target interactively:

```bash
python3 scripts/agent_model.py
```

For explicit or automated use:

```bash
python3 scripts/agent_model.py status
python3 scripts/agent_model.py set 5.6
python3 scripts/agent_model.py set gpt-6 --dry-run
python3 scripts/agent_model.py set 6 --yes
```

Short profile names such as `5.6` and `6` are semantic aliases for their complete GPT model
generations. Before writing, the controller prints every model-field change and asks for
confirmation; `--yes` is the explicit non-interactive confirmation. A known mixed-generation
installation is reported and can be converged after confirmation. Writes are validated and rolled
back in memory on failure; the controller does not create persistent backup files.

## Change service tiers

`agent-team-policy.toml` is authoritative for managed tiers. Use the controller to update matching
profiles rather than editing their tiers individually. Resolve the requested model or role against
the installed policy; clarify ambiguous scope across model groups without assuming a Luna/Sol
mapping. Once the user specifies tier and scope, proceed without another confirmation.

From the installed Skill directory, run:

```bash
python3 scripts/agent_speed.py set <model> <fast|standard>
```

The CLI and policy use `standard` for the user's normal/default tier; role TOMLs use `default`.
Fast requires client and account support. Report the model family changed and resulting profile status.

## Inspect settings

Inspect the current service tiers and role files from the installed Skill directory:

```bash
python3 scripts/agent_model.py status
python3 scripts/agent_speed.py status
python3 scripts/validate_agent_team.py
```

Use `--codex-home <path>` with `validate_agent_team.py` to inspect another installation. These
commands are read-only.
