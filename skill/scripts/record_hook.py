#!/usr/bin/env python3
"""Collect native-agent facts from Codex hooks with bounded incremental recovery."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import agent_policy  # noqa: E402
import record_common  # noqa: E402


DEFAULT_ROOT = Path.home() / ".codex" / "agent-team-records"
AGENT_POLICY = agent_policy.load_policy()
SCHEMA_VERSION = 4
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
TOOL_CALL_TYPES = {"custom_tool_call", "function_call"}
TOOL_OUTPUT_TYPES = {"custom_tool_call_output", "function_call_output"}
# Keep this allowlist deliberately small.  The hook matcher intentionally does
# not include the read-only polling operations; parent-tail recovery is the
# only place where they are observed.
COORDINATION_OPERATIONS = record_common.COORDINATION_OPERATIONS
WAIT_OUTCOMES = record_common.WAIT_OUTCOMES
COORDINATION_CALL_CACHE_LIMIT = 256
COORDINATION_CLASSIFIER_VERSION = record_common.COORDINATION_CLASSIFIER_VERSION
is_spawn_tool = record_common.is_spawn_tool
normalize_coordination_operation = record_common.normalize_coordination_operation
classify_wait_outcome = record_common.classify_wait_outcome
payload_dict = record_common.payload_dict
ulid_time_key = record_common.ulid_time_key
parse_timestamp = record_common.parse_timestamp
FORK_REQUEST_OBSERVABILITY = {
    "observed",
    "omitted",
    "invalid",
    "not_observed",
    "legacy",
}
TERMINAL_STATUS = {
    "task_complete": "completed",
    "task_failed": "errored",
    "turn_failed": "errored",
    "turn_aborted": "interrupted",
}
TERMINAL_AGENT_STATUSES = {"completed", "errored", "interrupted"}
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded(value: Any, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def sanitize_requested_fork_turns(
    tool_input: dict[str, Any],
) -> tuple[str | int | None, str]:
    """Return only the allowlisted fork request and its evidence state.

    The spawn prompt is deliberately not inspected.  A missing key is distinct
    from a hook/replay path where the request was never observed, and malformed
    values collapse to ``invalid`` without retaining their representation.
    """
    return record_common.sanitize_requested_fork_turns(
        tool_input, missing_observability="omitted"
    )


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((end_time - start_time).total_seconds() * 1000))


def expected_role_runtimes(agent: dict[str, Any]) -> tuple[tuple[str, str], ...] | None:
    return agent_policy.expected_role_runtimes(
        AGENT_POLICY, agent.get("role"), agent.get("started_at")
    )


def service_tier_mismatch(agent: dict[str, Any]) -> str | None:
    role = agent.get("actual_role") or agent.get("role")
    expected = agent_policy.expected_service_tier_aliases(
        AGENT_POLICY, role, agent.get("started_at")
    )
    observed = agent.get("service_tier")
    if not expected or not isinstance(observed, str) or not observed.strip():
        return None
    normalized = observed.strip().lower()
    if normalized in expected:
        return None
    return (
        f"{agent.get('agent_id') or 'unknown'} role {role} expected service tier "
        f"{('/'.join(sorted(expected)))} aliases, observed {observed}"
    )


def key_for(*values: str) -> str:
    joined = "\0".join(values).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def fsync_directory(path: Path) -> None:
    """Persist a directory entry after rename/mkdir on POSIX filesystems."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno in {errno.EINVAL, errno.ENOTSUP, errno.ENOTTY}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            # Some POSIX filesystems expose directory descriptors but reject
            # fsync with EINVAL/ENOTSUP. Preserve normal operation there while
            # still surfacing real I/O failures.
            if error.errno not in {errno.EINVAL, errno.ENOTSUP, errno.ENOTTY}:
                raise
    finally:
        os.close(descriptor)


def ensure_private_directory(path: Path) -> None:
    existed = path.is_dir()
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    path.chmod(PRIVATE_DIRECTORY_MODE)
    if not existed:
        fsync_directory(path.parent)


def state_dirs(root: Path, *, create: bool = True) -> dict[str, Path]:
    resolved = root.expanduser().resolve()
    if create:
        ensure_private_directory(resolved)
        ensure_private_directory(resolved / "hook-state")
    result = {
        "routine": resolved / "routine",
        "anomalies": resolved / "anomalies",
        "turns": resolved / "hook-state" / "turns",
        "cursors": resolved / "hook-state" / "cursors",
        "parent_cursors": resolved / "hook-state" / "parent-cursors",
        "agents_index": resolved / "hook-state" / "agents",
        "child_turns": resolved / "hook-state" / "child-turns",
        "path_bindings": resolved / "hook-state" / "path-bindings",
        "pending_lifecycle": resolved / "hook-state" / "pending-lifecycle",
        "finalized": resolved / "hook-state" / "finalized",
        "locks": resolved / "hook-state" / "locks",
        "event_keys": resolved / "hook-state" / "event-keys",
        "event_key_cursors": resolved / "hook-state" / "event-key-cursors",
        "metric_snapshots": resolved / "hook-state" / "metric-snapshots",
    }
    if create:
        for directory in result.values():
            ensure_private_directory(directory)
    return result


def turn_key(parent_thread_id: str, turn_id: str) -> str:
    return key_for(parent_thread_id, turn_id)


def turn_journal(dirs: dict[str, Path], parent_thread_id: str, turn_id: str) -> Path:
    return dirs["turns"] / f"{turn_key(parent_thread_id, turn_id)}.jsonl"


def turn_pointer(dirs: dict[str, Path], parent_thread_id: str, turn_id: str) -> Path:
    return dirs["finalized"] / f"{turn_key(parent_thread_id, turn_id)}.json"


def cursor_path(dirs: dict[str, Path], transcript_path: Path) -> Path:
    return dirs["cursors"] / f"{key_for(str(transcript_path.resolve()))}.json"


def metric_snapshot_path(dirs: dict[str, Path], agent_id: str) -> Path:
    return dirs["metric_snapshots"] / f"{key_for(agent_id)}.json"


def parent_cursor_path(
    dirs: dict[str, Path], parent_thread_id: str, turn_id: str
) -> Path:
    return dirs["parent_cursors"] / f"{turn_key(parent_thread_id, turn_id)}.json"


def agent_index_path(dirs: dict[str, Path], agent_id: str) -> Path:
    return dirs["agents_index"] / f"{key_for(agent_id)}.json"


def child_turn_index_path(
    dirs: dict[str, Path], parent_thread_id: str, subagent_turn_id: str
) -> Path:
    return dirs["child_turns"] / f"{key_for(parent_thread_id, subagent_turn_id)}.json"


def path_binding_path(
    dirs: dict[str, Path], parent_thread_id: str, child_ref: str
) -> Path:
    return dirs["path_bindings"] / f"{key_for(parent_thread_id, child_ref)}.json"


def pending_lifecycle_directory(
    dirs: dict[str, Path], parent_thread_id: str, *, create: bool = True
) -> Path:
    path = dirs["pending_lifecycle"] / key_for(parent_thread_id)
    if create:
        ensure_private_directory(path)
    return path


def pending_lifecycle_path(
    dirs: dict[str, Path], parent_thread_id: str, agent_id: str
) -> Path:
    return pending_lifecycle_directory(dirs, parent_thread_id) / f"{key_for(agent_id)}.json"


def agent_routing_lock_path(
    dirs: dict[str, Path], parent_thread_id: str, agent_id: str
) -> Path:
    return dirs["locks"] / f"agent-routing-{key_for(parent_thread_id, agent_id)}.lock"


