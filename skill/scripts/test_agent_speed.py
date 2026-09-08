#!/usr/bin/env python3
"""Focused tests for the agent-speed controller."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import agent_policy


SCRIPT = Path(__file__).with_name("agent_speed.py")


class AgentSpeedTests(unittest.TestCase):
    def make_home(self, directory: str) -> Path:
        home = Path(directory) / ".codex"
        agents = home / "agents"
        agents.mkdir(parents=True)
        policy = agent_policy.load_policy()
        (home / agent_policy.POLICY_FILENAME).write_text(
            agent_policy.render_policy(policy), encoding="utf-8"
        )
        (home / "config.toml").write_text(
            '[features]\nfast_mode = true\n', encoding="utf-8"
        )
        source_agents = agent_policy.default_codex_home() / "agents"
        for expected in agent_policy.profile_expectations(policy).values():
            source = source_agents / expected["filename"]
            (agents / expected["filename"]).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
        return home

    def run_controller(self, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--codex-home", str(home), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(home)},
        )

    def test_status_and_validate_current_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            status = self.run_controller(home, "status")
            validate = self.run_controller(home, "validate")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("luna: standard", status.stdout)
            self.assertEqual(validate.returncode, 0, validate.stderr)

    def test_dry_run_does_not_change_policy_or_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            policy_path = home / agent_policy.POLICY_FILENAME
            before = policy_path.read_bytes()
            result = self.run_controller(home, "set", "luna", "fast", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(policy_path.read_bytes(), before)

    def test_switch_updates_policy_and_only_matching_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            sol_path = home / "agents" / "worker-xhigh.toml"
            sol_before = sol_path.read_bytes()
            result = self.run_controller(home, "set", "luna", "fast")
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = agent_policy.load_policy(home)
            self.assertEqual(
                policy["models"]["gpt-5.6-luna"]["service_tier"], "fast"
            )
            for role, expected in agent_policy.profile_expectations(policy).items():
                profile = (home / "agents" / expected["filename"]).read_text(
                    encoding="utf-8"
                )
                if expected["model"] == "gpt-5.6-luna":
                    self.assertIn('service_tier = "fast"', profile, role)
            self.assertEqual(sol_path.read_bytes(), sol_before)


if __name__ == "__main__":
    unittest.main()
