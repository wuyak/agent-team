---
name: agent-team
description: >-
  Coordinate native Codex subagents when the user requests delegation or when
  retrieval, execution, observation, or independent review can be usefully
  handed off. Arrange outcomes and dependencies, select roles, prepare handoffs,
  and integrate results. Using this Skill does not require creating a child.
---

# Agent Team

Organize work so each owner can make useful progress and the results combine
into the requested outcome. Own the division of work, handoffs, coordination,
and integration. The calling workflow retains task-level decisions and
engineering methods.

## Choose the working arrangement

Look for an assessable result a child can own with sufficient context and
authority. Delegation can reduce the parent's execution and context burden
even without parallel work; it is an available choice, not a required step.

Handle work directly when that is clearly simpler or when progress requires
continuous judgment grounded in the main conversation. For troubleshooting,
the parent owns the overall diagnosis and integration. Delegate bounded evidence
gathering, diagnosis, reproduction, repair, or validation when the child has
sufficient context and authority.

Parallelize independent outcomes and batch small changes of the same kind.
For dependent work, identify the upstream result the consumer needs. Keep
decisions that require continuous joint reasoning with one owner until a
usable boundary emerges. Separate file ownership alone does not establish
that work can proceed independently. Prioritize work that releases a blocked
consumer or resolves uncertainty that could invalidate later work.

Read [dependent-work.md](references/dependent-work.md) when agents share an
unsettled interface or input, a consumer needs an intermediate result, or an
upstream change may invalidate ongoing or completed downstream work. Ordinary
independent assignments use the main workflow directly.

## Select the role

Use the current runtime's role descriptions and capabilities. The important
selection boundaries are:

- `default` owns bounded execution.
- `worker` owns execution that warrants additional reasoning effort. Choose
  it directly without requiring a failed `default` attempt.
- `worker_max` has the same execution responsibilities as `worker`. Select it
  only when the user explicitly requests `worker_max` or Luna/max for this
  delegation. Do not select it autonomously based on task difficulty.
- `explorer` gathers evidence; `reviewer` independently examines a defined
  claim or artifact. Diagnosis, reproduction, and repair need execution roles.
- `sol_xhigh` fits evidenced semantic conflicts, competing causal explanations,
  or difficult cross-module interpretation.
- `monitor` observes an existing command, process, or task. A single wait needs
  no child; observe other children through native collaboration tools.

Missing information, unsuitable scope, role mismatch, and reasoning
difficulty call for different responses. Diagnose the limitation before
supplementing, reshaping, reassigning, or upgrading the work. Do not force
a role ladder or escalate merely because work is lengthy or important.
Once difficult reasoning is resolved, assign the remaining work to the
role it now needs.

## Assign an outcome

Give the child an outcome it can own through the required checks: its purpose,
scope, available evidence, permitted operations, decisions it may make, and
acceptance evidence. Distinguish its validation from the parent's integration
checks. For a pending dependency, identify its owner, the result needed, and
how availability or a change will be communicated. Separate work that can
proceed now from work that needs that result.

Request an interim handoff when another owner needs it to proceed. Otherwise
let the child carry its assignment to final delivery. If the user requests
progress in the parent task, specify useful reporting points.

A clear assignment does not require a known solution. Technical unknowns
may be part of the work; unresolved user choices or missing authorization
block only the actions that depend on them. Tools and resources needed for
execution must be accessible to the child; parent-visible tools do not
establish child capabilities. Resolve or report missing access. An interface
being implemented by another owner is a work dependency, not tool access.

Explicitly set `fork_turns` when spawning. Default to `fork_turns="none"`
with a self-contained task description, and supplement missing information
as needed. Inherit recent turns only when necessary original context cannot
be reliably restated. Use full history only when a smaller selection is
insufficient, and explain why.

Keep delegation one level deep and explicitly prohibit further delegation
in each child assignment. Reuse the child for related follow-ups so useful
task context survives.

## Coordinate around events

Use events to choose the next action:

