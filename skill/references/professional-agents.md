# Professional Agents

A professional agent is a durable operating unit: a recurring job plus the
capabilities, authority, context contract, and evaluation needed to perform it
reliably. It is not a character description.

## Promotion Test

Begin with generic roles. Create a named specialist only when all are true:

1. **Recurring job:** the lane appears across multiple tasks or project phases.
2. **Distinct capability:** it needs particular instructions, a Skill, a command set, a data source, or project knowledge.
3. **Bounded authority:** its read/write scope and escalation boundary can be stated clearly.
4. **Measurable artifact:** it returns evidence, a patch, a report, a verified state change, or another inspectable result.
5. **Evaluation evidence:** representative tasks can distinguish it from the generic role it would replace.

If only the tone, title, or claimed expertise differs, keep the generic agent and pass better task context.

## Where Each Concern Lives

| Concern | Home |
| --- | --- |
| Reusable workflow and domain instructions | Skill |
| Live external capability or authenticated data | MCP server or app connector |
| Agent instructions, and optionally a fixed model or reasoning effort | Agent TOML |
| Permissions, MCP servers, and service tier | Parent session; roles cannot change them |
| Project-specific specialist registration | Project `.codex/agents/` and `.codex/config.toml` |
| Cross-project primitive roles | User `~/.codex/agents/` |
| Hard policy and deterministic validation | Sandbox, hooks, scripts, tests, or CI |

A Skill defines a reusable workflow; an agent is an independently scheduled
context with a model, permissions, tools, and lifecycle. An agent may invoke a
Skill, but the two are not interchangeable.

## Build Sequence

1. Collect several real tasks from the recurring lane.
2. Write its capability manifest: inputs, tools, authority, output, done criteria, and escalation boundary.
3. Start with the cheapest model that passes those tasks. Do not infer model class from the prestige of the job title.
4. Configure a project-scoped agent with concrete instructions.
5. Compare smoke cases with `explorer` or `default` on correctness, intervention rate, latency, and cost.
6. Keep specialists near the projects they serve. Retain only a stable advantage; otherwise retire them and improve the Skill or task context.

## Useful Team Shapes

- **Repository maintainer:** explorer maps ownership; project maintainer agent applies repository-specific invariants; main agent integrates.
- **Release team:** release-evidence agent assembles changelog and test evidence; release operator performs bounded actions; main agent authorizes remote writes.
- **Incident team:** telemetry specialist gathers live evidence; a `worker` reproduces and isolates
  the fault, at `xhigh` when evidence shows competing causal models or semantic conflicts.
- **Migration team:** source and target specialists validate their own systems; a `worker` implements the transformer within scope; main agent owns cutover decisions.

Add a reviewer or verifier only for an independent acceptance surface. Give it
a compact evidence capsule and disputed claims; rereading the same full context
with the same tools is not automatically independent.
