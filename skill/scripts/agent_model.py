#!/usr/bin/env python3
"""Inspect or switch model generations for managed native Codex agents."""

from __future__ import annotations

import argparse
import copy
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_policy import PolicyError, default_codex_home, validate_policy
from agent_speed import atomic_write


MODEL_PROFILES: dict[str, dict[str, str]] = {
    "5.6": {
        "luna": "gpt-5.6-luna",
        "sol": "gpt-5.6-sol",
    },
    "6": {
        "luna": "gpt-6-luna",
        "sol": "gpt-6-sol",
    },
}

STRING_ASSIGNMENT_RE = re.compile(
    r"(?m)^[ \t]*(?P<key>[A-Za-z0-9_-]+|\"[^\"\n]+\"|'[^'\n]+')"
    r"[ \t]*=[ \t]*"
    r"(?P<value>\"\"\"(?:\\[\s\S]|[^\\])*?\"\"\"|'''[\s\S]*?'''|"
    r'"(?:\\.|[^"\\\n])*"|\'[^\'\n]*\')'
)


@dataclass(frozen=True)
class ModelRef:
    profile: str
    family: str


@dataclass(frozen=True)
class ModelField:
    label: str
    path: Path
    model: str
    ref: ModelRef


@dataclass
class InstallationState:
    codex_home: Path
    policy_path: Path
    policy_text: str
    policy: dict[str, Any]
    config_path: Path
    config_text: str
    config: dict[str, Any]
    roles: dict[str, tuple[Path, str, dict[str, Any]]]
    fields: list[ModelField]
    issues: list[str]

    @property
    def profiles(self) -> set[str]:
        return {field.ref.profile for field in self.fields}

    @property
    def profile(self) -> str | None:
        profiles = self.profiles
        return next(iter(profiles)) if len(profiles) == 1 and not self.issues else None


@dataclass(frozen=True)
class Change:
    path: Path
    before: str
    after: str
    details: tuple[str, ...]


def profile_label(profile: str) -> str:
    return f"gpt-{profile}"


