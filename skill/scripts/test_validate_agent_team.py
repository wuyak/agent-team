"""Behavior checks for the installed configuration validator; no live Hooks run."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_agent_team as validator
from agent_policy import render_policy


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "installation with spaces"
        (self.home / "agents").mkdir(parents=True)
        self.policy = {
            "version": 1,
            "models": {"test-model": {"alias": "test", "service_tier": "standard"}},
            "roles": {"worker": {"filename": "worker.toml", "model": "test-model",
                                  "reasoning_effort": "high", "sandbox_mode": "workspace-write"}},
            "service_tier_history": [{"effective_at": "2026-01-01T00:00:00Z", "model": "test-model",
                                      "service_tier": "standard"}],
            "role_runtime_history": [{"effective_at": "2026-01-01T00:00:00Z", "role": "worker",
                                      "model": "test-model", "reasoning_effort": "high"}],
        }
        (self.home / "agent-team-policy.toml").write_text(render_policy(self.policy))
        (self.home / "config.toml").write_text('[agents]\nenabled = true\n')
        self.role = self.home / "agents/worker.toml"
        self.original = ('name = "worker"\nmodel = "test-model"\nmodel_reasoning_effort = "high"\n'
                         'sandbox_mode = "workspace-write"\nservice_tier = "default"\n'
                         'description = "Any clear description"\ndeveloper_instructions = "Any useful instructions"\n')
        self.role.write_text(self.original)

    def run_check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = validator.main(["--codex-home", str(self.home)])
        return code, out.getvalue(), err.getvalue()

    def install_hooks(self):
        recorder = self.home / "skills/agent-team/scripts/record_hook.py"
        recorder.parent.mkdir(parents=True, exist_ok=True)
        recorder.write_text('raise RuntimeError("Validator must not execute the recorder")\n')
        hooks = {}
        for event, operation in validator.EVENTS.items():
            entry = {"hooks": [{"type": "command", "command": shlex.join([sys.executable, str(recorder), operation])}]}
            if event in ("PreToolUse", "PostToolUse"):
                entry["matcher"] = "^(spawn_agent|followup_task|send_message|interrupt_agent)$"
            hooks[event] = [entry]
        return {"hooks": hooks}

    def write_hooks(self, hooks):
        (self.home / "hooks.json").write_text(json.dumps(hooks))

    def test_default_install_needs_no_catalog_hooks_or_prose_templates(self):
        self.role.write_text(self.original.replace('Any useful instructions', 'DECISION is ordinary prose'))
        self.assertEqual(self.run_check()[0], 0)

    def test_policy_drift_is_rejected(self):
        for old, new in [('"test-model"', '"wrong-model"'), ('"high"', '"low"'),
                         ('"workspace-write"', '"read-only"'), ('"default"', '"priority"'),
                         ('name = "worker"', 'name = "other"')]:
            with self.subTest(new=new):
                self.role.write_text(self.original.replace(old, new))
                self.assertEqual(self.run_check()[0], 1)

    def test_missing_malformed_or_empty_profiles_are_rejected(self):
        self.role.unlink()
        self.assertEqual(self.run_check()[0], 1)
        for content in ['not valid toml = [', self.original.replace('Any useful instructions', ''),
                        self.original + 'fork_turns = "none"\n']:
            self.role.write_text(content)
            self.assertEqual(self.run_check()[0], 1)

    def test_history_disagreement_is_rejected(self):
        p = self.home / "agent-team-policy.toml"
        p.write_text(p.read_text().replace('reasoning_effort = "high"', 'reasoning_effort = "low"', 1))
        self.assertEqual(self.run_check()[0], 1)

    def test_fast_requires_matching_feature_and_profiles(self):
        self.policy['models']['test-model']['service_tier'] = 'fast'
        self.policy['service_tier_history'][0]['service_tier'] = 'fast'
        (self.home / 'agent-team-policy.toml').write_text(render_policy(self.policy))
        self.role.write_text(self.original.replace('service_tier = "default"', 'service_tier = "fast"'))
        self.assertEqual(self.run_check()[0], 1)
        (self.home / 'config.toml').write_text('[features]\nfast_mode = true\n')
        self.assertEqual(self.run_check()[0], 0)

    def test_unrelated_hooks_can_coexist_or_be_used_alone(self):
        other = {'hooks': [{'type': 'command', 'command': 'another-program --arg'}]}
        self.write_hooks({'hooks': {'Stop': [other]}})
        self.assertEqual(self.run_check()[0], 0)
        hooks = self.install_hooks()
        hooks['hooks']['Stop'].append(other)
        self.write_hooks(hooks)
        self.assertEqual(self.run_check()[0], 0)

    def test_partial_duplicate_and_overbroad_recording_are_rejected(self):
        original = self.install_hooks()
        for mutation in ['partial', 'duplicate', 'overbroad', 'invalid_regex', 'wrong_operation']:
            with self.subTest(mutation=mutation):
                hooks = copy.deepcopy(original)
                if mutation == 'partial': del hooks['hooks']['SubagentStop']
                elif mutation == 'duplicate': hooks['hooks']['Stop'] *= 2
                elif mutation == 'overbroad': hooks['hooks']['PreToolUse'][0]['matcher'] = '.*'
                elif mutation == 'invalid_regex': hooks['hooks']['PreToolUse'][0]['matcher'] = '['
                else: hooks['hooks']['Stop'][0]['hooks'][0]['command'] += ' extra'
                self.write_hooks(hooks)
                self.assertEqual(self.run_check()[0], 1)

    def test_missing_recorder_and_interpreter_are_rejected(self):
        hooks = self.install_hooks()
        hooks['hooks']['Stop'][0]['hooks'][0]['command'] = shlex.join([
            '/no/such/python', str(self.home / 'skills/agent-team/scripts/record_hook.py'), 'auto-finalize'])
        self.write_hooks(hooks)
        self.assertEqual(self.run_check()[0], 1)
        self.write_hooks(self.install_hooks())
        (self.home / 'skills/agent-team/scripts/record_hook.py').unlink()
        self.assertEqual(self.run_check()[0], 1)

    def test_bad_config_and_json_report_errors_without_tracebacks(self):
        (self.home / 'config.toml').write_text('invalid = [')
        self.assertEqual(self.run_check()[0], 1)
        (self.home / 'config.toml').write_text('[agents]\nenabled = false\n')
        self.assertEqual(self.run_check()[0], 1)
        (self.home / 'config.toml').write_text('')
        (self.home / 'hooks.json').write_text('{')
        code, _, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertNotIn('Traceback', err)

    def test_cli_is_read_only_and_does_not_execute_hooks(self):
        self.write_hooks(self.install_hooks())
        before = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, str(Path(validator.__file__)), '--codex-home', str(self.home)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertIn('static configuration only', result.stdout)


if __name__ == '__main__':
    unittest.main()
