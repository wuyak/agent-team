---
name: agent-team
description: |-
  Evaluate and operate native Codex delegation with the workflow that owns the task. Use when
  the user requests subagents or parallel agents, or when evidence exposes a bounded independent
  read-only search, review, test, observation, implementation, repair, validation, or compression
  lane, including a large-evidence bounded question, long-running command/task, or evidenced
  semantic ambiguity requiring escalation. Agent Team owns lane eligibility, role selection, context, authority, communication,
  lifecycle, failure containment, escalation, reuse, and handback; loading it evaluates a lane and
  does not require creating a child.
---

# Agent Team

Build the right-sized team from ready, independent work boundaries. This is a peer delegation
control plane: the main agent or calling workflow owns task scope and completion.
Agent Team owns lane eligibility, role, context,
authority, coordination, and child lifecycle; the main agent organizes consequential architecture/product
choices with the user and owns Git state, integration, and final verification. Agent Team is not a runtime DAG, scheduler,
ACK protocol, or delivery receipt.

## Dispatch contract and readiness

Before spawning or reusing a child, decide three things:

1. the independently useful outcome or evidence and how the parent will use it;
2. whether blockers, required interfaces, authority, dependencies, and single ownership are settled;
3. the next ready action, fitting role, read scope or exclusive write boundary, completion evidence,
   and compact return.

Do not dispatch execution with an unresolved blocker, interface, authority, or owner. An `explorer`
may discover one through safe read-only/local work, but dependent execution remains blocked. Reuse
the same owner for a true follow-up; if scope, source ownership, authority, dependencies, or the
completion condition changes, invalidate or stop affected work before replacement. Never overlap owners.

Keep one lane for a connected evidence chain, artifact, observation target, or decision. Split only
when outcomes can finish independently and integrate without another child owning the same facts.
Create a child only when the lane is concrete, bounded, independently useful, and delegation value
(parallel throughput, context/model offload, or independent judgment) exceeds handoff/integration
cost. Task length, file count, importance, elapsed time, and slot count alone are not triggers.

Treat non-universal interfaces as readiness dependencies: parent visibility is not child visibility.
For historical thread evidence use child-visible `read_thread`, otherwise
`app-server-stdio-once` via the absolute `scripts/read_thread_once.py` path (`metadata`, then bounded
cursor-based `turns`; pass `nextCursor`). A parent relay is degraded takeover only after both
providers fail or the user requests it, and must be marked blocked/failed/reassigned rather than
claimed as independent retrieval.

## Live coordination

A child interim follows these rules:

- `interim` is only for a blocker/input, authority-safety boundary, `ROLE_MISMATCH`, `DECISION`,
  `BRANCH`, `UPGRADE`, or decisive capsule change that changes parent action/readiness. Routine progress stays
  child-local. When the user asks to watch progress in the child task, emit routine updates as child
  commentary; never relay them with `send_message`, because that wakes the parent and duplicates the
  same information.
- `final` is a self-contained canonical handback merging every accepted interim decision delta,
  decisive evidence, artifacts/checks, uncertainty, and smallest next action. The parent must understand the final
  without reading interim messages. If the conclusion is formed and the turn
  can end, return the final directly; never `send_message` to pre-deliver or copy an imminent final:
  that is a delivery shortcut and needs no extra ACK.
- Classify steering as scope-preserving or dependency-changing. A send/interrupt reports only
  `sent` or `unknown`, never `applied`, and never proves runtime ACK or semantic completion. A
  dependency-changing steering stops/retasks the single owner and invalidates affected downstream
  work when interruption is unavailable.
- At a real dependency or slot boundary, use the longest bounded wait permitted by runtime and
  higher-priority instructions. On timeout without state change, wait again; do not list, steer, or
  send commentary merely because time elapsed. `list_agents` is for contradiction, explicit status,
  or real-slot scheduling; never use `monitor` to watch child agents.

## Ownership and lifecycle

Track only what the parent needs to coordinate live lanes: stable owner, scope, blockers, next
action/role, state, produced result, and completion condition. Do not create a DAG, JSON state file,
Markdown plan, Goal, checkpoint, or project artifact merely because lanes exist. Persist only when
the user requests checkpoint or cross-task recovery.

Dispatch every positive-value ready lane the runtime can run; queue excess by critical path, unlock
value, evidence compression, and uncertainty reduction. Re-evaluate when evidence, authority,
ownership, dependencies, contradictions, failure, or return signals change. Editing children get
mutually exclusive files; the main agent retains integration.

Validate each final against the current task and classify it as accepted/reusable, failed, or stale.
A runtime completion or final alone is not semantic success. Reuse the owner when the task is
unchanged; changed sources, scope, authority, dependencies, or completion conditions invalidate the
old result before retry or replacement. No-op artifact churn is not a reason to reopen work. If
`worker_xhigh` resolves competing causes to one clear boundary, route remaining execution to
`worker` or `worker_max` instead of retaining XHigh for confidence.

A terminal child with no decision delta, artifact, meaningful observation, or error evidence is
failed even if runtime says completed. Completion also requires a canonical final:
zero-tool/zero-final, tools-without-final, and final-without-evidence are failed delivery. For shared
delivery failure, repair the cause and run one
same-role, same-authority canary with the same input path before reopening siblings; freeze them if
the canary is empty/failed. Do not clone a failed wave. Confirm every child is completed, errored,
interrupted, or explicitly reclaimed before parent handback.

## Role routing