def normalize_profile(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.startswith("gpt-"):
        normalized = normalized[4:]
    if normalized not in MODEL_PROFILES:
        supported = ", ".join(profile_label(profile) for profile in MODEL_PROFILES)
        raise PolicyError(f"unsupported model profile {value!r}; choose {supported}")
    return normalized


def model_ref(model: Any) -> ModelRef:
    if not isinstance(model, str):
        raise PolicyError("model value must be a string")
    for profile, families in MODEL_PROFILES.items():
        for family, slug in families.items():
            if model == slug:
                return ModelRef(profile=profile, family=family)
    raise PolicyError(f"unsupported managed model: {model}")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyError(f"cannot read {path}: {error}") from error


def parse_toml(path: Path, content: str) -> dict[str, Any]:
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise PolicyError(f"cannot parse {path}: {error}") from error


def inspect_installation(codex_home: Path) -> InstallationState:
    policy_path = codex_home / "agent-team-policy.toml"
    policy_text = read_text(policy_path)
    policy = parse_toml(policy_path, policy_text)
    validate_policy(policy)

    config_path = codex_home / "config.toml"
    config_text = read_text(config_path)
    config = parse_toml(config_path, config_text)
    agents = config.get("agents")
    if not isinstance(agents, dict) or "default_subagent_model" not in agents:
        raise PolicyError(f"{config_path}: missing [agents].default_subagent_model")

    fields: list[ModelField] = []
    issues: list[str] = []
    policy_models_by_alias: dict[str, str] = {}
    for model, spec in policy["models"].items():
        alias = str(spec["alias"]).strip().lower()
        if alias not in MODEL_PROFILES["6"]:
            raise PolicyError(f"unsupported managed model alias: {alias}")
        ref = model_ref(model)
        if ref.family != alias:
            issues.append(
                f"policy alias {alias!r} points to {model!r}, whose family is {ref.family!r}"
            )
        policy_models_by_alias[alias] = model
        fields.append(ModelField(f"policy.models.{alias}", policy_path, model, ref))
    expected_aliases = set(MODEL_PROFILES["6"])
    if set(policy_models_by_alias) != expected_aliases:
        raise PolicyError(
            "managed policy must contain exactly the luna and sol model aliases"
        )

    default_model = agents["default_subagent_model"]
    default_ref = model_ref(default_model)
    fields.append(
        ModelField(
            "config.agents.default_subagent_model",
            config_path,
            default_model,
            default_ref,
        )
    )

    roles: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    for role, spec in policy["roles"].items():
        path = codex_home / "agents" / spec["filename"]
        content = read_text(path)
        profile = parse_toml(path, content)
        if profile.get("name") != role:
            raise PolicyError(f"{path}: expected role name {role!r}")
        model = profile.get("model")
        ref = model_ref(model)
        if model not in policy["models"]:
            issues.append(f"{role} uses {model}, which is not in the current policy")
        roles[role] = (path, content, profile)
        fields.append(ModelField(f"role.{role}.model", path, model, ref))

    default_role = roles.get("default")
    if default_role is None:
        raise PolicyError("managed policy is missing the default role")
    role_default_model = default_role[2].get("model")
    if default_model != role_default_model:
        issues.append(
            "config default_subagent_model does not match the default role model "
            f"({default_model} != {role_default_model})"
        )

    return InstallationState(
        codex_home=codex_home,
        policy_path=policy_path,
        policy_text=policy_text,
        policy=policy,
        config_path=config_path,
        config_text=config_text,
        config=config,
        roles=roles,
        fields=fields,
        issues=issues,
    )


def replace_string_assignment(
    path: Path,
    content: str,
    key_path: tuple[str, ...],
    value: str,
) -> str:
    original = parse_toml(path, content)
    desired = copy.deepcopy(original)
    current: Any = desired
    for key in key_path[:-1]:
        if not isinstance(current, dict) or key not in current:
            raise PolicyError(f"{path}: missing {'.'.join(key_path)}")
        current = current[key]
    final_key = key_path[-1]
    if not isinstance(current, dict) or final_key not in current:
        raise PolicyError(f"{path}: missing {'.'.join(key_path)}")
    if current[final_key] == value:
        return content
    current[final_key] = value

    for match in STRING_ASSIGNMENT_RE.finditer(content):
        raw_key = match.group("key")
        key = raw_key[1:-1] if raw_key[:1] in {"\"", "'"} else raw_key
        if key != final_key:
            continue
        candidate = content[: match.start("value")] + f'"{value}"' + content[match.end("value") :]
        try:
            if tomllib.loads(candidate) == desired:
                return candidate
        except tomllib.TOMLDecodeError:
            continue
    raise PolicyError(f"{path}: cannot safely edit {'.'.join(key_path)}")


def replace_policy_model_headers(
    path: Path,
    content: str,
    policy: dict[str, Any],
    target: str,
) -> tuple[str, tuple[str, ...]]:
    desired = copy.deepcopy(policy)
    desired_models: dict[str, Any] = {}
    replacements: list[tuple[str, str]] = []
    details: list[str] = []
    for current_model, spec in policy["models"].items():
        alias = str(spec["alias"]).strip().lower()
        target_model = MODEL_PROFILES[target][alias]
        if target_model in desired_models:
            raise PolicyError(f"target policy would duplicate model {target_model}")
        desired_models[target_model] = copy.deepcopy(spec)
        if current_model != target_model:
            replacements.append((current_model, target_model))
            details.append(f"models.{alias}: {current_model} -> {target_model}")
    desired["models"] = desired_models

    candidate = content
    for old, new in replacements:
        patterns = (
            (
                re.compile(rf'(?m)^(?P<prefix>[ \t]*\[models\.)"{re.escape(old)}"(?P<suffix>\][ \t]*(?:#.*)?)$'),
                rf'\g<prefix>"{new}"\g<suffix>',
            ),
            (
                re.compile(rf"(?m)^(?P<prefix>[ \t]*\[models\.)'{re.escape(old)}'(?P<suffix>\][ \t]*(?:#.*)?)$"),
                rf"\g<prefix>'{new}'\g<suffix>",
            ),
            (
                re.compile(rf"(?m)^(?P<prefix>[ \t]*\[models\.){re.escape(old)}(?P<suffix>\][ \t]*(?:#.*)?)$"),
                rf"\g<prefix>{new}\g<suffix>",
            ),
        )
        replaced = False
        for pattern, replacement in patterns:
            candidate, count = pattern.subn(replacement, candidate, count=1)
            if count:
                replaced = True
                break
        if not replaced:
            raise PolicyError(f"{path}: cannot safely edit model table {old}")

    parsed = parse_toml(path, candidate)
    if parsed != desired:
        raise PolicyError(f"{path}: model table edit changed unexpected policy data")
    validate_policy(parsed)
    return candidate, tuple(details)


def build_changes(state: InstallationState, target: str) -> list[Change]:
    changes: list[Change] = []

    policy_after, policy_details = replace_policy_model_headers(
        state.policy_path, state.policy_text, state.policy, target
    )
    if policy_after != state.policy_text:
        changes.append(
            Change(state.policy_path, state.policy_text, policy_after, policy_details)
        )

    config_ref = model_ref(state.config["agents"]["default_subagent_model"])
    config_model = MODEL_PROFILES[target][config_ref.family]
    config_after = replace_string_assignment(
        state.config_path,
        state.config_text,
        ("agents", "default_subagent_model"),
        config_model,
    )
    if config_after != state.config_text:
        changes.append(
            Change(
                state.config_path,
                state.config_text,
                config_after,
                (
                    "agents.default_subagent_model: "
                    f"{state.config['agents']['default_subagent_model']} -> {config_model}",
                ),
            )
        )

    for role, (path, content, profile) in state.roles.items():
        current_model = profile["model"]
        target_model = MODEL_PROFILES[target][model_ref(current_model).family]
        after = replace_string_assignment(path, content, ("model",), target_model)
        if after != content:
            changes.append(
                Change(
                    path,
                    content,
                    after,
                    (f"role.{role}.model: {current_model} -> {target_model}",),
                )
            )
    return changes


def print_status(state: InstallationState) -> None:
    if state.profile is not None:
        print(f"Profile: {profile_label(state.profile)} (consistent)")
    else:
        profiles = ", ".join(sorted(profile_label(item) for item in state.profiles))
        print(f"Profile: mixed ({profiles})")
    for field in state.fields:
        print(f"  {field.label}: {field.model}")
    for issue in state.issues:
        print(f"  WARNING: {issue}")


def print_plan(state: InstallationState, target: str, changes: list[Change]) -> None:
    current = profile_label(state.profile) if state.profile is not None else "mixed"
    print(f"Plan: {current} -> {profile_label(target)}; {len(changes)} files")
    if state.profile is None:
        print("WARNING: installed model fields are not one consistent profile.")
    for issue in state.issues:
        print(f"WARNING: {issue}")
    for change in changes:
        print(f"  {change.path}")
        for detail in change.details:
            print(f"    {detail}")


def confirm_changes() -> bool:
    try:
        answer = input("Apply these model changes? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def apply_changes(codex_home: Path, target: str, changes: list[Change]) -> None:
    written: list[Change] = []
    try:
        for change in changes:
            mode = change.path.stat().st_mode & 0o777
            atomic_write(change.path, change.after, mode)
            written.append(change)
        updated = inspect_installation(codex_home)
        if updated.profile != target:
            raise PolicyError(
                f"post-write validation found {updated.profile or 'a mixed profile'}"
            )
    except Exception as error:
        rollback_errors: list[str] = []
        for change in reversed(written):
            try:
                mode = change.path.stat().st_mode & 0o777
                atomic_write(change.path, change.before, mode)
            except Exception as rollback_error:
                rollback_errors.append(f"{change.path}: {rollback_error}")
        if rollback_errors:
            joined = "; ".join(rollback_errors)
            raise PolicyError(f"switch failed ({error}); rollback also failed: {joined}") from error
        raise


def command_status(args: argparse.Namespace) -> int:
    state = inspect_installation(Path(args.codex_home).expanduser())
    print_status(state)
    return 0 if state.profile is not None else 1


def switch_to_profile(
    codex_home: Path,
    target_value: str,
    *,
    dry_run: bool,
    assume_yes: bool,
) -> int:
    target = normalize_profile(target_value)
    state = inspect_installation(codex_home)
    changes = build_changes(state, target)
    if not changes:
        print_status(state)
        print("No changes: requested model profile is already active.")
        return 0

    print_plan(state, target, changes)
    if dry_run:
        print("Dry run: no files changed.")
        return 0
    if not assume_yes and not confirm_changes():
        print("Canceled: no files changed.")
        return 2

    apply_changes(codex_home, target, changes)
    print_status(inspect_installation(codex_home))
    print("PASS: managed model profile updated; all other fields were preserved")
    return 0


def command_set(args: argparse.Namespace) -> int:
    return switch_to_profile(
        Path(args.codex_home).expanduser(),
        args.profile,
        dry_run=args.dry_run,
        assume_yes=args.yes,
    )


def command_interactive(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    state = inspect_installation(codex_home)
    print_status(state)
    print("Available profiles:")
    for index, profile in enumerate(MODEL_PROFILES, start=1):
        marker = " (current)" if state.profile == profile else ""
        print(f"  {index}. {profile_label(profile)}{marker}")
    try:
        selected = input("Select target profile: ").strip()
    except EOFError:
        print("Canceled: no files changed.")
        return 2
    if selected.isdigit():
        index = int(selected) - 1
        profiles = list(MODEL_PROFILES)
        if index < 0 or index >= len(profiles):
            raise PolicyError(f"invalid profile selection: {selected}")
        selected = profiles[index]
    return switch_to_profile(
        codex_home,
        selected,
        dry_run=False,
        assume_yes=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-model",
        description="Inspect or switch model generations for managed native Codex agents.",
    )
    parser.add_argument(
        "--codex-home", default=str(default_codex_home()), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command")

    status = subparsers.add_parser("status", help="show installed managed models")
    status.set_defaults(handler=command_status)

    set_command = subparsers.add_parser("set", help="switch the managed model profile")
    set_command.add_argument("profile", help="profile such as 5.6, gpt-5.6, 6, or gpt-6")
    set_command.add_argument("--dry-run", action="store_true")
    set_command.add_argument("--yes", action="store_true", help="apply without an interactive prompt")
    set_command.set_defaults(handler=command_set)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "handler"):
        args.handler = command_interactive
    try:
        return args.handler(args)
    except (OSError, PolicyError, tomllib.TOMLDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCanceled: no further files changed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
