---
name: agent-team
description: >-
  Delegate work to native Codex subagents. Use when the user asks for
  subagents, or when retrieval, execution, observation, or independent review
  can be handed off. Covers what to delegate, role and effort choice,
  assignments, waiting, and integrating results; spawning a child is optional.
---

# Agent Team

The parent owns the division of work, handoffs, and integration. The calling
workflow keeps task-level decisions and engineering methods.

## Decide what to delegate

Delegate an outcome a child can own and verify with the context and authority
the assignment gives it. Delegation is optional, and it can pay off without
parallel work by keeping execution out of the parent's context. Keep work that
is simpler to do directly, or that needs continuous judgment grounded in the
conversation; in troubleshooting, the parent owns the overall diagnosis.

Run independent outcomes in parallel and batch small changes of the same kind.
Give each document, and each module that needs one consistent judgment, a
single author: the parent or one execution child, who writes the body and
applies every revision. Other children contribute material, findings, and
evidence.

When agents share an unsettled interface or input, a consumer needs an
intermediate result, or an upstream change may invalidate other work, read
[dependent-work.md](references/dependent-work.md).

## Choose the role and effort

Choose a role from those the spawn tool lists, by its description. A role may
fix its model or reasoning effort; Codex applies a fixed value regardless of
the spawn request and marks it as unchangeable. Pass a model only when the
user specifies one for this delegation.

A role description may end with a line such as
``Effort: `high`; `xhigh` when hard.`` For such a role, pass one of the two
efforts at spawn. A child keeps its effort for its whole life, including
follow-up tasks:

- The first for ordinary work in the role, including long or important work.
- The second when the work turns on hard reasoning: reconciling conflicting
  evidence, choosing among competing explanations or designs, or following
  behavior across modules.

When a child's return shows it was stuck on reasoning rather than on missing
information or scope, assign the hard part to a new child at the second
effort, and put the first child's findings in its assignment. For a role with
neither a fixed effort nor an effort line, omit effort so the configured
default applies. The user's explicit effort for this delegation takes
precedence.

## Write the assignment

Spawn with `fork_turns="none"` and a self-contained assignment. Inherit recent
turns only when necessary context cannot be restated, and full history only
when a smaller selection is insufficient. State:

- the outcome and why it is needed;
- scope, available evidence, and permitted operations, including the files the
  child owns when others work nearby;
- decisions the child may make, and choices it must return to the parent;
- the evidence that will show the outcome is done;
- for a pending dependency, who delivers it and how the child will hear.

The child needs its own access to the tools the work requires; resolve or
report a gap before spawning. Send related follow-ups to the same child so its
context carries over.

## Wait and communicate

When the next step needs a specific child's result, or nothing independent
remains, wait with the native wait tool (`wait_agent`) at its default timeout,
and keep waiting after a timeout with no new development. Send a message only
to supply information or request action that a current decision or handoff
needs; judge progress by the child's work and delivery. On a capacity
rejection, wait for, reuse, or combine existing children before retrying.

Tell the user about changes to the overall outcome, not each child's activity.

## Integrate and close

Accept a result against its assigned outcome and the conditions its checks
actually covered, then run the integration checks that span assignments. When
a child ends without a usable result, find what is missing before retrying.
Before closing the task, account for every child as completed, errored,
interrupted, or reclaimed.

## Configuration

Read [configuration.md](references/configuration.md) to install Agent Team or
edit role files, and [professional-agents.md](references/professional-agents.md)
to create or evaluate a recurring project specialist.
