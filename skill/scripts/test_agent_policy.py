#!/usr/bin/env python3
"""Managed role configuration has no independent service tiers."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_policy


def build_test_policy():
    return {
        "version": 2,
        "models": {
            "gpt-5.6-luna": {"alias": "luna"},
            "gpt-5.6-sol": {"alias": "sol"},
        },
        "roles": {name: {"filename": filename} for name, filename in (
            ("default", "default.toml"), ("worker", "worker.toml"), ("sol_xhigh", "sol-xhigh.toml"))},
    }


def make_test_home(directory):
    home = Path(directory) / "installation with spaces"
    (home / "agents").mkdir(parents=True)
    policy = build_test_policy()
    (home / "agent-team-policy.toml").write_text(agent_policy.render_policy(policy))
    (home / "config.toml").write_text(
        'service_tier = "default"\n[agents]\nenabled = true\n'
        'default_subagent_model = "gpt-5.6-luna"\n[features]\nfast_mode = true\n')
    for role, model, effort in (("default", "luna", "high"), ("worker", "luna", "xhigh"),
                                ("sol_xhigh", "sol", "xhigh")):
        (home / "agents" / policy["roles"][role]["filename"]).write_text(
            f'name = "{role}"\nmodel = "gpt-5.6-{model}"\nmodel_reasoning_effort = "{effort}"\n'
            'description = "A useful role"\n'
            'developer_instructions = "Complete the assigned task."\n')
    return home


class AgentPolicyTests(unittest.TestCase):
    def test_policy_roundtrip_without_tiers(self):
        policy = build_test_policy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.toml'
            path.write_text(agent_policy.render_policy(policy))
            self.assertEqual(agent_policy.load_policy(explicit_path=path), policy)
            self.assertNotIn('service_tier', path.read_text())

    def test_model_and_role_tiers_ambiguous_alias_and_unsafe_filename_rejected(self):
        for change in ('model_tier', 'role_tier', 'global_tier', 'alias', 'filename'):
            with self.subTest(change=change):
                policy = copy.deepcopy(build_test_policy())
                if change == 'model_tier': policy['models']['gpt-5.6-luna']['service_tier'] = 'fast'
                if change == 'role_tier': policy['roles']['worker']['service_tier'] = 'fast'
                if change == 'global_tier': policy['service_tier'] = 'fast'
                if change == 'alias': policy['models']['gpt-5.6-sol']['alias'] = 'luna'
                if change == 'filename': policy['roles']['worker']['filename'] = '../worker.toml'
                with self.assertRaises(agent_policy.PolicyError): agent_policy.validate_policy(policy)

    def test_profiles_preserve_role_settings_and_reject_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            policy = agent_policy.load_policy(home)
            path = home / 'agents/worker.toml'
            path.write_text(path.read_text().replace('"xhigh"', '"high"'))
            self.assertEqual(agent_policy.load_profiles(policy, home)['worker']['model_reasoning_effort'], 'high')
            path.write_text(path.read_text() + 'service_tier = "fast"\n')
            with self.assertRaisesRegex(agent_policy.PolicyError, 'remove role service_tier'):
                agent_policy.load_profiles(policy, home)


if __name__ == '__main__':
    unittest.main()
