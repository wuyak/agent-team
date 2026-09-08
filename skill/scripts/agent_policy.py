#!/usr/bin/env python3
"""Shared runtime policy for managed native Codex agents."""

from __future__ import annotations

import copy
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POLICY_FILENAME = "agent-team-policy.toml"
VALID_TIERS = {"fast", "standard"}
TIER_ALIASES = {
    "fast": {"fast", "priority"},
    "standard": {"default", "standard"},
}
PROFILE_TIER_VALUES = {"fast": "fast", "standard": "default"}
VALID_SANDBOXES = {"inherit", "read-only", "workspace-write"}


class PolicyError(ValueError):
    """Raised when the managed-agent policy is absent or incoherent."""


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def policy_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / POLICY_FILENAME


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_tier(value: Any) -> str:
    if not isinstance(value, str):
        raise PolicyError("service tier must be fast or standard")
    normalized = value.strip().lower()
    if normalized == "default":
        normalized = "standard"
    if normalized not in VALID_TIERS:
        raise PolicyError(f"unsupported service tier: {value}")
    return normalized


def profile_tier_value(tier: str) -> str:
    return PROFILE_TIER_VALUES[canonical_tier(tier)]


def tier_aliases(tier: str) -> set[str]:
    return set(TIER_ALIASES[canonical_tier(tier)])


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise PolicyError(f"{field} must be a non-empty table")
    return value


def _history_rows(policy: dict[str, Any], field: str) -> list[dict[str, Any]]:
    rows = policy.get(field)
    if not isinstance(rows, list) or not rows:
        raise PolicyError(f"{field} must be a non-empty array of tables")
    if not all(isinstance(row, dict) for row in rows):
        raise PolicyError(f"{field} contains a non-table entry")
    return rows


def _history_for(
    policy: dict[str, Any], field: str, key: str, value: str
) -> list[dict[str, Any]]:
    """Return history entries belonging to one model or role."""
    return [row for row in policy[field] if row[key] == value]


def _latest_at_or_before(
    rows: list[dict[str, Any]], started: datetime
) -> dict[str, Any] | None:
    """Return the latest history entry effective at ``started``."""
    selected = [
        row for row in rows if parse_timestamp(row["effective_at"]) <= started
    ]
    return selected[-1] if selected else None


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("version") != 1:
        raise PolicyError("policy version must be 1")

    models = _require_mapping(policy.get("models"), "models")
    aliases: dict[str, str] = {}
    for model, spec in models.items():
        if not isinstance(model, str) or not model:
            raise PolicyError("model names must be non-empty strings")
        spec = _require_mapping(spec, f"models.{model}")
        alias = spec.get("alias")
        if not isinstance(alias, str) or not alias.strip():
            raise PolicyError(f"models.{model}.alias must be non-empty")
        alias = alias.strip().lower()
        if alias in aliases:
            raise PolicyError(f"duplicate model alias {alias}: {aliases[alias]}, {model}")
        aliases[alias] = model
        canonical_tier(spec.get("service_tier"))

    roles = _require_mapping(policy.get("roles"), "roles")
    filenames: set[str] = set()
    for role, spec in roles.items():
        spec = _require_mapping(spec, f"roles.{role}")
        filename = spec.get("filename")
        if (
            not isinstance(filename, str)
            or not filename.endswith(".toml")
            or Path(filename).name != filename
        ):
            raise PolicyError(f"roles.{role}.filename must be one TOML basename")
        if filename in filenames:
            raise PolicyError(f"duplicate role filename: {filename}")
        filenames.add(filename)
        if spec.get("model") not in models:
            raise PolicyError(f"roles.{role}.model is not a managed current model")
        effort = spec.get("reasoning_effort")
        if not isinstance(effort, str) or not effort:
            raise PolicyError(f"roles.{role}.reasoning_effort must be non-empty")
        if spec.get("sandbox_mode") not in VALID_SANDBOXES:
            raise PolicyError(f"roles.{role}.sandbox_mode is invalid")

    service_rows = _history_rows(policy, "service_tier_history")
    service_seen: dict[str, datetime] = {}
    latest_service: dict[str, str] = {}
    for row in service_rows:
        model = row.get("model")
        if not isinstance(model, str) or not model:
            raise PolicyError("service_tier_history.model must be non-empty")
        effective = parse_timestamp(row.get("effective_at"))
        if effective is None:
            raise PolicyError("service_tier_history.effective_at must be ISO-8601")
        if model in service_seen and effective <= service_seen[model]:
            raise PolicyError(f"service tier history for {model} is not strictly ordered")
        service_seen[model] = effective
        latest_service[model] = canonical_tier(row.get("service_tier"))
    for model, spec in models.items():
        if latest_service.get(model) != canonical_tier(spec.get("service_tier")):
            raise PolicyError(f"current service tier for {model} disagrees with history")

    runtime_rows = _history_rows(policy, "role_runtime_history")
    runtime_seen: dict[str, datetime] = {}
    latest_runtime: dict[str, tuple[str, str]] = {}
    for row in runtime_rows:
        role = row.get("role")
        if role not in roles:
            raise PolicyError(f"role_runtime_history has unknown role: {role}")
        effective = parse_timestamp(row.get("effective_at"))
        if effective is None:
            raise PolicyError("role_runtime_history.effective_at must be ISO-8601")
        if role in runtime_seen and effective <= runtime_seen[role]:
            raise PolicyError(f"runtime history for {role} is not strictly ordered")
        runtime_seen[role] = effective
        model = row.get("model")
        effort = row.get("reasoning_effort")
        if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
            raise PolicyError(f"runtime history for {role} is incomplete")
        latest_runtime[role] = (model, effort)
    for role, spec in roles.items():
        current = (spec["model"], spec["reasoning_effort"])
        if latest_runtime.get(role) != current:
            raise PolicyError(f"current runtime for {role} disagrees with history")


