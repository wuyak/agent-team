#!/usr/bin/env python3
"""Read-only checks for installed Agent Team profiles, policy, and optional Hooks.

Checks declared configuration, not prompt quality, model access, or runtime behavior.
No Codex process or configured Hook is executed; no files or history are rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tomllib

sys.dont_write_bytecode = True
from agent_policy import PolicyError, load_policy, profile_expectations
from agent_speed import validate_managed_configuration

EVENTS = {
    "PreToolUse": "ingest",
    "PostToolUse": "ingest",
    "SubagentStart": "ingest",
    "SubagentStop": "ingest",
    "Stop": "auto-finalize",
}
RECORDED = ("spawn_agent", "followup_task", "send_message", "interrupt_agent")
EXCLUDED = ("wait_agent", "list_agents", "close_agent", "resume_agent", "send_input")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def validate_profiles(home: Path) -> int:
    config = read_toml(home / "config.toml")
    policy = load_policy(home)  # Includes ordering and agreement of recorded policy history.
    expected = profile_expectations(policy)
    validate_managed_configuration(home, policy)  # Reuse tier and Fast checks.
    agents = config.get("agents", {})
    require(isinstance(agents, dict), "config [agents] must be a table")
    require(agents.get("enabled") is not False, "config [agents].enabled is false")
    for role, settings in expected.items():
        path = home / "agents" / settings["filename"]
        profile = read_toml(path)
        require(profile.get("name") == role, f"{path}: expected role name {role!r}")
        for field in ("description", "developer_instructions"):
            value = profile.get(field)
            require(isinstance(value, str) and bool(value.strip()), f"{path}: empty {field}")
        for field, key in (("model", "model"), ("model_reasoning_effort", "reasoning_effort"),
                           ("sandbox_mode", "sandbox_mode")):
            require(profile.get(field) == settings[key],
                    f"{path}: {field}={profile.get(field)!r}; policy expects {settings[key]!r}")
        require("fork_turns" not in profile,
                f"{path}: fork_turns belongs in the spawn call, not a role profile")
    return len(expected)


def validate_hooks(home: Path) -> str:
    path = home / "hooks.json"
    if not path.exists():
        return "recording Hooks not configured (optional)"
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict) and isinstance(payload.get("hooks"), dict),
            f"{path}: expected a hooks object")
    recorder = home / "skills" / "agent-team" / "scripts" / "record_hook.py"
    owned: dict[str, list[dict]] = {}
    for event, entries in payload["hooks"].items():
        require(isinstance(entries, list), f"hooks.{event}: expected an array")
        for entry in entries:
            require(isinstance(entry, dict) and isinstance(entry.get("hooks"), list),
                    f"hooks.{event}: expected an entry with a hooks array")
            for hook in entry["hooks"]:
                require(isinstance(hook, dict), f"hooks.{event}: expected a hook object")
                command = hook.get("command", "")
                if not isinstance(command, str) or "record_hook.py" not in command:
                    continue  # Other Hooks are outside this checker's ownership.
                require(event in EVENTS, f"Agent Team recorder attached to unsupported event {event}")
                require(hook.get("type") == "command", f"hooks.{event}: recorder must be a command")
                args = shlex.split(command)
                require(len(args) == 3 and args[2] == EVENTS[event],
                        f"hooks.{event}: expected direct '<python> <record_hook.py> {EVENTS[event]}' command")
                executable = shutil.which(os.path.expanduser(args[0]))
                require(executable is not None, f"hooks.{event}: interpreter not executable: {args[0]}")
                target = Path(args[1]).expanduser()
                require(target.is_absolute() and target.resolve() == recorder.resolve() and target.is_file(),
                        f"hooks.{event}: recorder must exist at {recorder}")
                owned.setdefault(event, []).append(entry)
    if not owned:
        return "recording Hooks not configured (optional); other Hooks preserved"
    patterns = []
    for event in EVENTS:
        require(len(owned.get(event, [])) == 1, f"Agent Team recording requires one {event} command")
        entry = owned[event][0]
        if event in ("PreToolUse", "PostToolUse"):
            pattern = entry.get("matcher")
            require(isinstance(pattern, str) and bool(pattern), f"hooks.{event}: matcher is missing")
            matcher = re.compile(pattern)
            require(all(matcher.fullmatch(op) for op in RECORDED),
                    f"hooks.{event}: matcher misses a recorded operation")
            require(not any(matcher.fullmatch(op) for op in EXCLUDED),
                    f"hooks.{event}: matcher includes an unrecorded operation")
            patterns.append(pattern)
        else:
            require(entry.get("matcher") is None, f"hooks.{event}: recorder should receive every event")
    require(patterns[0] == patterns[1], "Agent Team PreToolUse and PostToolUse matchers differ")
    return "recording Hook commands, targets, and event matchers are consistent"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
                        help="installation to inspect (default: CODEX_HOME or ~/.codex)")
    args = parser.parse_args(argv)
    home = args.codex_home.expanduser().resolve()
    try:
        count = validate_profiles(home)
        hook_status = validate_hooks(home)
    except (OSError, ValueError, PolicyError, re.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {count} managed role profiles agree with policy and history; {hook_status}.")
    print("Scope: static configuration only; model access, effective permissions, Hook delivery, "
          "and delegation behavior require runtime evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
