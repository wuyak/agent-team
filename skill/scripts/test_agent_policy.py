#!/usr/bin/env python3
"""Current settings work without runtime records or duplicated role settings."""
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
        "version": 1,
        "models": {
            "gpt-6-luna": {"alias": "luna", "service_tier": "standard"},
            "gpt-6-sol": {"alias": "sol", "service_tier": "standard"},
        },
        "roles": {name: {"filename": filename} for name, filename in (
            ("default", "default.toml"), ("worker", "worker.toml"), ("sol_xhigh", "sol-xhigh.toml"))},
    }


def make_test_home(directory):
    home = Path(directory) / "installation with spaces"
    (home / "agents").mkdir(parents=True)
    policy = build_test_policy()
    (home / "agent-team-policy.toml").write_text(agent_policy.render_policy(policy))
    (home / "config.toml").write_text('[agents]\nenabled = true\n[features]\nfast_mode = true\n')
    for role, model, effort in (("default", "luna", "xhigh"), ("worker", "luna", "max"),
                                ("sol_xhigh", "sol", "xhigh")):
        (home / "agents" / policy["roles"][role]["filename"]).write_text(
            f'name = "{role}"\nmodel = "gpt-6-{model}"\nmodel_reasoning_effort = "{effort}"\n'
            'service_tier = "default"\ndescription = "A useful role"\n'
            'developer_instructions = "Complete the assigned task."\n')
    return home


class AgentPolicyTests(unittest.TestCase):
    def test_roundtrip_and_tier_change_need_only_current_settings(self):
        policy = build_test_policy()
        updated, model, changed = agent_policy.update_model_tier(policy, 'LUNA', 'fast')
        self.assertTrue(changed)
        self.assertEqual(model, 'gpt-6-luna')
        self.assertEqual(policy['models'][model]['service_tier'], 'standard')
        self.assertEqual(set(updated), {'version', 'models', 'roles'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.toml'
            path.write_text(agent_policy.render_policy(updated))
            self.assertEqual(agent_policy.load_policy(explicit_path=path), updated)
        self.assertFalse(agent_policy.update_model_tier(updated, model, 'fast')[2])

    def test_bad_tier_ambiguous_alias_and_unsafe_filename_are_rejected(self):
        for change in ('tier', 'alias', 'filename'):
            with self.subTest(change=change):
                policy = copy.deepcopy(build_test_policy())
                if change == 'tier': policy['models']['gpt-6-luna']['service_tier'] = 'invalid'
                if change == 'alias': policy['models']['gpt-6-sol']['alias'] = 'luna'
                if change == 'filename': policy['roles']['worker']['filename'] = '../worker.toml'
                with self.assertRaises(agent_policy.PolicyError): agent_policy.validate_policy(policy)

    def test_profiles_are_read_from_role_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = make_test_home(directory)
            policy = agent_policy.load_policy(home)
            path = home / 'agents/worker.toml'
            path.write_text(path.read_text().replace('"max"', '"high"'))
            self.assertEqual(agent_policy.load_profiles(policy, home)['worker']['model_reasoning_effort'], 'high')
            self.assertEqual(policy['roles']['worker'], {'filename': 'worker.toml'})


if __name__ == '__main__':
    unittest.main()
