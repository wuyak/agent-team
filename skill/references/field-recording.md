# Field Recording

Capture native-agent facts through Codex Hooks and finalize one immutable record per delegated
parent turn. Keep ordinary closeout event-driven. When the current runtime omits a lifecycle or
failed-tool hook, recover only the parent suffix written after the first Agent call; never load a
complete child JSONL into memory.

## Contents

- Primary hook path
- Storage and identity
- Incremental collection
- Runtime cost model
- Closeout and assessment
- Schema v3
- Fallback, corrections, and backfills
- Privacy
- Hook configuration and trust
- Commands and validation

## Primary Hook Path

Use Codex's native `PreToolUse`, `PostToolUse`, `SubagentStart`, and `SubagentStop` events. The
installed user hook runs `scripts/record_hook.py ingest` only for current dispatch, communication,
and reconfiguration tools (`spawn_agent`, `followup_task`, `send_message`, and `interrupt_agent`)
or subagent lifecycle events. Read-only `wait_agent` and `list_agents` calls are not recorded. A
turn with no native-agent activity does not invoke this recorder.

The collaboration runtime may omit a lifecycle hook on some execution paths, may omit
`PostToolUse` for a rejected spawn, and may return only an agent path—not its UUID—from a successful
spawn. The collector therefore treats lifecycle hooks as the preferred source, not as an
assumption. Each Agent `PreToolUse` captures the parent transcript path and its current byte size.
Finalization consults that saved offset only if the hook journal is incomplete.

Lifecycle hooks can also report the child's own turn id rather than the delegated parent turn id,
and may arrive after a later parent turn has already begun. Parent-turn identity must therefore
come from stable evidence, never arrival order or a mutable "current turn" pointer. A successful
Agent tool result binds a direct child UUID immediately. If the result exposes only an agent path,
the collector records that path as a candidate and lets the child transcript's matching
`agent_path` resolve it. A successful follow-up target is accepted only after `PostToolUse`; UUID
targets bind directly, while canonical path targets remain subject to the same uniqueness rule.
Once a lifecycle start is owned, its child turn id is retained as stable history, so an old stop
that arrives after a later follow-up still returns to its original parent turn. A follow-up never
claims lifecycle evidence that was already pending without a stable owner; that evidence remains
pending until UUID history, a unique path, or an explicit parent resolves it.

When `SubagentStart` or `SubagentStop` arrives before any such binding exists, the sanitized event
is kept in `pending-lifecycle/`. It is appended to a parent-turn journal only after UUID, unique
path, or explicit-target evidence resolves the owner. Ambiguous paths remain pending rather than
being assigned to whichever parent turn ran most recently. This keeps one record per delegated
parent turn without cross-turn contamination.

The hook path captures:

- requested role, model, and reasoning effort without retaining the child prompt;
- sanitized `fork_turns` request shape for `spawn_agent` plus explicit omission/invalid/not-observed
  status, without retaining the prompt or raw value;
- dispatch success or a bounded error classification without retaining raw tool output;
- parent thread id, parent turn id, child id, role, and working directory;
- stable child/session identity evidence: `child_session_id`, `parent_thread_id`,
  `parent_turn_id`, and (when available) `agent_path`; task names and timestamps are never
  used as identity joins;
- requested and observed roles are separate (`requested_role`, `actual_role`,
  `role_binding_source`). Missing child role metadata remains `null`/unlabeled rather than
  becoming `default`; requested-role sources use `spawn-request`, `agent-path-join`,
  `conflicting-requests`, or `not_observed`, while actual-role binding uses
  `child-session-meta` or `not_observed`;
- child transcript path and whether a final assistant message existed;
- actual runtime, lifecycle, tool counts, token usage, and nested-agent calls through an
  incremental byte cursor.
- child fork observation from `session_meta.forked_from_id`, kept separate from the request and
  never used to alter the child-local metric boundary.

Coordination telemetry is intentionally post-hoc and allowlisted.  The hook matcher still
excludes `wait_agent` and `list_agents`, so ordinary polling never starts Python.  A delegated
closeout may stream the suffix beginning at the parent offset captured by the first Agent call;
that one pass counts `spawn_agent`, `followup_task`, `send_message`, `interrupt_agent`,
`wait_agent`, and `list_agents` occurrences, classifies explicit wait results as
`timeout`/`completed`/`error`/`unknown`, and records requested/observed wait milliseconds only
when a numeric field is present.  It keeps only counters and short-lived call ids for
de-duplication (with a fixed per-turn cache bound); prompts, message bodies, and tool output are
never retained.  The cursor makes the work O(new suffix bytes) with bounded metric state and no
historical replay.

