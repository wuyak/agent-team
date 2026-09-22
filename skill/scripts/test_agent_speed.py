#!/usr/bin/env python3
"""Focused tests for the agent-speed controller."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import agent_policy
import agent_speed
from test_agent_policy import make_test_home


SCRIPT = Path(__file__).with_name("agent_speed.py")


class AgentSpeedTests(unittest.TestCase):
    def make_home(self, directory: str) -> Path:
        return make_test_home(directory)

    def run_controller(self, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--codex-home", str(home), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(home)},
        )

    def test_status_checks_current_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            status = self.run_controller(home, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("luna: standard", status.stdout)

    def test_fast_uses_native_default_but_respects_explicit_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            config = home / 'config.toml'
            config.write_text('[agents]\nenabled = true\n')
            self.assertEqual(self.run_controller(home, 'set', 'luna', 'fast', '--dry-run').returncode, 0)
            config.write_text('[features]\nfast_mode = false\n')
            result = self.run_controller(home, 'set', 'luna', 'fast', '--dry-run')
            self.assertEqual(result.returncode, 1)
            self.assertIn('explicitly disables', result.stderr)

    def test_dry_run_does_not_change_policy_or_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            policy_path = home / agent_policy.POLICY_FILENAME
            before = policy_path.read_bytes()
            profile_paths = sorted((home / "agents").glob("*.toml"))
            profiles_before = {
                path: path.read_bytes() for path in profile_paths
            }
            result = self.run_controller(home, "set", "luna", "fast", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(policy_path.read_bytes(), before)
            self.assertEqual(
                {path: path.read_bytes() for path in profile_paths}, profiles_before
            )

    def test_switch_updates_policy_and_only_matching_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            sol_path = home / "agents" / "sol-xhigh.toml"
            sol_before = sol_path.read_bytes()
            result = self.run_controller(home, "set", "luna", "fast")
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = agent_policy.load_policy(home)
            self.assertEqual(
                policy["models"]["gpt-6-luna"]["service_tier"], "fast"
            )
            for role, profile_settings in agent_policy.load_profiles(policy, home).items():
                profile = (home / "agents" / policy["roles"][role]["filename"]).read_text(
                    encoding="utf-8"
                )
                if profile_settings["model"] == "gpt-6-luna":
                    self.assertIn('service_tier = "fast"', profile, role)
            self.assertEqual(sol_path.read_bytes(), sol_before)

    def test_switch_preserves_toml_comments_and_repairs_selected_tier_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            policy_path = home / 'agent-team-policy.toml'
            policy_path.write_text('# local policy note\n' + policy_path.read_text())
            policy_before = policy_path.read_bytes()
            unchanged_role = home / 'agents/default.toml'
            unchanged_mtime = unchanged_role.stat().st_mtime_ns
            path = home / 'agents/worker.toml'
            path.write_text(path.read_text().replace('service_tier = "default"',
                                                     "service_tier = 'fast' # keep this comment"))
            result = self.run_controller(home, 'set', 'luna', 'standard')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('service_tier = "default" # keep this comment', path.read_text())
            self.assertEqual(policy_path.read_bytes(), policy_before)
            self.assertEqual(unchanged_role.stat().st_mtime_ns, unchanged_mtime)

    def test_multiline_string_tier_can_be_left_unchanged_or_switched(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            for value in ('"""default"""', "'''default'''", '"""\ndefault"""'):
                with self.subTest(value=value):
                    path = home / 'agents/worker.toml'
                    original = path.read_text()
                    path.write_text(original.replace('service_tier = "default"', 'service_tier = ' + value))
                    before = path.read_bytes()
                    result = self.run_controller(home, 'set', 'luna', 'standard')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(path.read_bytes(), before)
                    result = self.run_controller(home, 'set', 'luna', 'fast')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.run_controller(home, 'set', 'luna', 'standard').returncode, 0)

    def test_failed_switch_restores_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(directory)
            before = {p: p.read_bytes() for p in home.rglob('*') if p.is_file()}
            original_write = agent_speed.atomic_write
            failed = False

            def fail_once(path, content, mode=None):
                nonlocal failed
                if path.name == 'worker.toml' and not failed:
                    failed = True
                    raise OSError('simulated write failure')
                return original_write(path, content, mode)

            args = argparse.Namespace(codex_home=home, model='luna', tier='fast', dry_run=False)
            with mock.patch.object(agent_speed, 'atomic_write', side_effect=fail_once):
                with self.assertRaises(OSError): agent_speed.command_set(args)
            self.assertTrue(failed)
            self.assertEqual(before, {p: p.read_bytes() for p in home.rglob('*') if p.is_file()})


if __name__ == "__main__":
    unittest.main()
