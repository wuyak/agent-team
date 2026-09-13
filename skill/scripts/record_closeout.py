#!/usr/bin/env python3
"""Replay transcripts for historical Agent Team backfills, corrections, and hook gaps."""

from __future__ import annotations

import argparse
from graphlib import CycleError, TopologicalSorter
import json
import os
import secrets
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import record_common  # noqa: E402
from agent_policy import default_codex_home  # noqa: E402


DEFAULT_ROOT = record_common.DEFAULT_RECORD_ROOT
SCHEMA_VERSION = 3
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
ANOMALY_TYPES = (
    "dispatch-failure",
    "runtime-capability-mismatch",
    "topology-violation",
    "agent-binding-uncertainty",
    "child-lifecycle-incomplete",
    "zero-yield-child",
    "child-delivery-failure",
    "fork-request-observation-mismatch",
    "record-correction",
)
TOOL_CALL_TYPES = {"custom_tool_call", "function_call"}
TOOL_OUTPUT_TYPES = {"custom_tool_call_output", "function_call_output"}
COORDINATION_OPERATIONS = record_common.COORDINATION_OPERATIONS
is_spawn_tool = record_common.is_spawn_tool
normalize_coordination_operation = record_common.normalize_coordination_operation
classify_wait_outcome = record_common.classify_wait_outcome
payload_dict = record_common.payload_dict
ulid_time_key = record_common.ulid_time_key
parse_timestamp = record_common.parse_timestamp
lifecycle_incomplete_messages = record_common.lifecycle_incomplete_messages
delivery_failure_messages = record_common.delivery_failure_messages
fork_request_observation_mismatch_messages = record_common.fork_request_observation_mismatch_messages
parent_message_phase = record_common.parent_message_phase
elapsed_ms = record_common.elapsed_ms


def empty_coordination_metrics(
    *, observability: str = "legacy", source: str = "transcript-replay"
) -> dict[str, Any]:
    return record_common.empty_coordination_metrics(
        observability=observability, source=source, evidence="legacy"
    )


def sanitize_requested_fork_turns(
    arguments: dict[str, Any], *, legacy: bool = True
) -> tuple[str | int | None, str]:
    """Canonicalize replayed fork requests without retaining arbitrary input."""
    return record_common.sanitize_requested_fork_turns(
        arguments,
        missing_observability="not_observed" if legacy else "omitted",
    )


def classify_tool_error(value: Any) -> str | None:
    """Reduce raw tool/terminal error evidence to a finite privacy-safe code."""
    if isinstance(value, dict):
        if value.get("isError") is True:
            return "tool-error"
        parts = [str(item) for item in value.values() if isinstance(item, (str, int, float, bool))]
        text = " ".join(parts).lower()[:4096]
    elif isinstance(value, str):
        text = value[:4096].lower()
    else:
        text = str(value)[:4096].lower() if value is not None else ""
    if "unknown model" in text or "available models" in text:
        return "unknown-model"
    if "full-history forked agents" in text or "omit agent_type" in text:
        return "invalid-dispatch"
    if "not found" in text:
        return "not-found"
    if "interrupt" in text or "aborted" in text:
        return "interrupted"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if any(marker in text for marker in ("error", "failed", "rejected", "invalid", "must be")):
        return "tool-error"
    return None


def classify_terminal_error(value: Any, terminal_type: str | None = None) -> str:
    if terminal_type == "turn_aborted":
        return "interrupted"
    return classify_tool_error(value) or "unknown"


def _duration_from(value: Any, keys: tuple[str, ...]) -> int | None:
    return record_common.duration_from(value, keys)


def parse_parent_coordination(
    events: list[dict[str, Any]], turn_id: str | None = None
) -> dict[str, Any]:
    """Replay only strict coordination fields; never retain arguments/output text."""
    metrics = empty_coordination_metrics()
    calls: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    timeout_streak = 0
    for index, event in enumerate(events):
        payload = payload_dict(event)
        event_turn_id = payload.get("turn_id")
        if not isinstance(event_turn_id, str):
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            event_turn_id = payload.get("turn_id")
            if not isinstance(event_turn_id, str):
                event_turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
        if turn_id and isinstance(event_turn_id, str) and event_turn_id != turn_id:
            continue
        if event.get("type") != "response_item":
            if event.get("type") == "event_msg" and payload.get("type") == "sub_agent_activity":
                timeout_streak = 0
            continue
        item_type = payload.get("type")
        if item_type == "message":
            phase = parent_message_phase(payload, turn_id)
            if phase is not None:
                metrics["parent_message_phase_counts"][phase] += 1
            continue
        if item_type == "agent_message":
            author = payload.get("author")
            recipient = payload.get("recipient")
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            event_turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
            if (
                not isinstance(author, str)
                or not author.startswith("/root/")
                or recipient != "/root"
                or not isinstance(event_turn_id, str)
                or (turn_id is not None and event_turn_id != turn_id)
            ):
                continue
            content = payload.get("content")
            text_value = (
                content[0].get("text")
                if isinstance(content, list)
                and content
                and isinstance(content[0], dict)
                and isinstance(content[0].get("text"), str)
                else None
            )
            first_line = text_value.splitlines()[0] if text_value else ""
            bucket = (
                "message"
                if first_line == "Message Type: MESSAGE"
                else "final_answer"
                if first_line == "Message Type: FINAL_ANSWER"
                else "unknown"
            )
            metrics["child_handback_counts"][bucket] += 1
            continue
        if item_type in TOOL_CALL_TYPES:
            operation = normalize_coordination_operation(payload.get("name"))
            if operation is None:
                continue
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"offset:{index}"
            if call_id in seen_ids:
                continue
            seen_ids.add(call_id)
            metrics["operation_counts"][operation] += 1
            parsed_args = parse_arguments(payload.get("arguments"))
            request_ms = (
                _duration_from(parsed_args, ("timeout_ms", "wait_ms", "requested_wait_ms"))
                if operation == "wait_agent"
                else None
            )
            calls[call_id] = {"operation": operation, "outcome_recorded": False}
            if request_ms is not None:
                metrics["requested_wait_ms"] = int(metrics["requested_wait_ms"] or 0) + request_ms
                metrics["requested_wait_ms_observability"] = "observed"
            continue
        if item_type not in TOOL_OUTPUT_TYPES:
            continue
        call_id = payload.get("call_id")
        call = calls.get(call_id) if isinstance(call_id, str) else None
        if not isinstance(call, dict) or call.get("operation") != "wait_agent":
            continue
        if call.get("outcome_recorded"):
            continue
        outcome = classify_wait_outcome(payload.get("output"))
        metrics["wait_outcomes"][outcome] += 1
        observed_ms = _duration_from(
            payload.get("output"),
            ("observed_wait_ms", "waited_ms", "duration_ms", "elapsed_ms"),
        )
        if observed_ms is not None:
            metrics["observed_wait_ms"] = int(metrics["observed_wait_ms"] or 0) + observed_ms
            metrics["observed_wait_ms_observability"] = "observed"
        if outcome == "timeout":
            timeout_streak += 1
            metrics["timeout_without_agent_update_count"] += 1
            metrics["max_consecutive_timeout_without_agent_update"] = max(
                metrics["max_consecutive_timeout_without_agent_update"], timeout_streak
            )
        else:
            timeout_streak = 0
        call["outcome_recorded"] = True
    metrics["operation_observability"] = {
        operation: "legacy" for operation in COORDINATION_OPERATIONS
    }
    return metrics


TERMINAL_AGENT_STATUSES = record_common.TERMINAL_AGENT_STATUSES