`coordination_metrics` is explicit about evidence.  Its operation counts are observed when the
parent suffix was scanned (or are marked `unknown`/`legacy` when no parent path or only replay
evidence exists).  `max_consecutive_timeout_without_agent_update` advances only across explicit
timeout results without a native `sub_agent_activity` event.  `commitment`, `ready_transition`,
`steering_applied`, and `native_live_status` remain `not_observed` unless a native event names the
transition.  A `PostToolUse` result with `status=completed` records a completed tool call; it is
never promoted to an applied-steering acknowledgement.

The coordination classifier version is stored with the parent cursor.  If the classifier changes,
the collector discards only the stale coordination cache and re-scans the already-captured
`start_offset`→cursor suffix; lifecycle counters and bindings are not replayed, and no full history
walk is introduced.  A dry-run performs that same bounded computation entirely in memory and does
not update cursors, child cursors, bindings, locks, or finalized pointers.

The same bounded suffix pass may count privacy-safe parent assistant messages only when the stable
identity schema is present: `response_item` with `payload.type=message`, `role=assistant`, and
`internal_chat_message_metadata_passthrough.turn_id` equal to the current turn.  It reads only the
explicit `phase` (`commentary`, `final_answer`, or `unknown`).  `event_msg` records with
`payload.type=agent_message`, or any message without the current-turn identity, are not counted;
they must not be mistaken for parent updates.  Message bodies, senders, and child identities are
neither stored nor attributed.  Hook-era records mark this counter `observed` when the suffix was
available, while replay records mark it `legacy` and missing-parent records mark it `not_observed`.
Unattributed child chatter remains `not_observed` because it has no stable sender/event evidence.

When the parent transcript exposes the stable handback schema (`response_item` with
`type=agent_message`, `author=/root/...`, `recipient=/root`, and the current turn id), the pass
also records aggregate `child_handback_counts` for an exact first line of `Message Type: MESSAGE`
or `Message Type: FINAL_ANSWER` (otherwise `unknown`).  It never stores the text, author, or a
per-child attribution.  Missing or legacy schema evidence stays `not_observed`/`legacy`.

At normal closeout, finalize from the small hook journal:

    python3 scripts/record_hook.py finalize \
      --parent-thread-id <parent-thread-id> \
      --turn-id <turn-id>

When all required hook fields arrived, this command does not read the parent transcript for
lifecycle recovery. A delegated closeout nevertheless performs one bounded coordination pass when
a captured path/offset is available: it streams only the new suffix after the first Agent offset to
count the allowlisted operations, then stores a sanitized cursor. Without a parent path, polling
remains `unknown`. It never rereads bytes already processed from a parent or child transcript.
Idempotency is based on an evidence revision, not merely the hook-event count, so an appended child
turn or a late lifecycle event produces an immutable correction while an unchanged closeout is
O(1).

## Storage and Identity

Keep records and bounded hook state outside the Skill source:

    ~/.codex/agent-team-records/
    ├── routine/
    ├── anomalies/
    └── hook-state/
        ├── turns/       # small sanitized event journals
        ├── cursors/     # child byte offsets plus aggregate counters
        ├── parent-cursors/ # sanitized gap recovery and suffix offsets
        ├── agents/      # temporary child-to-parent bindings, removed at terminal closeout
        ├── child-turns/ # durable child-turn ownership for late stop routing
        ├── path-bindings/ # stable agent-path candidates from spawn results
        ├── pending-lifecycle/ # sanitized lifecycle events awaiting stable ownership
        ├── event-keys/  # per-turn semantic idempotency markers
        ├── event-key-cursors/ # crash-recoverable indexed journal offsets
        ├── metric-snapshots/ # per-child cumulative baselines and assignment sequence
        ├── finalized/   # O(1) idempotency pointers
        └── locks/       # concurrent-hook serialization

Write one routine record per parent turn that attempted delegation, not one file per child and not
one file per conversation. Preserve every child instance in `agents` and multiplicity in
`role_counts`.

Each record identifies the project, parent thread, parent turn, abstract task signature, source,
and collection mode. Never infer project identity from the recorder process working directory;
Agent tool hooks and lifecycle hooks supply the native session `cwd`.

## Incremental Collection

