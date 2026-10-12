---
name: agent-team
description: >-
  Delegate work to native Codex subagents. Use when the user asks for
  subagents, or when retrieval, execution, observation, or independent review
  can be handed off. Covers what to delegate, role and effort choice,
  assignments, waiting, and integrating results; spawning a child is optional.
---

# Agent Team

The parent owns delegation, handoffs, integration, and task-level decisions.

## Delegate work

Delegate a bounded outcome a child can own and verify. Keep work that is
simpler to do directly or needs continuous conversation-level judgment; the
parent owns the overall diagnosis. Run independent outcomes in parallel. Give
each document or module one author; other children provide evidence or review.

When agents share an unsettled interface or input, a consumer needs an
intermediate result, or an upstream change may invalidate other work, read
[dependent-work.md](references/dependent-work.md).

## Choose the role and effort

| Role | Use | Ordinary | Hard |
| --- | --- | --- | --- |
| `default` | Bounded mixed work | `medium` | `high` |
| `worker` | Execution or a deliverable | `high` | `xhigh` |
| `explorer` | Read-only retrieval | `medium` | `high` |
| `reviewer` | Independent read-only review | `high` | `xhigh` |
| `monitor` | Repeated observation to a terminal condition | fixed `medium` | — |

For configurable roles, pass the selected effort unless `fork_turns="all"`
forces inheritance. Hard means conflicting evidence, competing explanations or
designs, or cross-module reasoning—not merely long or important. A user's
explicit choice wins where the effort is configurable.

The distributed `[agents]` default is `high` only as a fail-safe for omissions;
an omission is not a successful selection. It may over-provision simple work
and still under-provision hard `worker` or `reviewer` work.

For another role, obey its fixed effort or `Effort` line; otherwise use the
configured default. Pass a model only when the user selects one. A child's
effort cannot change; if it is stuck on reasoning, spawn a hard-effort child
with the unresolved work and prior findings.

## Assign the outcome

Default to `fork_turns="none"` with a self-contained assignment:

- the outcome, purpose, and acceptance evidence;
- scope, evidence, permitted operations, and file ownership;
- decisions it may make and choices it must return;
- dependencies, required handoffs, and their owners.

Use `fork_turns="all"` only when necessary context cannot be restated. Confirm
tool access; send related follow-ups to the same child.

## Coordinate

When a result is needed or nothing independent remains, use `wait_agent` and
keep waiting after an unchanged timeout. Message only when the child needs
information or action. On a capacity rejection, wait for, reuse, or combine
existing children. Report outcome changes, not routine child activity.

## Integrate

Assess results against their assignments and actual checks, then run shared
checks. Resolve missing evidence before retrying. Before closing, account for
every child as completed, errored, interrupted, or reclaimed.

## Configuration

Read [configuration.md](references/configuration.md) to install Agent Team or
edit role files, and [professional-agents.md](references/professional-agents.md)
to create or evaluate a recurring project specialist.
