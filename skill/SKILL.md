---
name: agent-team
description: >-
  Coordinate native Codex subagents when the user requests delegation or when
  retrieval, execution, observation, or independent review can be usefully
  handed off. Select roles, prepare context, coordinate ongoing work, and
  assess returned results. Using this Skill does not require creating a child.
---

# Agent Team

Organize delegated work within the task owned by the calling workflow.
Decide what to hand off, establish usable task boundaries, coordinate the
agents, and integrate their results. The responsible Skills retain their
task-level decision and engineering methods.

## Decide what to delegate

Actively identify work that a child can carry through with sufficient
context and return as an assessable result. Delegation can reduce the
parent's execution and context burden even without parallel work; it is
an available choice, not a required step.

Handle work directly when that is clearly simpler or when progress requires
continuous judgment grounded in the main conversation. For troubleshooting,
keep the diagnostic thread with the parent while delegating information
gathering, bounded reproduction, repair, or validation as appropriate.

Divide work by coherent outcomes and dependencies. Keep connected causes
and tightly coupled decisions together, batch small changes of the same
kind, and parallelize work that can progress without conflicting decisions
or writes. When capacity is limited, prioritize work that removes blockers,
advances the critical path, or resolves important uncertainty.

## Select the role

Choose by the next work to be done, using the current role descriptions
and configured capabilities.

| Role | When to use |
| --- | --- |
| `explorer` | Retrieval and information gathering: locate code, files, logs, or documentation and organize relevant facts, references, and unknowns. Answers where something is, what exists, and what the evidence records; root-cause diagnosis, reproduction, and repair belong to execution roles. |
| `worker` | Default for bounded execution: investigation, implementation, reproduction, repair, or validation. Resolves technical questions within the assignment without requiring the parent to prescribe the complete method. |
| `worker_max` | Difficult execution with a clear scope, where evidence shows that ordinary worker-level reasoning is insufficient for the analysis or repair. |
| `worker_xhigh` | Deeper reasoning for unresolved semantic conflicts, competing causal explanations, or cross-module interpretation problems supported by evidence. |
| `reviewer` | Independent scrutiny of a specific claim, approach, change, or artifact against requirements, counterexamples, or evidence gaps. Needs a defined review question; does not replace ordinary retrieval or execution. |
| `monitor` | Continued interpretation of progress, anomalies, and terminal conditions for an existing command, process, or task. A single wait needs no delegation; use collaboration tools to observe other children. |
| `default` | A clear, bounded task that does not fit a specialist. Use a matching specialist when one exists. |

Missing information, unsuitable scope, role mismatch, and reasoning
difficulty call for different responses. Diagnose the limitation before
supplementing, reshaping, reassigning, or upgrading the work. Do not force
a role ladder or escalate merely because work is lengthy or important.
Once difficult reasoning is resolved, assign the remaining work to the
role it now needs.

## Establish the handoff

Give the child the objective and its purpose, assigned scope, available
evidence, relevant decisions and dependencies, permitted operations,
completion requirements, and expected return. If the user wants progress
in the parent task, specify what the child should report and when.

A clear assignment does not require a known solution. Technical unknowns
may be part of the work; unresolved user choices or missing authorization
block only the actions that depend on them. Required interfaces must
actually be available to the child. Resolve or report missing access
rather than treating parent-visible tools as child capabilities.

Explicitly set `fork_turns` when spawning. Default to `fork_turns="none"`
with a self-contained task description, and supplement missing information
as needed. Inherit recent turns only when necessary original context cannot
be reliably restated. Use full history only when a smaller selection is
insufficient, and explain why.

Keep delegation one level deep and explicitly prohibit further delegation
in each child assignment. Reuse the child for related follow-ups so useful
task context survives.

For historical task evidence, use child-visible `read_thread`, or the
included `scripts/read_thread_once.py` through its absolute path:
`metadata`, then bounded `turns` pages using `nextCursor`. Parent relay
is a fallback after both providers fail or when the user requests it;
identify that evidence as parent-assisted, not independently retrieved.

## Coordinate execution

Continue independent work while children run. When no independent work
remains, wait for child events using the runtime's child-wait tool
(`wait_agent` where available) with its default timeout. An unchanged
timeout alone does not justify another status query or message. Use
`list_agents` for contradictory state, explicit status requests, or
capacity decisions.

Send progress updates as agreed at handoff and interim messages when the
parent must act before completion. Keep other progress in the child task.
Use available collaboration tools and the identifiers they accept. Report
delivery from the actual result; delivery does not establish that the
recipient acted on it.

Maintain clear ownership and avoid conflicting writes. Changes to scope,
evidence, authority, or dependencies may invalidate work already underway.
Reassess the affected results, stop or retask the owner before replacement,
and verify interruption before treating work as stopped. Keep unaffected
work moving.

Track only enough state to know ownership, dependencies, progress, and
completion. Do not create coordination documents or checkpoints merely
because children are in use.

## Assess and integrate results

Require a self-contained final containing the result, decisive evidence or
artifacts, checks and outcomes, remaining uncertainty, and needed next action.
Include accepted changes agreed during execution. Return the final directly
without sending a duplicate message first.

Assess whether the result satisfies its purpose and whether its evidence
supports the claims needed downstream. Integrate usable results; investigate
gaps and contradictions. Do not repeat work or checks solely because a child
performed them.

Request independent review for a concrete claim, change, or uncertainty.
Provide current material, acceptance criteria, decisive sources, and the
question the review must resolve. Update reviews when their basis changes;
do not impose a fixed sequence of review agents.

For evidence-heavy returns, target at most 8,000 Unicode characters and
20 findings or exceptions. If truncated, identify omitted material, its
location, and the smallest follow-up needed.

Runtime completion alone is not success. Missing finals or returns without
meaningful findings, artifacts, observations, or error evidence are delivery
failures. Correct the cause before retrying. For a shared failure, try one
child with the same role, authority, and input path before restarting the
affected group; do not repeat the failed batch unchanged.

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
- [field-recording.md](references/field-recording.md): recording Hooks,
  record maintenance, privacy, and historical audits.
- [professional-agents.md](references/professional-agents.md): creating
  or evaluating recurring project specialists.
