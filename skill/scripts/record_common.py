"""Shared privacy-safe transcript normalization for Agent Team recorders.

The hook and closeout entrypoints have different lifecycle and evidence
policies, but they consume the same small set of native transcript shapes.
Keep those pure classifiers here so the two paths cannot drift accidentally.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping


COORDINATION_OPERATIONS = (
    "spawn_agent",
    "followup_task",
    "send_message",
    "interrupt_agent",
    "wait_agent",
    "list_agents",
)
WAIT_OUTCOMES = ("timeout", "completed", "error", "unknown")
COORDINATION_CLASSIFIER_VERSION = 4


def sanitize_requested_fork_turns(
    arguments: Mapping[str, Any], *, missing_observability: str = "omitted"
) -> tuple[str | int | None, str]:
    """Return the allowlisted fork request and its evidence state.

    The caller supplies the missing-evidence label because a live hook can
    distinguish an omitted field while historical replay may only know that
    the field was never observed.
    """
    if "fork_turns" not in arguments:
        return None, missing_observability
    value = arguments.get("fork_turns")
    if value == "none" or value == "all":
        return value, "observed"
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, int) and value > 0:
        return value, "observed"
    normalized = value.strip() if isinstance(value, str) else ""
    if len(normalized) <= 18 and re.fullmatch(r"[1-9][0-9]*", normalized):
        try:
            parsed = int(normalized)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed, "observed"
    return None, "invalid"


def is_spawn_tool(name: Any) -> bool:
    """Recognize native spawn tools across runtime naming variants."""
    normalized = str(name or "").lower()
    return normalized == "agent" or normalized.endswith("spawn_agent")


def normalize_coordination_operation(name: Any) -> str | None:
    """Map a transcript tool name to the strict coordination allowlist."""
    normalized = str(name or "").lower()
    for operation in COORDINATION_OPERATIONS:
        if normalized == operation or normalized.endswith(f".{operation}"):
            return operation
    return None


def duration_ms(value: Any, *, floor_before_bound: bool = False) -> int | None:
    """Accept numeric duration evidence with the caller's legacy cap order."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if floor_before_bound:
        if value >= 0:
            result = int(value)
            return result if result <= 86_400_000 else None
    elif 0 <= value <= 86_400_000:
        return int(value)
    return None


def duration_from(
    value: Any, keys: tuple[str, ...], *, floor_before_bound: bool = False
) -> int | None:
    """Read one bounded duration field from an object or encoded JSON object."""
    if isinstance(value, dict):
        for key in keys:
            parsed = duration_ms(value.get(key), floor_before_bound=floor_before_bound)
            if parsed is not None:
                return parsed
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return duration_from(decoded, keys, floor_before_bound=floor_before_bound)
    return None


def classify_wait_outcome(value: Any) -> str:
    """Classify only explicit wait result signals into a finite code."""
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("error"):
            return "error"
        status = value.get("status") or value.get("outcome") or value.get("result")
        if isinstance(status, str):
            normalized = status.strip().lower().replace("-", "_")
            if normalized in {"timeout", "timed_out", "timedout"}:
                return "timeout"
            if normalized in {"completed", "complete", "done", "success", "ok"}:
                return "completed"
            if normalized in {"error", "errored", "failed", "rejected"}:
                return "error"
        completed_flag = any(
            value.get(key) is True for key in ("completed", "complete", "done", "success")
        )
        message_text = " ".join(
            str(value.get(key))
            for key in ("message", "detail", "reason")
            if isinstance(value.get(key), str)
        )[:4096].lower()
        if any(
            marker in message_text
            for marker in (
                "error",
                "failed",
                "rejected",
                "invalid",
                "must be",
                "parameter",
                "argument",
            )
        ):
            return "error"
        if (
            value.get("timed_out") is True
            or value.get("timeout") is True
            or "timed out" in message_text
            or "timeout" in message_text
        ):
            return "timeout"
        if completed_flag or any(
            re.search(rf"\b{marker}\b", message_text)
            for marker in ("completed", "complete", "done", "success")
        ):
            return "completed"
        if value.get("ok") is True:
            return "completed"
        return "unknown"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list, str)) and parsed != value:
            return classify_wait_outcome(parsed)
        normalized = value[:4096].strip().lower()
        if any(
            marker in normalized
            for marker in (
                "error",
                "failed",
                "rejected",
                "invalid",
                "must be",
                "parameter",
                "argument",
            )
        ):
            return "error"
        if "timed out" in normalized or "timeout" in normalized:
            return "timeout"
        if normalized in {"completed", "complete", "done", "success", "ok"}:
            return "completed"
        if any(
            re.search(rf"\b{marker}\b", normalized)
            for marker in ("completed", "complete", "done", "success")
        ):
            return "completed"
        return "unknown"
    return "unknown"


def payload_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def ulid_time_key(value: Any) -> str | None:
    """Return the UUID/ULID millisecond prefix used by Codex thread ids."""
    if not isinstance(value, str):
        return None
    compact = value.replace("-", "")
    if len(compact) < 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", compact[:12]):
        return None
    return compact[:12].lower()


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