def bounded(value: str, field: str, limit: int) -> str:
    text = " ".join(value.split())
    if not text:
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def optional_bounded(value: str | None, field: str, limit: int) -> str | None:
    return bounded(value, field, limit) if value else None


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    path.chmod(PRIVATE_DIRECTORY_MODE)


def storage(root: Path) -> tuple[Path, Path]:
    resolved = root.expanduser().resolve()
    ensure_private_directory(resolved)
    routine = resolved / "routine"
    anomalies = resolved / "anomalies"
    ensure_private_directory(routine)
    ensure_private_directory(anomalies)
    return routine, anomalies


def write_new(path: Path, payload: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"JSONL session does not exist: {resolved}")
    events: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{resolved}:{line_number}: invalid JSON: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError(f"{resolved}:{line_number}: JSONL event must be an object")
            events.append(payload)
    if not events:
        raise ValueError(f"JSONL session is empty: {resolved}")
    return events


def first_payload(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in events:
        if event.get("type") == event_type:
            payload = event.get("payload")
            if isinstance(payload, dict):
                return payload
    raise ValueError(f"session has no {event_type} event")


def event_timestamp(event: dict[str, Any] | None) -> str | None:
    if event is None:
        return None
    value = event.get("timestamp")
    return value if isinstance(value, str) and value else None


def optional_role(value: Any) -> str | None:
    """Keep absent runtime role unlabeled; never turn it into Default/None text."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:80] if text else None


def service_tier_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:80] if text else None


def fork_session_meta(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return fork metadata when a child transcript declares inherited history."""
    session_event = next(
        (event for event in events if event.get("type") == "session_meta"), None
    )
    if session_event is None:
        return None
    payload = payload_dict(session_event)
    forked_from = payload.get("forked_from_id") or session_event.get("forked_from_id")
    child_id = (
        payload.get("id")
        or payload.get("session_id")
        or session_event.get("id")
        or session_event.get("session_id")
    )
    if not isinstance(forked_from, str) or not forked_from:
        return None
    if not isinstance(child_id, str) or not child_id:
        return None
    return {
        "forked_from_id": bounded(forked_from, "forked_from_id", 160),
        "child_id": bounded(child_id, "child_id", 160),
        "timestamp": payload.get("timestamp") or session_event.get("timestamp"),
    }


def fork_metric_boundary_info(events: list[dict[str, Any]]) -> tuple[int, str] | None:
    """Find the first child-local task in a forked transcript.

    A forked child starts with a session header and a serialized parent-history
    prefix.  The child's task id is the strongest boundary marker.  Timestamp
    and gap fallbacks are intentionally used only when the id is unavailable;
    returning ``None`` keeps legacy replay conservative when the boundary is
    ambiguous.
    """
    meta = fork_session_meta(events)
    if meta is None:
        return None

    child_id = meta["child_id"]
    child_key = ulid_time_key(child_id)
    candidates: list[tuple[int, dict[str, Any], str | None, datetime | None]] = []
    for index, event in enumerate(events[1:], start=1):
        if event.get("type") != "event_msg":
            continue
        payload = payload_dict(event)
        if payload.get("type") != "task_started":
            continue
        turn_id = payload.get("turn_id")
        event_time = parse_timestamp(event_timestamp(event) or payload.get("timestamp"))
        candidates.append((index, event, turn_id, event_time))
        if turn_id == child_id:
            return index, "child-id"
        turn_key = ulid_time_key(turn_id)
        child_time = parse_timestamp(meta.get("timestamp"))
        if (
            child_time is not None
            and event_time is not None
            and event_time >= child_time
            and turn_key is not None
            and child_key is not None
            and turn_key >= child_key
        ):
            return index, "uuidv7-time"

    if not candidates:
        return None

    # Legacy fork fixtures often preserve event timestamps but omit turn ids.
    # Require a timestamp transition across the child session timestamp so a
    # single ambiguous task cannot silently discard parent history.
    child_time = parse_timestamp(meta.get("timestamp"))
    if child_time is not None:
        before = [item for item in candidates if item[3] is not None and item[3] < child_time]
        after = [item for item in candidates if item[3] is not None and item[3] >= child_time]
        if before and after:
            return after[0][0], "timestamp-transition"

    # Last-resort legacy behavior mirrors the hook collector's gap probe.  If
    # this cannot establish a boundary, the caller replays the full transcript.
    previous = parse_timestamp(
        event_timestamp(events[0]) or payload_dict(events[0]).get("timestamp")
    )
    latest_task: int | None = None
    for index, event in enumerate(events[1:], start=1):
        current = parse_timestamp(event_timestamp(event) or payload_dict(event).get("timestamp"))
        if event.get("type") == "event_msg" and payload_dict(event).get("type") == "task_started":
            latest_task = index
        if previous is not None and current is not None:
            delta_ms = (current - previous).total_seconds() * 1000
            if delta_ms > 500 and latest_task is not None:
                return latest_task, "timestamp-gap"
        if current is not None:
            previous = current
    return None


def fork_metric_boundary(events: list[dict[str, Any]]) -> int | None:
    info = fork_metric_boundary_info(events)
    return info[0] if info is not None else None


def metric_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude inherited fork history while retaining the child session header."""
    boundary = fork_metric_boundary(events)
    if boundary is None:
        return events
    session_index = next(
        (index for index, event in enumerate(events) if event.get("type") == "session_meta"),
        0,
    )
    if session_index == boundary:
        return events
    return [events[session_index], *events[boundary:]]


def compact_token_usage(events: list[dict[str, Any]]) -> dict[str, int] | None:
    usage: dict[str, Any] | None = None
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        candidate = payload.get("info", {}).get("total_token_usage")
        if isinstance(candidate, dict):
            usage = candidate
    if usage is None:
        return None
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    result = {field: usage[field] for field in fields if isinstance(usage.get(field), int)}
    return result or None


def parse_keyed_values(
    values: list[str], field: str, value_limit: int
) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"{field} must use AGENT_ID=value")
        key, value = raw.split("=", 1)
        key = bounded(key, f"{field} agent id", 160)
        if key in result:
            raise ValueError(f"duplicate {field} for {key}")
        result[key] = bounded(value, field, value_limit)
    return result


def indexed_status_events(
    events: list[dict[str, Any]], names: set[str]
) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, event)
        for index, event in enumerate(events)
        if event.get("type") == "event_msg"
        and event.get("payload", {}).get("type") in names
    ]


def summarize_agent_session(
    path: Path,
    parent_thread_id: str,
    summaries: dict[str, str],
    task_names: dict[str, str],
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    events = read_jsonl(resolved)
    meta = first_payload(events, "session_meta")
    metric = metric_events(events)
    # A forked child can inherit the parent's turn_context in the history
    # prefix.  Runtime metadata is descriptive only, so prefer the child-local
    # context when present and retain the legacy first context as a fallback.
    try:
        turn = first_payload(metric, "turn_context")
    except ValueError:
        # Never borrow inherited parent runtime from a fork prefix.  A missing
        # child-local context is explicitly unobserved instead.
        turn = {} if fork_session_meta(events) is not None else first_payload(
            events, "turn_context"
        )
    service_tier = service_tier_value(
        turn.get("service_tier") or turn.get("serviceTier") or turn.get("tier")
    )
    agent_id = meta.get("id") or meta.get("session_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError(f"{resolved}: session_meta has no child id")
    actual_parent = meta.get("parent_thread_id")
    if actual_parent != parent_thread_id:
        raise ValueError(
            f"{resolved}: expected parent {parent_thread_id}, got {actual_parent}"
        )
    if agent_id not in summaries:
        raise ValueError(f"missing --agent-summary for {agent_id}")

    started_events = indexed_status_events(metric, {"task_started"})
    terminal_events = indexed_status_events(
        metric, {"task_complete", "task_failed", "turn_failed", "turn_aborted"}
    )
    started = started_events[0][1] if started_events else None
    latest_started_index = started_events[-1][0] if started_events else -1
    terminal_index, terminal = terminal_events[-1] if terminal_events else (-1, None)
    terminal_type = terminal.get("payload", {}).get("type") if terminal else None
    if terminal is None or latest_started_index > terminal_index:
        status = "unknown"
        ended = None
    else:
        status = {
            "task_complete": "completed",
            "task_failed": "errored",
            "turn_failed": "errored",
            "turn_aborted": "interrupted",
        }[terminal_type]
        ended = terminal

    started_at = event_timestamp(started) or meta.get("timestamp")
    ended_at = event_timestamp(ended)
    duration_ms = elapsed_ms(started_at, ended_at)
    response_items = [
        event.get("payload", {})
        for event in metric
        if event.get("type") == "response_item"
    ]
    tool_calls = sum(item.get("type") in TOOL_CALL_TYPES for item in response_items)
    tool_outputs = sum(item.get("type") in TOOL_OUTPUT_TYPES for item in response_items)
    nested_agent_calls = sum(
        item.get("type") in TOOL_CALL_TYPES and is_spawn_tool(item.get("name"))
        for item in response_items
    )
    final_message = (
        terminal.get("payload", {}).get("last_agent_message", "")
        if status == "completed" and terminal is not None
        else ""
    )
    agent_path = meta.get("agent_path")
    inferred_task_name = (
        agent_path.rsplit("/", 1)[-1]
        if isinstance(agent_path, str) and agent_path
        else None
    )
    task_name = task_names.get(agent_id) or inferred_task_name
    if not task_name:
        raise ValueError(
            f"{resolved}: cannot infer task name; supply --agent-task {agent_id}=NAME"
        )

    error_value = None
    if status == "errored" and terminal is not None:
        terminal_payload = terminal.get("payload", {})
        raw_error = terminal_payload.get("error") or terminal_payload.get("reason")
        if raw_error is not None:
            error_value = classify_terminal_error(
                raw_error,
                terminal_payload.get("type"),
            )

    forked = fork_session_meta(events) is not None
    fork_meta = fork_session_meta(events)
    fork_observed = True if fork_meta is not None else False
    fork_observation_source = "child-session-meta"
    boundary_info = fork_metric_boundary_info(events) if forked else None
    fork_boundary_known = not forked or boundary_info is not None
    boundary_method = boundary_info[1] if boundary_info is not None else (
        "unknown" if forked else None
    )
    boundary_valid = boundary_method in {"child-id", "uuidv7-time"}

    return {
        "agent_id": agent_id,
        "agent_path": agent_path if isinstance(agent_path, str) else None,
        "task_name": bounded(task_name, "agent task name", 160),
        "nickname": optional_bounded(meta.get("agent_nickname"), "agent nickname", 160),
        # This is observed child metadata, not the requested role.  Keep a
        # missing value null so legacy fallback cannot silently label it default.
        "role": optional_role(meta.get("agent_role")),
        "actual_role": optional_role(meta.get("agent_role")),
        "actual_role_source": "child-session-meta"
        if optional_role(meta.get("agent_role"))
        else "not_observed",
        "model_provider": optional_bounded(
            meta.get("model_provider"), "model provider", 120
        ),
        "model": optional_bounded(turn.get("model"), "agent model", 160),
        "reasoning_effort": optional_bounded(
            turn.get("effort"), "agent reasoning effort", 80
        ),
        "service_tier": service_tier,
        "service_tier_source": "child-turn-context"
        if service_tier
        else "not_observed",
        "service_tier_observability": "observed" if service_tier else "not_observed",
        "multi_agent_version": optional_bounded(
            turn.get("multi_agent_version"), "multi-agent version", 80
        ),
        "status": status,
        "turn_count": len(started_events),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "event_count": len(metric),
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "nested_agent_calls": nested_agent_calls,
        "token_usage": compact_token_usage(metric),
        "final_message_chars": len(final_message) if isinstance(final_message, str) else 0,
        "result_summary": summaries[agent_id],
        "error": error_value,
        "session_path": str(resolved),
        "metric_scope": "fork-boundary-unknown"
        if forked and not fork_boundary_known
        else "child-local"
        if forked
        else "legacy",
        "metric_validity": "invalid"
        if forked and not fork_boundary_known
        else "valid"
        if forked and boundary_valid
        else "legacy-unverified"
        if forked
        else "legacy-unverified",
        "metric_observability": "transcript-replay",
        "metric_boundary_method": boundary_method,
        "forked": forked,
        "fork_observed": fork_observed,
        "fork_observation_source": fork_observation_source,
        "forked_from_id": fork_meta.get("forked_from_id") if fork_meta else None,
    }


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_parent_attempts(
    path: Path, parent_thread_id: str, turn_id: str
) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    events = read_jsonl(resolved)
    meta = first_payload(events, "session_meta")
    parent_id = meta.get("id") or meta.get("session_id")
    if parent_id != parent_thread_id:
        raise ValueError(
            f"{resolved}: expected parent session {parent_thread_id}, got {parent_id}"
        )

    outputs: dict[str, Any] = {}
    for event in events:
        payload = event.get("payload", {})
        if (
            event.get("type") == "response_item"
            and payload.get("type") in TOOL_OUTPUT_TYPES
            and isinstance(payload.get("call_id"), str)
        ):
            outputs[payload["call_id"]] = payload.get("output")

    attempts: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        metadata = payload.get("internal_chat_message_metadata_passthrough", {})
        if (
            event.get("type") != "response_item"
            or payload.get("type") not in TOOL_CALL_TYPES
            or not is_spawn_tool(payload.get("name"))
            or metadata.get("turn_id") != turn_id
        ):
            continue
        arguments = parse_arguments(payload.get("arguments"))
        call_id = payload.get("call_id")
        raw_output = outputs.get(call_id)
        output_text = (
            raw_output
            if isinstance(raw_output, str)
            else json.dumps(raw_output, ensure_ascii=False)
            if raw_output is not None
            else ""
        )
        parsed_output = parse_arguments(raw_output)
        child_ref = parsed_output.get("agent_id") or parsed_output.get("task_name")
        requested_fork_turns, fork_observability = sanitize_requested_fork_turns(
            arguments
        )
        if child_ref:
            outcome = "started"
            error = None
        elif output_text:
            outcome = (
                "rejected"
                if "unknown model" in output_text.lower()
                or "available models" in output_text.lower()
                else "errored"
            )
            error = classify_tool_error(raw_output) or "unknown"
        else:
            outcome = "unknown"
            error = None
        attempts.append(
            {
                "attempted_at": event_timestamp(event),
                "call_id": call_id if isinstance(call_id, str) else None,
                "task_name": optional_bounded(
                    arguments.get("task_name"), "attempt task name", 160
                ),
                "requested_role": optional_bounded(
                    arguments.get("agent_type"), "requested role", 80
                ),
                "requested_model": optional_bounded(
                    arguments.get("model"), "requested model", 160
                ),
                "requested_reasoning_effort": optional_bounded(
                    arguments.get("reasoning_effort"),
                    "requested reasoning effort",
                    80,
                ),
                "requested_service_tier": service_tier_value(
                    arguments.get("service_tier") or arguments.get("serviceTier")
                ),
                "requested_fork_turns": requested_fork_turns,
                "requested_fork_turns_observability": fork_observability,
                "outcome": outcome,
                "error": error,
                "child_ref": optional_bounded(
                    str(child_ref) if child_ref else None, "child reference", 200
                ),
                "evidence_source": "parent-session",
            }
        )
    return attempts


def connect_attempts_to_agents(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    connected: set[str] = set()
    started_child_ref_counts = Counter(
        attempt.get("child_ref")
        for attempt in attempts
        if attempt.get("outcome") == "started"
        and isinstance(attempt.get("child_ref"), str)
        and attempt.get("child_ref")
    )
    for attempt in attempts:
        if attempt["outcome"] != "started":
            continue
        child_ref = attempt.get("child_ref")
        candidates = [
            agent
            for agent in agents
            if child_ref in {agent["agent_id"], agent.get("agent_path")}
        ]
        if (
            isinstance(child_ref, str)
            and started_child_ref_counts[child_ref] > 1
        ):
            attempt["binding"] = "ambiguous"
            candidate_ids = sorted(agent["agent_id"] for agent in candidates)
            if not candidate_ids and not child_ref.startswith("/"):
                candidate_ids = [child_ref]
            attempt["binding_candidates"] = candidate_ids
            continue
        available = [agent for agent in candidates if agent["agent_id"] not in connected]
        if len(candidates) == 1 and len(available) == 1:
            agent = available[0]
            attempt["agent_id"] = agent["agent_id"]
            attempt["binding"] = (
                "agent-path" if child_ref == agent.get("agent_path") else "agent-id"
            )
            connected.add(agent["agent_id"])
        elif candidates:
            # Duplicate paths/IDs or duplicate attempts are not enough
            # evidence to identify a child.  Keep the attempt unbound so a
            # closeout cannot silently attach one task name to another agent.
            attempt["binding"] = "ambiguous"
            attempt["binding_candidates"] = sorted(
                agent["agent_id"] for agent in candidates
            )
        else:
            attempt.setdefault("binding", "unbound")
    for agent in agents:
        if agent["agent_id"] in connected:
            continue
        attempts.append(
            {
                "attempted_at": agent["started_at"],
                "call_id": None,
                "task_name": agent["task_name"],
                "requested_role": None,
                "requested_model": None,
                "requested_reasoning_effort": None,
                "requested_service_tier": None,
                "requested_fork_turns": None,
                "requested_fork_turns_observability": "not_observed",
                "outcome": "started",
                "error": None,
                "child_ref": agent["agent_id"],
                "agent_id": agent["agent_id"],
                "binding": "agent-session",
                "evidence_source": "child-session",
            }
        )
    return sorted(
        attempts,
        key=lambda item: (
            item.get("attempted_at") or "",
            item.get("call_id") or "",
            item.get("agent_id") or "",
        ),
    )


def enrich_child_identity(
    attempts: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    parent_thread_id: str,
    parent_turn_id: str,
) -> None:
    """Attach stable identity and role binding evidence to replay records.

    Joins use child session id and, when available, the session's parent/path.
    Task names and timestamps are intentionally excluded from identity joins.
    """
    for attempt in attempts:
        child_id = attempt.get("agent_id")
        child_ref = attempt.get("child_ref")
        attempt["parent_thread_id"] = parent_thread_id
        attempt["parent_turn_id"] = parent_turn_id
        attempt["identity_evidence"] = {
            "child_session_id": child_id,
            "parent_thread_id": parent_thread_id,
            "parent_turn_id": parent_turn_id,
            "agent_path": child_ref if isinstance(child_ref, str) and child_ref.startswith("/") else None,
            "binding": attempt.get("binding", "unbound"),
        }
    by_id = {agent.get("agent_id"): agent for agent in agents}
    for agent in agents:
        agent_id = agent.get("agent_id")
        matching = [item for item in attempts if item.get("agent_id") == agent_id]
        requested_roles = sorted(
            {
                item.get("requested_role")
                for item in matching
                if isinstance(item.get("requested_role"), str)
                and item.get("requested_role")
            }
        )
        agent["child_session_id"] = agent_id
        agent["parent_thread_id"] = parent_thread_id
        agent["parent_turn_id"] = parent_turn_id
        agent["identity_evidence"] = {
            "child_session_id": agent_id,
            "parent_thread_id": parent_thread_id,
            "parent_turn_id": parent_turn_id,
            "agent_path": agent.get("agent_path"),
            "binding": matching[0].get("binding", "agent-session")
            if len(matching) == 1
            else "ambiguous"
            if matching
            else "child-session",
        }
        if len(requested_roles) == 1:
            agent["requested_role"] = requested_roles[0]
            agent["requested_role_source"] = (
                "spawn-request"
                if any(item.get("binding") == "agent-id" for item in matching)
                else "agent-path-join"
                if any(item.get("binding") == "agent-path" for item in matching)
                else "not_observed"
            )
        elif requested_roles:
            agent["requested_role"] = None
            agent["requested_role_candidates"] = requested_roles
            agent["requested_role_source"] = "conflicting-requests"
        else:
            agent["requested_role"] = None
            agent["requested_role_candidates"] = []
            agent["requested_role_source"] = "not_observed"
        agent.setdefault("actual_role", optional_role(agent.get("role")))
        agent["role"] = agent.get("actual_role")
        agent.setdefault(
            "actual_role_source",
            "child-session-meta" if agent.get("actual_role") else "not_observed",
        )
        agent["role_binding_source"] = agent.get("actual_role_source")
        if not service_tier_value(agent.get("service_tier")):
            agent["service_tier"] = None
            agent["service_tier_source"] = "not_observed"
            agent["service_tier_observability"] = "not_observed"
        else:
            agent["service_tier_observability"] = "observed"
        agent.setdefault("metric_scope", "unobserved")
        agent.setdefault("metric_validity", "unobserved")
        agent.setdefault("metric_observability", "missing")


def aggregate_outcome(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> str:
    statuses = [agent["status"] for agent in agents]
    if not agents:
        return "dispatch_failed" if attempts else "no_dispatch"
    if statuses and all(status == "completed" for status in statuses):
        return "completed"
    if "completed" in statuses:
        return "partial"
    if "errored" in statuses:
        return "agent_error"
    if "interrupted" in statuses:
        return "interrupted"
    return "unknown"


def binding_uncertainty_messages(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> list[str]:
    """Report unresolved successful spawns only once lifecycle state is terminal."""
    unresolved = [
        attempt
        for attempt in attempts
        if attempt.get("outcome") == "started"
        and (
            attempt.get("binding", "unbound") == "ambiguous"
            or (
                not attempt.get("agent_id")
                and attempt.get("binding", "unbound") == "unbound"
            )
        )
    ]
    if not unresolved:
        return []
    if agents:
        if not all(
            agent.get("status") in TERMINAL_AGENT_STATUSES for agent in agents
        ):
            return []
    labels: list[str] = []
    for attempt in unresolved:
        label = (
            attempt.get("call_id")
            or attempt.get("task_name")
            or attempt.get("child_ref")
            or "unknown"
        )
        if attempt.get("binding", "unbound") == "ambiguous":
            candidates = attempt.get("binding_candidates") or []
            candidate_text = ", ".join(str(value) for value in candidates) or "none"
            labels.append(
                f"{label} has ambiguous agent binding (candidates: {candidate_text})"
            )
        else:
            labels.append(f"{label} has no agent binding after a successful spawn")
    return labels


def zero_yield_messages(agents: list[dict[str, Any]]) -> list[str]:
    """Identify completed children that produced no observable work or answer."""
    return [
        f"{agent.get('agent_id') or 'unknown'} completed with zero tool calls "
        "and no final message"
        for agent in agents
        if agent.get("status") == "completed"
        and agent.get("tool_calls") == 0
        and agent.get("final_message_chars") == 0
    ]


def observed_window(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> tuple[str | None, str | None, int | None]:
    starts = [
        value
        for value in (
            [attempt.get("attempted_at") for attempt in attempts]
            + [agent.get("started_at") for agent in agents]
        )
        if isinstance(value, str)
    ]
    ends = [
        agent.get("ended_at")
        for agent in agents
        if isinstance(agent.get("ended_at"), str)
    ]
    started_at = min(starts) if starts else None
    ended_at = max(ends) if ends else (max(starts) if starts else None)
    return started_at, ended_at, elapsed_ms(started_at, ended_at)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Replay transcripts only when hook-driven closeout is unavailable."
    )
    commands = result.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create the routine and anomalies directories.")
    init.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    audit = commands.add_parser(
        "audit", help="Resolve active/superseded records and validate record links."
    )
    audit.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    reconcile = commands.add_parser(
        "reconcile",
        aliases=["coverage-audit"],
        help="Maintenance-only bounded coverage reconciliation (session_meta vs records).",
    )
    reconcile.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    reconcile.add_argument(
        "--sessions-root",
        type=Path,
        default=default_codex_home() / "sessions",
        help="Session tree to scan when --session is not supplied.",
    )
    reconcile.add_argument(
        "--session",
        action="append",
        type=Path,
        default=[],
        help="Bounded child JSONL input; may be repeated.",
    )
    reconcile.add_argument("--since", help="Inclusive ISO-8601 window start.")
    reconcile.add_argument("--until", help="Inclusive ISO-8601 window end.")

    record = commands.add_parser(
        "record", help="Write one schema-v3 replay-fallback record and optional anomaly."
    )
    record.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    record.add_argument("--parent-thread-id", required=True)
    record.add_argument("--turn-id", required=True)
    record.add_argument("--project", required=True)
    record.add_argument("--task-signature", required=True)
    record.add_argument(
        "--source", choices=("live", "backfill", "correction"), default="live"
    )
    record.add_argument("--supersedes", action="append", default=[])
    record.add_argument("--parent-session", type=Path)
    record.add_argument("--agent-session", action="append", type=Path, default=[])
    record.add_argument("--agent-summary", action="append", default=[])
    record.add_argument("--agent-task", action="append", default=[])
    record.add_argument("--anomaly-type", action="append", choices=ANOMALY_TYPES, default=[])
    record.add_argument("--anomaly-summary")
    record.add_argument("--anomaly-evidence")
    record.add_argument("--anomaly-impact")
    record.add_argument("--dry-run", action="store_true")
    return result


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read record {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"record is not a JSON object: {path}")
    return payload


def existing_record_ids(routine_dir: Path) -> set[str]:
    if not routine_dir.is_dir():
        return set()
    result: set[str] = set()
    for path in sorted(routine_dir.glob("*.json")):
        payload = load_json_file(path)
        record_id = payload.get("record_id")
        if isinstance(record_id, str):
            result.add(record_id)
    return result


def audit_records(root: Path) -> dict[str, Any]:
    routine_dir = root.expanduser() / "routine"
    anomaly_dir = root.expanduser() / "anomalies"
    errors: list[str] = []
    routines: dict[str, dict[str, Any]] = {}
    routine_paths: dict[str, Path] = {}
    if not routine_dir.is_dir():
        errors.append(f"missing routine directory: {routine_dir}")
    else:
        for path in sorted(routine_dir.glob("*.json")):
            try:
                payload = load_json_file(path)
            except ValueError as error:
                errors.append(str(error))
                continue
            record_id = payload.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                errors.append(f"{path}: missing record_id")
                continue
            if record_id in routines:
                errors.append(f"duplicate record_id: {record_id}")
                continue
            if path.stem != record_id:
                errors.append(f"{path}: filename does not match record_id {record_id}")
            routines[record_id] = payload
            routine_paths[record_id] = path

    superseded_by: dict[str, list[str]] = {}
    for record_id, payload in routines.items():
        supersedes = payload.get("supersedes", [])
        if supersedes is None:
            supersedes = []
        if not isinstance(supersedes, list):
            errors.append(f"{record_id}: supersedes must be an array")
            continue
        for target in supersedes:
            if not isinstance(target, str):
                errors.append(f"{record_id}: supersedes contains a non-string id")
                continue
            superseded_by.setdefault(target, []).append(record_id)
            if target not in routines:
                errors.append(f"{record_id}: supersedes missing record {target}")

    try:
        TopologicalSorter(superseded_by).prepare()
    except CycleError as error:
        errors.append("supersedes cycle: " + " -> ".join(error.args[1]))

    active_ids = sorted(record_id for record_id in routines if record_id not in superseded_by)
    active_turns: dict[tuple[str, str], str] = {}
    for record_id in active_ids:
        payload = routines[record_id]
        key = (payload.get("parent_thread_id"), payload.get("turn_id"))
        if not any(key):
            continue
        if not all(isinstance(value, str) and value for value in key):
            errors.append(f"{record_id}: active record lacks thread/turn identity")
            continue
        if key in active_turns:
            errors.append(
                f"active duplicate turn {key[0]}/{key[1]}: "
                f"{active_turns[key]} and {record_id}"
            )
        else:
            active_turns[key] = record_id

    anomalies: list[dict[str, Any]] = []
    if anomaly_dir.is_dir():
        for path in sorted(anomaly_dir.glob("*.json")):
            try:
                payload = load_json_file(path)
            except ValueError as error:
                errors.append(str(error))
                continue
            related = payload.get("related_routine")
            anomaly_record_id = payload.get("record_id")
            if not isinstance(anomaly_record_id, str) or not anomaly_record_id:
                errors.append(f"{path}: anomaly has no record_id")
            elif path.stem != anomaly_record_id:
                errors.append(
                    f"{path}: filename does not match anomaly record_id "
                    f"{anomaly_record_id}"
                )
            if not isinstance(related, str) or not related:
                errors.append(f"{path}: anomaly has no related_routine")
            elif related.removesuffix(".json") not in routines:
                errors.append(f"{path}: anomaly references missing routine {related}")
            anomaly_types = payload.get("anomaly_types", [])
            if not isinstance(anomaly_types, list) or not all(
                isinstance(value, str) for value in anomaly_types
            ):
                errors.append(f"{path}: anomaly_types must be an array of strings")
                anomaly_types = []
            anomalies.append(
                {
                    "record_id": anomaly_record_id,
                    "related_routine": related,
                    "anomaly_types": anomaly_types,
                    "path": str(path.resolve()),
                }
            )
    for record_id in active_ids:
        payload = routines[record_id]
        agents = payload.get("agents", [])
        attempts = payload.get("spawn_attempts", [])
        if not isinstance(agents, list) or not isinstance(attempts, list):
            errors.append(f"{record_id}: agents and spawn_attempts must be arrays")
            continue
        for count, rows in (("child_count", agents), ("attempt_count", attempts)):
            if count in payload and payload[count] != len(rows):
                errors.append(f"{record_id}: {count} does not match stored rows")
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict) or not isinstance(agent.get("agent_id"), str):
                errors.append(f"{record_id}: agents[{index}] lacks agent identity")
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                errors.append(f"{record_id}: spawn_attempts[{index}] must be an object")

    def summary(record_id: str) -> dict[str, Any]:
        payload = routines[record_id]
        return {
            "record_id": record_id,
            "schema_version": payload.get("schema_version"),
            "source": payload.get("source", "legacy"),
            "project": payload.get("project"),
            "parent_thread_id": payload.get("parent_thread_id"),
            "turn_id": payload.get("turn_id"),
            "task_signature": payload.get("task_signature"),
            "outcome": payload.get("outcome"),
            "child_count": payload.get("child_count"),
            "path": str(routine_paths[record_id].resolve()),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root.expanduser().resolve()),
        "valid": not errors,
        "errors": errors,
        "routine_count": len(routines),
        "active_count": len(active_ids),
        "superseded_count": len(superseded_by),
        "anomaly_count": len(anomalies),
        "active_records": [summary(record_id) for record_id in active_ids],
        "superseded_records": [
            {
                **summary(record_id),
                "superseded_by": sorted(superseded_by[record_id]),
            }
            for record_id in sorted(superseded_by)
            if record_id in routines
        ],
        "anomalies": anomalies,
    }


def _read_session_meta_only(path: Path) -> dict[str, Any]:
    """Read only session_meta from one child JSONL; never retain prompts/messages."""
    resolved = path.expanduser().resolve()
    result: dict[str, Any] = {
        "session_path": str(resolved),
        "child_session_id": None,
        "parent_thread_id": None,
        "agent_path": None,
        "actual_role": None,
        "timestamp": None,
        "thread_source": None,
        "_is_child_evidence": False,
        "_has_session_meta": False,
        "invalid_metric_identifiers": [],
    }
    try:
        handle = resolved.open(encoding="utf-8")
    except OSError as error:
        result["invalid_metric_identifiers"].append(f"session unreadable: {error}")
        return result
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Keep scanning until the header can be observed, while
                # retaining only a bounded malformed-line classification.
                result["invalid_metric_identifiers"].append(
                    f"malformed JSON at line {line_number}"
                )
                continue
            if not isinstance(event, dict) or event.get("type") != "session_meta":
                continue
            payload = payload_dict(event)
            result["_has_session_meta"] = True
            source = payload.get("source")
            subagent = source.get("subagent") if isinstance(source, dict) else None
            thread_spawn = (
                subagent.get("thread_spawn")
                if isinstance(subagent, dict)
                and isinstance(subagent.get("thread_spawn"), dict)
                else {}
            )
            result["child_session_id"] = (
                payload.get("id")
                or payload.get("session_id")
                or thread_spawn.get("id")
                or thread_spawn.get("agent_id")
            )
            result["parent_thread_id"] = (
                payload.get("parent_thread_id")
                or thread_spawn.get("parent_thread_id")
            )
            result["agent_path"] = payload.get("agent_path") or thread_spawn.get(
                "agent_path"
            )
            result["actual_role"] = optional_role(
                payload.get("agent_role") or thread_spawn.get("agent_role")
            )
            result["timestamp"] = payload.get("timestamp") or event.get("timestamp")
            result["thread_source"] = payload.get("thread_source") or (
                "subagent" if isinstance(subagent, dict) else None
            )
            result["_is_child_evidence"] = bool(
                result.get("parent_thread_id")
                or result.get("agent_path")
                or result.get("thread_source") == "subagent"
                or (isinstance(source, dict) and isinstance(source.get("subagent"), dict))
            )
            break
    if not isinstance(result.get("child_session_id"), str) or not result.get(
        "child_session_id"
    ):
        result["invalid_metric_identifiers"].append("missing child session id")
    if not isinstance(result.get("parent_thread_id"), str) or not result.get(
        "parent_thread_id"
    ):
        result["invalid_metric_identifiers"].append("missing parent thread id")
    path_value = result.get("agent_path")
    if path_value is not None and (
        not isinstance(path_value, str) or not path_value.startswith("/")
    ):
        result["invalid_metric_identifiers"].append("agent_path is not an absolute path")
    if result.get("timestamp") is not None and parse_timestamp(result.get("timestamp")) is None:
        result["invalid_metric_identifiers"].append("invalid session timestamp")
    return result


def _window_contains(value: Any, start: datetime | None, end: datetime | None) -> bool:
    parsed = parse_timestamp(value)
    if parsed is None:
        return start is None and end is None
    if start is not None and parsed < start:
        return False
    if end is not None and parsed > end:
        return False
    return True


def _session_path_date(path: Path) -> Any:
    """Return a YYYY/MM/DD path discriminator for bounded fallback evidence."""
    parts = path.expanduser().resolve().parts
    for index in range(len(parts) - 2):
        year, month, day = parts[index : index + 3]
        if not (
            len(year) == 4
            and len(month) == 2
            and len(day) == 2
            and year.isdigit()
            and month.isdigit()
            and day.isdigit()
        ):
            continue
        try:
            return datetime(int(year), int(month), int(day), tzinfo=timezone.utc).date()
        except ValueError:
            continue
    return None


def _path_date_in_window(
    path: Path, start: datetime | None, end: datetime | None
) -> bool:
    path_day = _session_path_date(path)
    if path_day is None:
        return False
    if start is not None and path_day < start.date():
        return False
    if end is not None and path_day > end.date():
        return False
    return True


def reconcile_coverage(
    root: Path,
    sessions_root: Path | None = None,
    session_paths: list[Path] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Maintenance-only bounded reconciliation of child sessions and records."""
    start = parse_timestamp(since)
    end = parse_timestamp(until)
    if since and start is None:
        raise ValueError("--since must be an ISO-8601 timestamp")
    if until and end is None:
        raise ValueError("--until must be an ISO-8601 timestamp")
    if start and end and end < start:
        raise ValueError("--until must not be earlier than --since")
    paths = [path.expanduser().resolve() for path in (session_paths or [])]
    explicit_paths = set(paths)
    if not paths and (not since or not until):
        raise ValueError(
            "bounded reconciliation requires both --since and --until or explicit --session inputs"
        )
    if not paths:
        scan_root = (sessions_root or (default_codex_home() / "sessions")).expanduser()
        if scan_root.is_dir():
            paths = sorted(path.resolve() for path in scan_root.rglob("*.jsonl"))
    session_evidence: list[dict[str, Any]] = []
    header_invalid_metric_identifiers: list[dict[str, Any]] = []
    for path in paths:
        evidence = _read_session_meta_only(path)
        is_child = bool(
            evidence.get("_is_child_evidence")
            or (path in explicit_paths and not evidence.get("_has_session_meta"))
        )
        if not is_child:
            continue
        parsed_timestamp = parse_timestamp(evidence.get("timestamp"))
        if parsed_timestamp is not None:
            in_window = _window_contains(evidence.get("timestamp"), start, end)
        elif path in explicit_paths:
            in_window = True
        else:
            in_window = _path_date_in_window(path, start, end)
        if not in_window:
            continue
        for reason in evidence.get("invalid_metric_identifiers", []):
            header_invalid_metric_identifiers.append(
                {
                    "session_path": evidence.get("session_path"),
                    "child_session_id": evidence.get("child_session_id"),
                    "parent_thread_id": evidence.get("parent_thread_id"),
                    "reason": reason,
                }
            )
        # Invalid timestamps are retained only when the explicit input or its
        # bounded YYYY/MM/DD path places it inside the requested window.
        session_evidence.append(evidence)

    # Only active routine records participate.  This keeps corrections and
    # superseded snapshots from creating false duplicate coverage.
    routine_dir = root.expanduser() / "routine"
    records: list[dict[str, Any]] = []
    if routine_dir.is_dir():
        for path in sorted(routine_dir.glob("*.json")):
            try:
                payload = load_json_file(path)
            except ValueError:
                continue
            records.append(payload)
    superseded = {
        target
        for record in records
        for target in (
            record.get("supersedes", [])
            if isinstance(record.get("supersedes", []), list)
            else []
        )
        if isinstance(target, str)
    }
    active_records = [
        record
        for record in records
        if isinstance(record.get("record_id"), str)
        and record.get("record_id") not in superseded
        and _window_contains(
            record.get("observed_started_at") or record.get("recorded_at"), start, end
        )
    ]
    structured: list[dict[str, Any]] = []
    invalid_metric_identifiers: list[dict[str, Any]] = header_invalid_metric_identifiers
    for record in active_records:
        parent_thread_id = record.get("parent_thread_id")
        parent_turn_id = record.get("turn_id") or record.get("parent_turn_id")
        agents = record.get("agents")
        if not isinstance(parent_thread_id, str) or not parent_thread_id:
            invalid_metric_identifiers.append(
                {"record_id": record.get("record_id"), "reason": "missing parent thread id"}
            )
            continue
        if not isinstance(parent_turn_id, str) or not parent_turn_id:
            invalid_metric_identifiers.append(
                {"record_id": record.get("record_id"), "reason": "missing parent turn id"}
            )
        if not isinstance(agents, list):
            invalid_metric_identifiers.append(
                {"record_id": record.get("record_id"), "reason": "agents is not an array"}
            )
            continue
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                continue
            child_id = agent.get("child_session_id") or agent.get("agent_id")
            if not isinstance(child_id, str) or not child_id:
                invalid_metric_identifiers.append(
                    {
                        "record_id": record.get("record_id"),
                        "agent_index": index,
                        "reason": "missing child session id",
                    }
                )
                continue
            metric_validity = agent.get("metric_validity")
            if metric_validity not in {
                None,
                "valid",
                "invalid",
                "unobserved",
                "legacy-unverified",
            }:
                invalid_metric_identifiers.append(
                    {
                        "record_id": record.get("record_id"),
                        "child_session_id": child_id,
                        "reason": "invalid metric_validity",
                    }
                )
            structured.append(
                {
                    "_structured_key": f"{record.get('record_id')}:{child_id}:{index}",
                    "record_id": record.get("record_id"),
                    "parent_thread_id": parent_thread_id,
                    "parent_turn_id": parent_turn_id,
                    "child_session_id": child_id,
                    "agent_path": agent.get("agent_path"),
                    "actual_role": optional_role(
                        agent.get("actual_role")
                        if agent.get("actual_role") is not None
                        else agent.get("role")
                    ),
                    "session_path": agent.get("session_path"),
                }
            )

    assignment_groups: dict[tuple[str, str | None, str], list[dict[str, Any]]] = {}
    reuse_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in structured:
        assignment_groups.setdefault(
            (
                item["parent_thread_id"],
                item.get("parent_turn_id"),
                item["child_session_id"],
            ),
            [],
        ).append(item)
        reuse_groups.setdefault(
            (item["parent_thread_id"], item["child_session_id"]),
            [],
        ).append(item)
    duplicate_structured_records = [
        {
            "parent_thread_id": key[0],
            "parent_turn_id": key[1],
            "child_session_id": key[2],
            "records": sorted(item.get("record_id") for item in values),
        }
        for key, values in assignment_groups.items()
        if len(values) > 1
    ]
    reused_child_records = [
        {
            "parent_thread_id": key[0],
            "child_session_id": key[1],
            "parent_turn_ids": sorted(
                {
                    item["parent_turn_id"]
                    for item in values
                    if isinstance(item.get("parent_turn_id"), str)
                    and item["parent_turn_id"]
                }
            ),
            "records": sorted({item["record_id"] for item in values}),
        }
        for key, values in reuse_groups.items()
        if len(
            {
                item["parent_turn_id"]
                for item in values
                if isinstance(item.get("parent_turn_id"), str)
                and item["parent_turn_id"]
            }
        )
        > 1
    ]
    # A stable child can be created before the audit window and reused by an
    # in-window parent turn.  Resolve only the exact session_path already
    # carried by that structured row; do not widen ordinary reconciliation
    # into a second historical transcript scan.
    observed_session_keys = {
        (item.get("parent_thread_id"), item.get("child_session_id"))
        for item in session_evidence
        if item.get("parent_thread_id") and item.get("child_session_id")
    }
    out_of_window_session_evidence: list[dict[str, Any]] = []
    for item in structured:
        key = (item["parent_thread_id"], item["child_session_id"])
        if key in observed_session_keys:
            continue
        session_path = item.get("session_path")
        if not isinstance(session_path, str) or not session_path:
            continue
        evidence = _read_session_meta_only(Path(session_path).expanduser().resolve())
        if not evidence.get("_is_child_evidence"):
            continue
        if (
            evidence.get("parent_thread_id"),
            evidence.get("child_session_id"),
        ) != key:
            continue
        session_evidence.append(evidence)
        observed_session_keys.add(key)
        for reason in evidence.get("invalid_metric_identifiers", []):
            invalid_metric_identifiers.append(
                {
                    "session_path": evidence.get("session_path"),
                    "child_session_id": evidence.get("child_session_id"),
                    "parent_thread_id": evidence.get("parent_thread_id"),
                    "reason": reason,
                }
            )
        if not _window_contains(evidence.get("timestamp"), start, end):
            out_of_window_session_evidence.append(
                {
                    "parent_thread_id": key[0],
                    "child_session_id": key[1],
                    "session_path": evidence.get("session_path"),
                    "record_id": item.get("record_id"),
                    "parent_turn_id": item.get("parent_turn_id"),
                }
            )
    missing_structured_records: list[dict[str, Any]] = []
    role_mismatches: list[dict[str, Any]] = []
    matched_structured_keys: set[str] = set()
    session_key_counts = Counter(
        (item.get("parent_thread_id"), item.get("child_session_id"))
        for item in session_evidence
        if item.get("child_session_id") and item.get("parent_thread_id")
    )
    for evidence in session_evidence:
        child_id = evidence.get("child_session_id")
        parent_id = evidence.get("parent_thread_id")
        if not isinstance(child_id, str) or not isinstance(parent_id, str):
            continue
        matches = [
            item
            for item in structured
            if item["child_session_id"] == child_id
            and item["parent_thread_id"] == parent_id
        ]
        if session_key_counts[(parent_id, child_id)] > 1:
            invalid_metric_identifiers.append(
                {
                    "child_session_id": child_id,
                    "parent_thread_id": parent_id,
                    "reason": "duplicate child session_meta evidence",
                }
            )
        if not matches:
            missing_structured_records.append(
                {
                    "child_session_id": child_id,
                    "parent_thread_id": parent_id,
                    "agent_path": evidence.get("agent_path"),
                    "session_path": evidence.get("session_path"),
                }
            )
            continue
        for item in matches:
            if item.get("_structured_key"):
                matched_structured_keys.add(item["_structured_key"])
            expected_role = evidence.get("actual_role")
            observed_role = item.get("actual_role")
            if expected_role and observed_role and expected_role != observed_role:
                role_mismatches.append(
                    {
                        "child_session_id": child_id,
                        "parent_thread_id": parent_id,
                        "record_id": item.get("record_id"),
                        "session_role": expected_role,
                        "record_role": observed_role,
                    }
                )
            path_value = evidence.get("agent_path")
            record_path = item.get("agent_path")
            if path_value and record_path and path_value != record_path:
                invalid_metric_identifiers.append(
                    {
                        "child_session_id": child_id,
                        "parent_thread_id": parent_id,
                        "record_id": item.get("record_id"),
                        "reason": "agent_path mismatch",
                    }
                )
    unmatched_structured_records = [
        item
        for item in structured
        if item.get("_structured_key") not in matched_structured_keys
    ]
    for item in unmatched_structured_records:
        item.pop("_structured_key", None)
    for item in structured:
        item.pop("_structured_key", None)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "maintenance-coverage-reconciliation",
        "root": str(root.expanduser().resolve()),
        "window": {"since": since, "until": until},
        "session_evidence_count": len(session_evidence),
        "structured_child_count": len(structured),
        "missing_structured_records": missing_structured_records,
        "duplicate_structured_records": duplicate_structured_records,
        "reused_child_records": reused_child_records,
        "out_of_window_session_evidence": out_of_window_session_evidence,
        "role_mismatches": role_mismatches,
        "invalid_metric_identifiers": invalid_metric_identifiers,
        "unmatched_structured_records": unmatched_structured_records,
        "valid": not (
            missing_structured_records
            or duplicate_structured_records
            or role_mismatches
            or invalid_metric_identifiers
            or unmatched_structured_records
        ),
    }


