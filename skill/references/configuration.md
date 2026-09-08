# Configuration and maintenance

Use this reference for installation, role or permission settings, service-tier changes,
or configuration validation. Ordinary delegation follows the main Skill.

## Install or update

`<codex-home>` is the active `CODEX_HOME`, or `~/.codex` when unset.

| Distribution source | Installed location |
| --- | --- |
| `skill/` | `<codex-home>/skills/agent-team/` |
| `roles/*.toml` | `<codex-home>/agents/` |
| `agent-team-policy.toml` | `<codex-home>/agent-team-policy.toml` |
| `config.fragment.toml` | Merge into `<codex-home>/config.toml` |
| `hooks.example.json` | Optional: merge into the client's supported Hooks configuration |

Check the receiving client's role discovery, supported settings, and available models.
Compare existing files before replacing them; merge configuration keys and preserve unrelated
settings. Verify role recognition in a new session and actual model, permissions, and results
when delegation runs. Follow the user's selected installation or update scope.

The scripts require Python 3.11 or newer. Recording uses POSIX `fcntl`; native Windows setup
requires adapting paths, shell commands, and locking to the receiving environment and validating
that behavior. See [field-recording.md](field-recording.md) for optional recording Hooks.

## Role and permission settings

Each role TOML supplies its name, selection description, behavior instructions, and configured
model settings. Keep role names, filenames, model selectors, reasoning efforts, policy entries,
and global subagent defaults consistent. Check support in the actual client and account;
configuration alone does not establish model availability.

Role `sandbox_mode` is a default, not a guaranteed hard permission boundary: live parent
permissions may override it. For enforced read-only work, the parent must itself be restrictive.
Verify effective permissions rather than inferring isolation from a role declaration.

Choose `fork_turns` in the spawn call, not in role TOMLs. Use the main Skill's context rules.
For a new recurring specialist, read [professional-agents.md](professional-agents.md).

## Initialize policy history

The distributed policy is a template with empty histories. After selecting the receiving
installation's models and roles, initialize the installed policy before using the policy scripts:

- One `service_tier_history` entry per model: `effective_at`, `model`, `service_tier`.
- One `role_runtime_history` entry per role: `effective_at`, `role`, `model`, `reasoning_effort`.

Use the actual configuration effective time in timezone-aware ISO 8601 format and values from
the final settings. Remove the corresponding empty array declarations when adding table entries.
Preserve existing local history and append actual changes. These histories are required by the
policy scripts even when recording Hooks are disabled; an uninitialized template fails validation.

## Change service tiers

`agent-team-policy.toml` is authoritative for managed tiers. Use the controller to update matching
profiles rather than editing their tiers individually. Resolve the requested model or role against
the installed policy; clarify ambiguous scope across model groups without assuming a Luna/Sol
mapping. Once the user specifies tier and scope, proceed without another confirmation.

From the installed Skill directory, run:

```bash
python3 scripts/agent_speed.py set <model> <fast|standard>
python3 scripts/agent_speed.py validate
```

The CLI and policy use `standard` for the user's normal/default tier; role TOMLs use `default`.
Fast requires client and account support. Report the model family changed and validation result.

## Validate changes

- After tier changes, run `agent_speed.py validate` to check policy history, profile tiers,
  and Fast settings.
- After profile, policy, or recording Hook configuration changes, run
  `python3 scripts/validate_agent_team.py`. It checks role fields against the policy and history,
  plus recording commands and event matchers when configured. Other Hooks may coexist.
  Use `--codex-home <path>` to inspect another installation. The check is read-only; it does
  not evaluate prompt wording, require a custom model catalog, or prove runtime capabilities.
  After changing the validator, run `python3 scripts/test_validate_agent_team.py`.
- After Skill changes, run the installed system skill-creator's `quick_validate.py` against
  the changed Skill directory when available, and check its relative references.
- After client upgrades, verify the configuration and Hook events actually supported.

Static checks do not prove safe dispatch, model availability, or effective runtime permissions.
Recording remains optional and passive. Read [field-recording.md](field-recording.md) for recorder
maintenance or historical audits; correct records through explicit superseding records rather
than rewriting routine history.
