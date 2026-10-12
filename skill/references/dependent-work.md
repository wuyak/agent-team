# Coordinate Dependent Work

Use this method when agents share an unsettled interface or input, a consumer
needs an intermediate result, or an upstream change affects downstream work.
The parent arranges dependencies; each owner retains autonomy within its
assignment.

## Identify what releases the consumer

Start from the consumer's next useful action and identify the smallest upstream
result that enables it: a decision, agreed interface, verified data sample, or
working implementation.

For example, a project connecting to a shared SQL publisher needs its inputs,
return fields, and partial-failure semantics to implement the connection. It
needs the working publisher for a real integration check, but may not need
the producer's remaining documentation. Distinguish those handoffs so neither
side waits for unrelated work.

Record only what changes an assignment or next action: the result, its owner,
the consuming work, and what makes it usable. Create a separate dependency
document only when it helps owners coordinate or resume.

## Choose what can proceed

Identify why the needed result is unavailable:

| Unresolved dependency | Arrangement |
| --- | --- |
| A consequential user choice or missing authorization | Bring the choice and its consequences to the calling workflow; continue work independent of that choice. |
| A technical fact or behavior is unknown | Assign the investigation or experiment that can settle it. Keep dependent production work behind that result. |
| The agreement is settled; implementation is pending | Let consumers implement against it while the producer builds it. Reserve real integration checks for the delivered implementation. |
| Owners repeatedly need to decide the same behavior together | Put the coupled decisions with one owner, or complete a focused joint handoff before splitting further. |

Resolve only details that could invalidate the consumer's work; the producer
still chooses its internal structure and methods. Several files may share one
dependency, while independent parts of one outcome can proceed.

Exploratory prototypes may help settle a dependency when their assumptions
and purpose are explicit. Treat their results as evidence for that decision,
not as a completed consumer implementation.

If a consumer has no independent work, defer it. If already running, have it
finish the independent part and return its result and blocker; resume it when
the dependency arrives. Respect any assigned monitoring or waiting duty.

## Arrange a usable handoff

Name the producer, consumer, and readiness signal. Request an interim delivery
only when it enables work before the producer's final. If interim communication
is unavailable, make the result a bounded final delivery and sequence it
through the parent.

The handoff carries the artifact or decision, relevant version or conditions,
supporting evidence, changes from the previous agreement, and remaining gaps.
Keep this in the artifact or message, not a duplicate status report.

Pass the result with the next action it enables. The consumer verifies the
boundary it relies on and may reuse upstream checks whose conditions still
apply. Mocks establish behavior against an assumed interface, not integration
with an undelivered implementation. The parent owns cross-assignment acceptance.

## Apply changes to affected work

When an agreement, input, or authority changes, identify which consumers rely
on the changed part and what each is currently doing:

- For work not started, update the assignment before dispatch.
- For ongoing work, redirect the affected part; when continued action could
  produce invalid or conflicting writes, stop it and confirm interruption
  before replacement.
- For delivered work, check whether its result and evidence still apply.
  Reopen only the part made invalid or uncertain by the change.

Provide the changed decision, the assumption it replaces, and the required
adjustment together. Retain unaffected results and checks. Repeated coordination
over the same behavior is evidence to reconsider ownership.

For the SQL publisher, a change to whether skipped documents update metadata
affects consumers that publish or interpret that metadata. It need not reopen
unrelated document collection. Check each consumer against the changed
behavior before deciding its work must be redone.

## Return to ordinary coordination

Once owners know what can proceed, what remains outstanding, and who performs
the remaining checks, return to the main Skill's coordination and integration
rules. Revisit this method only when a dependency or assumption changes.