def build_payloads(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any] | None]:
    parent_thread_id = bounded(args.parent_thread_id, "parent_thread_id", 160)
    turn_id = bounded(args.turn_id, "turn_id", 160)
    summaries = parse_keyed_values(args.agent_summary, "agent summary", 800)
    task_names = parse_keyed_values(args.agent_task, "agent task", 160)
    agents = [
        summarize_agent_session(path, parent_thread_id, summaries, task_names)
        for path in args.agent_session
    ]
    agent_ids = [agent["agent_id"] for agent in agents]
    if len(set(agent_ids)) != len(agent_ids):
        raise ValueError("duplicate child session ids")

    attempts = (
        parse_parent_attempts(args.parent_session, parent_thread_id, turn_id)
        if args.parent_session
        else []
    )
    if args.parent_session:
        # Replay is an explicit legacy path.  Counters can be useful, but the
        # transcript does not provide the hook-era observability guarantees.
        coordination_metrics = parse_parent_coordination(
            read_jsonl(args.parent_session),
            turn_id,
        )
    else:
        coordination_metrics = empty_coordination_metrics(
            observability="unknown", source="not_observed"
        )
        coordination_metrics["operation_observability"] = {
            operation: "not_observed" for operation in COORDINATION_OPERATIONS
        }
    attempts = connect_attempts_to_agents(attempts, agents)
    enrich_child_identity(attempts, agents, parent_thread_id, turn_id)
    if not attempts and not agents:
        raise ValueError("record requires at least one dispatch attempt or child session")
    if args.source == "correction" and not args.supersedes:
        raise ValueError("correction records require at least one --supersedes id")

    binding_issues = binding_uncertainty_messages(attempts, agents)
    lifecycle_issues = lifecycle_incomplete_messages(agents)
    zero_yield_issues = zero_yield_messages(agents)
    delivery_issues = delivery_failure_messages(agents)
    fork_mismatch_issues = fork_request_observation_mismatch_messages(
        attempts, agents
    )
    automatic_issue_sets = (
        ("agent-binding-uncertainty", binding_issues),
        ("child-lifecycle-incomplete", lifecycle_issues),
        ("zero-yield-child", zero_yield_issues),
        ("child-delivery-failure", delivery_issues),
        ("fork-request-observation-mismatch", fork_mismatch_issues),
    )
    automatic_evidence: list[str] = []
    for anomaly_type, issues in automatic_issue_sets:
        if issues:
            if anomaly_type not in args.anomaly_type:
                args.anomaly_type.append(anomaly_type)
            automatic_evidence.extend(issues)
    if automatic_evidence:
        observed_evidence = "; ".join(automatic_evidence)
        args.anomaly_evidence = (
            f"{args.anomaly_evidence}; {observed_evidence}"
            if args.anomaly_evidence
            else observed_evidence
        )
    if not args.anomaly_type and any(
        (args.anomaly_summary, args.anomaly_evidence, args.anomaly_impact)
    ):
        raise ValueError("anomaly detail fields require at least one --anomaly-type")

    now = datetime.now(timezone.utc)
    record_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(4)}"
    role_counts = dict(
        sorted(
            Counter(
                agent["actual_role"]
                for agent in agents
                if isinstance(agent.get("actual_role"), str)
                and agent.get("actual_role")
            ).items()
        )
    )
    observed_started_at, observed_ended_at, observed_duration_ms = observed_window(
        attempts, agents
    )
    common = {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "recorded_at": now.isoformat(),
        "source": args.source,
        "supersedes": sorted(
            {bounded(value, "supersedes", 200) for value in args.supersedes}
        ),
        "project": bounded(args.project, "project", 500),
        "parent_thread_id": parent_thread_id,
        "turn_id": turn_id,
        "task_signature": bounded(args.task_signature, "task_signature", 160),
    }
    routine_payload = {
        **common,
        "collection_mode": "transcript-replay-fallback",
        "outcome": aggregate_outcome(attempts, agents),
        "observed_started_at": observed_started_at,
        "observed_ended_at": observed_ended_at,
        "observed_duration_ms": observed_duration_ms,
        "attempt_count": len(attempts),
        "child_count": len(agents),
        "roles": sorted(role_counts),
        "role_counts": role_counts,
        "agents": agents,
        "spawn_attempts": attempts,
        "coordination_metrics": coordination_metrics,
        "collector": {
            "transcript_strategy": "full-replay-fallback",
            "coordination_metrics_source": coordination_metrics.get("source"),
        },
    }
    anomaly_payload = None
    if args.anomaly_type:
        anomaly_payload = {
            **common,
            "related_routine": f"{record_id}.json",
            "anomaly_types": sorted(set(args.anomaly_type)),
            "summary": optional_bounded(args.anomaly_summary, "anomaly_summary", 500),
            "evidence": optional_bounded(args.anomaly_evidence, "anomaly_evidence", 1600),
            "impact": optional_bounded(args.anomaly_impact, "anomaly_impact", 800),
        }
    return routine_payload, anomaly_payload