Role TOML `description` is the spawn-time source of truth; `developer_instructions` governs only
post-spawn behavior. Route by the next ready action:

| Role | Boundary |
| --- | --- |
| `explorer` | bounded read-only retrieval/compression; one connected evidence lane |
| `reviewer` | independent challenge of a named capsule/acceptance boundary |
| `worker` | clear exclusive implementation, reproduction, or focused validation |
| `monitor` | interpreted observation of an already-running target; never start/replace it |
| `worker_max` | rare upgrade for difficult but still bounded causal repair |
| `worker_xhigh` | rare upgrade for evidenced competing causal models/semantic conflict |
| `default` | fallback only when no narrower role fits a clear bounded action |

### Monitor lifecycle

A monitor owns observation until the named target reaches its explicit terminal condition, the user
stops monitoring, or a real blocker requires attention. While the target is still live, continue
bounded waits and observations in the same child turn. A progress snapshot, an unchanged poll, or a
temporary lack of logs is not a terminal result and must not be returned as `final`. Do not make the
parent repeatedly trigger follow-up turns merely to keep observation alive.

When periodic user-visible progress is requested, write it as commentary in the monitor's own task
at the agreed cadence. Measure cadence from observed elapsed time. If an update includes wall-clock
time, read the current system clock in the same observation; never invent, increment, or extrapolate
a timestamp from the polling interval or from an earlier update. If current time was not observed,
omit the timestamp. Distinguish an event or log timestamp from the current wall clock when both are
shown.

Send the parent only an actual blocker, user-input need, authority boundary, decisive phase change
that changes parent action, or the canonical terminal handback. Routine progress and no-change
polls remain child-local and never wake the parent.

Role TOML `sandbox_mode` is a native default, not a guaranteed hard permission boundary: live
parent authority may supersede it. If hard read-only is required, the parent must itself be
restrictive; never infer enforced isolation from a role declaration alone.

## Managed service tier

For an explicit request to switch subagents between `fast` and `default`, use
`python3 scripts/agent_speed.py set <model> <fast|standard>`. The CLI calls the normal tier
`standard`; this is the user's `default`. `agent-team-policy.toml` is the source of truth, and the
controller updates the matching role profiles; do not edit those TOML files one by one.

Resolve the requested model or role against the installed policy. When multiple model groups
exist and the requested scope is ambiguous, clarify which group should change; the distribution
does not assume a Luna/Sol mapping. After the change,
run `python3 scripts/agent_speed.py validate` and report which model family changed. Do not ask for
another confirmation after the user has already named the tier and scope.

Start at the cheapest fitting role. `ROLE_MISMATCH` routes to the named role/main agent and does not imply a reasoning upgrade;
`UPGRADE` routes `worker` to `worker_max` when the causal boundary is clear or
to `worker_xhigh` for competing explanations; `DECISION` belongs to the main agent; `BRANCH` creates
a lane only when independently valuable and ready. Do not force a role ladder. Every editing child
has exclusive ownership and allowed validation commands; it returns `DECISION` for architecture.

## Spawn context and review

Every `spawn_agent` call must explicitly set `fork_turns` before spawn. Default `fork_turns="none"`
and transfer a compact capsule
(outcome, independence, state/evidence, read/write scope, provider/authority, done/acceptance,
return budget, and `Topology: Do not create or delegate to another agent`). Use positive integer `N`
only when exact recent wording is decisive and record why; use `fork_turns="all"` only when the
capsule gives a concrete `full-history-reason`. `followup_task` reuses the turn/thread and does not
re-fork. Every child task must explicitly forbid nested delegation; keep topology one level deep.
Role TOMLs must never declare `fork_turns`; it is selected before spawn.

A reviewer requires a named current capsule/artifact or claim, the disputed decision or acceptance
boundary, the smallest decisive sources, and how the answer changes acceptance. If those inputs
change materially, the old review is stale; review the updated material instead of treating a generic
second read as confirmation.

For evidence-heavy lanes, target at most 8,000 Unicode characters and 20 finding/exception items;
if truncated, return the decision delta, aggregates, exact decisive references, `truncated=true`,
omitted count, and smallest follow-up slice. Explorer acceptance requires a concrete decision delta,
exact decisive references, contradictions/unknowns, next discriminator, and smallest downstream
action. An explorer that cannot perform the next ready action returns `ROLE_MISMATCH`; use `UPGRADE`,
`DECISION`, and `BRANCH` only as defined above.

## Recording, specialist agents, and validation

If the optional recording Hooks are installed and enabled, they record sanitized dispatch/lifecycle
facts and Stop auto-finalizes once all children are terminal; recording is passive and never a scheduler or ACK. Read
`references/field-recording.md` only for explicit recorder/schema/privacy/trust maintenance or an
explicit historical audit—not ordinary closeout. Never rewrite routine records; correct them only
through an explicit superseding record.

When creating or evaluating a recurring project specialist, read
[professional-agents.md](references/professional-agents.md) for promotion criteria, ownership of
Skills versus tools versus agent profiles, and evaluation against generic roles. Ordinary dispatch
uses the role routing above.

After changing managed service tiers, run `python3 scripts/agent_speed.py validate`.
For other role changes, compare the role files with the selected policy and check the current
Codex runtime's actual supported settings. The author's environment-specific model-catalog
validator is not part of this distribution. Skill frontmatter can be checked with the installed
system skill-creator validator when available. Static checks do not prove safe dispatch or model
availability.
