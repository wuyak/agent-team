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
reasoning effort, and sandbox settings. In policy `version = 2`, the `roles` table maps each role
to its TOML filename and the `models` table supplies model aliases; neither the policy nor role
TOMLs stores `service_tier`. The root task's selected service tier is shared across the delegation
tree. Read the model, reasoning effort, and sandbox mode from the role TOML. Keep role names and
filename mappings consistent. Check support in the actual client and account; configuration alone
does not establish model availability.

Role `sandbox_mode` is a default, not a guaranteed hard permission boundary: live parent
permissions may override it. For enforced read-only work, the parent must itself be restrictive.
Verify effective permissions rather than inferring isolation from a role declaration.

Choose `fork_turns` in the spawn call, not in role TOMLs. Use the main Skill's context rules.
For a new recurring specialist, read [professional-agents.md](professional-agents.md).

## Change model profiles

Use the model controller to switch every managed subagent between supported model generations.
It updates only the model values in the installed `config.toml`, managed role TOMLs, and
`agent-team-policy.toml`; reasoning effort, permissions, the global service tier, context settings,
and other configuration remain unchanged. `agent_model.py` does not modify the root
`config.toml` `service_tier`.

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

The root `config.toml` `service_tier` is the saved global default. In the verified Codex
`0.155.0-alpha.9.2` behavior, the root task's tier is shared across the delegation tree and
overrides role-level settings. The policy and all role TOMLs contain no `service_tier`; policy
`version = 2` stores only `models.<model>.alias` and `roles.<role>.filename`.

From the installed Skill directory, run:

```bash
python3 scripts/agent_speed.py status
python3 scripts/agent_speed.py set fast --dry-run
python3 scripts/agent_speed.py set fast
python3 scripts/agent_speed.py set standard
```

The command accepts only `fast` or `standard`; it writes only the root `config.toml`
`service_tier`, using `"fast"` for `fast` and `"default"` for `standard`. `--dry-run` prints the
plan without changing files. The saved global value is a default for later tasks and may not
override an existing task or a client's separate selection. To switch an existing task, use that
task's native tier selection. A shared tree setting cannot be scoped to Luna only. The
`features.fast_mode` entry enables a feature but does not prove that a request uses Fast; Fast
also requires client and account support. Do not infer that all requests use Fast from the saved
default. `status` and `validate_agent_team.py` inspect static files only; they do not prove the
tier of a running task.

## Inspect settings

Inspect the saved global tier and role files from the installed Skill directory:

```bash
python3 scripts/agent_model.py status
python3 scripts/agent_speed.py status
python3 scripts/validate_agent_team.py
```

Use `--codex-home <path>` with `validate_agent_team.py` to inspect another installation. If a task
behaves differently from the saved default, check the task's native tier selection and
client/account support before treating the configuration as the cause.