def load_policy(
    codex_home: Path | None = None, *, explicit_path: Path | None = None
) -> dict[str, Any]:
    path = explicit_path or policy_path(codex_home)
    try:
        with path.open("rb") as handle:
            policy = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError(f"cannot load agent policy {path}: {error}") from error
    validate_policy(policy)
    return policy


def resolve_model(policy: dict[str, Any], target: str) -> str:
    normalized = target.strip().lower()
    for model, spec in policy["models"].items():
        if normalized in {model.lower(), str(spec["alias"]).lower()}:
            return model
    raise PolicyError(f"unknown managed model or alias: {target}")


def profile_expectations(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expectations: dict[str, dict[str, Any]] = {}
    for role, spec in policy["roles"].items():
        tier = canonical_tier(policy["models"][spec["model"]]["service_tier"])
        expectations[role] = {
            "filename": spec["filename"],
            "model": spec["model"],
            "reasoning_effort": spec["reasoning_effort"],
            "service_tier": profile_tier_value(tier),
            "sandbox_mode": None
            if spec["sandbox_mode"] == "inherit"
            else spec["sandbox_mode"],
        }
    return expectations


def policy_requires_fast_mode(policy: dict[str, Any]) -> bool:
    return any(
        canonical_tier(spec["service_tier"]) == "fast"
        for spec in policy["models"].values()
    )


def expected_role_runtimes(
    policy: dict[str, Any], role: str, started_at: Any
) -> tuple[tuple[str, str], ...] | None:
    if role not in policy["roles"]:
        return None
    rows = _history_for(policy, "role_runtime_history", "role", role)
    started = parse_timestamp(started_at)
    if started is None:
        values: list[tuple[str, str]] = []
        for row in rows:
            pair = (row["model"], row["reasoning_effort"])
            if pair not in values:
                values.append(pair)
        return tuple(values)
    row = _latest_at_or_before(rows, started)
    if row is None:
        return None
    return ((row["model"], row["reasoning_effort"]),)


def expected_service_tier_aliases(
    policy: dict[str, Any], role: str, started_at: Any
) -> set[str] | None:
    runtimes = expected_role_runtimes(policy, role, started_at)
    if not runtimes:
        return None
    started = parse_timestamp(started_at)
    aliases: set[str] = set()
    for model, _effort in runtimes:
        rows = _history_for(policy, "service_tier_history", "model", model)
        if started is None:
            for row in rows:
                aliases.update(tier_aliases(row["service_tier"]))
            continue
        row = _latest_at_or_before(rows, started)
        if row is not None:
            aliases.update(tier_aliases(row["service_tier"]))
    return aliases or None


def runtime_expectation(
    policy: dict[str, Any], role: str | None, started_at: Any
) -> dict[str, Any] | None:
    """Resolve one time-bounded managed-role expectation for durable records.

    The observed runtime can legitimately omit a service tier.  This helper
    keeps that absence separate from the controller expectation by returning
    the exact policy row that applied when the child started.
    """
    if not isinstance(role, str) or role not in policy["roles"]:
        return None
    started = parse_timestamp(started_at)
    if started is None:
        return None

    runtime_rows = _history_for(policy, "role_runtime_history", "role", role)
    runtime_row = _latest_at_or_before(runtime_rows, started)
    if runtime_row is None:
        return None
    model = runtime_row["model"]

    tier_rows = _history_for(policy, "service_tier_history", "model", model)
    tier_row = _latest_at_or_before(tier_rows, started)
    if tier_row is None:
        return None
    configured_sandbox = policy["roles"][role]["sandbox_mode"]
    return {
        "policy_version": policy["version"],
        "role": role,
        "model": model,
        "reasoning_effort": runtime_row["reasoning_effort"],
        "service_tier": canonical_tier(tier_row["service_tier"]),
        "service_tier_aliases": sorted(tier_aliases(tier_row["service_tier"])),
        "configured_sandbox_mode": configured_sandbox,
        "runtime_effective_at": runtime_row["effective_at"],
        "service_tier_effective_at": tier_row["effective_at"],
        "source": "agent-team-policy-history",
    }


def update_model_tier(
    policy: dict[str, Any], target: str, tier: str, effective_at: str
) -> tuple[dict[str, Any], str, bool]:
    validate_policy(policy)
    model = resolve_model(policy, target)
    normalized = canonical_tier(tier)
    current = canonical_tier(policy["models"][model]["service_tier"])
    if current == normalized:
        return copy.deepcopy(policy), model, False
    if parse_timestamp(effective_at) is None:
        raise PolicyError("effective_at must be an ISO-8601 timestamp")
    updated = copy.deepcopy(policy)
    updated["models"][model]["service_tier"] = normalized
    updated["service_tier_history"].append(
        {
            "effective_at": effective_at,
            "model": model,
            "service_tier": normalized,
        }
    )
    validate_policy(updated)
    return updated, model, True


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_policy(policy: dict[str, Any]) -> str:
    validate_policy(policy)
    lines = [
        "# Single source of truth for managed native-agent runtime profiles.",
        f"version = {policy['version']}",
        "",
    ]
    for model, spec in policy["models"].items():
        lines.extend(
            [
                f"[models.{_quoted(model)}]",
                f"alias = {_quoted(spec['alias'])}",
                f"service_tier = {_quoted(canonical_tier(spec['service_tier']))}",
                "",
            ]
        )
    for role, spec in policy["roles"].items():
        lines.extend(
            [
                f"[roles.{role}]",
                f"filename = {_quoted(spec['filename'])}",
                f"model = {_quoted(spec['model'])}",
                f"reasoning_effort = {_quoted(spec['reasoning_effort'])}",
                f"sandbox_mode = {_quoted(spec['sandbox_mode'])}",
                "",
            ]
        )
    for field in ("service_tier_history", "role_runtime_history"):
        for row in policy[field]:
            lines.append(f"[[{field}]]")
            for key, value in row.items():
                lines.append(f"{key} = {_quoted(value)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
