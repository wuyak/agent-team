# Professional Agents

A professional agent is a durable operating unit, not a character description. It combines a recurring job with the capabilities, authority, context contract, and evaluation needed to perform that job reliably.

## Promotion Test

Create a named specialist only when all of these are true:

1. **Recurring job:** the lane appears across multiple tasks or project phases.
2. **Distinct capability:** it needs a particular Skill, MCP server, command set, data source, sandbox, or project knowledge.
3. **Bounded authority:** its read/write scope and escalation boundary can be stated clearly.
4. **Measurable artifact:** it returns evidence, a patch, a report, a verified state change, or another inspectable result.
5. **Evaluation evidence:** representative tasks can distinguish it from the generic role it would replace.

If only the tone, title, or claimed expertise differs, keep the generic agent and pass better task context.

## Where Each Concern Lives

| Concern | Home |
| --- | --- |
| Reusable workflow and domain instructions | Skill |
| Live external capability or authenticated data | MCP server or app connector |
| Model, reasoning effort, sandbox, and agent instructions | Agent TOML |
| Project-specific specialist registration | Project `.codex/agents/` and `.codex/config.toml` |
| Cross-project primitive roles | User `~/.codex/agents/` |
| Hard policy and deterministic validation | Sandbox, hooks, scripts, tests, or CI |

An agent may invoke a Skill, but the two are not interchangeable: the Skill explains how to perform a workflow; the agent is an independently scheduled execution context with a model, permissions, tools, and lifecycle.

## Build Sequence

1. Collect several real tasks from the recurring lane.
2. Write its capability manifest: inputs, tools, authority, output, done criteria, and escalation boundary.
3. Start with the cheapest model that passes those tasks. Do not infer model class from the prestige of the job title.
4. Configure a project-scoped agent with concrete instructions and least necessary sandbox access.
5. Run smoke cases and compare it with the generic `explorer` or `worker` on correctness, intervention rate, latency, and cost.
6. Keep it only when the specialist produces a stable advantage. Otherwise improve the calling Skill or context capsule.

## Useful Team Shapes

- **Repository maintainer:** explorer maps ownership; project maintainer agent applies repository-specific invariants; main agent integrates.
- **Release team:** release-evidence agent assembles changelog and test evidence; release operator performs bounded actions; main agent authorizes remote writes.
- **Incident team:** telemetry specialist gathers live evidence; reproduction worker isolates the
  fault; Sol XHigh is used only for evidenced competing causal models or semantic conflicts.
- **Migration team:** source and target specialists validate their own systems; a bounded worker implements the transformer; main agent owns cutover decisions.

Add a reviewer or verifier only when it has an independent acceptance surface. Give it a compact
evidence capsule and disputed claims; a second agent rereading the same full context with the same
tools is not automatically useful independence.

## Avoid Catalog Inflation

Do not pre-create agents for every technology or job title. Begin with the seven generic primitives.
Promote a specialist from repeated evidence, keep it close to the project whose capabilities it
uses, and retire it when its distinction disappears.
