#!/usr/bin/env python3
"""Focused tests for the managed native-agent runtime policy."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import agent_policy


class AgentPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = agent_policy.load_policy()

    def test_current_profiles_derive_from_model_policy(self) -> None:
        profiles = agent_policy.profile_expectations(self.policy)
        self.assertEqual(profiles["explorer"]["service_tier"], "default")
        self.assertEqual(profiles["worker_xhigh"]["service_tier"], "default")

    def test_historical_service_tiers_are_time_resolved(self) -> None:
        self.assertEqual(
            agent_policy.expected_service_tier_aliases(
                self.policy, "explorer", "2026-08-12T00:00:00Z"
            ),
            {"default", "standard"},
        )
        self.assertEqual(
            agent_policy.expected_service_tier_aliases(
                self.policy, "explorer", "2026-08-12T07:00:00Z"
            ),
            {"fast", "priority"},
        )

    def test_runtime_history_is_time_resolved(self) -> None:
        self.assertEqual(
            agent_policy.expected_role_runtimes(
                self.policy, "worker_xhigh", "2026-08-03T08:54:21Z"
            ),
            (("gpt-5.6-terra", "max"),),
        )
        self.assertEqual(
            agent_policy.expected_role_runtimes(
                self.policy, "worker_xhigh", "2026-08-03T08:54:23Z"
            ),
            (("gpt-5.6-sol", "xhigh"),),
        )

    def test_runtime_expectation_keeps_expected_tier_separate_from_observation(self) -> None:
        expectation = agent_policy.runtime_expectation(
            self.policy, "explorer", "2026-08-13T13:00:00Z"
        )
        self.assertIsNotNone(expectation)
        assert expectation is not None
        self.assertEqual(expectation["model"], "gpt-5.6-luna")
        self.assertEqual(expectation["reasoning_effort"], "medium")
        self.assertEqual(expectation["service_tier"], "standard")
        self.assertEqual(expectation["service_tier_aliases"], ["default", "standard"])
        self.assertEqual(expectation["configured_sandbox_mode"], "read-only")

    def test_tier_update_is_idempotent_and_appends_history_once(self) -> None:
        unchanged, model, changed = agent_policy.update_model_tier(
            self.policy, "luna", "standard", "2026-08-14T08:00:00Z"
        )
        self.assertEqual(model, "gpt-5.6-luna")
        self.assertFalse(changed)
        self.assertEqual(unchanged, self.policy)

        updated, model, changed = agent_policy.update_model_tier(
            self.policy, "luna", "fast", "2026-08-14T08:00:00Z"
        )
        self.assertTrue(changed)
        self.assertEqual(model, "gpt-5.6-luna")
        self.assertEqual(updated["models"][model]["service_tier"], "fast")
        self.assertEqual(
            len(updated["service_tier_history"]),
            len(self.policy["service_tier_history"]) + 1,
        )

    def test_invalid_current_history_disagreement_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.policy)
        invalid["models"]["gpt-5.6-luna"]["service_tier"] = "fast"
        with self.assertRaises(agent_policy.PolicyError):
            agent_policy.validate_policy(invalid)


if __name__ == "__main__":
    unittest.main()