def main() -> None:
    args = parser().parse_args()
    if args.command == "init":
        routine_dir, anomaly_dir = storage(args.root)
        print(json.dumps({"routine": str(routine_dir), "anomalies": str(anomaly_dir)}))
        return

    if args.command == "audit":
        try:
            payload = audit_records(args.root)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not payload["valid"]:
            raise SystemExit(1)
        return

    if args.command in {"reconcile", "coverage-audit"}:
        try:
            payload = reconcile_coverage(
                args.root,
                args.sessions_root,
                args.session,
                args.since,
                args.until,
            )
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not payload["valid"]:
            raise SystemExit(1)
        return

    try:
        routine_payload, anomaly_payload = build_payloads(args)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    if args.dry_run:
        print(
            json.dumps(
                {"routine": routine_payload, "anomaly": anomaly_payload},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    routine_dir, anomaly_dir = storage(args.root)
    record_id = routine_payload["record_id"]
    if routine_payload["source"] == "correction":
        missing = sorted(
            set(routine_payload["supersedes"]) - existing_record_ids(routine_dir)
        )
        if missing:
            raise SystemExit(
                "correction supersedes missing record(s): " + ", ".join(missing)
            )
    routine_path = routine_dir / f"{record_id}.json"
    anomaly_path = anomaly_dir / f"{record_id}.json" if anomaly_payload else None
    write_new(routine_path, routine_payload)
    try:
        if anomaly_path is not None and anomaly_payload is not None:
            write_new(anomaly_path, anomaly_payload)
    except Exception:
        routine_path.unlink(missing_ok=True)
        raise

    print(
        json.dumps(
            {
                "routine": str(routine_path),
                "anomaly": str(anomaly_path) if anomaly_path is not None else None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