def has_pending_lifecycle(
    root: Path, parent_thread_id: str, agent_id: str
) -> bool:
    dirs = state_dirs(root)
    path = pending_lifecycle_path(dirs, parent_thread_id, agent_id)
    lock = dirs["locks"] / f"pending-lifecycle-{path.stem}.lock"
    with locked(lock):
        return path.is_file()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    ensure_private_directory(path.parent)
    with path.open("a+", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def write_new(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    with path.open("x", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def event_key(event: dict[str, Any]) -> str:
    """Return a semantic idempotency key for one sanitized hook event."""
    normalized = {
        key: value
        for key, value in event.items()
        if key
        not in {
            "parent_thread_id",
            "turn_id",
            "observed_at",
            "event_key",
            "parent_transcript_offset",
        }
    }
    metrics = normalized.get("metrics")
    if isinstance(metrics, dict):
        normalized["metrics"] = {
            key: value
            for key, value in metrics.items()
            if key not in {"collection_passes", "invalid_line_count"}
        }
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def event_key_path(
    dirs: dict[str, Path], parent_thread_id: str, turn_id: str, key: str
) -> Path:
    """Return one durable marker for a journal event key.

    Markers are deliberately one-file-per-key: ingest checks only the keys in
    the incoming hook payload and never materializes the whole turn journal.
    """
    directory = dirs["event_keys"] / turn_key(parent_thread_id, turn_id)
    ensure_private_directory(directory)
    key_text = str(key)
    safe_key = (
        key_text
        if re.fullmatch(r"[0-9a-f]{64}", key_text)
        else hashlib.sha256(key_text.encode("utf-8")).hexdigest()
    )
    return directory / f"{safe_key}.json"


def event_key_index_marker(
    dirs: dict[str, Path], parent_thread_id: str, turn_id: str
) -> Path:
    directory = dirs["event_keys"] / turn_key(parent_thread_id, turn_id)
    ensure_private_directory(directory)
    return directory / ".initialized"


def event_key_cursor_path(
    dirs: dict[str, Path], parent_thread_id: str, turn_id: str
) -> Path:
    return dirs["event_key_cursors"] / f"{turn_key(parent_thread_id, turn_id)}.json"


def reconcile_event_key_index_locked(
    journal: Path,
    dirs: dict[str, Path],
    parent_thread_id: str,
    turn_id: str,
) -> None:
    """Advance the durable key index over only the unindexed journal suffix."""
    cursor_path = event_key_cursor_path(dirs, parent_thread_id, turn_id)
    cursor = read_json(cursor_path) if cursor_path.is_file() else {}
    indexed_offset = cursor.get("offset", 0)
    if not isinstance(indexed_offset, int) or indexed_offset < 0:
        indexed_offset = 0
    file_size = journal.stat().st_size if journal.is_file() else 0
    if indexed_offset > file_size:
        indexed_offset = 0
    next_offset = indexed_offset
    indexed_count = cursor.get("indexed_event_count", 0)
    if not isinstance(indexed_count, int) or indexed_count < 0:
        indexed_count = 0
    if journal.is_file() and indexed_offset < file_size:
        with journal.open("rb") as handle:
            handle.seek(indexed_offset)
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n") and handle.tell() == file_size:
                    handle.seek(line_start)
                    break
                try:
                    line = raw.decode("utf-8")
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(f"{journal}: invalid JSON: {error}") from error
                if isinstance(event, dict):
                    key = event.get("event_key") or event_key(event)
                    marker_path = event_key_path(
                        dirs, parent_thread_id, turn_id, key
                    )
                    if not marker_path.is_file():
                        # Marker durability precedes cursor advancement. If a
                        # process dies after journal fsync, the old cursor
                        # causes this suffix to be replayed on the next hook.
                        atomic_json(marker_path, {"event_key": key})
                    indexed_count += 1
                next_offset = handle.tell()
    atomic_json(
        cursor_path,
        {
            "parent_thread_id": parent_thread_id,
            "turn_id": turn_id,
            "offset": next_offset,
            "indexed_event_count": indexed_count,
            "updated_at": utc_now(),
        },
    )
    marker = event_key_index_marker(dirs, parent_thread_id, turn_id)
    if not marker.is_file():
        atomic_json(marker, {"initialized_at": utc_now()})


def initialize_event_key_index_locked(
    journal: Path,
    dirs: dict[str, Path],
    parent_thread_id: str,
    turn_id: str,
) -> None:
    """Backward-compatible name for the offset-based reconciliation pass."""
    reconcile_event_key_index_locked(
        journal, dirs, parent_thread_id, turn_id
    )


def append_journal_events_locked(
    journal: Path,
    parent_thread_id: str,
    turn_id: str,
    events: list[dict[str, Any]],
) -> None:
    dirs = state_dirs(journal.parents[2])
    reconcile_event_key_index_locked(
        journal, dirs, parent_thread_id, turn_id
    )
    new_payloads: list[dict[str, Any]] = []
    pending_keys: set[str] = set()
    for event in events:
        key = event.get("event_key") or event_key(event)
        if (
            key in pending_keys
            or event_key_path(dirs, parent_thread_id, turn_id, key).is_file()
        ):
            continue
        pending_keys.add(key)
        new_payloads.append(
            {
                "parent_thread_id": parent_thread_id,
                "turn_id": turn_id,
                **event,
                "event_key": key,
            }
        )
    if not new_payloads:
        return
    with journal.open("a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        for payload in new_payloads:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    # The journal is durable now; replay only this new suffix to install
    # markers and atomically advance the indexed offset. If the process dies
    # before this call completes, the next ingest repairs the same suffix.
    reconcile_event_key_index_locked(
        journal, dirs, parent_thread_id, turn_id
    )


def append_event(
    root: Path, parent_thread_id: str, turn_id: str, event: dict[str, Any]
) -> Path:
    dirs = state_dirs(root)
    journal = turn_journal(dirs, parent_thread_id, turn_id)
    lock = dirs["locks"] / f"turn-{turn_key(parent_thread_id, turn_id)}.lock"
    with locked(lock):
        append_journal_events_locked(
            journal, parent_thread_id, turn_id, [event]
        )
    return journal


def bind_agent_turn(
    root: Path, agent_id: str, parent_thread_id: str, parent_turn_id: str
) -> None:
    dirs = state_dirs(root)
    path = agent_index_path(dirs, agent_id)
    lock = dirs["locks"] / f"agent-{path.stem}.lock"
    with locked(lock):
        atomic_json(
            path,
            {
                "agent_id": agent_id,
                "parent_thread_id": parent_thread_id,
                "parent_turn_id": parent_turn_id,
                "updated_at": utc_now(),
            },
        )


def bound_parent_turn(
    root: Path,
    agent_id: str,
    parent_thread_id: str,
    fallback_turn_id: str | None = None,
    *,
    create: bool = True,
) -> str | None:
    dirs = state_dirs(root, create=create)
    path = agent_index_path(dirs, agent_id)
    if path.is_file():
        payload = read_json(path)
        if payload.get("parent_thread_id") == parent_thread_id:
            value = bounded(payload.get("parent_turn_id"), 160)
            if value:
                return value
    return fallback_turn_id


def bind_child_turn(
    root: Path,
    parent_thread_id: str,
    subagent_turn_id: str,
    parent_turn_id: str,
) -> None:
    dirs = state_dirs(root)
    path = child_turn_index_path(dirs, parent_thread_id, subagent_turn_id)
    lock = dirs["locks"] / f"child-turn-{path.stem}.lock"
    with locked(lock):
        if path.is_file():
            existing = read_json(path)
            existing_parent_turn = bounded(existing.get("parent_turn_id"), 160)
            if existing_parent_turn and existing_parent_turn != parent_turn_id:
                return
        atomic_json(
            path,
            {
                "parent_thread_id": parent_thread_id,
                "subagent_turn_id": subagent_turn_id,
                "parent_turn_id": parent_turn_id,
                "updated_at": utc_now(),
            },
        )


def bound_child_turn(
    root: Path,
    parent_thread_id: str,
    subagent_turn_id: str,
    *,
    create: bool = True,
) -> str | None:
    dirs = state_dirs(root, create=create)
    path = child_turn_index_path(dirs, parent_thread_id, subagent_turn_id)
    if not path.is_file():
        return None
    payload = read_json(path)
    if payload.get("parent_thread_id") != parent_thread_id:
        return None
    return bounded(payload.get("parent_turn_id"), 160)


def bind_lifecycle_turns(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    events: list[dict[str, Any]],
) -> None:
    for event in events:
        subagent_turn_id = event.get("subagent_turn_id")
        if isinstance(subagent_turn_id, str) and subagent_turn_id:
            bind_child_turn(
                root,
                parent_thread_id,
                subagent_turn_id,
                parent_turn_id,
            )


def register_path_binding(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    child_ref: str,
    tool_use_id: str | None,
) -> None:
    """Remember stable path evidence without choosing between duplicate parent turns."""
    dirs = state_dirs(root)
    path = path_binding_path(dirs, parent_thread_id, child_ref)
    lock = dirs["locks"] / f"path-binding-{path.stem}.lock"
    with locked(lock):
        payload = (
            read_json(path)
            if path.is_file()
            else {
                "parent_thread_id": parent_thread_id,
                "child_ref": child_ref,
                "candidates": [],
            }
        )
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        candidate = {
            "parent_turn_id": parent_turn_id,
            "tool_use_id": tool_use_id,
        }
        if candidate not in candidates:
            candidates.append(candidate)
        payload["candidates"] = candidates
        payload["updated_at"] = utc_now()
        atomic_json(path, payload)


def parent_turn_from_path_binding(
    root: Path,
    parent_thread_id: str,
    child_ref: str | None,
    *,
    create: bool = True,
) -> str | None:
    if not child_ref:
        return None
    dirs = state_dirs(root, create=create)
    path = path_binding_path(dirs, parent_thread_id, child_ref)
    if not path.is_file():
        return None
    payload = read_json(path)
    parent_turns = {
        candidate.get("parent_turn_id")
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, dict)
        and isinstance(candidate.get("parent_turn_id"), str)
        and candidate.get("parent_turn_id")
    }
    return next(iter(parent_turns)) if len(parent_turns) == 1 else None


def store_pending_lifecycle(
    root: Path,
    parent_thread_id: str,
    agent_id: str,
    event: dict[str, Any],
) -> None:
    """Keep sanitized lifecycle evidence until a stable parent-turn binding exists."""
    dirs = state_dirs(root)
    path = pending_lifecycle_path(dirs, parent_thread_id, agent_id)
    lock = dirs["locks"] / f"pending-lifecycle-{path.stem}.lock"
    with locked(lock):
        payload = (
            read_json(path)
            if path.is_file()
            else {
                "parent_thread_id": parent_thread_id,
                "agent_id": agent_id,
                "events": [],
            }
        )
        events = payload.get("events")
        if not isinstance(events, list):
            events = []
        key = event.get("event_key") or event_key(event)
        existing_keys = {
            item.get("event_key") or event_key(item)
            for item in events
            if isinstance(item, dict)
        }
        if key not in existing_keys:
            events.append({**event, "event_key": key})
        payload["events"] = events[-8:]
        payload["updated_at"] = utc_now()
        atomic_json(path, payload)


def flush_pending_lifecycle(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    agent_id: str,
    extra_events: list[dict[str, Any]] | None = None,
) -> None:
    # Publish the stable UUID binding before draining pending events so a
    # concurrent lifecycle hook cannot recreate an unowned pending file in
    # the gap between deletion and binding.
    bind_agent_turn(root, agent_id, parent_thread_id, parent_turn_id)
    dirs = state_dirs(root)
    path = pending_lifecycle_path(dirs, parent_thread_id, agent_id)
    pending_lock = dirs["locks"] / f"pending-lifecycle-{path.stem}.lock"
    journal = turn_journal(dirs, parent_thread_id, parent_turn_id)
    turn_lock = dirs["locks"] / f"turn-{turn_key(parent_thread_id, parent_turn_id)}.lock"
    with locked(turn_lock):
        with locked(pending_lock):
            if not path.is_file():
                events: list[dict[str, Any]] = []
            else:
                payload = read_json(path)
                raw_events = payload.get("events")
                events = (
                    [event for event in raw_events if isinstance(event, dict)]
                    if isinstance(raw_events, list)
                    else []
                )
                path.unlink()
        events.extend(extra_events or [])
        events.sort(key=lambda item: item.get("observed_at") or "")
        bind_lifecycle_turns(
            root, parent_thread_id, parent_turn_id, events
        )
        append_journal_events_locked(
            journal, parent_thread_id, parent_turn_id, events
        )


def resolve_pending_lifecycle_by_path(
    root: Path, parent_thread_id: str
) -> None:
    dirs = state_dirs(root)
    directory = pending_lifecycle_directory(dirs, parent_thread_id)
    for path in sorted(directory.glob("*.json")):
        try:
            initial = read_json(path)
        except FileNotFoundError:
            continue
        agent_id = initial.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        routing_lock = agent_routing_lock_path(
            dirs, parent_thread_id, agent_id
        )
        with locked(routing_lock):
            pending_lock = dirs["locks"] / f"pending-lifecycle-{path.stem}.lock"
            with locked(pending_lock):
                if not path.is_file():
                    continue
                payload = read_json(path)
            events = payload.get("events")
            if not isinstance(events, list):
                continue
            agent_path = None
            for event in reversed(events):
                metrics = event.get("metrics") if isinstance(event, dict) else None
                if isinstance(metrics, dict) and isinstance(
                    metrics.get("agent_path"), str
                ):
                    agent_path = metrics["agent_path"]
                    break
            parent_turn_id = parent_turn_from_path_binding(
                root, parent_thread_id, agent_path
            )
            if parent_turn_id:
                flush_pending_lifecycle(
                    root, parent_thread_id, parent_turn_id, agent_id
                )


def register_tool_binding(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    event: dict[str, Any],
) -> None:
    operation = event.get("operation")
    if (
        operation == "followup_task"
        and event.get("kind") == "tool_post"
        and event.get("outcome") == "completed"
        and event.get("target_agent_id")
    ):
        target = str(event["target_agent_id"])
        if UUID_PATTERN.fullmatch(target):
            dirs = state_dirs(root)
            routing_lock = agent_routing_lock_path(
                dirs, parent_thread_id, target
            )
            with locked(routing_lock):
                previous_turn = bound_parent_turn(
                    root, target, parent_thread_id
                )
                if previous_turn == parent_turn_id:
                    flush_pending_lifecycle(
                        root, parent_thread_id, parent_turn_id, target
                    )
                elif previous_turn:
                    flush_pending_lifecycle(
                        root, parent_thread_id, previous_turn, target
                    )
                    bind_agent_turn(
                        root, target, parent_thread_id, parent_turn_id
                    )
                elif not has_pending_lifecycle(
                    root, parent_thread_id, target
                ):
                    bind_agent_turn(
                        root, target, parent_thread_id, parent_turn_id
                    )
        else:
            child_ref = target if target.startswith("/") else f"/root/{target}"
            register_path_binding(
                root,
                parent_thread_id,
                parent_turn_id,
                child_ref,
                event.get("tool_use_id")
                if isinstance(event.get("tool_use_id"), str)
                else None,
            )
            resolve_pending_lifecycle_by_path(root, parent_thread_id)
        return
    if (
        operation != "spawn_agent"
        or event.get("kind") != "tool_post"
        or event.get("outcome") != "started"
    ):
        return
    agent_id = event.get("agent_id")
    child_ref = event.get("child_ref")
    if isinstance(agent_id, str) and agent_id:
        dirs = state_dirs(root)
        routing_lock = agent_routing_lock_path(
            dirs, parent_thread_id, agent_id
        )
        with locked(routing_lock):
            flush_pending_lifecycle(
                root, parent_thread_id, parent_turn_id, agent_id
            )
    if isinstance(child_ref, str) and child_ref.startswith("/"):
        register_path_binding(
            root,
            parent_thread_id,
            parent_turn_id,
            child_ref,
            event.get("tool_use_id")
            if isinstance(event.get("tool_use_id"), str)
            else None,
        )
        resolve_pending_lifecycle_by_path(root, parent_thread_id)


def register_recovered_tool_bindings(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    events: list[dict[str, Any]],
) -> None:
    """Index deterministic recovered spawn evidence without flushing the locked turn."""
    for event in events:
        if (
            event.get("operation") != "spawn_agent"
            or event.get("kind") != "tool_post"
            or event.get("outcome") != "started"
        ):
            continue
        agent_id = event.get("agent_id")
        child_ref = event.get("child_ref")
        if isinstance(agent_id, str) and agent_id:
            bind_agent_turn(root, agent_id, parent_thread_id, parent_turn_id)
        if isinstance(child_ref, str) and child_ref.startswith("/"):
            register_path_binding(
                root,
                parent_thread_id,
                parent_turn_id,
                child_ref,
                event.get("tool_use_id")
                if isinstance(event.get("tool_use_id"), str)
                else None,
            )


def pending_lifecycle_events_for_turn(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    *,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Read only lifecycle evidence that resolves uniquely to this parent turn."""
    dirs = state_dirs(root, create=persist)
    directory = pending_lifecycle_directory(
        dirs, parent_thread_id, create=persist
    )
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        lock = dirs["locks"] / f"pending-lifecycle-{path.stem}.lock"
        with (locked(lock) if persist else nullcontext()):
            if not path.is_file():
                continue
            payload = read_json(path)
        agent_id = payload.get("agent_id")
        events = payload.get("events")
        if not isinstance(agent_id, str) or not isinstance(events, list):
            continue
        child_turns = {
            resolved
            for event in events
            if isinstance(event, dict)
            and isinstance(event.get("subagent_turn_id"), str)
            and (
                resolved := bound_child_turn(
                    root,
                    parent_thread_id,
                    event["subagent_turn_id"],
                    create=persist,
                )
            )
        }
        resolved_turn = next(iter(child_turns)) if len(child_turns) == 1 else None
        if len(child_turns) > 1:
            continue
        resolved_turn = resolved_turn or bound_parent_turn(
            root,
            agent_id,
            parent_thread_id,
            create=persist,
        )
        if resolved_turn is None:
            agent_path = None
            for event in reversed(events):
                metrics = event.get("metrics") if isinstance(event, dict) else None
                if isinstance(metrics, dict) and isinstance(
                    metrics.get("agent_path"), str
                ):
                    agent_path = metrics["agent_path"]
                    break
            resolved_turn = parent_turn_from_path_binding(
                root,
                parent_thread_id,
                agent_path,
                create=persist,
            )
        if resolved_turn != parent_turn_id:
            continue
        if persist:
            bind_agent_turn(root, agent_id, parent_thread_id, parent_turn_id)
            bind_lifecycle_turns(root, parent_thread_id, parent_turn_id, events)
        result.extend(event for event in events if isinstance(event, dict))
    return result


def release_terminal_agent_bindings(
    root: Path,
    parent_thread_id: str,
    parent_turn_id: str,
    agents: list[dict[str, Any]],
) -> None:
    dirs = state_dirs(root)
    for agent in agents:
        if agent.get("status") not in {"completed", "errored", "interrupted"}:
            continue
        agent_id = agent.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        path = agent_index_path(dirs, agent_id)
        lock = dirs["locks"] / f"agent-{path.stem}.lock"
        with locked(lock):
            if not path.is_file():
                continue
            payload = read_json(path)
            if (
                payload.get("parent_thread_id") == parent_thread_id
                and payload.get("parent_turn_id") == parent_turn_id
            ):
                path.unlink()


def load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if isinstance(payload, dict):
                events.append(payload)
    return events


def empty_cursor(path: Path) -> dict[str, Any]:
    return {
        "transcript_path": str(path.resolve()),
        "offset": 0,
        "file_size": 0,
        "passes": 0,
        "event_count": 0,
        "invalid_line_count": 0,
        "session_meta": {},
        "runtime_samples": [],
        "authority_samples": [],
        "service_tier": None,
        "service_tier_source": "not_observed",
        "first_started_at": None,
        "latest_started_index": -1,
        "turn_count": 0,
        "latest_terminal_index": -1,
        "latest_terminal_type": None,
        "latest_terminal_at": None,
        "latest_terminal_error": None,
        "latest_final_message_chars": 0,
        "tool_calls": 0,
        "tool_outputs": 0,
        "nested_agent_calls": 0,
        "token_usage": None,
        # Forked child rollouts contain a serialized parent-history prefix.
        # Keep the byte cursor independent from the metric boundary so that
        # the prefix can be skipped once, while later child turns remain
        # incrementally consumable.
        "forked": False,
        "fork_observed": None,
        "fork_observation_source": "not_observed",
        "forked_from_id": None,
        "metric_start_offset": None,
        "fork_history_event_count": 0,
        "metric_scope": "child-local",
        "metric_validity": "valid",
        "metric_observability": "incremental-child-cursor",
        "metric_boundary_method": None,
    }


def first_transcript_event(path: Path) -> dict[str, Any] | None:
    """Read only the first complete JSONL event for metadata probing."""
    with path.open("rb") as handle:
        while True:
            raw = handle.readline()
            if not raw:
                return None
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                return event


def fork_session_meta(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "session_meta":
        return None
    payload = payload_dict(event)
    forked_from = payload.get("forked_from_id") or event.get("forked_from_id")
    child_id = payload.get("id")
    if not isinstance(forked_from, str) or not forked_from:
        return None
    if not isinstance(child_id, str) or not child_id:
        return None
    return {
        "forked_from_id": bounded(forked_from, 160),
        "child_id": bounded(child_id, 160),
        "timestamp": payload.get("timestamp") or event.get("timestamp"),
    }


def fork_observation_from_session_meta(
    event: dict[str, Any] | None,
) -> tuple[bool | None, str, str | None]:
    """Classify fork evidence without treating absent metadata as no fork."""
    if not isinstance(event, dict) or event.get("type") != "session_meta":
        return None, "not_observed", None
    metadata = fork_session_meta(event)
    if metadata is None:
        return False, "child-session-meta", None
    return True, "child-session-meta", metadata["forked_from_id"]


def fork_metric_boundary(transcript: Path) -> tuple[int, int, str] | None:
    """Find the first live child event and number of skipped history events.

    Forked rollouts begin with a child session header followed by copied parent
    events.  The first live task's UUIDv7 timestamp is at/after the child id;
    this child-local anchor avoids replaying the parent transcript.  A compact
    timestamp-gap fallback handles legacy metadata that lacks usable ids.
    """
    first = first_transcript_event(transcript)
    if first is None:
        return None
    meta = fork_session_meta(first)
    if meta is None:
        return None
    child_key = ulid_time_key(meta["child_id"])
    first_timestamp = parse_timestamp(first.get("timestamp"))
    meta_timestamp = parse_timestamp(meta.get("timestamp"))
    previous = first_timestamp
    saw_older_timestamp = False
    latest_task: tuple[int, int] | None = None
    event_index = 0
    with transcript.open("rb") as handle:
        # Consume the first line again only to establish its byte boundary;
        # no event-sized collection is retained.
        if not handle.readline():
            return None
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            event_index += 1
            payload = payload_dict(event)
            current = parse_timestamp(event.get("timestamp"))
            if (
                event.get("type") == "event_msg"
                and payload.get("type") == "task_started"
            ):
                turn_key = ulid_time_key(payload.get("turn_id"))
                if payload.get("turn_id") == meta["child_id"]:
                    return start, event_index, "child-id"
                if (
                    meta_timestamp is not None
                    and current is not None
                    and current >= meta_timestamp
                    and turn_key is not None
                    and child_key is not None
                    and turn_key >= child_key
                ):
                    return start, event_index, "uuidv7-time"
                latest_task = (start, event_index)
            if current is not None and meta_timestamp is not None:
                if current < meta_timestamp:
                    saw_older_timestamp = True
                elif (
                    saw_older_timestamp
                    and event.get("type") == "event_msg"
                    and payload.get("type") == "task_started"
                ):
                    return start, event_index, "timestamp-transition"
            if previous is not None and current is not None and not saw_older_timestamp:
                delta_ms = (current - previous).total_seconds() * 1000
                if delta_ms > 500 and latest_task is not None:
                    return latest_task[0], latest_task[1], "timestamp-gap"
            if current is not None:
                previous = current
    return None


def consume_transcript_event(
    state: dict[str, Any], event: dict[str, Any], *, include_metrics: bool = True
) -> None:
    event_type = event.get("type")
    payload = payload_dict(event)

    # Session metadata belongs to the child even when the event is being
    # inspected while locating the inherited fork prefix.  Do not count that
    # probe as a runtime event until the real child boundary is known.
    if event_type == "session_meta" and not state["session_meta"]:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        subagent = source.get("subagent") if isinstance(source.get("subagent"), dict) else {}
        spawn = (
            subagent.get("thread_spawn")
            if isinstance(subagent.get("thread_spawn"), dict)
            else {}
        )
        state["session_meta"] = {
            "agent_id": payload.get("id") or payload.get("session_id"),
            "parent_thread_id": payload.get("parent_thread_id"),
            "timestamp": payload.get("timestamp") or event.get("timestamp"),
            "agent_path": payload.get("agent_path") or spawn.get("agent_path"),
            "agent_nickname": payload.get("agent_nickname") or spawn.get("agent_nickname"),
            "agent_role": payload.get("agent_role") or spawn.get("agent_role"),
            "model_provider": payload.get("model_provider"),
            "cli_version": payload.get("cli_version"),
            "originator": payload.get("originator"),
            "service_tier": payload.get("service_tier")
            or payload.get("serviceTier"),
            "thread_source": payload.get("thread_source"),
        }
        if state["session_meta"].get("service_tier"):
            state["service_tier"] = bounded(
                state["session_meta"]["service_tier"], 80
            )
            state["service_tier_source"] = "child-session-meta"
    if not include_metrics:
        return

    state["event_count"] += 1
    event_index = state["event_count"]
    timestamp = event.get("timestamp") if isinstance(event.get("timestamp"), str) else None

    if event_type == "session_meta":
        return

    if event_type == "turn_context":
        sample = {
            "model": bounded(payload.get("model"), 160),
            "reasoning_effort": bounded(payload.get("effort"), 80),
            "multi_agent_version": bounded(payload.get("multi_agent_version"), 80),
            "service_tier": bounded(
                payload.get("service_tier")
                or payload.get("serviceTier")
                or payload.get("tier"),
                80,
            ),
        }
        if sample not in state["runtime_samples"]:
            state["runtime_samples"].append(sample)
        if sample["service_tier"]:
            state["service_tier"] = sample["service_tier"]
            state["service_tier_source"] = "child-turn-context"
        sandbox = payload.get("sandbox_policy")
        permission = payload.get("permission_profile")
        workspace_roots = payload.get("workspace_roots")
        authority_sample = {
            "sandbox_mode": bounded(
                sandbox.get("type") if isinstance(sandbox, dict) else sandbox,
                80,
            ),
            "permission_profile": bounded(
                permission.get("type")
                if isinstance(permission, dict)
                else permission,
                80,
            ),
            "approval_policy": bounded(payload.get("approval_policy"), 80),
            "approvals_reviewer": bounded(payload.get("approvals_reviewer"), 80),
            "workspace_root_count": len(workspace_roots)
            if isinstance(workspace_roots, list)
            else None,
            "source": "child-turn-context",
        }
        if authority_sample not in state["authority_samples"]:
            state["authority_samples"].append(authority_sample)
        return

    if event_type == "event_msg":
        message_type = payload.get("type")
        if message_type == "task_started":
            state["turn_count"] += 1
            state["latest_started_index"] = event_index
            if state["first_started_at"] is None:
                state["first_started_at"] = timestamp
        elif message_type in TERMINAL_STATUS:
            state["latest_terminal_index"] = event_index
            state["latest_terminal_type"] = message_type
            state["latest_terminal_at"] = timestamp
            error = payload.get("error") or payload.get("reason")
            state["latest_terminal_error"] = (
                classify_terminal_error(error, message_type)
                if error is not None or message_type == "turn_aborted"
                else None
            )
            if message_type == "task_complete":
                message = payload.get("last_agent_message")
                state["latest_final_message_chars"] = (
                    len(message) if isinstance(message, str) else 0
                )
        elif message_type == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = info.get("total_token_usage")
            if isinstance(usage, dict):
                fields = (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "total_tokens",
                )
                state["token_usage"] = {
                    field: usage[field]
                    for field in fields
                    if isinstance(usage.get(field), int)
                }
        return

    if event_type == "response_item":
        item_type = payload.get("type")
        if item_type in TOOL_CALL_TYPES:
            state["tool_calls"] += 1
            if is_spawn_tool(payload.get("name")):
                state["nested_agent_calls"] += 1
        elif item_type in TOOL_OUTPUT_TYPES:
            state["tool_outputs"] += 1


def cursor_summary(state: dict[str, Any]) -> dict[str, Any]:
    meta = state["session_meta"]
    runtime = state["runtime_samples"][-1] if state["runtime_samples"] else {}
    authority_samples = state.get("authority_samples", [])
    effective_authority = authority_samples[-1] if authority_samples else {
        "sandbox_mode": None,
        "permission_profile": None,
        "approval_policy": None,
        "approvals_reviewer": None,
        "workspace_root_count": None,
        "source": "not_observed",
    }
    terminal_is_current = (
        state["latest_terminal_index"] >= state["latest_started_index"]
        and state["latest_terminal_index"] >= 0
    )
    status = (
        TERMINAL_STATUS.get(state["latest_terminal_type"], "unknown")
        if terminal_is_current
        else "unknown"
    )
    started_at = state["first_started_at"] or meta.get("timestamp")
    ended_at = state["latest_terminal_at"] if terminal_is_current else None
    return {
        "agent_id": meta.get("agent_id"),
        "parent_thread_id": meta.get("parent_thread_id"),
        "agent_path": meta.get("agent_path"),
        "nickname": meta.get("agent_nickname"),
        "role": meta.get("agent_role"),
        "model_provider": meta.get("model_provider"),
        "model": runtime.get("model"),
        "reasoning_effort": runtime.get("reasoning_effort"),
        "multi_agent_version": runtime.get("multi_agent_version"),
        "service_tier": state.get("service_tier") or runtime.get("service_tier"),
        "service_tier_source": state.get("service_tier_source", "not_observed"),
        "runtime_samples": state["runtime_samples"],
        "effective_authority": effective_authority,
        "runtime_provenance": {
            "cli_version": bounded(meta.get("cli_version"), 80),
            "originator": bounded(meta.get("originator"), 160),
            "thread_source": bounded(meta.get("thread_source"), 80),
            "multi_agent_version": runtime.get("multi_agent_version"),
            "source": "child-session-and-turn-context",
        },
        "status": status,
        "turn_count": max(1, state["turn_count"]),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": elapsed_ms(started_at, ended_at),
        "event_count": state["event_count"],
        "tool_calls": state["tool_calls"],
        "tool_outputs": state["tool_outputs"],
        "nested_agent_calls": state["nested_agent_calls"],
        "token_usage": state["token_usage"],
        "final_message_chars": state["latest_final_message_chars"],
        "error": state["latest_terminal_error"] if status == "errored" else None,
        "session_path": state["transcript_path"],
        "transcript_bytes_processed": state["offset"],
        "collection_passes": state["passes"],
        "invalid_line_count": state["invalid_line_count"],
        "metric_scope": state.get("metric_scope", "child-local"),
        "metric_validity": state.get("metric_validity", "valid"),
        "metric_observability": state.get(
            "metric_observability", "incremental-child-cursor"
        ),
        "metric_boundary_method": state.get("metric_boundary_method"),
        "forked": bool(state.get("forked")),
        "fork_observed": state.get("fork_observed"),
        "fork_observation_source": state.get(
            "fork_observation_source", "not_observed"
        ),
        "forked_from_id": state.get("forked_from_id"),
    }


def harvest_transcript(
    root: Path, transcript: Path, *, persist: bool = True
) -> dict[str, Any]:
    resolved = transcript.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"subagent transcript does not exist: {resolved}")
    dirs = state_dirs(root, create=persist)
    cursor = cursor_path(dirs, resolved)
    lock = dirs["locks"] / f"cursor-{cursor.stem}.lock"
    with (locked(lock) if persist else nullcontext()):
        state = read_json(cursor) if cursor.is_file() else empty_cursor(resolved)
        file_size = resolved.stat().st_size
        if state.get("transcript_path") != str(resolved) or state.get("offset", 0) > file_size:
            state = empty_cursor(resolved)
        state.setdefault("authority_samples", [])

        # Forked child rollouts replay parent history into the new transcript.
        # Probe that prefix without advancing the normal metric cursor, then
        # consume only the inclusive live-child boundary and subsequent bytes.
        if state.get("metric_start_offset") is None:
            first_event = first_transcript_event(resolved)
            fork_meta = fork_session_meta(first_event) if first_event else None
            state["forked"] = fork_meta is not None
            (
                state["fork_observed"],
                state["fork_observation_source"],
                state["forked_from_id"],
            ) = fork_observation_from_session_meta(first_event)
            if fork_meta is not None:
                state["session_meta"] = {}
                assert first_event is not None
                consume_transcript_event(state, first_event, include_metrics=False)
                boundary = fork_metric_boundary(resolved)
                if boundary is None:
                    state["metric_scope"] = "fork-boundary-unknown"
                    state["metric_validity"] = "invalid"
                    state["metric_observability"] = "incremental-child-cursor"
                    state["metric_boundary_method"] = "unknown"
                    state["file_size"] = file_size
                    state["passes"] = int(state.get("passes", 0)) + 1
                    state["offset"] = 0
                    if persist:
                        atomic_json(cursor, state)
                    return cursor_summary(state)

                boundary_offset, boundary_index, boundary_method = boundary
                # Reset all metric counters from the probe; retain the child
                # metadata and count its header exactly once.
                metadata = dict(state["session_meta"])
                prior_passes = int(state.get("passes", 0))
                fresh = empty_cursor(resolved)
                fresh["session_meta"] = metadata
                fresh["forked"] = True
                fresh["fork_observed"] = True
                fresh["fork_observation_source"] = "child-session-meta"
                fresh["forked_from_id"] = fork_meta["forked_from_id"]
                fresh["metric_start_offset"] = boundary_offset
                fresh["fork_history_event_count"] = max(0, boundary_index - 1)
                fresh["metric_scope"] = "child-local"
                fresh["metric_validity"] = (
                    "valid"
                    if boundary_method in {"child-id", "uuidv7-time"}
                    else "legacy-unverified"
                )
                fresh["metric_observability"] = "incremental-child-cursor"
                fresh["metric_boundary_method"] = boundary_method
                fresh["passes"] = prior_passes
                state = fresh
                consume_transcript_event(state, first_event)
                invalid_before = 0
                invalid_after = 0
                with resolved.open("rb") as handle:
                    # The header has already been consumed above.  Start at
                    # the live child task boundary for all runtime metrics.
                    handle.seek(boundary_offset)
                    while True:
                        line_start = handle.tell()
                        raw = handle.readline()
                        if not raw:
                            break
                        try:
                            event = json.loads(raw)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            if not raw.endswith(b"\n") and handle.tell() == file_size:
                                handle.seek(line_start)
                                break
                            invalid_after += 1
                            continue
                        if isinstance(event, dict):
                            consume_transcript_event(state, event)
                        state["offset"] = handle.tell()
                state["invalid_line_count"] = invalid_before + invalid_after
                state["file_size"] = file_size
                state["passes"] = int(state.get("passes", 0)) + 1
                if persist:
                    atomic_json(cursor, state)
                return cursor_summary(state)
            # Mark ordinary transcripts as initialized so subsequent harvests
            # retain the existing byte-cursor fast path.
            state["metric_start_offset"] = 0

        offset = int(state.get("offset", 0))
        if offset == file_size and state.get("passes", 0) > 0:
            return cursor_summary(state)
        with resolved.open("rb") as handle:
            handle.seek(offset)
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                try:
                    event = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    if not raw.endswith(b"\n") and handle.tell() == file_size:
                        handle.seek(line_start)
                        break
                    state["invalid_line_count"] += 1
                    state["offset"] = handle.tell()
                    continue
                if isinstance(event, dict):
                    consume_transcript_event(state, event)
                state["offset"] = handle.tell()
        state["file_size"] = file_size
        state["passes"] = int(state.get("passes", 0)) + 1
        if persist:
            atomic_json(cursor, state)
        return cursor_summary(state)


def scalar_strings(value: Any, limit: int = 4096) -> str:
    parts: list[str] = []
    remaining = limit

    def visit(item: Any) -> None:
        nonlocal remaining
        if remaining <= 0:
            return
        if isinstance(item, str):
            fragment = item[:remaining]
            parts.append(fragment)
            remaining -= len(fragment)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
                if remaining <= 0:
                    break
        elif isinstance(item, list):
            for child in item:
                visit(child)
                if remaining <= 0:
                    break

    visit(value)
    return " ".join(parts)


def extract_agent_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("agent_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and UUID_PATTERN.fullmatch(candidate):
                return candidate
        for child in value.values():
            candidate = extract_agent_id(child)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = extract_agent_id(child)
            if candidate:
                return candidate
    elif isinstance(value, str):
        match = UUID_PATTERN.search(value)
        if match:
            return match.group(0)
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return extract_agent_id(parsed)
    return None


def extract_nickname(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("nickname") or value.get("agent_nickname")
        if isinstance(candidate, str):
            return bounded(candidate, 160)
        for child in value.values():
            result = extract_nickname(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = extract_nickname(child)
            if result:
                return result
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return extract_nickname(parsed)
    return None


def extract_child_ref(value: Any) -> str | None:
    """Extract a native agent path without retaining arbitrary response content."""
    if isinstance(value, dict):
        for key in ("agent_path", "task_name", "agent_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("/"):
                return bounded(candidate, 300)
        for child in value.values():
            result = extract_child_ref(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = extract_child_ref(child)
            if result:
                return result
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return extract_child_ref(parsed)
    return None


def classify_tool_error(response: Any) -> str | None:
    text = scalar_strings(response).lower()
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
    if "error" in text or "failed" in text or "rejected" in text:
        return "tool-error"
    if isinstance(response, dict) and response.get("isError") is True:
        return "tool-error"
    return None


def classify_terminal_error(value: Any, terminal_type: str | None = None) -> str:
    if terminal_type == "turn_aborted":
        return "interrupted"
    return classify_tool_error(value) or "unknown"


def normalize_operation(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    normalized = tool_name.lower()
    for operation in (
        "spawn_agent",
        "followup_task",
        "send_message",
        "interrupt_agent",
    ):
        if normalized.endswith(operation):
            return operation
    if normalized == "agent" and ("agent_type" in tool_input or "message" in tool_input):
        return "spawn_agent"
    return None


def empty_coordination_metrics(
    *, observability: str = "unknown", source: str = "not_observed"
) -> dict[str, Any]:
    return {
        "version": COORDINATION_CLASSIFIER_VERSION,
        "observability": observability,
        "source": source,
        "operation_counts": {operation: 0 for operation in COORDINATION_OPERATIONS},
        "operation_observability": {
            operation: "not_observed" for operation in COORDINATION_OPERATIONS
        },
        "wait_outcomes": {outcome: 0 for outcome in WAIT_OUTCOMES},
        "parent_message_phase_counts": {
            "commentary": 0,
            "final_answer": 0,
            "unknown": 0,
        },
        "parent_message_observability": "not_observed",
        "child_handback_counts": {
            "message": 0,
            "final_answer": 0,
            "unknown": 0,
        },
        "child_handback_observability": "not_observed",
        "requested_wait_ms": None,
        "observed_wait_ms": None,
        "requested_wait_ms_observability": "not_observed",
        "observed_wait_ms_observability": "not_observed",
        "max_consecutive_timeout_without_agent_update": 0,
        "timeout_without_agent_update_count": 0,
        # No native event currently proves these transitions.  In particular,
        # a completed PostToolUse is not an applied steering acknowledgement.
        "commitment": "not_observed",
        "ready_transition": "not_observed",
        "steering_applied": "not_observed",
        "native_live_status": "not_observed",
    }


def _duration_ms(value: Any) -> int | None:
    return record_common.duration_ms(value, floor_before_bound=True)


def _wait_duration(value: Any, keys: tuple[str, ...]) -> int | None:
    return record_common.duration_from(value, keys, floor_before_bound=True)


def _inc_metric(metrics: dict[str, Any], operation: str) -> None:
    counts = metrics.setdefault("operation_counts", {})
    counts[operation] = int(counts.get(operation, 0) or 0) + 1
    metrics.setdefault("operation_observability", {})[operation] = "observed"


def _merge_wait_metric_total(metrics: dict[str, Any], field: str, value: int | None) -> None:
    if value is None:
        return
    current = metrics.get(field)
    metrics[field] = int(current or 0) + value
    metrics[f"{field}_observability"] = "observed"


def coordination_metrics_from_hook_events(
    events: list[dict[str, Any]],
    suffix_metrics: dict[str, Any] | None = None,
    *,
    suffix_observed: bool = False,
    legacy: bool = False,
) -> dict[str, Any]:
    """Combine sanitized hook occurrences with optional parent-tail metrics."""
    metrics = empty_coordination_metrics(
        observability="legacy" if legacy else "observed" if suffix_observed else "unknown",
        source="transcript-replay" if legacy else "parent-suffix" if suffix_observed else "hook-journal",
    )
    if isinstance(suffix_metrics, dict):
        for key in (
            "observability",
            "operation_counts",
            "wait_outcomes",
            "parent_message_phase_counts",
            "parent_message_observability",
            "child_handback_counts",
            "child_handback_observability",
            "requested_wait_ms",
            "observed_wait_ms",
            "requested_wait_ms_observability",
            "observed_wait_ms_observability",
            "max_consecutive_timeout_without_agent_update",
            "timeout_without_agent_update_count",
        ):
            if key in suffix_metrics:
                value = suffix_metrics[key]
                if isinstance(value, dict):
                    metrics[key].update(value)
                else:
                    metrics[key] = value
        for key in (
            "commitment",
            "ready_transition",
            "steering_applied",
            "native_live_status",
        ):
            if suffix_metrics.get(key) not in {None, "not_observed"}:
                metrics[key] = suffix_metrics[key]

    # A tool call is one occurrence.  Prefer the pre-hook, but accept a
    # post-only event when a pre-hook was omitted.  UUIDs provide the only
    # reliable de-duplication key; body text is never inspected or stored.
    seen_ids: set[str] = set()
    events_by_id: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        operation = event.get("operation")
        if operation not in COORDINATION_OPERATIONS:
            continue
        tool_id = event.get("tool_use_id")
        if isinstance(tool_id, str) and tool_id:
            events_by_id.setdefault(tool_id, []).append(event)
        elif event.get("kind") == "tool_pre":
            _inc_metric(metrics, operation)
    for tool_id, grouped in events_by_id.items():
        preferred = next(
            (event for event in grouped if event.get("kind") == "tool_pre"),
            grouped[0],
        )
        if tool_id in seen_ids:
            continue
        seen_ids.add(tool_id)
        operation = preferred.get("operation")
        if operation in COORDINATION_OPERATIONS:
            # Parent-suffix state already contains this call when the hook
            # event arrived late; do not count it a second time.
            suffix_ids = suffix_metrics.get("call_ids", {}) if isinstance(suffix_metrics, dict) else {}
            if tool_id not in suffix_ids:
                _inc_metric(metrics, operation)
    if not suffix_observed and not legacy:
        metrics["operation_observability"] = {
            operation: "not_observed" for operation in COORDINATION_OPERATIONS
        }
        metrics["operation_observability"].update(
            {
                operation: "observed"
                for operation in ("spawn_agent", "followup_task", "send_message", "interrupt_agent")
                if metrics["operation_counts"].get(operation, 0)
            }
        )
    else:
        metrics["operation_observability"] = {
            operation: "legacy" if legacy else "observed"
            for operation in COORDINATION_OPERATIONS
        }
    return metrics


def sanitize_tool_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = bounded(payload.get("tool_name"), 160)
    tool_input = payload.get("tool_input")
    if not tool_name or not isinstance(tool_input, dict):
        return None
    operation = normalize_operation(tool_name, tool_input)
    if operation is None:
        return None
    transcript_value = payload.get("transcript_path")
    transcript_path = (
        Path(transcript_value).expanduser().resolve()
        if isinstance(transcript_value, str) and transcript_value
        else None
    )
    transcript_offset = None
    if transcript_path is not None and transcript_path.is_file():
        transcript_offset = transcript_path.stat().st_size
    event: dict[str, Any] = {
        "kind": "tool_post" if payload.get("hook_event_name") == "PostToolUse" else "tool_pre",
        "observed_at": utc_now(),
        "tool_use_id": bounded(payload.get("tool_use_id"), 200),
        "operation": operation,
        "tool_name": tool_name,
        "requested_role": bounded(tool_input.get("agent_type"), 80),
        "requested_model": bounded(tool_input.get("model"), 160),
        "requested_reasoning_effort": bounded(tool_input.get("reasoning_effort"), 80),
        "requested_service_tier": bounded(
            tool_input.get("service_tier") or tool_input.get("serviceTier"), 80
        ),
        "task_name": bounded(tool_input.get("task_name"), 160),
        "target_agent_id": bounded(
            tool_input.get("target") or tool_input.get("id"), 160
        ),
        "cwd": bounded(payload.get("cwd"), 1000),
        "parent_transcript_path": bounded(transcript_path, 1200),
        "parent_transcript_offset": transcript_offset,
    }
    if operation == "spawn_agent":
        requested_fork_turns, fork_observability = sanitize_requested_fork_turns(
            tool_input
        )
        event["requested_fork_turns"] = requested_fork_turns
        event["requested_fork_turns_observability"] = fork_observability
    if event["kind"] == "tool_post":
        response = payload.get("tool_response")
        agent_id = extract_agent_id(response)
        child_ref = extract_child_ref(response)
        error_code = classify_tool_error(response)
        self_binding_rejected = False
        if (
            operation == "spawn_agent"
            and agent_id
            and agent_id == bounded(payload.get("session_id"), 160)
        ):
            # A parent session id is not a child identity.  Preserve the
            # failed attempt, but never let it become a child binding.
            agent_id = None
            error_code = "invalid-dispatch"
            self_binding_rejected = True
        # The native interrupt operation reports the target's resulting state
        # as ``interrupted`` when the interrupt itself completed.  That state
        # is not a failed tool call and must not become a persisted error code.
        response_status = (
            response.get("status") if isinstance(response, dict) else None
        )
        if isinstance(response, str):
            try:
                parsed_response = json.loads(response)
            except json.JSONDecodeError:
                parsed_response = None
            if isinstance(parsed_response, dict):
                response_status = parsed_response.get("status")
        if (
            operation == "interrupt_agent"
            and response_status == "interrupted"
        ):
            error_code = None
        if operation == "spawn_agent" and not agent_id and not child_ref and not error_code:
            error_code = "unknown"
        event.update(
            {
                "agent_id": agent_id,
                "child_ref": child_ref,
                "nickname": extract_nickname(response),
                "self_binding_rejected": self_binding_rejected,
                "outcome": (
                    "rejected"
                    if error_code == "unknown-model"
                    else "errored"
                    if error_code
                    else "started"
                    if operation == "spawn_agent" and (agent_id or child_ref)
                    else "completed"
                ),
                "error_code": error_code,
            }
        )
    return event


def require_identity(payload: dict[str, Any]) -> tuple[str, str]:
    parent_thread_id = bounded(payload.get("session_id"), 160)
    turn_id = bounded(payload.get("turn_id"), 160)
    if not parent_thread_id or not turn_id:
        raise ValueError("hook event lacks session_id or turn_id")
    return parent_thread_id, turn_id


def ingest_hook(payload: dict[str, Any], root: Path) -> None:
    event_name = payload.get("hook_event_name")
    if event_name in {"PreToolUse", "PostToolUse"}:
        event = sanitize_tool_event(payload)
        if event is None:
            return
        parent_thread_id, turn_id = require_identity(payload)
        append_event(root, parent_thread_id, turn_id, event)
        register_tool_binding(root, parent_thread_id, turn_id, event)
        return

    if event_name == "SubagentStart":
        parent_thread_id, hook_turn_id = require_identity(payload)
        agent_id = bounded(payload.get("agent_id"), 160)
        if not agent_id:
            raise ValueError("SubagentStart lacks agent_id")
        if agent_id == parent_thread_id:
            raise ValueError("SubagentStart agent_id equals parent session_id")
        observed_at = utc_now()
        role = bounded(payload.get("agent_type"), 80)
        expected_runtime = agent_policy.runtime_expectation(
            AGENT_POLICY, role, observed_at
        )
        if expected_runtime is not None:
            expected_runtime = {
                **expected_runtime,
                "source": "agent-team-policy-at-subagent-start",
            }
        event = {
            "kind": "subagent_start",
            "observed_at": observed_at,
            "agent_id": agent_id,
            "subagent_turn_id": hook_turn_id,
            "role": role,
            "hook_model": bounded(payload.get("model"), 160),
            "hook_service_tier": bounded(
                payload.get("service_tier") or payload.get("serviceTier"), 80
            ),
            "permission_mode": bounded(payload.get("permission_mode"), 80),
            "expected_runtime": expected_runtime,
            "cwd": bounded(payload.get("cwd"), 500),
        }
        dirs = state_dirs(root)
        routing_lock = agent_routing_lock_path(
            dirs, parent_thread_id, agent_id
        )
        with locked(routing_lock):
            explicit_parent_turn = bounded(payload.get("parent_turn_id"), 160)
            parent_turn_id = (
                explicit_parent_turn
                or bound_child_turn(root, parent_thread_id, hook_turn_id)
                or bound_parent_turn(root, agent_id, parent_thread_id)
            )
            if parent_turn_id:
                flush_pending_lifecycle(
                    root,
                    parent_thread_id,
                    parent_turn_id,
                    agent_id,
                    [event],
                )
            else:
                store_pending_lifecycle(root, parent_thread_id, agent_id, event)
        return

    if event_name == "SubagentStop":
        parent_thread_id, hook_turn_id = require_identity(payload)
        agent_id = bounded(payload.get("agent_id"), 160)
        if not agent_id:
            raise ValueError("SubagentStop lacks agent_id")
        if agent_id == parent_thread_id:
            raise ValueError("SubagentStop agent_id equals parent session_id")
        transcript_value = payload.get("agent_transcript_path")
        metrics = (
            harvest_transcript(root, Path(transcript_value))
            if isinstance(transcript_value, str) and transcript_value
            else None
        )
        last_message = payload.get("last_assistant_message")
        event = {
            "kind": "subagent_stop",
            "observed_at": utc_now(),
            "agent_id": agent_id,
            "subagent_turn_id": hook_turn_id,
            "role": bounded(payload.get("agent_type"), 80),
            "hook_model": bounded(payload.get("model"), 160),
            "hook_service_tier": bounded(
                payload.get("service_tier") or payload.get("serviceTier"), 80
            ),
            "transcript_path": bounded(transcript_value, 1000),
            "last_assistant_message_chars": (
                len(last_message) if isinstance(last_message, str) else 0
            ),
            "metrics": metrics,
        }
        dirs = state_dirs(root)
        routing_lock = agent_routing_lock_path(
            dirs, parent_thread_id, agent_id
        )
        with locked(routing_lock):
            explicit_parent_turn = bounded(payload.get("parent_turn_id"), 160)
            parent_turn_id = (
                explicit_parent_turn
                or bound_child_turn(root, parent_thread_id, hook_turn_id)
                or bound_parent_turn(root, agent_id, parent_thread_id)
            )
            if parent_turn_id is None and isinstance(metrics, dict):
                parent_turn_id = parent_turn_from_path_binding(
                    root,
                    parent_thread_id,
                    metrics.get("agent_path")
                    if isinstance(metrics.get("agent_path"), str)
                    else None,
                )
            if parent_turn_id:
                flush_pending_lifecycle(
                    root,
                    parent_thread_id,
                    parent_turn_id,
                    agent_id,
                    [event],
                )
            else:
                store_pending_lifecycle(root, parent_thread_id, agent_id, event)


def timestamp_from_millis(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def needs_parent_recovery(events: list[dict[str, Any]]) -> bool:
    pre_ids = {
        str(event.get("tool_use_id"))
        for event in events
        if event.get("kind") == "tool_pre"
        and event.get("operation") == "spawn_agent"
        and event.get("tool_use_id")
    }
    posts = {
        str(event.get("tool_use_id")): event
        for event in events
        if event.get("kind") == "tool_post"
        and event.get("operation") == "spawn_agent"
        and event.get("tool_use_id")
    }
    if pre_ids - posts.keys():
        return True

    started_posts = [
        event for event in posts.values() if event.get("outcome") == "started"
    ]
    direct_agent_ids = {
        event.get("agent_id") for event in started_posts if event.get("agent_id")
    }
    unresolved_path_counts = Counter(
        event.get("child_ref")
        for event in started_posts
        if not event.get("agent_id") and event.get("child_ref")
    )
    stop_agents_by_path: dict[str, set[str]] = {}
    for event in events:
        if event.get("kind") != "subagent_stop":
            continue
        metrics = event.get("metrics")
        if not isinstance(metrics, dict):
            continue
        agent_path = metrics.get("agent_path")
        agent_id = event.get("agent_id") or metrics.get("agent_id")
        if isinstance(agent_path, str) and agent_path and isinstance(agent_id, str) and agent_id:
            stop_agents_by_path.setdefault(agent_path, set()).add(agent_id)

    claimed_agent_ids = set(direct_agent_ids)
    for event in started_posts:
        if event.get("agent_id"):
            continue
        child_ref = event.get("child_ref")
        if not isinstance(child_ref, str) or not child_ref:
            return True
        if unresolved_path_counts[child_ref] != 1:
            return True
        candidates = stop_agents_by_path.get(child_ref, set()) - claimed_agent_ids
        if len(candidates) != 1:
            return True
        claimed_agent_ids.update(candidates)

    unmatched_starts = {
        event.get("agent_id")
        for event in events
        if event.get("kind") == "subagent_start" and event.get("agent_id")
    } - claimed_agent_ids
    return bool(unmatched_starts)


def empty_parent_cursor(path: Path, start_offset: int) -> dict[str, Any]:
    return {
        "transcript_path": str(path.resolve()),
        "start_offset": start_offset,
        "offset": start_offset,
        "file_size": start_offset,
        "passes": 0,
        "invalid_line_count": 0,
        "closed": False,
        "recovery_window_closed": False,
        "terminal_seen": False,
        "terminal_turn_id": None,
        "tool_results": {},
        "activities": {},
        "coordination_metrics": empty_coordination_metrics(
            observability="observed", source="parent-suffix"
        ),
        # Sanitized call-id -> operation/request metadata is bounded to the
        # current parent turn and contains no arguments or tool output.
        "coordination_calls": {},
    }


def parent_cursor_state(
    root: Path,
    parent_thread_id: str,
    turn_id: str,
    *,
    create: bool = True,
) -> dict[str, Any] | None:
    dirs = state_dirs(root, create=create)
    path = parent_cursor_path(dirs, parent_thread_id, turn_id)
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def parent_cursor_is_open(
    root: Path,
    parent_thread_id: str,
    turn_id: str,
    *,
    create: bool = True,
) -> bool:
    """Return whether a prior parent-tail cursor may still accept late evidence."""
    state = parent_cursor_state(
        root, parent_thread_id, turn_id, create=create
    )
    if state is None:
        return False
    if "recovery_window_closed" in state:
        return not bool(state.get("recovery_window_closed"))
    # Legacy cursors used `closed=True` after the first terminal event. They
    # had no later-turn boundary state, so reopen once for same-turn recovery.
    return True


def parent_cursor_transcript(
    state: dict[str, Any] | None,
) -> Path | None:
    if not isinstance(state, dict):
        return None
    value = state.get("transcript_path")
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve()


def _coordination_call_id(payload: dict[str, Any], line_offset: int) -> str:
    call_id = payload.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else f"offset:{line_offset}"


def _parent_message_phase(
    payload: dict[str, Any], turn_id: str | None
) -> str | None:
    """Return a phase only for an identity-bound parent assistant message."""
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return None
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    metadata_turn_id = (
        metadata.get("turn_id")
        if isinstance(metadata, dict) and isinstance(metadata.get("turn_id"), str)
        else None
    )
    if not isinstance(metadata_turn_id, str) or turn_id is None or metadata_turn_id != turn_id:
        return None
    phase = payload.get("phase")
    return phase if phase in {"commentary", "final_answer"} else "unknown"


def consume_parent_coordination_event(
    state: dict[str, Any],
    event: dict[str, Any],
    known_call_ids: set[str],
    line_offset: int,
    turn_id: str | None = None,
) -> None:
    """Collect strict coordination counters from one parent suffix event."""
    metrics = state.setdefault(
        "coordination_metrics",
        empty_coordination_metrics(observability="observed", source="parent-suffix"),
    )
    calls = state.setdefault("coordination_calls", {})
    payload = payload_dict(event)
    event_type = event.get("type")
    event_turn_id = payload.get("turn_id")
    if not isinstance(event_turn_id, str):
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        event_turn_id = (
            metadata.get("turn_id")
            if isinstance(metadata, dict)
            else None
        )
    if turn_id and isinstance(event_turn_id, str) and event_turn_id != turn_id:
        return
    if event_type == "response_item" and payload.get("type") == "message":
        phase = _parent_message_phase(payload, turn_id)
        if phase is not None:
            counts = metrics.setdefault("parent_message_phase_counts", {})
            counts[phase] = int(counts.get(phase, 0) or 0) + 1
            metrics["parent_message_observability"] = "observed"
        return
    if event_type == "response_item" and payload.get("type") == "agent_message":
        author = payload.get("author")
        recipient = payload.get("recipient")
        if (
            not isinstance(author, str)
            or not author.startswith("/root/")
            or recipient != "/root"
            or not isinstance(event_turn_id, str)
            or (turn_id is not None and event_turn_id != turn_id)
        ):
            return
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
        counts = metrics.setdefault("child_handback_counts", {})
        counts[bucket] = int(counts.get(bucket, 0) or 0) + 1
        metrics["child_handback_observability"] = "observed"
        return
    if event_type == "response_item" and payload.get("type") in TOOL_CALL_TYPES:
        operation = normalize_coordination_operation(payload.get("name"))
        if operation is None:
            return
        call_id = _coordination_call_id(payload, line_offset)
        if call_id in known_call_ids or call_id in calls:
            return
        args = payload.get("arguments")
        parsed = args if isinstance(args, dict) else {}
        if isinstance(args, str):
            try:
                decoded = json.loads(args)
                parsed = decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                parsed = {}
        request_ms = _wait_duration(
            parsed,
            ("timeout_ms", "wait_ms", "requested_wait_ms"),
        ) if operation == "wait_agent" else None
        if len(calls) < COORDINATION_CALL_CACHE_LIMIT:
            calls[call_id] = {
                "operation": operation,
                "requested_wait_ms": request_ms,
                "outcome_recorded": False,
            }
        else:
            # Keep memory bounded even for pathological turns.  The call is
            # still counted, but its later output is explicitly unjoinable.
            metrics["observability"] = "unknown"
        call_ids = metrics.setdefault("call_ids", {})
        if len(call_ids) < COORDINATION_CALL_CACHE_LIMIT:
            call_ids[call_id] = operation
        _inc_metric(metrics, operation)
        if operation == "wait_agent":
            _merge_wait_metric_total(metrics, "requested_wait_ms", request_ms)
        return

    if event_type == "response_item" and payload.get("type") in TOOL_OUTPUT_TYPES:
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return
        call = calls.get(call_id)
        if not isinstance(call, dict) or call.get("operation") != "wait_agent":
            return
        if call.get("outcome_recorded"):
            return
        output = payload.get("output")
        outcome = classify_wait_outcome(output)
        metrics.setdefault("wait_outcomes", {})[outcome] = (
            int(metrics["wait_outcomes"].get(outcome, 0) or 0) + 1
        )
        observed_ms = _wait_duration(
            output,
            ("observed_wait_ms", "waited_ms", "duration_ms", "elapsed_ms"),
        )
        _merge_wait_metric_total(metrics, "observed_wait_ms", observed_ms)
        if outcome == "timeout":
            streak = int(metrics.get("_timeout_streak", 0) or 0) + 1
            metrics["_timeout_streak"] = streak
            metrics["max_consecutive_timeout_without_agent_update"] = max(
                int(metrics.get("max_consecutive_timeout_without_agent_update", 0) or 0),
                streak,
            )
            metrics["timeout_without_agent_update_count"] = int(
                metrics.get("timeout_without_agent_update_count", 0) or 0
            ) + 1
        else:
            metrics["_timeout_streak"] = 0
        call["outcome_recorded"] = True
        return

    # Native sub-agent activity is the only currently exposed evidence that a
    # wait observed an update.  It resets the no-update timeout run.
    if event_type == "event_msg" and payload.get("type") == "sub_agent_activity":
        metrics["_timeout_streak"] = 0


def consume_parent_recovery_event(
    state: dict[str, Any],
    event: dict[str, Any],
    spawn_call_ids: set[str],
    known_coordination_call_ids: set[str],
    turn_id: str,
    line_offset: int,
) -> bool:
    """Retain only sanitized spawn results and lifecycle identity from a parent suffix."""
    payload = payload_dict(event)
    timestamp = event.get("timestamp") if isinstance(event.get("timestamp"), str) else None

    consume_parent_coordination_event(
        state, event, known_coordination_call_ids, line_offset, turn_id
    )

    metadata = payload.get("internal_chat_message_metadata_passthrough")
    metadata_turn_id = (
        metadata.get("turn_id")
        if isinstance(metadata, dict) and isinstance(metadata.get("turn_id"), str)
        else None
    )
    event_turn_id = payload.get("turn_id")
    if not isinstance(event_turn_id, str):
        event_turn_id = metadata_turn_id
    terminal_seen = bool(state.get("terminal_seen"))

    # A task_started/explicitly tagged later turn closes the recovery window.
    # The terminal event itself does not: Codex can append decisive spawn or
    # sub_agent_activity evidence after task_complete in the same turn.
    if terminal_seen:
        if event_turn_id is not None and event_turn_id != turn_id:
            state["recovery_window_closed"] = True
            state["closed"] = True
            return True
        if event.get("type") == "event_msg" and payload.get("type") == "task_started":
            state["recovery_window_closed"] = True
            state["closed"] = True
            return True

    if event.get("type") == "response_item" and payload.get("type") in TOOL_OUTPUT_TYPES:
        call_id = payload.get("call_id")
        if isinstance(call_id, str) and call_id in spawn_call_ids:
            response = payload.get("output")
            error_code = classify_tool_error(response)
            agent_id = extract_agent_id(response)
            child_ref = extract_child_ref(response)
            if not agent_id and not child_ref and not error_code:
                error_code = "unknown"
            state["tool_results"][call_id] = {
                "observed_at": timestamp,
                "agent_id": agent_id,
                "child_ref": child_ref,
                "nickname": extract_nickname(response),
                "outcome": (
                    "rejected"
                    if error_code == "unknown-model"
                    else "errored"
                    if error_code
                    else "started"
                    if agent_id or child_ref
                    else "completed"
                ),
                "error_code": error_code,
            }
        return False

    if event.get("type") == "event_msg" and payload.get("type") == "sub_agent_activity":
        agent_id = payload.get("agent_thread_id")
        if isinstance(agent_id, str) and UUID_PATTERN.fullmatch(agent_id):
            activity = state["activities"].setdefault(agent_id, {})
            kind = bounded(payload.get("kind"), 80)
            activity.update(
                {
                    "agent_id": agent_id,
                    "agent_path": bounded(payload.get("agent_path"), 300),
                    "tool_use_id": bounded(payload.get("event_id"), 200),
                    "kind": kind,
                }
            )
            observed_at = timestamp_from_millis(payload.get("occurred_at_ms")) or timestamp
            if kind == "started":
                activity["started_at"] = observed_at
            elif kind:
                activity["ended_at"] = observed_at
        return False

    if event.get("type") == "event_msg" and payload.get("type") in {
        "task_complete",
        "task_failed",
        "turn_failed",
        "turn_aborted",
    }:
        if event_turn_id not in {None, turn_id}:
            if terminal_seen:
                state["recovery_window_closed"] = True
                state["closed"] = True
                return True
            return False
        state["terminal_seen"] = True
        state["terminal_turn_id"] = event_turn_id or turn_id
    return False


def recover_parent_tail(
    root: Path,
    parent_thread_id: str,
    turn_id: str,
    events: list[dict[str, Any]],
    transcript_override: Path | None = None,
    *,
    persist: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill known runtime hook gaps by streaming only the parent suffix after first Agent use."""
    enriched = [dict(event) for event in events]
    default_stats = {
        "transcript_path": None,
        "start_offset": None,
        "end_offset": None,
        "bytes_processed": 0,
        "new_bytes_processed": 0,
        "passes": 0,
        "legacy_full_scan": False,
        "used": False,
        "coordination_metrics": None,
    }
    path_values = [
        event.get("parent_transcript_path")
        for event in events
        if isinstance(event.get("parent_transcript_path"), str)
        and event.get("parent_transcript_path")
    ]
    resolved_override = transcript_override.expanduser().resolve() if transcript_override else None
    cursor_state = parent_cursor_state(
        root, parent_thread_id, turn_id, create=persist
    )
    cursor_transcript = parent_cursor_transcript(cursor_state)
    transcript = (
        Path(path_values[0]).expanduser().resolve()
        if path_values
        else resolved_override or cursor_transcript
    )
    should_recover = (
        transcript_override is not None
        or needs_parent_recovery(events)
        or parent_cursor_is_open(
            root, parent_thread_id, turn_id, create=persist
        )
        or (
            isinstance(cursor_state, dict)
            and (
                not isinstance(cursor_state.get("coordination_metrics"), dict)
                or cursor_state.get("coordination_metrics", {}).get("version")
                != COORDINATION_CLASSIFIER_VERSION
            )
        )
        # Polling is deliberately excluded from per-tool hooks.  When the
        # native pre-hook captured a parent path/offset, one closeout suffix
        # scan is the bounded source for those post-hoc counters.
        or bool(path_values)
    )
    if not should_recover:
        return enriched, default_stats
    if transcript is None or not transcript.is_file():
        return enriched, default_stats
    offsets = [
        int(event["parent_transcript_offset"])
        for event in events
        if event.get("parent_transcript_path") == str(transcript)
        and isinstance(event.get("parent_transcript_offset"), int)
        and event["parent_transcript_offset"] >= 0
    ]
    if offsets:
        start_offset = min(offsets)
        legacy_full_scan = False
    elif (
        cursor_state
        and cursor_transcript is not None
        and transcript == cursor_transcript
        and isinstance(cursor_state.get("start_offset"), int)
        and cursor_state["start_offset"] >= 0
    ):
        start_offset = cursor_state["start_offset"]
        legacy_full_scan = False
    else:
        start_offset = 0
        legacy_full_scan = True
    file_size = transcript.stat().st_size
    start_offset = min(start_offset, file_size)
    spawn_call_ids = {
        str(event.get("tool_use_id"))
        for event in events
        if event.get("operation") == "spawn_agent" and event.get("tool_use_id")
    }
    known_coordination_call_ids = {
        str(event.get("tool_use_id"))
        for event in events
        if event.get("operation") in COORDINATION_OPERATIONS
        and event.get("tool_use_id")
    }

    dirs = state_dirs(root, create=persist)
    cursor = parent_cursor_path(dirs, parent_thread_id, turn_id)
    lock = dirs["locks"] / f"parent-cursor-{cursor.stem}.lock"
    with (locked(lock) if persist else nullcontext()):
        state = read_json(cursor) if cursor.is_file() else empty_parent_cursor(
            transcript, start_offset
        )
        if (
            state.get("transcript_path") != str(transcript)
            or int(state.get("offset", 0)) > file_size
            or int(state.get("start_offset", start_offset)) > start_offset
        ):
            state = empty_parent_cursor(transcript, start_offset)
        # Cursors written by the previous collector permanently closed after
        # the first terminal event. Reopen those cursors once so a late same-
        # turn event can be consumed under the new state machine.
        if "recovery_window_closed" not in state:
            legacy_closed = bool(state.get("closed"))
            state["recovery_window_closed"] = False
            state["closed"] = False
            if legacy_closed:
                state["terminal_seen"] = True
                state["terminal_turn_id"] = turn_id
        coordination_rebuild_start: int | None = None
        cached_coordination = state.get("coordination_metrics")
        if (
            not isinstance(cached_coordination, dict)
            or cached_coordination.get("version") != COORDINATION_CLASSIFIER_VERSION
        ):
            # Reclassify only the already-captured parent suffix.  Lifecycle
            # cursors and bindings remain intact; a stale classifier cache is
            # never silently treated as current evidence.
            coordination_rebuild_start = int(
                state.get("start_offset", start_offset) or start_offset
            )
            state["coordination_metrics"] = empty_coordination_metrics(
                observability="observed", source="parent-suffix"
            )
            state["coordination_calls"] = {}
        scan_start = int(state.get("offset", start_offset))
        if coordination_rebuild_start is not None:
            rebuild_end = min(file_size, max(scan_start, coordination_rebuild_start))
            if coordination_rebuild_start < rebuild_end:
                with transcript.open("rb") as handle:
                    handle.seek(coordination_rebuild_start)
                    while handle.tell() < rebuild_end:
                        line_start = handle.tell()
                        raw = handle.readline()
                        if not raw:
                            break
                        if handle.tell() > rebuild_end:
                            break
                        try:
                            event = json.loads(raw)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if isinstance(event, dict):
                            consume_parent_coordination_event(
                                state,
                                event,
                                known_coordination_call_ids,
                                line_start,
                                turn_id,
                            )
        persisted = False
        if not state.get("recovery_window_closed") and scan_start < file_size:
            with transcript.open("rb") as handle:
                handle.seek(scan_start)
                while True:
                    line_start = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    try:
                        event = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if not raw.endswith(b"\n") and handle.tell() == file_size:
                            handle.seek(line_start)
                            break
                        state["invalid_line_count"] = int(
                            state.get("invalid_line_count", 0)
                        ) + 1
                        state["offset"] = handle.tell()
                        continue
                    closed = False
                    if isinstance(event, dict):
                        closed = consume_parent_recovery_event(
                            state,
                            event,
                            spawn_call_ids,
                            known_coordination_call_ids,
                            turn_id,
                            line_start,
                        )
                    state["offset"] = handle.tell()
                    if closed:
                        break
            state["passes"] = int(state.get("passes", 0)) + 1
            state["file_size"] = file_size
            if persist:
                atomic_json(cursor, state)
                persisted = True
        # Classifier invalidation is itself durable evidence.  Persist the
        # rebuilt v4 metrics even when a legacy closed cursor has no new
        # lifecycle suffix to scan; the next closeout must remain O(1).
        if persist and coordination_rebuild_start is not None and not persisted:
            atomic_json(cursor, state)
        scan_end = int(state.get("offset", scan_start))

    posts = {
        str(event.get("tool_use_id")): event
        for event in enriched
        if event.get("kind") == "tool_post"
        and event.get("operation") == "spawn_agent"
        and event.get("tool_use_id")
    }
    pres = {
        str(event.get("tool_use_id")): event
        for event in enriched
        if event.get("kind") == "tool_pre"
        and event.get("operation") == "spawn_agent"
        and event.get("tool_use_id")
    }
    for call_id, result in state.get("tool_results", {}).items():
        post = posts.get(call_id)
        if post is None:
            pre = pres.get(call_id, {})
            post = {
                "kind": "tool_post",
                "observed_at": result.get("observed_at") or pre.get("observed_at"),
                "tool_use_id": call_id,
                "operation": "spawn_agent",
                "tool_name": pre.get("tool_name"),
                "requested_role": pre.get("requested_role"),
                "requested_model": pre.get("requested_model"),
                "requested_reasoning_effort": pre.get("requested_reasoning_effort"),
                "requested_service_tier": pre.get("requested_service_tier"),
                "requested_fork_turns": pre.get("requested_fork_turns"),
                "requested_fork_turns_observability": pre.get(
                    "requested_fork_turns_observability", "not_observed"
                ),
                "task_name": pre.get("task_name"),
                "cwd": pre.get("cwd"),
                "evidence_source": "incremental-parent-tail",
            }
            enriched.append(post)
            posts[call_id] = post
        for key in ("agent_id", "child_ref", "nickname", "outcome", "error_code"):
            if result.get(key) is not None or key in {"outcome", "error_code"}:
                post[key] = result.get(key)

    existing_starts = {
        event.get("agent_id")
        for event in enriched
        if event.get("kind") == "subagent_start" and event.get("agent_id")
    }
    for agent_id, activity in state.get("activities", {}).items():
        if agent_id in existing_starts:
            continue
        pre = pres.get(str(activity.get("tool_use_id")), {})
        enriched.append(
            {
                "kind": "subagent_start",
                "observed_at": activity.get("started_at") or activity.get("ended_at"),
                "agent_id": agent_id,
                "agent_path": activity.get("agent_path"),
                "tool_use_id": activity.get("tool_use_id"),
                "role": pre.get("requested_role"),
                "cwd": pre.get("cwd"),
                "evidence_source": "incremental-parent-tail",
            }
        )
        existing_starts.add(agent_id)
    for call_id, post in posts.items():
        agent_id = post.get("agent_id")
        if not agent_id or agent_id in existing_starts:
            continue
        pre = pres.get(call_id, {})
        enriched.append(
            {
                "kind": "subagent_start",
                "observed_at": post.get("observed_at"),
                "agent_id": agent_id,
                "agent_path": post.get("child_ref"),
                "tool_use_id": call_id,
                "role": pre.get("requested_role") or post.get("requested_role"),
                "cwd": pre.get("cwd") or post.get("cwd"),
                "evidence_source": "Agent-tool-hook",
            }
        )
        existing_starts.add(agent_id)

    return enriched, {
        "transcript_path": str(transcript),
        "start_offset": start_offset,
        "end_offset": scan_end,
        "bytes_processed": max(0, scan_end - start_offset),
        "new_bytes_processed": max(0, scan_end - scan_start),
        "passes": int(state.get("passes", 0)),
        "legacy_full_scan": legacy_full_scan,
        "used": bool(state.get("passes")) or scan_end > start_offset,
        "coordination_metrics": state.get("coordination_metrics"),
    }


def locate_child_transcript(agent_id: str, parent_transcript: str | None) -> Path | None:
    """Locate one child using bounded parent/date evidence only.

    Ordinary closeout must not walk the global sessions tree.  UUIDv7 child
    ids encode a millisecond timestamp; use that to probe the matching date
    directory with one adjacent day on either side for timezone/rollover
    tolerance.  Ambiguous candidates remain unobserved.
    """
    pattern = f"*{agent_id}.jsonl"
    candidate_paths: set[Path] = set()
    parent_path = Path(parent_transcript).expanduser().resolve() if parent_transcript else None
    if parent_path is not None and parent_path.parent.is_dir():
        candidate_paths.update(path.resolve() for path in parent_path.parent.glob(pattern))

    compact = agent_id.replace("-", "")
    date_key = compact[:12] if re.fullmatch(r"[0-9a-fA-F]{12,}", compact) else None
    if date_key is not None:
        try:
            child_time = datetime.fromtimestamp(
                int(date_key, 16) / 1000, timezone.utc
            )
        except (OverflowError, OSError, ValueError):
            child_time = None
    else:
        child_time = None
    sessions_root = Path.home() / ".codex" / "sessions"
    if child_time is not None:
        for day_delta in (-1, 0, 1):
            day = (child_time + timedelta(days=day_delta)).date()
            day_dir = sessions_root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
            if day_dir.is_dir():
                candidate_paths.update(path.resolve() for path in day_dir.glob(pattern))
    if len(candidate_paths) != 1:
        return None
    return next(iter(candidate_paths))


def enrich_child_metrics(
    root: Path,
    parent_thread_id: str,
    events: list[dict[str, Any]],
    parent_stats: dict[str, Any],
    *,
    persist: bool = True,
) -> list[dict[str, Any]]:
    enriched = list(events)
    starts = {
        event.get("agent_id"): event
        for event in enriched
        if event.get("kind") == "subagent_start" and event.get("agent_id")
    }
    stops = {
        event.get("agent_id"): event
        for event in enriched
        if event.get("kind") == "subagent_stop" and event.get("agent_id")
    }
    for agent_id, start in starts.items():
        if not isinstance(agent_id, str):
            continue
        prior_stop = stops.get(agent_id, {})
        transcript_value = prior_stop.get("transcript_path")
        transcript = (
            Path(transcript_value).expanduser().resolve()
            if isinstance(transcript_value, str) and transcript_value
            else locate_child_transcript(
                agent_id, parent_stats.get("transcript_path")
            )
        )
        if transcript is None:
            continue
        metrics = harvest_transcript(root, transcript, persist=persist)
        observed_parent = metrics.get("parent_thread_id")
        if observed_parent and observed_parent != parent_thread_id:
            continue
        enriched.append(
            {
                "kind": "subagent_stop",
                "observed_at": metrics.get("ended_at")
                or prior_stop.get("observed_at")
                or utc_now(),
                "agent_id": agent_id,
                "role": metrics.get("role") or start.get("role"),
                "transcript_path": str(transcript),
                "last_assistant_message_chars": max(
                    int(metrics.get("final_message_chars") or 0),
                    int(prior_stop.get("last_assistant_message_chars") or 0),
                ),
                "metrics": metrics,
                "evidence_source": "discovered-child-session",
            }
        )
    return enriched


def connected_attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pre_events: dict[str, dict[str, Any]] = {}
    post_events: dict[str, dict[str, Any]] = {}
    starts = [event for event in events if event.get("kind") == "subagent_start"]
    for event in events:
        if event.get("operation") != "spawn_agent":
            continue
        call_id = event.get("tool_use_id") or f"missing-{len(pre_events) + len(post_events)}"
        if event.get("kind") == "tool_pre":
            pre_events[str(call_id)] = event
        elif event.get("kind") == "tool_post":
            post_events[str(call_id)] = event

    direct_agent_id_counts = Counter(
        post.get("agent_id")
        for post in post_events.values()
        if isinstance(post.get("agent_id"), str) and post.get("agent_id")
    )
    attempts: list[dict[str, Any]] = []
    for call_id in dict.fromkeys([*pre_events, *post_events]):
        pre = pre_events.get(call_id, {})
        post = post_events.get(call_id, {})
        direct_agent_id = post.get("agent_id")
        duplicate_direct_id = (
            isinstance(direct_agent_id, str)
            and direct_agent_id_counts[direct_agent_id] > 1
        )
        attempts.append(
            {
                "attempted_at": pre.get("observed_at") or post.get("observed_at"),
                "call_id": None if str(call_id).startswith("missing-") else call_id,
                "task_name": pre.get("task_name") or post.get("task_name"),
                "requested_role": pre.get("requested_role") or post.get("requested_role"),
                "requested_model": pre.get("requested_model") or post.get("requested_model"),
                "requested_reasoning_effort": pre.get("requested_reasoning_effort")
                or post.get("requested_reasoning_effort"),
                "requested_service_tier": pre.get("requested_service_tier")
                or post.get("requested_service_tier"),
                "requested_fork_turns": (
                    pre.get("requested_fork_turns")
                    if "requested_fork_turns" in pre
                    else post.get("requested_fork_turns")
                ),
                "requested_fork_turns_observability": (
                    pre.get("requested_fork_turns_observability")
                    or post.get("requested_fork_turns_observability")
                    or "not_observed"
                ),
                "outcome": post.get("outcome", "unknown"),
                "error": post.get("error_code"),
                "self_binding_rejected": bool(post.get("self_binding_rejected")),
                "child_ref": post.get("agent_id") or post.get("child_ref"),
                "agent_id": None if duplicate_direct_id else direct_agent_id,
                "binding": (
                    "ambiguous"
                    if duplicate_direct_id
                    else "agent-id"
                    if direct_agent_id
                    else "unbound"
                ),
                **(
                    {"binding_candidates": [direct_agent_id]}
                    if duplicate_direct_id
                    else {}
                ),
                "evidence_source": post.get("evidence_source") or "codex-hook",
            }
        )

    # A SubagentStart hook normally carries only the child id.  The native
    # child transcript, however, carries the stable agent_path, while the
    # spawn result carries that same path as child_ref.  Build an explicit
    # identity index from those facts instead of assigning starts to attempts
    # by arrival/completion order.  Concurrent children are free to start and
    # stop in any order, so positional matching is not evidence.
    identity_paths: dict[str, set[str]] = {}
    for event in events:
        if event.get("kind") not in {"subagent_start", "subagent_stop"}:
            continue
        agent_id = event.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        paths = identity_paths.setdefault(agent_id, set())
        for value in (
            event.get("agent_path"),
            (event.get("metrics") or {}).get("agent_path")
            if isinstance(event.get("metrics"), dict)
            else None,
        ):
            if isinstance(value, str) and value:
                paths.add(value)

    path_to_agents: dict[str, set[str]] = {}
    for agent_id, paths in identity_paths.items():
        for path in paths:
            path_to_agents.setdefault(path, set()).add(agent_id)

    connected = {attempt.get("agent_id") for attempt in attempts if attempt.get("agent_id")}
    for start in starts:
        agent_id = start.get("agent_id")
        if not agent_id or agent_id in connected:
            continue
        start_tool_id = start.get("tool_use_id")
        matching_attempts = [
            attempt
            for attempt in attempts
            if not attempt.get("agent_id")
            and attempt.get("outcome") in {"unknown", "completed", "started"}
            and (
                attempt.get("child_ref") == agent_id
                or (
                    isinstance(attempt.get("child_ref"), str)
                    and agent_id in path_to_agents.get(attempt.get("child_ref"), set())
                )
                or (
                    isinstance(start_tool_id, str)
                    and attempt.get("call_id") == start_tool_id
                )
            )
        ]
        candidate_ids: set[str] = set()
        for candidate in matching_attempts:
            child_ref = candidate.get("child_ref")
            if child_ref == agent_id:
                candidate_ids.add(agent_id)
            elif isinstance(child_ref, str):
                candidate_ids.update(path_to_agents.get(child_ref, set()))
            if (
                isinstance(start_tool_id, str)
                and candidate.get("call_id") == start_tool_id
            ):
                candidate_ids.add(agent_id)
        if len(matching_attempts) == 1 and candidate_ids <= {agent_id}:
            candidate = matching_attempts[0]
            candidate["agent_id"] = agent_id
            candidate["child_ref"] = candidate.get("child_ref") or agent_id
            candidate["outcome"] = "started"
            candidate["binding"] = (
                "agent-path"
                if isinstance(candidate.get("child_ref"), str)
                and candidate.get("child_ref") in path_to_agents
                else "agent-id"
            )
            connected.add(agent_id)
        elif matching_attempts:
            # Preserve uncertainty rather than silently pairing a child with
            # whichever attempt happened to be most recent.
            for candidate in matching_attempts:
                candidate["binding"] = "ambiguous"
                candidate["binding_candidates"] = sorted(
                    {
                        value
                        for value in (
                            (
                                {candidate.get("child_ref")}
                                if candidate.get("child_ref") in identity_paths
                                else path_to_agents.get(candidate.get("child_ref"), set())
                            )
                            if isinstance(candidate.get("child_ref"), str)
                            else set()
                        )
                        if value
                    }
                )
        else:
            attempts.append(
                {
                    "attempted_at": start.get("observed_at"),
                    "call_id": None,
                    "task_name": None,
                    "requested_role": start.get("role"),
                    "requested_model": None,
                    "requested_reasoning_effort": None,
                    "requested_service_tier": None,
                    "requested_fork_turns": None,
                    "requested_fork_turns_observability": "not_observed",
                    "outcome": "started",
                    "error": None,
                    "child_ref": agent_id,
                    "agent_id": agent_id,
                    "binding": "agent-id",
                    "evidence_source": "SubagentStart-hook",
                }
            )
            connected.add(agent_id)
    return sorted(attempts, key=lambda item: item.get("attempted_at") or "")


def _role_binding_for_agent(
    agent_id: str,
    agent_path: str | None,
    attempts: list[dict[str, Any]],
) -> tuple[str | None, str, list[str]]:
    """Join requested role to a child only through stable identity evidence."""
    matches = [
        attempt
        for attempt in attempts
        if attempt.get("agent_id") == agent_id
        or (
            agent_path
            and attempt.get("child_ref") == agent_path
            and attempt.get("binding") == "agent-path"
        )
    ]
    roles = sorted(
        {
            role
            for attempt in matches
            for role in [attempt.get("requested_role")]
            if isinstance(role, str) and role
        }
    )
    if len(roles) == 1:
        source = "spawn-request" if any(
            attempt.get("requested_role") == roles[0] and attempt.get("agent_id") == agent_id
            for attempt in matches
        ) else "agent-path-join"
        return roles[0], source, roles
    if len(roles) > 1:
        return None, "conflicting-requests", roles
    return None, "not_observed", roles


def runtime_resolution_for_agent(
    role: str | None,
    started_at: str | None,
    attempts: list[dict[str, Any]],
    observed: dict[str, Any],
    expected_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = expected_override or agent_policy.runtime_expectation(
        AGENT_POLICY, role, started_at
    )
    requested = {
        "model": None,
        "reasoning_effort": None,
        "service_tier": None,
        "source": "not_observed",
    }
    if len(attempts) == 1:
        requested = {
            "model": attempts[0].get("requested_model"),
            "reasoning_effort": attempts[0].get("requested_reasoning_effort"),
            "service_tier": attempts[0].get("requested_service_tier"),
            "source": "spawn-request",
        }

    observed_tier = observed.get("service_tier")
    expected_aliases = set(expected.get("service_tier_aliases", [])) if expected else set()

    def match(expected_value: Any, observed_value: Any) -> str:
        if expected_value is None:
            return "expectation-not-resolved"
        if observed_value is None:
            return "not_observed"
        return "match" if expected_value == observed_value else "mismatch"

    tier_match = "expectation-not-resolved"
    if expected is not None:
        tier_match = (
            "not_observed"
            if not isinstance(observed_tier, str) or not observed_tier
            else "match"
            if observed_tier.strip().lower() in expected_aliases
            else "mismatch"
        )
    return {
        "requested": requested,
        "expected": expected,
        "observed": observed,
        "match": {
            "model": match(
                expected.get("model") if expected else None,
                observed.get("model"),
            ),
            "reasoning_effort": match(
                expected.get("reasoning_effort") if expected else None,
                observed.get("reasoning_effort"),
            ),
            "service_tier": tier_match,
        },
    }


def build_agents(
    events: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    summaries: dict[str, str],
    parent_thread_id: str | None = None,
    parent_turn_id: str | None = None,
) -> list[dict[str, Any]]:
    starts: dict[str, dict[str, Any]] = {}
    stops: dict[str, dict[str, Any]] = {}
    for event in events:
        agent_id = event.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        if event.get("kind") == "subagent_start":
            starts[agent_id] = event
        elif event.get("kind") == "subagent_stop":
            stops[agent_id] = event

    task_names = {
        attempt["agent_id"]: attempt.get("task_name")
        for attempt in attempts
        if attempt.get("agent_id")
    }
    agents: list[dict[str, Any]] = []
    for agent_id in dict.fromkeys([*starts, *stops]):
        if parent_thread_id is not None and agent_id == parent_thread_id:
            continue
        start = starts.get(agent_id, {})
        stop = stops.get(agent_id, {})
        metrics = stop.get("metrics") if isinstance(stop.get("metrics"), dict) else {}
        actual_role = metrics.get("actual_role") or metrics.get("role")
        if not isinstance(actual_role, str) or not actual_role:
            actual_role = None
        actual_role_source = (
            "child-session-meta"
            if actual_role and metrics.get("actual_role") is not None
            else "child-session-meta"
            if actual_role
            else "not_observed"
        )
        requested_role, requested_role_source, requested_role_candidates = (
            _role_binding_for_agent(agent_id, metrics.get("agent_path"), attempts)
        )
        started_at = metrics.get("started_at") or start.get("observed_at")
        ended_at = metrics.get("ended_at") or stop.get("observed_at")
        task_name = task_names.get(agent_id)
        if not task_name and metrics.get("agent_path"):
            task_name = str(metrics["agent_path"]).rsplit("/", 1)[-1]
        if not task_name:
            task_name = "agent"
        service_tier = metrics.get("service_tier") or stop.get("hook_service_tier")
        service_tier_source = metrics.get("service_tier_source") or (
            "subagent-stop-hook" if stop.get("hook_service_tier") else "not_observed"
        )
        if not isinstance(service_tier, str) or not service_tier:
            service_tier = None
            service_tier_source = "not_observed"
        metric_validity = metrics.get("metric_validity", "unobserved")
        metric_scope = metrics.get("metric_scope", "unobserved")
        metric_observability = metrics.get("metric_observability", "missing")
        final_chars = max(
            int(metrics.get("final_message_chars") or 0),
            int(stop.get("last_assistant_message_chars") or 0),
        )
        identity_matches = [
            attempt
            for attempt in attempts
            if attempt.get("agent_id") == agent_id
            or (
                metrics.get("agent_path")
                and attempt.get("child_ref") == metrics.get("agent_path")
                and attempt.get("binding") == "agent-path"
            )
        ]
        observed_runtime = {
            "model_provider": metrics.get("model_provider"),
            "model": metrics.get("model")
            or stop.get("hook_model")
            or start.get("hook_model"),
            "reasoning_effort": metrics.get("reasoning_effort"),
            "service_tier": service_tier,
            "service_tier_source": service_tier_source,
            "service_tier_observability": (
                "observed" if service_tier is not None else "not_observed"
            ),
            "multi_agent_version": metrics.get("multi_agent_version"),
            "source": "child-transcript-and-lifecycle-hooks",
        }
        expected_runtime_override = (
            {
                **start["expected_runtime"],
                "captured_at": start.get("observed_at"),
            }
            if isinstance(start.get("expected_runtime"), dict)
            else None
        )
        runtime_resolution = runtime_resolution_for_agent(
            actual_role or requested_role,
            started_at,
            identity_matches,
            observed_runtime,
            expected_runtime_override,
        )
        effective_authority = dict(
            metrics.get("effective_authority")
            if isinstance(metrics.get("effective_authority"), dict)
            else {
                "sandbox_mode": None,
                "permission_profile": None,
                "approval_policy": None,
                "approvals_reviewer": None,
                "workspace_root_count": None,
                "source": "not_observed",
            }
        )
        effective_authority["hook_permission_mode"] = start.get("permission_mode")
        agents.append(
            {
                "agent_id": agent_id,
                "child_session_id": agent_id,
                "parent_thread_id": parent_thread_id,
                "parent_turn_id": parent_turn_id,
                "identity_evidence": {
                    "child_session_id": agent_id,
                    "parent_thread_id": parent_thread_id,
                    "parent_turn_id": parent_turn_id,
                    "agent_path": metrics.get("agent_path"),
                    "binding": identity_matches[0].get("binding", "unbound")
                    if len(identity_matches) == 1
                    else "ambiguous"
                    if identity_matches
                    else "child-session",
                },
                "agent_path": metrics.get("agent_path"),
                "task_name": task_name,
                "nickname": metrics.get("nickname"),
                # `role` remains a legacy alias, but is never filled from the
                # requested role. Missing child metadata stays null/unlabeled.
                "role": actual_role,
                "requested_role": requested_role,
                "requested_role_candidates": requested_role_candidates,
                "requested_role_source": requested_role_source,
                "actual_role": actual_role,
                "actual_role_source": actual_role_source,
                "role_binding_source": actual_role_source,
                "model_provider": metrics.get("model_provider"),
                "model": metrics.get("model") or stop.get("hook_model") or start.get("hook_model"),
                "reasoning_effort": metrics.get("reasoning_effort"),
                "service_tier": service_tier,
                "service_tier_source": service_tier_source,
                "service_tier_observability": (
                    "observed" if service_tier is not None else "not_observed"
                ),
                "multi_agent_version": metrics.get("multi_agent_version"),
                "runtime_resolution": runtime_resolution,
                "runtime_provenance": metrics.get("runtime_provenance")
                if isinstance(metrics.get("runtime_provenance"), dict)
                else {
                    "cli_version": None,
                    "originator": None,
                    "thread_source": None,
                    "multi_agent_version": metrics.get("multi_agent_version"),
                    "source": "not_observed",
                },
                "effective_authority": effective_authority,
                "status": metrics.get("status", "unknown"),
                "turn_count": max(1, int(metrics.get("turn_count") or 1)),
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": metrics.get("duration_ms")
                if metrics.get("duration_ms") is not None
                else elapsed_ms(started_at, ended_at),
                "event_count": int(metrics.get("event_count") or 0),
                "tool_calls": int(metrics.get("tool_calls") or 0),
                "tool_outputs": int(metrics.get("tool_outputs") or 0),
                "nested_agent_calls": int(metrics.get("nested_agent_calls") or 0),
                "token_usage": metrics.get("token_usage"),
                "final_message_chars": final_chars,
                "result_summary": summaries.get(agent_id),
                "error": metrics.get("error"),
                "session_path": metrics.get("session_path") or stop.get("transcript_path"),
                "transcript_bytes_processed": int(
                    metrics.get("transcript_bytes_processed") or 0
                ),
                "collection_passes": int(metrics.get("collection_passes") or 0),
                "metric_scope": metric_scope,
                "metric_validity": metric_validity,
                "metric_observability": metric_observability,
                "metric_boundary_method": metrics.get("metric_boundary_method"),
                "forked": metrics.get("forked")
                if "forked" in metrics
                else None,
                "fork_observed": metrics.get("fork_observed"),
                "fork_observation_source": metrics.get(
                    "fork_observation_source", "not_observed"
                ),
                "forked_from_id": metrics.get("forked_from_id"),
            }
        )
    return agents


def aggregate_outcome(attempts: list[dict[str, Any]], agents: list[dict[str, Any]]) -> str:
    statuses = [agent["status"] for agent in agents]
    if not agents:
        if any(
            attempt.get("outcome") in {"rejected", "errored"}
            for attempt in attempts
        ):
            return "dispatch_failed"
        return "unknown" if attempts else "no_dispatch"
    if statuses and all(status == "completed" for status in statuses):
        return "completed"
    if "completed" in statuses:
        return "partial"
    if "errored" in statuses:
        return "agent_error"
    if "interrupted" in statuses:
        return "interrupted"
    return "unknown"


def task_signature(attempts: list[dict[str, Any]], agents: list[dict[str, Any]]) -> str:
    names = sorted({str(item["task_name"]) for item in attempts if item.get("task_name")})
    if names:
        joined = "+".join(names)
        if len(joined) <= 160:
            return joined
        return f"agent-team-{key_for(joined)[:16]}"
    roles = sorted(
        {str(agent["actual_role"]) for agent in agents if agent.get("actual_role")}
    )
    return "native-agent:" + ("+".join(roles) if roles else "dispatch")


def binding_uncertainty_messages(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> list[str]:
    """Report unresolved successful spawns only once lifecycle state is terminal."""
    rejected_self_bindings = [
        attempt
        for attempt in attempts
        if attempt.get("self_binding_rejected") is True
    ]
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
    if not unresolved and not rejected_self_bindings:
        return []
    if agents:
        if not all(
            agent.get("status") in TERMINAL_AGENT_STATUSES for agent in agents
        ):
            return []
    labels: list[str] = []
    for attempt in rejected_self_bindings:
        label = attempt.get("call_id") or attempt.get("task_name") or "unknown"
        labels.append(f"{label} returned the parent session id as a child binding")
    for attempt in unresolved:
        label = (
            attempt.get("call_id")
            or attempt.get("task_name")
            or attempt.get("child_ref")
            or "unknown"
        )
        binding = attempt.get("binding", "unbound")
        if binding == "ambiguous":
            candidates = attempt.get("binding_candidates") or []
            candidate_text = ", ".join(str(value) for value in candidates) or "none"
            labels.append(f"{label} has ambiguous agent binding (candidates: {candidate_text})")
        else:
            labels.append(f"{label} has no agent binding after a successful spawn")
    return labels


def lifecycle_incomplete_messages(agents: list[dict[str, Any]]) -> list[str]:
    return [
        f"{agent.get('agent_id') or 'unknown'} has nonterminal status "
        f"{agent.get('status') or 'unknown'} without terminal or reclaim evidence"
        for agent in agents
        if agent.get("status") not in TERMINAL_AGENT_STATUSES
    ]


def delivery_failure_messages(agents: list[dict[str, Any]]) -> list[str]:
    """A completed child did work, but its result was not delivered."""
    return [
        f"{agent.get('agent_id') or 'unknown'} completed after tool activity without a final message"
        for agent in agents
        if agent.get("status") == "completed"
        and int(agent.get("tool_calls") or 0) > 0
        and int(agent.get("final_message_chars") or 0) == 0
    ]


def authority_contract_mismatch_messages(
    agents: list[dict[str, Any]],
) -> list[str]:
    messages: list[str] = []
    for agent in agents:
        resolution = agent.get("runtime_resolution")
        expected = resolution.get("expected") if isinstance(resolution, dict) else None
        authority = agent.get("effective_authority")
        if not isinstance(expected, dict) or not isinstance(authority, dict):
            continue
        configured = expected.get("configured_sandbox_mode")
        observed = authority.get("sandbox_mode")
        if configured in {None, "inherit"} or observed is None:
            continue
        if configured != observed:
            messages.append(
                f"{agent.get('agent_id') or 'unknown'} role sandbox expected "
                f"{configured}, observed effective {observed}"
            )
    return messages


def fork_request_observation_mismatch_messages(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]]
) -> list[str]:
    """Return only contradictions proven by both request and child metadata."""
    agents_by_id = {agent.get("agent_id"): agent for agent in agents}
    messages: list[str] = []
    for attempt in attempts:
        requested = attempt.get("requested_fork_turns")
        if not (
            requested in {"none", "all"}
            if isinstance(requested, str)
            else False
        ) and not (
            isinstance(requested, int) and not isinstance(requested, bool) and requested > 0
        ):
            continue
        agent = agents_by_id.get(attempt.get("agent_id"))
        if not isinstance(agent, dict):
            continue
        observed = agent.get("fork_observed")
        if observed is None:
            continue
        mismatch = requested == "none" and observed is True
        mismatch = mismatch or requested != "none" and observed is False
        if not mismatch:
            continue
        actual = "forked" if observed else "not-forked"
        messages.append(
            f"{agent.get('agent_id') or 'unknown'} requested fork_turns={requested} "
            f"but child metadata proves {actual}"
        )
    return messages


def automatic_anomaly_types(
    attempts: list[dict[str, Any]], agents: list[dict[str, Any]], correction: bool
) -> tuple[list[str], list[str]]:
    types: set[str] = set()
    evidence: list[str] = []
    failed = [item for item in attempts if item.get("outcome") in {"rejected", "errored"}]
    if failed:
        types.add("dispatch-failure")
        codes = sorted({str(item.get("error") or "unknown") for item in failed})
        evidence.append(f"{len(failed)} dispatch attempt(s) failed: {', '.join(codes)}")
    agents_by_id = {agent["agent_id"]: agent for agent in agents}
    for attempt in attempts:
        agent = agents_by_id.get(attempt.get("agent_id"))
        if agent is None:
            continue
        requested_model = attempt.get("requested_model")
        requested_effort = attempt.get("requested_reasoning_effort")
        model_mismatch = requested_model and requested_model != agent.get("model")
        effort_mismatch = requested_effort and requested_effort != agent.get(
            "reasoning_effort"
        )
        if model_mismatch or effort_mismatch:
            types.add("runtime-capability-mismatch")
            requested = "/".join(
                value or "unspecified"
                for value in (requested_model, requested_effort)
            )
            observed = "/".join(
                value or "unknown"
                for value in (agent.get("model"), agent.get("reasoning_effort"))
            )
            evidence.append(
                f"{agent['agent_id']} dispatch requested {requested}, observed {observed}"
            )
    for agent in agents:
        expected = expected_role_runtimes(agent)
        actual = (agent.get("model"), agent.get("reasoning_effort"))
        if expected and all(actual) and actual not in expected:
            current = expected[0]
            types.add("runtime-capability-mismatch")
            evidence.append(
                f"{agent['agent_id']} {agent['role']} expected "
                f"{current[0]}/{current[1]}, observed {actual[0]}/{actual[1]}"
            )
        tier_mismatch = service_tier_mismatch(agent)
        if tier_mismatch:
            types.add("runtime-capability-mismatch")
            evidence.append(tier_mismatch)
        if agent.get("nested_agent_calls", 0) > 0:
            types.add("topology-violation")
            evidence.append(
                f"{agent['agent_id']} emitted {agent['nested_agent_calls']} nested spawn call(s)"
            )
        if (
            agent.get("status") == "completed"
            and int(agent.get("tool_calls") or 0) == 0
            and int(agent.get("final_message_chars") or 0) == 0
        ):
            types.add("zero-yield-child")
            evidence.append(
                f"{agent['agent_id']} completed with zero tool calls and zero final-message characters"
            )
        delivery = delivery_failure_messages([agent])
        if delivery:
            types.add("child-delivery-failure")
            evidence.extend(delivery)
    binding_issues = binding_uncertainty_messages(attempts, agents)
    if binding_issues:
        types.add("agent-binding-uncertainty")
        evidence.extend(binding_issues)
    authority_issues = authority_contract_mismatch_messages(agents)
    if authority_issues:
        types.add("authority-contract-mismatch")
        evidence.extend(authority_issues)
    lifecycle_issues = lifecycle_incomplete_messages(agents)
    if lifecycle_issues:
        types.add("child-lifecycle-incomplete")
        evidence.extend(lifecycle_issues)
    if correction:
        types.add("record-correction")
        evidence.append("new hook events arrived after the prior immutable snapshot")
    fork_mismatches = fork_request_observation_mismatch_messages(attempts, agents)
    if fork_mismatches:
        types.add("fork-request-observation-mismatch")
        evidence.extend(fork_mismatches)
    return sorted(types), evidence


def parse_keyed(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use AGENT_ID=value")
        agent_id, text = value.split("=", 1)
        agent_id = bounded(agent_id, 160) or ""
        summary = bounded(text, 800) or ""
        if not agent_id or not summary:
            raise ValueError(f"{label} must not be empty")
        result[agent_id] = summary
    return result


ADDITIVE_METRIC_FIELDS = (
    "turn_count",
    "event_count",
    "tool_calls",
    "tool_outputs",
    "nested_agent_calls",
)
TOKEN_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def cumulative_agent_metrics(agent: dict[str, Any]) -> dict[str, Any]:
    usage = agent.get("token_usage")
    return {
        **{
            field: max(0, int(agent.get(field) or 0))
            for field in ADDITIVE_METRIC_FIELDS
        },
        "token_usage": {
            field: max(0, int(usage.get(field) or 0))
            for field in TOKEN_USAGE_FIELDS
        }
        if isinstance(usage, dict)
        else None,
    }


def metric_delta(
    current: dict[str, Any], baseline: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    reset_detected = False

    def subtract(current_value: Any, baseline_value: Any) -> int:
        nonlocal reset_detected
        current_int = max(0, int(current_value or 0))
        baseline_int = max(0, int(baseline_value or 0))
        if current_int < baseline_int:
            reset_detected = True
            return current_int
        return current_int - baseline_int

    result: dict[str, Any] = {
        field: subtract(current.get(field), baseline.get(field))
        for field in ADDITIVE_METRIC_FIELDS
    }
    current_usage = current.get("token_usage")
    baseline_usage = baseline.get("token_usage")
    result["token_usage"] = (
        {
            field: subtract(
                current_usage.get(field),
                baseline_usage.get(field)
                if isinstance(baseline_usage, dict)
                else 0,
            )
            for field in TOKEN_USAGE_FIELDS
        }
        if isinstance(current_usage, dict)
        else None
    )
    return result, reset_detected


def previous_routine_payload(previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(previous, dict):
        return None
    path_value = previous.get("routine")
    if not isinstance(path_value, str):
        return None
    path = Path(path_value).expanduser()
    return read_json(path) if path.is_file() else None


def attach_metric_snapshots(
    dirs: dict[str, Path],
    agents: list[dict[str, Any]],
    previous_record: dict[str, Any] | None,
) -> list[tuple[Path, dict[str, Any]]]:
    """Attach active-record deltas and return ledger updates to commit later."""
    previous_agents = {
        item.get("agent_id"): item
        for item in (previous_record or {}).get("agents", [])
        if isinstance(item, dict) and isinstance(item.get("agent_id"), str)
    }
    updates: list[tuple[Path, dict[str, Any]]] = []
    for agent in agents:
        agent_id = agent.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        current = cumulative_agent_metrics(agent)
        prior_agent = previous_agents.get(agent_id)
        prior_snapshot = (
            prior_agent.get("metric_snapshot")
            if isinstance(prior_agent, dict)
            and isinstance(prior_agent.get("metric_snapshot"), dict)
            else None
        )
        ledger_path = metric_snapshot_path(dirs, agent_id)
        ledger = read_json(ledger_path) if ledger_path.is_file() else {}
        same_assignment = prior_snapshot is not None
        if same_assignment:
            baseline = prior_snapshot.get("baseline_cumulative")
            baseline = baseline if isinstance(baseline, dict) else {}
            sequence = max(1, int(prior_snapshot.get("sequence") or 1))
            revision = max(1, int(prior_snapshot.get("revision") or 1))
            if current != prior_snapshot.get("cumulative"):
                revision += 1
            baseline_record_id = prior_snapshot.get("baseline_record_id")
        else:
            baseline = ledger.get("cumulative")
            baseline = baseline if isinstance(baseline, dict) else {}
            sequence = max(0, int(ledger.get("sequence") or 0)) + 1
            revision = 1
            baseline_record_id = ledger.get("last_record_id")
        delta, reset_detected = metric_delta(current, baseline)
        agent["metric_snapshot"] = {
            "semantics": "cumulative-child-session",
            "aggregation": "sum-active-record-delta-only",
            "sequence": sequence,
            "revision": revision,
            "baseline_record_id": baseline_record_id,
            "baseline_cumulative": baseline,
            "cumulative": current,
            "delta": delta,
            "reset_detected": reset_detected,
        }
        updates.append(
            (
                ledger_path,
                {
                    "agent_id": agent_id,
                    "sequence": sequence,
                    "cumulative": current,
                    "parent_thread_id": agent.get("parent_thread_id"),
                    "parent_turn_id": agent.get("parent_turn_id"),
                },
            )
        )
    return updates


def commit_metric_snapshots(
    updates: list[tuple[Path, dict[str, Any]]], record_id: str
) -> None:
    for path, payload in updates:
        atomic_json(
            path,
            {
                **payload,
                "last_record_id": record_id,
                "updated_at": utc_now(),
            },
        )


def source_revision(
    raw_event_count: int,
    parent_stats: dict[str, Any],
    attempts: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    semantic_inputs: dict[str, Any],
) -> str:
    agent_fields = (
        "agent_id",
        "session_path",
        "transcript_bytes_processed",
        "model",
        "reasoning_effort",
        "service_tier",
        "metric_scope",
        "metric_validity",
        "metric_observability",
        "metric_boundary_method",
        "requested_role",
        "requested_role_source",
        "actual_role",
        "actual_role_source",
        "forked",
        "fork_observed",
        "fork_observation_source",
        "forked_from_id",
        "status",
        "turn_count",
        "event_count",
        "tool_calls",
        "tool_outputs",
        "nested_agent_calls",
        "final_message_chars",
        "runtime_resolution",
        "runtime_provenance",
        "effective_authority",
        "metric_snapshot",
    )
    stable = {
        "raw_event_count": raw_event_count,
        "parent": {
            key: parent_stats.get(key)
            for key in (
                "transcript_path",
                "start_offset",
                "legacy_full_scan",
            )
        },
        "attempts": attempts,
        "agents": [
            {key: agent.get(key) for key in agent_fields}
            for agent in agents
        ],
        "semantic_inputs": semantic_inputs,
    }
    encoded = json.dumps(
        stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finalize(
    root: Path,
    parent_thread_id: str,
    turn_id: str,
    task_signature_override: str | None,
    summaries: dict[str, str],
    assessment: dict[str, Any] | None,
    parent_transcript: Path | None,
    dry_run: bool,
    finalization_trigger: str = "manual-cli",
) -> dict[str, Any]:
    dirs = state_dirs(root, create=not dry_run)
    journal = turn_journal(dirs, parent_thread_id, turn_id)
    pointer = turn_pointer(dirs, parent_thread_id, turn_id)
    lock = dirs["locks"] / f"turn-{turn_key(parent_thread_id, turn_id)}.lock"
    metric_lock = dirs["locks"] / "metric-snapshot-ledger.lock"
    with (
        locked(lock) if not dry_run else nullcontext()
    ), (
        locked(metric_lock) if not dry_run else nullcontext()
    ):
        events = load_journal(journal)
        if not events:
            raise ValueError("no hook events exist for this parent turn")
        previous = read_json(pointer) if pointer.is_file() else None
        enriched_events, parent_stats = recover_parent_tail(
            root,
            parent_thread_id,
            turn_id,
            events,
            parent_transcript,
            persist=not dry_run,
        )
        if not dry_run:
            register_recovered_tool_bindings(
                root, parent_thread_id, turn_id, enriched_events
            )
        enriched_events.extend(
            pending_lifecycle_events_for_turn(
                root,
                parent_thread_id,
                turn_id,
                persist=not dry_run,
            )
        )
        enriched_events = enrich_child_metrics(
            root,
            parent_thread_id,
            enriched_events,
            parent_stats,
            persist=not dry_run,
        )
        attempts = connected_attempts(enriched_events)
        for attempt in attempts:
            attempt["parent_thread_id"] = parent_thread_id
            attempt["parent_turn_id"] = turn_id
            child_id = attempt.get("agent_id")
            child_ref = attempt.get("child_ref")
            attempt["identity_evidence"] = {
                "child_session_id": child_id,
                "parent_thread_id": parent_thread_id,
                "parent_turn_id": turn_id,
                "agent_path": child_ref if isinstance(child_ref, str) and child_ref.startswith("/") else None,
                "binding": attempt.get("binding", "unbound"),
            }
        agents = build_agents(
            enriched_events,
            attempts,
            summaries,
            parent_thread_id,
            turn_id,
        )
        previous_record = previous_routine_payload(previous)
        metric_snapshot_updates = attach_metric_snapshots(
            dirs, agents, previous_record
        )
        coordination_metrics = coordination_metrics_from_hook_events(
            enriched_events,
            parent_stats.get("coordination_metrics"),
            suffix_observed=bool(parent_stats.get("used")),
            legacy=bool(parent_stats.get("legacy_full_scan")),
        )
        revision = source_revision(
            len(events),
            parent_stats,
            attempts,
            agents,
            {
                "task_signature": task_signature_override,
                "summaries": summaries,
                "assessment": assessment,
                "coordination_metrics": coordination_metrics,
            },
        )
        if previous and previous.get("evidence_revision") == revision and not dry_run:
            commit_metric_snapshots(
                metric_snapshot_updates,
                str(previous.get("record_id") or "unknown"),
            )
            return {
                "routine": previous.get("routine"),
                "anomaly": previous.get("anomaly"),
                "idempotent": True,
            }

        if not attempts and not agents:
            raise ValueError("hook journal has no native-agent dispatch evidence")
        correction = previous is not None
        now = datetime.now(timezone.utc)
        record_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(4)}"
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
        observed_started_at = min(starts) if starts else None
        observed_ended_at = max(ends) if ends else None
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
        anomaly_types, anomaly_evidence = automatic_anomaly_types(
            attempts, agents, correction
        )
        source = "correction" if correction else "live"
        supersedes = [previous["record_id"]] if previous else []
        project = next(
            (
                event.get("cwd")
                for event in enriched_events
                if event.get("cwd")
            ),
            None,
        ) or "unknown"
        common = {
            "schema_version": SCHEMA_VERSION,
            "record_id": record_id,
            "recorded_at": now.isoformat(),
            "source": source,
            "supersedes": supersedes,
            "project": project,
            "parent_thread_id": parent_thread_id,
            "turn_id": turn_id,
            "task_signature": task_signature_override
            or task_signature(attempts, agents),
            "finalization": {
                "trigger": finalization_trigger,
                "automatic": finalization_trigger != "manual-cli",
                "source": "codex-hook" if finalization_trigger != "manual-cli" else "operator",
                "provenance_verified": finalization_trigger == "parent-stop-hook",
            },
        }
        routine = {
            **common,
            "collection_mode": "codex-hooks-incremental",
            "outcome": aggregate_outcome(attempts, agents),
            "observed_started_at": observed_started_at,
            "observed_ended_at": observed_ended_at,
            "observed_duration_ms": elapsed_ms(
                observed_started_at, observed_ended_at
            ),
            "attempt_count": len(attempts),
            "child_count": len(agents),
            "roles": sorted(role_counts),
            "role_counts": role_counts,
            "agents": agents,
            "spawn_attempts": attempts,
            "coordination_metrics": coordination_metrics,
            "assessment": assessment,
            "collector": {
                "lifecycle_policy_version": 1,
                "child_metric_boundary_version": 1,
                "zero_yield_policy_version": 1,
                "delivery_failure_policy_version": 1,
                "metric_observability_version": 1,
                "runtime_resolution_version": 1,
                "effective_authority_version": 1,
                "metric_snapshot_version": 1,
                "coordination_telemetry_version": 1,
                "hook_event_count": len(events),
                "recovered_event_count": max(0, len(enriched_events) - len(events)),
                "parent_transcript_bytes_processed": parent_stats["bytes_processed"],
                "parent_transcript_new_bytes_processed": parent_stats[
                    "new_bytes_processed"
                ],
                "parent_cursor_start_offset": parent_stats["start_offset"],
                "parent_cursor_end_offset": parent_stats["end_offset"],
                "parent_legacy_full_scan": parent_stats["legacy_full_scan"],
                "child_transcript_bytes_processed": sum(
                    agent["transcript_bytes_processed"] for agent in agents
                ),
                "transcript_strategy": (
                    "hook-events-plus-legacy-parent-recovery"
                    if parent_stats["legacy_full_scan"]
                    else "hook-events-plus-incremental-parent-tail"
                    if parent_stats["used"]
                    else "hook-events-plus-child-byte-cursors"
                ),
                "raw_prompts_stored": False,
                "raw_messages_stored": False,
                "coordination_metrics_source": coordination_metrics.get("source"),
            },
        }
        anomaly = None
        if anomaly_types:
            anomaly = {
                **common,
                "related_routine": f"{record_id}.json",
                "anomaly_types": anomaly_types,
                "summary": "Automatic Codex hook evidence detected: "
                + ", ".join(anomaly_types),
                "evidence": "; ".join(anomaly_evidence)[:1600],
                "impact": (
                    "The immutable runtime snapshot differs from a normal native-agent "
                    "closeout and remains explicitly auditable."
                ),
            }
        if dry_run:
            return {"routine": routine, "anomaly": anomaly, "idempotent": False}

        routine_path = dirs["routine"] / f"{record_id}.json"
        anomaly_path = dirs["anomalies"] / f"{record_id}.json" if anomaly else None
        write_new(routine_path, routine)
        try:
            if anomaly_path is not None and anomaly is not None:
                write_new(anomaly_path, anomaly)
        except Exception:
            routine_path.unlink(missing_ok=True)
            raise
        pointer_payload = {
            "record_id": record_id,
            "event_count": len(events),
            "evidence_revision": revision,
            "routine": str(routine_path),
            "anomaly": str(anomaly_path) if anomaly_path else None,
        }
        atomic_json(pointer, pointer_payload)
        commit_metric_snapshots(metric_snapshot_updates, record_id)
        release_terminal_agent_bindings(
            root, parent_thread_id, turn_id, agents
        )
        return {**pointer_payload, "idempotent": False}


def verified_parent_final_transcript(
    payload: dict[str, Any],
    parent_thread_id: str,
    turn_id: str,
    root: Path,
) -> Path:
    """Verify a Stop payload against the final assistant response already on disk.

    Codex writes the identity-bound final assistant response before invoking the
    Stop hook.  A premature simulated payload cannot satisfy this check.  The
    raw response is compared only in memory and is never copied into hook state.
    """
    transcript_value = payload.get("transcript_path")
    if not isinstance(transcript_value, str) or not transcript_value:
        raise ValueError("Stop lacks a parent transcript path")
    transcript = Path(transcript_value).expanduser().resolve()
    if not transcript.is_file():
        raise ValueError("Stop parent transcript is not readable")
    if root.expanduser().resolve() == DEFAULT_ROOT.expanduser().resolve():
        sessions_root = (Path.home() / ".codex" / "sessions").resolve()
        if not transcript.is_relative_to(sessions_root):
            raise ValueError("production Stop transcript is outside Codex sessions")

    supplied_message = payload.get("last_assistant_message")
    if not isinstance(supplied_message, str) or not supplied_message:
        raise ValueError("Stop lacks a final assistant message")

    session_identity_seen = False
    latest_final_message: str | None = None
    with transcript.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_payload = payload_dict(event)
            if event.get("type") == "session_meta":
                session_id = event_payload.get("id") or event_payload.get("session_id")
                if session_id == parent_thread_id:
                    session_identity_seen = True
                continue
            if (
                event.get("type") != "response_item"
                or event_payload.get("type") != "message"
                or event_payload.get("role") != "assistant"
                or event_payload.get("phase") != "final_answer"
            ):
                continue
            metadata = event_payload.get("internal_chat_message_metadata_passthrough")
            message_turn_id = (
                metadata.get("turn_id")
                if isinstance(metadata, dict)
                else None
            )
            if message_turn_id != turn_id:
                continue
            content = event_payload.get("content")
            if not isinstance(content, list):
                continue
            text_parts = [
                part.get("text")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if text_parts:
                latest_final_message = "".join(text_parts)

    if not session_identity_seen:
        raise ValueError("Stop transcript does not belong to the parent session")
    if latest_final_message is None:
        raise ValueError("Stop arrived before the final assistant response was recorded")
    if latest_final_message != supplied_message:
        raise ValueError("Stop final assistant message does not match the transcript")
    return transcript


def parent_stop_event(
    payload: dict[str, Any], transcript: Path
) -> dict[str, Any]:
    transcript_offset = (
        transcript.stat().st_size if transcript.is_file() else None
    )
    last_message = payload.get("last_assistant_message")
    return {
        "kind": "parent_stop",
        "observed_at": utc_now(),
        "cwd": bounded(payload.get("cwd"), 1000),
        "parent_transcript_path": bounded(transcript, 1200),
        "parent_transcript_offset": transcript_offset,
        "last_assistant_message_chars": len(last_message)
        if isinstance(last_message, str)
        else 0,
        "stop_hook_active": bool(payload.get("stop_hook_active")),
        "provenance": "verified-parent-final-response",
    }


def closeout_is_terminal(preview: dict[str, Any]) -> bool:
    routine = preview.get("routine")
    if not isinstance(routine, dict):
        return False
    agents = routine.get("agents")
    if not isinstance(agents, list):
        return False
    return all(
        isinstance(agent, dict)
        and agent.get("status") in TERMINAL_AGENT_STATUSES
        for agent in agents
    )


def finalized_result(pointer: Path) -> dict[str, Any]:
    previous = read_json(pointer)
    return {
        **previous,
        "recorded": True,
        "reason": "already-finalized",
        "idempotent": True,
    }


def auto_finalize_stop(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        raise ValueError("auto-finalize requires a Stop hook event")
    parent_thread_id, turn_id = require_identity(payload)
    dirs = state_dirs(root, create=False)
    journal = turn_journal(dirs, parent_thread_id, turn_id)
    if not journal.is_file():
        return {"recorded": False, "reason": "no-native-agent-journal"}
    pointer = turn_pointer(dirs, parent_thread_id, turn_id)
    if pointer.is_file():
        return finalized_result(pointer)
    transcript = verified_parent_final_transcript(
        payload, parent_thread_id, turn_id, root
    )
    append_event(
        root,
        parent_thread_id,
        turn_id,
        parent_stop_event(payload, transcript),
    )
    preview = finalize(
        root,
        parent_thread_id,
        turn_id,
        None,
        {},
        None,
        None,
        True,
        "parent-stop-hook",
    )
    if not closeout_is_terminal(preview):
        return {"recorded": False, "reason": "awaiting-terminal-agents"}
    return finalize(
        root,
        parent_thread_id,
        turn_id,
        None,
        {},
        None,
        None,
        False,
        "parent-stop-hook",
    )


def auto_finalize_late_subagent_stop(
    payload: dict[str, Any], root: Path
) -> dict[str, Any] | None:
    """Finish a deferred parent Stop once all children are terminal.

    A finalized parent turn is immutable and unique: later lifecycle replay is
    idempotent and never creates a second formal routine record.
    """
    if payload.get("hook_event_name") != "SubagentStop":
        return None
    parent_thread_id, hook_turn_id = require_identity(payload)
    agent_id = bounded(payload.get("agent_id"), 160)
    if not agent_id or agent_id == parent_thread_id:
        return None
    parent_turn_id = (
        bounded(payload.get("parent_turn_id"), 160)
        or bound_child_turn(root, parent_thread_id, hook_turn_id)
        or bound_parent_turn(root, agent_id, parent_thread_id)
    )
    if not parent_turn_id:
        return None
    dirs = state_dirs(root, create=False)
    pointer = turn_pointer(dirs, parent_thread_id, parent_turn_id)
    if pointer.is_file():
        return finalized_result(pointer)
    journal = turn_journal(dirs, parent_thread_id, parent_turn_id)
    events = load_journal(journal)
    if not any(event.get("kind") == "parent_stop" for event in events):
        return None
    preview = finalize(
        root,
        parent_thread_id,
        parent_turn_id,
        None,
        {},
        None,
        None,
        True,
        "parent-stop-hook",
    )
    if not closeout_is_terminal(preview):
        return {"recorded": False, "reason": "awaiting-terminal-agents"}
    return finalize(
        root,
        parent_thread_id,
        parent_turn_id,
        None,
        {},
        None,
        None,
        False,
        "parent-stop-hook",
    )


def enforce_manual_finalize_scope(root: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if root.expanduser().resolve() == DEFAULT_ROOT.expanduser().resolve():
        raise ValueError(
            "production records may only be written by the verified automatic Stop hook; "
            "use finalize --dry-run for preview"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Incrementally collect and finalize native-agent hook facts."
    )
    commands = result.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="Read one Codex hook event from stdin.")
    ingest.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    auto_finalize = commands.add_parser(
        "auto-finalize",
        help="Read a Stop hook event and close only a turn with Agent evidence.",
    )
    auto_finalize.add_argument("--root", type=Path, default=DEFAULT_ROOT)

    closeout = commands.add_parser(
        "finalize", help="Write one immutable record from the small hook journal."
    )
    closeout.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    closeout.add_argument("--parent-thread-id", required=True)
    closeout.add_argument("--turn-id", required=True)
    closeout.add_argument(
        "--parent-transcript",
        type=Path,
        help="Explicit parent transcript for repairing legacy hook journals without path metadata.",
    )
    closeout.add_argument("--task-signature")
    closeout.add_argument("--agent-summary", action="append", default=[])
    closeout.add_argument("--role-fit")
    closeout.add_argument("--upgrade")
    closeout.add_argument("--team-value")
    closeout.add_argument("--evidence")
    closeout.add_argument("--specialist-candidate")
    closeout.add_argument("--dry-run", action="store_true")

    status = commands.add_parser("status", help="Inspect one bounded hook journal.")
    status.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    status.add_argument("--parent-thread-id", required=True)
    status.add_argument("--turn-id", required=True)
    return result


def assessment_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    fields = (args.role_fit, args.upgrade, args.team_value, args.evidence)
    if not any(fields) and not args.specialist_candidate:
        return None
    if not all(fields):
        raise ValueError(
            "semantic assessment requires --role-fit, --upgrade, --team-value, and --evidence"
        )
    return {
        "source": "parent",
        "role_fit": bounded(args.role_fit, 80),
        "upgrade": bounded(args.upgrade, 80),
        "team_value": bounded(args.team_value, 80),
        "evidence": bounded(args.evidence, 1200),
        "specialist_candidate": bounded(args.specialist_candidate, 160),
    }


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "ingest":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("hook input must be a JSON object")
            ingest_hook(payload, args.root)
            auto_finalize_late_subagent_stop(payload, args.root)
            print("{}")
            return
        if args.command == "auto-finalize":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("hook input must be a JSON object")
            auto_finalize_stop(payload, args.root)
            print("{}")
            return
        if args.command == "status":
            dirs = state_dirs(args.root)
            journal = turn_journal(dirs, args.parent_thread_id, args.turn_id)
            pointer = turn_pointer(dirs, args.parent_thread_id, args.turn_id)
            print(
                json.dumps(
                    {
                        "journal": str(journal),
                        "event_count": len(load_journal(journal)),
                        "finalized": read_json(pointer) if pointer.is_file() else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        enforce_manual_finalize_scope(args.root, args.dry_run)
        payload = finalize(
            args.root,
            bounded(args.parent_thread_id, 160) or "",
            bounded(args.turn_id, 160) or "",
            bounded(args.task_signature, 160),
            parse_keyed(args.agent_summary, "agent summary"),
            assessment_from_args(args),
            args.parent_transcript,
            args.dry_run,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