`SubagentStop` normally supplies the child transcript path. If that hook is absent but the parent
suffix supplied a child UUID, the collector locates the matching session filename beside the
parent session (or a UUIDv7-derived date directory with minimal adjacent-day tolerance). Ordinary
closeout never recursively walks the global sessions tree; it then opens that child JSONL at its
saved byte offset, streams newly appended lines, updates aggregate counters, and persists the new
offset. It never builds an in-memory list of transcript events.

For a first stop, every child byte is read once. For a resumed or follow-up child, later stops read
only the appended suffix. Parent recovery is conditional: complete hooks cost zero parent reads;
incomplete hooks cause one streaming scan from the byte size captured before the first Agent tool
ran. That scan keeps only matching spawn outcomes, bounded error codes, child UUID/path, and
lifecycle timestamps. A turn-completion marker keeps a narrow same-turn recovery window open for
late Agent evidence. An explicit later `task_started` or different turn id seals that window, so
later conversation turns do not trigger false corrections.

`SubagentStop` may fire just before the child writes its final `task_complete` event. Finalization
therefore refreshes each known child cursor once; if nothing was appended this is an O(1) stat and
cursor read, while newly appended terminal bytes update the same aggregate without replay.

A child `task_complete` closes its current turn, not necessarily the addressable child thread. A
later successful follow-up may append another `task_started` to the same transcript. The collector
therefore preserves one child identity with a `turn_count` and derives current status from the latest
start/terminal ordering. It does not interpret `task_complete` as physical closure or invent a close
operation that the active collaboration tools do not expose.

Pending lifecycle drain and parent-journal append share the parent-turn lock, so finalization
cannot observe the temporary event as deleted before it is durably appended. Sanitized hook events
also carry a semantic idempotency key. A per-turn key index and journal byte cursor reconcile only
the unindexed suffix before and after append. If a process exits after the journal is durable but
before its key marker is committed, the next hook repairs that suffix before deduplication; a legacy
journal is streamed once to initialize the index. Replayed hooks with the same evidence are ignored,
while a real state change such as newly appended terminal bytes produces a distinct event and correction.
Lifecycle routing and UUID follow-up rebinding additionally share a per-agent routing lock, so a
concurrent pending event cannot appear between the follow-up's ownership check and binding update.

The public Hook schema does not expose reasoning effort, token totals, turn multiplicity, or every
terminal detail. Reading each child byte once is therefore the narrow deterministic enrichment
needed to preserve those facts. The cursor makes this O(new bytes) I/O with bounded memory rather
than O(full history) replay at every closeout.

When a child is created with recent or full turn forking, its transcript may contain inherited
parent events after the child `session_meta`. Those inherited events are context, not child runtime.
The incremental reader must advance across them without adding their tasks, tools, tokens, or final
messages to child metrics. Anchor child-local accounting at the child session start and preserve that
boundary for later follow-up suffixes. Records created before this boundary was enforced remain
immutable historical evidence; do not use their aggregate child token or turn totals for cost claims.

## Runtime Cost Model

There are three bounded costs:

- ordinary turns without Agent Team: one short `Stop` recorder process that checks for the exact
  parent-thread/turn journal and exits without creating state; it performs zero transcript I/O;
- each matched dispatch/communication/reconfiguration or lifecycle hook: one short Python process
  that reconciles only the unindexed journal suffix, consults a per-event key marker, and appends one
  small sanitized JSON line; `wait_agent` and `list_agents` do not start the recorder;
- delegated closeout: one small journal read, plus only new child bytes and one bounded parent
  suffix pass when a parent path/offset is available for coordination telemetry.  A repeated
  closeout consumes no additional suffix bytes; missing parent path evidence leaves polling and
  wait outcomes `unknown`.

The number of historical records does not affect ordinary closeout. Only the explicit full audit
walks the record library, so its cost grows with record count and belongs to maintenance rather
than every task.

## Closeout and Assessment

The ordinary closeout path is automatic. A global `Stop` hook invokes `record_hook.py
auto-finalize`; it writes a record only when the exact parent thread and turn already have a native
Agent journal and the Stop payload's final assistant message exactly matches the identity-bound
`final_answer` already present in the Codex parent transcript. This closes failed-dispatch turns as
well as successful child lifecycles while preventing a premature simulated Stop from entering the
formal record directories. A turn with nonterminal children is deferred without writing; the first
later terminal `SubagentStop` completes the already-observed parent Stop and writes the sole formal
record. Once a finalized pointer exists, repeated `Stop` or lifecycle events are idempotent and never
create a second routine record for that parent turn.

