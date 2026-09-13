#!/usr/bin/env python3
"""Current service-tier policy and installed role profiles."""

from __future__ import annotations

import copy
import json
import os
import tomllib
from pathlib import Path
from typing import Any


POLICY_FILENAME = "agent-team-policy.toml"
VALID_TIERS = {"fast", "standard"}
TIER_ALIASES = {
    "fast": {"fast", "priority"},
    "standard": {"default", "standard"},
}
PROFILE_TIER_VALUES = {"fast": "fast", "standard": "default"}


class PolicyError(ValueError):
    """The managed configuration cannot be read or used."""


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def policy_path(codex_home: Path | None = None) -> Path:
    return (codex_home or default_codex_home()) / POLICY_FILENAME


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError(f"cannot load {path}: {error}") from error


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


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("version") != 1:
        raise PolicyError("policy version must be 1")
    aliases: set[str] = set()
    for model, spec in _require_mapping(policy.get("models"), "models").items():
        spec = _require_mapping(spec, f"models.{model}")
        alias = spec.get("alias")
        if not model or not isinstance(alias, str) or not alias.strip():
            raise PolicyError(f"models.{model}: model and alias must be non-empty")
        alias = alias.strip().lower()
        if alias in aliases:
            raise PolicyError(f"duplicate model alias: {alias}")
        aliases.add(alias)
        canonical_tier(spec.get("service_tier"))
    filenames: set[str] = set()
    for role, spec in _require_mapping(policy.get("roles"), "roles").items():
        spec = _require_mapping(spec, f"roles.{role}")
        filename = spec.get("filename")
        if (not role or not isinstance(filename, str)
                or not filename.endswith(".toml") or Path(filename).name != filename):
            raise PolicyError(f"roles.{role}.filename must be one TOML basename")
        if filename in filenames:
            raise PolicyError(f"duplicate role filename: {filename}")
        filenames.add(filename)


def load_policy(
    codex_home: Path | None = None, *, explicit_path: Path | None = None
) -> dict[str, Any]:
    policy = read_toml(explicit_path or policy_path(codex_home))
    validate_policy(policy)
    return policy


def load_profiles(policy: dict[str, Any], codex_home: Path) -> dict[str, dict[str, Any]]:
    profiles = {}
    for role, spec in policy["roles"].items():
        path = codex_home / "agents" / spec["filename"]
        profile = read_toml(path)
        if profile.get("name") != role:
            raise PolicyError(f"{path}: expected role name {role!r}")
        model = profile.get("model")
        if not isinstance(model, str) or model not in policy["models"]:
            raise PolicyError(f"{path}: model is not in the managed tier policy")
        profiles[role] = profile
    return profiles


def resolve_model(policy: dict[str, Any], target: str) -> str:
    normalized = target.strip().lower()
    for model, spec in policy["models"].items():
        if normalized in {model.lower(), str(spec["alias"]).lower()}:
            return model
    raise PolicyError(f"unknown managed model or alias: {target}")


def policy_requires_fast_mode(policy: dict[str, Any]) -> bool:
    return any(canonical_tier(spec["service_tier"]) == "fast"
               for spec in policy["models"].values())


def update_model_tier(
    policy: dict[str, Any], target: str, tier: str
) -> tuple[dict[str, Any], str, bool]:
    validate_policy(policy)
    model = resolve_model(policy, target)
    normalized = canonical_tier(tier)
    updated = copy.deepcopy(policy)
    changed = canonical_tier(policy["models"][model]["service_tier"]) != normalized
    updated["models"][model]["service_tier"] = normalized
    return updated, model, changed


def render_policy(policy: dict[str, Any]) -> str:
    validate_policy(policy)
    quoted = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ["# Managed role files and current service tiers.", f"version = {policy['version']}", ""]
    for model, spec in policy["models"].items():
        lines.extend([f"[models.{quoted(model)}]", f"alias = {quoted(spec['alias'])}",
                      f"service_tier = {quoted(canonical_tier(spec['service_tier']))}", ""])
    for role, spec in policy["roles"].items():
        lines.extend([f"[roles.{quoted(role)}]", f"filename = {quoted(spec['filename'])}", ""])
    return "\n".join(lines).rstrip() + "\n"
