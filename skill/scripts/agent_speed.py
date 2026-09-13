#!/usr/bin/env python3
"""Inspect or switch service tiers for managed native Codex agents."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from agent_policy import (
    PolicyError,
    canonical_tier,
    default_codex_home,
    load_policy,
    load_profiles,
    policy_path,
    policy_requires_fast_mode,
    profile_tier_value,
    read_toml as load_toml,
    render_policy,
    update_model_tier,
)


SERVICE_TIER_RE = re.compile(
    r"(?m)^[ \t]*(?:service_tier|\"service_tier\"|'service_tier')[ \t]*=[ \t]*"
    r"(\"\"\"(?:\\[\s\S]|[^\\])*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'[^'\n]*')"
)


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode if mode is not None else 0o644)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def replace_profile_tier(path: Path, expected: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyError(f"cannot read profile {path}: {error}") from error
    original = tomllib.loads(content)
    desired = {**original, "service_tier": expected}
    if "service_tier" not in original:
        return f'service_tier = "{expected}"\n' + content
    for match in SERVICE_TIER_RE.finditer(content):
        candidate = content[:match.start(1)] + f'"{expected}"' + content[match.end(1):]
        # A matching line can also occur in instructions or a nested table.
        # Accept only a replacement that changes the intended top-level value.
        try:
            if tomllib.loads(candidate) == desired:
                return candidate
        except tomllib.TOMLDecodeError:
            continue
    raise PolicyError(f"{path}: cannot safely edit the service_tier assignment")


def profile_status(codex_home: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for role, profile in load_profiles(policy, codex_home).items():
        path = codex_home / "agents" / policy["roles"][role]["filename"]
        model = profile["model"]
        expected = profile_tier_value(policy["models"][model]["service_tier"])
        actual = profile.get("service_tier")
        rows.append(
            {
                "role": role,
                "model": model,
                "expected": expected,
                "actual": actual,
                "ok": actual == expected,
                "path": path,
            }
        )
    return rows


def validate_fast_feature(codex_home: Path, policy: dict[str, Any]) -> None:
    if not policy_requires_fast_mode(policy):
        return
    config_path = codex_home / "config.toml"
    config = load_toml(config_path) if config_path.exists() else {}
    features = config.get("features")
    if isinstance(features, dict) and features.get("fast_mode") is False:
        raise PolicyError(
            "managed policy requests Fast but config.toml explicitly disables fast_mode"
        )


def validate_managed_configuration(
    codex_home: Path, policy: dict[str, Any]
) -> list[dict[str, Any]]:
    validate_fast_feature(codex_home, policy)
    rows = profile_status(codex_home, policy)
    mismatches = [row for row in rows if not row["ok"]]
    if mismatches:
        details = ", ".join(
            f"{row['role']}={row['actual']!r}, expected {row['expected']!r}"
            for row in mismatches
        )
        raise PolicyError(f"managed agent profile mismatch: {details}")
    return rows


def print_status(policy: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for model, spec in policy["models"].items():
        print(f"{spec['alias']}: {canonical_tier(spec['service_tier'])} ({model})")
        for row in rows:
            if row["model"] == model:
                marker = "ok" if row["ok"] else "mismatch"
                print(
                    f"  {row['role']}: {row['actual']} "
                    f"(expected {row['expected']}, {marker})"
                )


def command_status(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    policy = load_policy(codex_home)
    validate_fast_feature(codex_home, policy)
    rows = profile_status(codex_home, policy)
    print_status(policy, rows)
    return 1 if any(not row["ok"] for row in rows) else 0


def command_set(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser()
    policy = load_policy(codex_home)
    updated, model, changed = update_model_tier(
        policy, args.model, args.tier
    )
    touched = [
        row for row in profile_status(codex_home, updated) if row["model"] == model
    ]
    if not touched:
        raise PolicyError(f"no managed roles use {model}")

    if not changed and all(row["ok"] for row in touched):
        rows = validate_managed_configuration(codex_home, policy)
        print_status(policy, rows)
        print("No changes: requested tier is already active.")
        return 0

    rendered_profiles: list[tuple[Path, str]] = []
    for row in touched:
        if row["ok"]:
            continue
        path = row["path"]
        rendered_profiles.append(
            (path, replace_profile_tier(path, row["expected"]))
        )

    # Validate the target policy before the dry-run or any file replacement.
    validate_fast_feature(codex_home, updated)

    print(
        f"Plan: {model} -> {canonical_tier(args.tier)}; "
        f"{len(rendered_profiles)} profiles"
    )
    for path, _content in rendered_profiles:
        print(f"  {path}")
    if args.dry_run:
        print("Dry run: no files changed.")
        return 0

    policy_file = policy_path(codex_home)
    old_policy = policy_file.read_text(encoding="utf-8")
    backups = [(path, path.read_text(encoding="utf-8")) for path, _ in rendered_profiles]
    try:
        if changed:
            atomic_write(policy_file, render_policy(updated), policy_file.stat().st_mode & 0o777)
        for path, content in rendered_profiles:
            atomic_write(path, content, path.stat().st_mode & 0o777)
        rows = validate_managed_configuration(codex_home, updated)
    except Exception:
        if changed:
            atomic_write(policy_file, old_policy, policy_file.stat().st_mode & 0o777)
        for path, content in backups:
            atomic_write(path, content, path.stat().st_mode & 0o777)
        raise

    print_status(updated, rows)
    print("PASS: policy and managed profiles updated; bounded validation passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-speed",
        description="Inspect or switch service tiers for managed native Codex agents.",
    )
    parser.add_argument(
        "--codex-home", default=str(default_codex_home()), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show policy and profile tiers")
    status.set_defaults(handler=command_status)

    set_command = subparsers.add_parser("set", help="switch one managed model tier")
    set_command.add_argument("model", help="managed model slug or alias, such as luna")
    set_command.add_argument("tier", choices=("fast", "standard"))
    set_command.add_argument("--dry-run", action="store_true")
    set_command.set_defaults(handler=command_set)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, PolicyError, tomllib.TOMLDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
