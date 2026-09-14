#!/usr/bin/env python3
"""Read installed role files and check required fields and current managed tiers."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from agent_policy import default_codex_home, load_policy, load_profiles
from agent_speed import validate_managed_configuration

def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_profiles(home: Path) -> int:
    policy = load_policy(home)
    profiles = load_profiles(policy, home)
    for role, profile in profiles.items():
        path = home / "agents" / policy["roles"][role]["filename"]
        for field in ("description", "developer_instructions"):
            value = profile.get(field)
            require(isinstance(value, str) and bool(value.strip()), f"{path}: empty {field}")
    validate_managed_configuration(home, policy)
    return len(profiles)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=default_codex_home(),
                        help="installation to inspect (default: CODEX_HOME or ~/.codex)")
    args = parser.parse_args(argv)
    home = args.codex_home.expanduser().resolve()
    try:
        count = validate_profiles(home)
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {count} managed role profiles and current service tiers checked.")
    print("Scope: static configuration only; model access, effective permissions, "
          "and delegation behavior require runtime evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