Finalize only after child results have been absorbed and checked, or after a dispatch failure is
terminal and the parent has selected its fallback. Before a normal closeout, the main agent uses
native coordination to confirm that every child is `completed`, `errored`, `interrupted`, or has
otherwise been explicitly reclaimed. A nonterminal child does not block writing an immutable
hook journal, but it does block the formal snapshot until terminal evidence arrives. Finalization is
deterministic and requires no model-authored summary.

Semantic assessment is optional and preview-only on the production root. Add it only when it
provides real decision value:

    python3 scripts/record_hook.py finalize \
      --parent-thread-id <parent-thread-id> \
      --turn-id <turn-id> \
      --task-signature <abstract-signature> \
      --agent-summary '<child-id>=Concise decision delta.' \
      --role-fit clear \
      --upgrade none \
      --team-value positive \
      --evidence 'Accepted bounded evidence from the child.' \
      --dry-run

If one semantic assessment field is supplied, all four required fields must be supplied. Do not
launch an evaluator, ask children to grade themselves, or run extra searches or tests only for
recording.

## Schema v3 (Historical)

Hook-generated schema-v3 records contain:

- `collection_mode=codex-hooks-incremental`;
- aggregate outcome and observed start/end/duration;
- every sanitized spawn attempt and its requested runtime, outcome, child id, and error code;
- every child id, role, nickname, actual runtime samples, status, full-session timing, turn count,
  event/tool/token counts, nested-agent count, and transcript path;
- per-child metric scope/validity/observability (`metric_scope`, `metric_validity`,
  `metric_observability`) so fork-boundary failures and legacy replay are not treated as clean
  child-local measurements;
- explicit `service_tier` with `service_tier_source`/`service_tier_observability`; when the runtime
  does not expose a tier it is `null`/`not_observed` and is never inferred from a profile;
- sanitized `spawn_agent` fork requests in `requested_fork_turns`: only `none`, `all`, or a
  positive integer are retained. `requested_fork_turns_observability` distinguishes an explicit
  request (`observed`) from an omitted field (`omitted`), malformed input (`invalid`), and replay
  or missing-hook evidence (`not_observed`/`legacy`); no prompt or arbitrary value is persisted;
- child fork evidence is separate from the request: `fork_observed`,
  `fork_observation_source`, and (when present) `forked_from_id` come only from child
  `session_meta.forked_from_id`. A child session header without that field proves `false`; absent
  metadata stays unknown. A proven contradiction is recorded as the bounded
  `fork-request-observation-mismatch` anomaly. This telemetry does not change the child metric
  boundary or historical records that lack these optional fields;
- optional parent assessment, otherwise `assessment=null`;
- `coordination_metrics` with allowlisted operation counts, wait outcome buckets, bounded wait
  totals, timeout streak evidence, and explicit `not_observed` state for unexposed transitions;
- collector evidence distinguishing hook-only, incremental-parent-tail, and explicit legacy
  recovery; byte cursor boundaries; child bytes processed; and proof that raw prompts/messages
  were not stored.

The collector automatically writes anomalies for failed dispatches, explicit-request or
native-role runtime mismatch, nested delegation, a nonterminal child lifecycle, unresolved
terminal task-to-agent binding, a child labeled completed without a tool call or final message,
a completed child with tool activity but no final assistant message (`child-delivery-failure`), or a
an explicit fork request contradicted by child session metadata
(`fork-request-observation-mismatch`), or a later immutable correction. Binding is evidence-based: tool
ids, direct child ids, explicit follow-up targets, and unique child-session paths may establish
identity; a previously bound child turn preserves that ownership across follow-ups, but concurrent
start/stop order never does. If a successful terminal spawn cannot be
connected uniquely, the record keeps it `unbound` or `ambiguous`, preserves any candidates, and
adds `agent-binding-uncertainty` instead of guessing. Records emitted under lifecycle policy v1
also require `child-lifecycle-incomplete` whenever a child lacks terminal or reclaim evidence;
the audit does not retroactively impose this requirement on historical records without the policy
marker. Records emitted under zero-yield policy v1 likewise require `zero-yield-child` when a
completed child has neither a tool call nor final-message characters; historical records without
that marker are not retroactively reclassified. Records emitted under child-metric-boundary v1
exclude inherited fork context from the child's turns, tools, tokens, and final-message totals.
Records with an unresolved fork boundary carry `metric_validity=invalid` and
`metric_scope=fork-boundary-unknown`; historical replay records remain explicitly marked as
`transcript-replay`/legacy and are not backfilled during normal closeout.
`metric_boundary_method` records whether a boundary came from an exact child id or UUIDv7 anchor
(`child-id`/`uuidv7-time`) versus a timestamp/gap legacy fallback; the latter is
`metric_validity=legacy-unverified` and must not support efficiency claims.
Current and historical role runtimes and service tiers come from
`~/.codex/agent-team-policy.toml`. The recorder resolves expectations by role and session start time;
it does not duplicate model, effort, tier, alias, or cutover constants. Missing/null observed tiers
are not mismatches. Historical runtime remains explicit and immutable, while new sessions are judged
against the policy revision effective when they started.

