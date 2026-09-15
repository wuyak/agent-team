# Coordinate Dependent Work

Use this method when agents share an unsettled interface or input, a consumer
needs an intermediate result, or an upstream change affects downstream work.
The parent arranges the dependencies; each owner retains technical autonomy
within its assignment. Apply the relevant steps as the situation develops.

## Identify what releases the consumer

Start from the consumer's next useful action and identify the upstream result
needed to perform it. That result may be smaller than the producer's complete
assignment: a decision, an agreed interface, a verified data sample, or a
working implementation.

For example, a project connecting to a shared SQL publisher needs its inputs,
return fields, and partial-failure semantics to implement the connection. It
needs the working publisher for a real integration check, but may not need
the producer's remaining documentation. Distinguish those handoffs so neither
side waits for unrelated work.

Keep only dependency details that change an assignment or next action: the
needed result, its owner, the consuming work, and what makes it usable. Use
the existing assignment or project material; a separate dependency document
is warranted only when it helps owners coordinate or resume the work.

## Choose what can proceed

Identify why the needed result is unavailable:

| Unresolved dependency | Arrangement |
| --- | --- |
| A consequential user choice or missing authorization | Bring the choice and its consequences to the calling workflow; continue work independent of that choice. |
| A technical fact or behavior is unknown | Assign the investigation or experiment that can settle it. Keep dependent production work behind that result. |
| The agreement is settled; implementation is pending | Let consumers implement against it while the producer builds it. Reserve real integration checks for the delivered implementation. |
| Owners repeatedly need to decide the same behavior together | Put the coupled decisions with one owner, or complete a focused joint handoff before splitting further. |

Resolve only the details that could invalidate the consumer's work. The
producer can still choose internal structure and methods. Different files
may depend on the same unsettled decision; conversely, independent parts of
one outcome can proceed while a dependency is unresolved.

Exploratory prototypes may help settle a dependency when their assumptions
and purpose are explicit. Treat their results as evidence for that decision,
not as a completed consumer implementation.

If a consumer has no useful independent work, defer its assignment until the
dependency is usable. If already running, have it finish the independent
part and return its current result and blocker; resume the related owner
when the missing result arrives. Respect any explicitly assigned monitoring
or waiting duty.

## Arrange a usable handoff

Agree who supplies the result, who needs it, and how readiness will be
communicated. Ask for an interim delivery only when it enables another
owner's work before the producer's final. Use an available native messaging
path; if interim communication is unavailable, make the needed result a
bounded final delivery and sequence the work through the parent.

The handoff should identify the artifact or decision, its applicable version
or conditions when these matter, the evidence that supports use, and any
differences from the previous agreement. Explain remaining gaps that affect
the consumer. Keep this information in the artifact or message that carries
the result rather than duplicating it in a separate status report.

Pass the result to the affected owner with the next action it enables. The
consumer checks the boundary it relies on and completes its remaining work.
It can reuse upstream checks whose conditions still apply; mocks establish
behavior against an assumed interface, not integration with an implementation
that has not arrived. The parent owns any acceptance that spans assignments.

## Apply changes to affected work

When an agreement, input, or authority changes, identify which consumers rely
on the changed part and what each is currently doing:

- For work not started, update the assignment before dispatch.
- For ongoing work, redirect the affected part; when continued action could
  produce invalid or conflicting writes, stop it and confirm interruption
  before replacement.
- For delivered work, check whether its result and evidence still apply.
  Reopen only the part made invalid or uncertain by the change.

Provide the changed decision, which earlier assumption it replaces, and the
required adjustment together. Retain unaffected results and checks. Repeated
coordination over the same unsettled behavior is evidence to reconsider the
division of ownership, not simply to send more detailed instructions.

For the SQL publisher, a change to whether skipped documents update metadata
affects consumers that publish or interpret that metadata. It need not reopen
unrelated document collection. Check each consumer against the changed
behavior before deciding its work must be redone.

## Return to ordinary coordination

Once owners know what can proceed, which result remains outstanding, and who
will perform the remaining checks, use the main Skill's event and acceptance
rules. Revisit this method when a dependency or assumption changes; ordinary
progress does not require rebuilding the arrangement.
