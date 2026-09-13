"""Check only the inputs needed to load managed roles and inspect their tiers."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_agent_team as validator
from test_agent_policy import make_test_home


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = make_test_home(self.temp.name)
        self.role = self.home / 'agents/worker.toml'
        self.original = self.role.read_text()

    def run_check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = validator.main(['--codex-home', str(self.home)])
        return code, out.getvalue(), err.getvalue()

    def test_current_roles_do_not_depend_on_history_recorders_or_unmanaged_files(self):
        (self.home / 'config.toml').unlink()
        (self.home / 'hooks.json').write_text('{broken recorder json')
        (self.home / 'agents/unmanaged.toml').write_text('invalid = [')
        self.role.write_text(self.original.replace('"max"', '"high"') + 'sandbox_mode = "read-only"\n')
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        code, out, err = self.run_check()
        self.assertEqual(code, 0, err)
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_unreadable_or_incomplete_managed_role_is_reported(self):
        for content in ['invalid = [', self.original.replace('Complete the assigned task.', ''),
                        self.original.replace('name = "worker"', 'name = "other"')]:
            self.role.write_text(content)
            code, _, err = self.run_check()
            self.assertEqual(code, 1)
            self.assertNotIn('Traceback', err)
        self.role.unlink()
        self.assertEqual(self.run_check()[0], 1)

    def test_managed_tier_drift_is_reported(self):
        self.role.write_text(self.original.replace('service_tier = "default"', 'service_tier = "fast"'))
        self.assertEqual(self.run_check()[0], 1)


if __name__ == '__main__':
    unittest.main()
