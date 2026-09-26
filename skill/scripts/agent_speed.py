#!/usr/bin/env python3
"""Inspect or change the global Codex service-tier default."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from agent_policy import PolicyError, default_codex_home


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


def canonical_tier(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise PolicyError("global service_tier must be a non-empty string")
    return {"priority": "fast", "default": "standard"}.get(value, value)


def parse_config(path: Path, content: str) -> dict[str, Any]:
    try:
        return tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise PolicyError(f"cannot parse {path}: {error}") from error


def validate_global_config(config: dict[str, Any]) -> None:
    tier = config.get("service_tier")
    # Supported tiers are model-catalog dependent; validate shape, not a fixed allowlist.
    canonical_tier(tier)
    features = config.get("features", {})
    if (canonical_tier(tier) == "fast" and isinstance(features, dict)
            and features.get("fast_mode") is False):
        raise PolicyError("global tier requests Fast but config.toml explicitly disables fast_mode")


def read_global_config(home: Path) -> tuple[Path, str, dict[str, Any]]:
    path = home / "config.toml"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return path, content, parse_config(path, content)


def replace_global_tier(path: Path, content: str, expected: str) -> str:
    original = parse_config(path, content)
    desired = {**original, "service_tier": expected}
    if "service_tier" not in original:
        return f'service_tier = "{expected}"\n' + content
    for match in SERVICE_TIER_RE.finditer(content):
        candidate = content[:match.start(1)] + f'"{expected}"' + content[match.end(1):]
        # Skip lookalike keys inside instructions and nested tables.
        try:
            if tomllib.loads(candidate) == desired:
                return candidate
        except tomllib.TOMLDecodeError:
            continue
    raise PolicyError(f"{path}: cannot safely edit the global service_tier assignment")


def print_status(path: Path, config: dict[str, Any]) -> None:
    tier = config.get("service_tier")
    if tier is None:
        print("Global default: unset (uses the client's default)")
    else:
        print(f"Global default: {canonical_tier(tier)} (service_tier = {tier!r})")
    print(f"Source: {path}")
    print("Scope: saved global default only; existing tasks may retain their own tier. "
          "Subagents use their root task's tier. This is not runtime verification.")


def command_status(args: argparse.Namespace) -> int:
    path, _, config = read_global_config(Path(args.codex_home).expanduser())
    print_status(path, config)
    validate_global_config(config)
    return 0


def command_set(args: argparse.Namespace) -> int:
    home = Path(args.codex_home).expanduser()
    path, before, config = read_global_config(home)
    expected = "fast" if args.tier == "fast" else "default"
    desired = {**config, "service_tier": expected}
    validate_global_config(desired)
    if canonical_tier(config.get("service_tier")) == args.tier:
        print_status(path, config)
        print("No changes: the saved global default already matches.")
        return 0

    after = replace_global_tier(path, before, expected)
    if parse_config(path, after) != desired:
        raise PolicyError("global tier edit would change unrelated configuration")
    print(f"Plan: global service_tier -> {expected!r}; {path}")
    if args.dry_run:
        print("Dry run: no files changed.")
        return 0

    existed = path.exists()
    mode = path.stat().st_mode & 0o777 if existed else 0o600
    if (path.read_text(encoding="utf-8") if existed else "") != before:
        raise PolicyError("config.toml changed during this operation; retry against the new file")
    atomic_write(path, after, mode)
    try:
        _, _, actual = read_global_config(home)
        if actual != desired:
            raise PolicyError("saved global configuration does not match the requested change")
        validate_global_config(actual)
    except Exception:
        if existed:
            atomic_write(path, before, mode)
        else:
            path.unlink(missing_ok=True)
        raise
    print_status(path, actual)
    print("PASS: global configuration saved and read back. No live task settings changed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-speed", description=__doc__,
        epilog="Service tiers apply to the root task and its subagents, not individual models or roles.",
    )
    parser.add_argument("--codex-home", default=str(default_codex_home()), help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="show the saved global service tier")
    status.set_defaults(handler=command_status)
    set_command = subparsers.add_parser("set", help="change the global service-tier default")
    set_command.add_argument("tier", choices=("fast", "standard"))
    set_command.add_argument("--dry-run", action="store_true")
    set_command.set_defaults(handler=command_set)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (OSError, PolicyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