| Event | Parent action |
| --- | --- |
| Routine progress; the arrangement still holds | Continue independent work or wait. No acknowledgment is needed. |
| An upstream result is usable | Hand it to the owner whose next action depends on it. |
| A blocker or consequential question | Supply the missing input, arrange the needed decision, or retask the affected work. |
| Changed scope, evidence, authority, or dependency | Reassess affected work; stop or retask its owner before replacement. Verify a requested interruption before treating work as stopped; preserve unaffected work. |
| Final delivery | Assess the result and integrate what its evidence supports. |
| Capacity rejection | Wait, reuse, combine, defer, or reclaim work. Retry when relevant capacity or request conditions change. |
| Terminal state without a usable delivery | Identify the missing result or failure evidence before asking for completion or retrying. |

Send a message when it supplies information or requests action needed for a
current decision or handoff. Combine known requests for the same outcome.
While a request is outstanding, follow up only when changed evidence, a
missed agreed handoff, or a newly blocked next action warrants intervention.

Report changes that matter to the overall outcome, rather than replaying each
child's activity or presenting unchanged results as new progress. Keep
completion claims within the scope established by the evidence; name outstanding
dependencies, integration, or review that still prevent completion. Describe
remaining work directly instead of repeatedly predicting completion. When an
update is due without a material change, briefly state the known waiting
condition. Do not query a child solely to produce an update.

Use native collaboration tools and the identifiers they accept. Use
`send_message` for information and `followup_task` when work must start or
resume. Judge whether instructions were applied from the resulting work,
substantive reply, or final delivery; a delivery receipt does not require an
acknowledgment exchange. Use `list_agents` to resolve contradictory state,
answer an explicit status request, or make a capacity decision.

Use the native child-wait tool (`wait_agent` where available) with its
configured default timeout in these two cases:

- **Waiting for a specific child:** When the parent needs a particular child's
  result to continue, identify that child and the required result, then use the
  native wait tool without repeatedly querying status or sending confirmation
  messages. Resume the original task when the result is usable.
- **No parent work:** When no necessary independent work remains, directly use
  the native wait tool to suspend model execution, without looking for extra
  work to do. If the wait times out without a new development, continue waiting.

Keep enough working state to know the owners, outstanding handoffs, and next
actions. Existing task context is sufficient unless its loss would impede
resumption; children alone do not justify coordination documents.

## Assess and integrate results

Require a self-contained final containing the result, decisive evidence or
artifacts, checks and outcomes, remaining uncertainty, and needed next action.
Include accepted changes agreed during execution. Return the final directly
without sending a duplicate message first.

Assess the result against the assigned outcome and the conditions its checks
actually covered. Reuse valid evidence and perform the remaining integration
checks. Repeat or extend validation for a relevant change, failure, or evidence
gap; a child having performed the original check is not a reason to repeat it.

For conflicting results, first compare the target, version, conditions, and
acceptance criteria. Preserve conclusions that apply to different cases.
If a conflict remains, assign the specific unresolved question to the role
and workflow it needs. Technical diagnosis stays with the engineering
workflow; consequential user choices return to the calling workflow.

Request independent review for a concrete claim, change, or uncertainty.
Provide current material, acceptance criteria, decisive sources, and the
question the review must resolve. Update reviews when their basis changes;
do not impose a fixed sequence of review agents.

For evidence-heavy returns, target at most 8,000 Unicode characters and
20 findings or exceptions. If truncated, identify omitted material, its
location, and the smallest follow-up needed.

Retry after correcting an identified cause, or use a bounded retry to test a
specific failure hypothesis. When multiple child executions share a failure,
validate the correction with one child under the same role, authority, and input
path before resuming the affected group.
Handle capacity rejection through the scheduling branch above.

Before closing the parent task, account for every child as completed,
errored, interrupted, or explicitly reclaimed.

## Monitor an existing target

The monitor owns routine status and log checks for its assigned target.
The parent queries the same target to intervene, resolve missing or
conflicting evidence, or perform a separate acceptance check.

Assess completion against the named terminal condition or the user's
explicit end to monitoring. Interim progress and requests for help are
not final results.

For required updates before completion, the child needs an available parent
messaging channel; otherwise keep that observation with the parent.

Measure elapsed time rather than counting polls. Read the clock before
reporting current time; otherwise omit it. Distinguish event timestamps
from current time.

## Configuration and maintenance

Read these only for the corresponding request:

- [configuration.md](references/configuration.md): installation, role
  settings, runtime permissions, service tiers, and configuration validation.
- [professional-agents.md](references/professional-agents.md): creating
  or evaluating recurring project specialists.
