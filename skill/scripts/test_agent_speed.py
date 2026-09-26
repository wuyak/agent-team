#!/usr/bin/env python3
"""Global tier edits preserve all role settings and unrelated global configuration."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import agent_speed
from test_agent_policy import make_test_home


class AgentSpeedTests(unittest.TestCase):
    def run_controller(self, home, *arguments):
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT_DIR / 'agent_speed.py'),
             '--codex-home', str(home), *arguments],
            text=True, capture_output=True, check=False,
        )

    def snapshot(self, home):
        return {p.relative_to(home): p.read_bytes() for p in home.rglob('*') if p.is_file()}

    def test_switch_changes_only_global_tier_and_preserves_comments_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            config = home / 'config.toml'
            config.write_text(config.read_text().replace('service_tier = "default"',
                              "service_tier = 'default' # selected default"))
            config.chmod(0o600)
            before = self.snapshot(home)
            original = tomllib.loads(config.read_text())
            result = self.run_controller(home, 'set', 'fast')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(tomllib.loads(config.read_text()), {**original, 'service_tier': 'fast'})
            self.assertIn('# selected default', config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual({k:v for k,v in self.snapshot(home).items() if k != Path('config.toml')},
                             {k:v for k,v in before.items() if k != Path('config.toml')})
            self.assertIn('No live task settings changed', result.stdout)
            self.assertEqual(self.run_controller(home, 'set', 'standard').returncode, 0)
            self.assertEqual(tomllib.loads(config.read_text()), original)

    def test_dry_run_noop_and_old_model_scoped_command_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            before = self.snapshot(home)
            config = home / 'config.toml';mtime = config.stat().st_mtime_ns
            self.assertEqual(self.run_controller(home, 'set', 'fast', '--dry-run').returncode, 0)
            self.assertEqual(self.run_controller(home, 'set', 'standard').returncode, 0)
            self.assertNotEqual(self.run_controller(home, 'set', 'luna', 'fast').returncode, 0)
            self.assertEqual(self.snapshot(home), before)
            self.assertEqual(config.stat().st_mtime_ns, mtime)

    def test_feature_switch_is_not_tier_selection_and_explicit_disable_blocks_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory);config = home / 'config.toml'
            result = self.run_controller(home, 'status')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Global default: standard', result.stdout)
            config.write_text('[features]\nfast_mode = false\n')
            before = config.read_bytes()
            result = self.run_controller(home, 'set', 'fast')
            self.assertEqual(result.returncode, 1)
            self.assertIn('explicitly disables', result.stderr)
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(self.run_controller(home, 'set', 'standard').returncode, 0)

    def test_missing_key_and_file_and_native_priority_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory);config = home / 'config.toml'
            self.assertIn('unset', self.run_controller(home, 'status').stdout)
            self.assertFalse(config.exists())
            self.assertEqual(self.run_controller(home, 'set', 'fast').returncode, 0)
            self.assertEqual(tomllib.loads(config.read_text()), {'service_tier': 'fast'})
            config.write_text('service_tier = "priority"\n')
            before = config.read_bytes()
            self.assertIn('Global default: fast', self.run_controller(home, 'status').stdout)
            self.assertEqual(self.run_controller(home, 'set', 'fast').returncode, 0)
            self.assertEqual(config.read_bytes(), before)
            config.write_text('[features]\nfast_mode = true\n')
            self.assertEqual(self.run_controller(home, 'set', 'fast').returncode, 0)
            self.assertEqual(tomllib.loads(config.read_text()),
                             {'service_tier': 'fast', 'features': {'fast_mode': True}})

    def test_only_top_level_assignment_is_changed_with_lookalikes_and_multiline_values(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory);path = home / 'config.toml'
            for value in ('"""default"""', "'''default'''", '"""\ndefault"""'):
                content = ('developer_instructions = """\nservice_tier = \'default\'\n"""\n'
                           + f"'service_tier' = {value}\n"
                           + '[profiles.example]\nservice_tier = "default"\n')
                path.write_text(content)
                original = tomllib.loads(content)
                result = self.run_controller(home, 'set', 'fast')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(tomllib.loads(path.read_text()), {**original, 'service_tier': 'fast'})

    def test_malformed_global_config_does_not_get_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory);path = home / 'config.toml';path.write_text('broken = [')
            self.assertEqual(self.run_controller(home, 'set', 'fast').returncode, 1)
            self.assertEqual(path.read_text(), 'broken = [')

    def test_failed_verification_restores_existing_file_or_removes_new_file(self):
        for existed in (False, True):
            with self.subTest(existed=existed), tempfile.TemporaryDirectory() as directory:
                home = Path(directory);path = home / 'config.toml'
                if existed: path.write_text('service_tier = "default"\n')
                before = self.snapshot(home)
                real_read = agent_speed.read_global_config
                reads = 0
                def read_or_fail(home):
                    nonlocal reads
                    reads += 1
                    if reads == 2: raise OSError('simulated verification failure')
                    return real_read(home)
                args = argparse.Namespace(codex_home=home, tier='fast', dry_run=False)
                with mock.patch.object(agent_speed, 'read_global_config', side_effect=read_or_fail):
                    with self.assertRaises(OSError): agent_speed.command_set(args)
                self.assertEqual(self.snapshot(home), before)

    def test_model_switch_rejects_role_tier_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            role = home / 'agents/worker.toml'
            role.write_text(role.read_text() + 'service_tier = "fast"\n')
            before = self.snapshot(home)
            result = subprocess.run([sys.executable, '-B', str(SCRIPT_DIR / 'agent_model.py'),
                                     '--codex-home', str(home), 'set', '6', '--yes'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('remove role service_tier', result.stderr)
            self.assertEqual(self.snapshot(home), before)

    def test_invalid_tier_type_reports_error_without_traceback_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory);path = home / 'config.toml'
            path.write_text('service_tier = []\n')
            for args in [('status',), ('set', 'fast')]:
                result = self.run_controller(home, *args)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(path.read_text(), 'service_tier = []\n')

    def test_model_switch_preserves_global_tier(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            self.assertEqual(self.run_controller(home, 'set', 'fast').returncode, 0)
            result = subprocess.run([sys.executable, '-B', str(SCRIPT_DIR / 'agent_model.py'),
                                     '--codex-home', str(home), 'set', '6', '--yes'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = tomllib.loads((home / 'config.toml').read_text())
            self.assertEqual(config['service_tier'], 'fast')
            self.assertEqual(config['agents']['default_subagent_model'], 'gpt-6-luna')
            for path in (home / 'agents').glob('*.toml'):
                self.assertNotIn('service_tier', tomllib.loads(path.read_text()))


if __name__ == '__main__':
    unittest.main()