## Schema v4 (Future Hook Records)

Schema v4 leaves schema-v2/v3 files immutable and makes the evidence boundary explicit:

- `finalization` records whether the snapshot came from the parent `Stop` hook, a late
  `SubagentStop` correction, or an explicit manual closeout;
- `runtime_resolution.requested`, `.expected`, and `.observed` are separate. The expected model,
  effort, canonical service tier, tier aliases, configured sandbox, policy version, and effective
  timestamps are resolved from `agent-team-policy.toml`; an unexposed observed service tier remains
  `null`/`not_observed` rather than being filled with the expected value;
- `effective_authority` records the child-local `sandbox_policy.type`,
  `permission_profile.type`, approval policy/reviewer, Hook permission mode, and only the count of
  workspace roots. It never stores workspace paths;
- `runtime_provenance` records bounded child `cli_version`, `originator`, `thread_source`, and
  multi-agent version;
- `metric_snapshot` declares `cumulative-child-session` semantics and carries a stable child
  sequence, same-assignment revision, baseline cumulative values, current cumulative values, and
  the non-overlapping delta. Consumers aggregate only deltas from active (non-superseded) records;
- a parent session id returned as a child id is rejected before binding, remains a bounded failed
  attempt, and is classified as `agent-binding-uncertainty` rather than becoming an agent row;
- a proven mismatch between the configured role sandbox and effective child sandbox produces
  `authority-contract-mismatch`.

The full audit continues to validate active schema-v2/v3 records while new Hook records use schema
v4. Transcript replay remains schema v3 and is a maintenance fallback, not the future collection
path.

Historical `record_closeout.py record` output carries the same field as
`coordination_metrics`, but marks it `observability=legacy` (or `unknown` when no parent session
was supplied).  It does not infer readiness, commitment, live status, or steering application
from replayed tool completions.

Structured Agent Team records only exist for turns that attempted native-agent activity. They can
detect dispatch, lifecycle, binding, runtime, and zero-yield defects, but cannot prove that a root
turn should have delegated and did not. An explicit routing-quality audit must therefore compare the
same time window of root task transcripts with the Agent Team records, then judge state transitions,
evidence partitions, long-running observation targets, exclusive implementation boundaries, and
semantic conflicts. Tool count or elapsed time may select candidates for review but is never itself
proof of under-delegation.

The maintenance-only coverage reconciliation compares a bounded window of child `session_meta`
headers with active structured records. It reports missing coverage, true duplicate assignments,
role mismatches, unmatched rows, and invalid metric identifiers without reading or copying
prompts/messages. A true duplicate repeats the same parent thread, parent turn, and child session;
reuse of one stable child across different parent turns is reported separately as informational
`reused_child_records` and does not invalidate coverage. If the child was created before the window
but an in-window structured row carries its exact `session_path`, reconciliation reads only that
header and reports `out_of_window_session_evidence`; it does not rescan that historical transcript
or treat the valid follow-up as missing:

    python3 scripts/record_closeout.py reconcile \
      --root ~/.codex/agent-team-records \
      --sessions-root ~/.codex/sessions \
      --since <iso-start> --until <iso-end>

Use repeated `--session <child.jsonl>` for a smaller explicit window. Root transcripts without
child markers are ignored. Ordinary closeout never runs this historical walk and remains O(new
bytes).

## Fallback, Corrections, and Backfills

Use `scripts/record_closeout.py record` only when:

- repairing or backfilling historical turns created before hooks existed;
- hook events are missing because the definition was not trusted or enabled;
- investigating collector failure from the original transcripts.

That command is deliberately labeled `collection_mode=transcript-replay-fallback`. It may replay
complete transcripts and is not the ordinary closeout path.

For a legacy hook journal that captured Agent events before parent path/offset metadata was added,
repair that one turn with `record_hook.py finalize --parent-transcript <path>`. This explicitly
marks `parent_legacy_full_scan=true`; new hook journals must not need the option.

Treat routine JSON files as immutable. A correction writes a successor with `supersedes`; never
rewrite or delete the prior record. Use `source=backfill` when no prior record exists.

## Privacy

Never store raw prompts, source excerpts, command output, credentials, personal data, or complete
assistant messages. Hook ingestion uses a field allowlist:

- child, follow-up, and communication `message` fields plus child `items` are discarded;
- raw tool responses are reduced to child id, nickname, outcome, and bounded error code;
- new terminal/dispatch error fields use only `unknown-model`, `invalid-dispatch`, `not-found`,
  `timeout`, `interrupted`, `tool-error`, or `unknown`; previously written legacy text is not
  rewritten;
- `last_assistant_message` is reduced to character count;
- transcript content is reduced to counters and runtime metadata.
- fork telemetry keeps only the allowlisted request shape and boolean/unknown child observation;
  invalid values are classified without storing their text, and `forked_from_id` is retained only
  as the already-sanitized child/session identity evidence.
- parent-tail recovery ignores unrelated tool output and stores only sanitized evidence for the
  allowlisted coordination calls (with bounded wait outcome/duration counters).

Turn or assignment-level accounting may retain sanitized identities, timestamps, terminal state,
and aggregate counter deltas. It must not persist follow-up bodies, progress prose, source or file
lists, test commands or output, or other transcript content merely to compensate for a client UI
that shows only a generic agent-update marker.

Original native transcripts remain the detailed evidence pointer; the Agent Team record does not
copy their contents.

## Hook Configuration and Trust

The user hook is stored in `~/.codex/hooks.json`. It must contain handlers for current Agent-matched
`PreToolUse`/`PostToolUse`, `SubagentStart`, `SubagentStop`, and one global `Stop` closeout. The static
validator checks matcher coverage, deliberate polling exclusions, lifecycle handlers, the exact
`auto-finalize` Stop command, and the recorder path. The Stop process performs an exact journal-path
existence check before creating any directory or reading a transcript, so ordinary turns are a
bounded no-op while delegated turns cannot silently remain unfinalized.

Codex requires review and trust whenever a non-managed hook definition changes. In Codex Desktop,
open the user-configuration Hook list, expand each changed hook, review its command, click Trust,
and enable its toggle. Another client may expose a `/hooks` command; use it only when that command
is actually listed by the client. Until the changed definition is trusted and enabled, Codex skips
the hook; use the replay fallback only for delegated turns that occurred in that interval.

## Commands and Validation

Inspect whether the current parent turn has captured events or a finalized pointer:

    python3 scripts/record_hook.py status \
      --parent-thread-id <parent-thread-id> \
      --turn-id <turn-id>

Preview finalization without writing:

    python3 scripts/record_hook.py finalize \
      --parent-thread-id <parent-thread-id> \
      --turn-id <turn-id> \
      --dry-run

`auto-finalize` is reserved for the configured Codex `Stop` hook. Never pipe a synthetic Stop
payload into it. The production record root rejects non-dry-run manual `finalize`; simulations and
tests must use `--dry-run` or an isolated temporary root.

Run the full historical audit only after schema/collector changes, corrections, backfills, or
explicit maintenance—not after every routine closeout:

    python3 scripts/record_closeout.py audit

Run bounded coverage reconciliation only as an explicit maintenance action:

    python3 scripts/record_closeout.py reconcile --help

After changing the policy/controller, run only its focused tests and bounded coherence check:

    python3 -m unittest scripts/test_agent_policy.py scripts/test_agent_speed.py
    python3 scripts/agent_speed.py validate

`record_common.py` is an internal dependency loaded from the same `scripts/` directory as both
recording entrypoints; deploy and validate these files together.

After changing this collector or its record contract, run the focused affected recorder tests first;
run the complete recorder suite only when the change spans the collector or schema broadly:

    python3 -m unittest scripts/test_record_hook.py scripts/test_record_closeout.py scripts/test_record_common.py
    python3 -m py_compile scripts/record_common.py scripts/record_hook.py scripts/record_closeout.py
    python3 scripts/validate_agent_team.py
    python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
      "${CODEX_HOME:-$HOME/.codex}/skills/agent-team"
