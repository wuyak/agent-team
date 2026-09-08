#!/usr/bin/env python3
"""Regression checks for shared transcript normalization and policy boundaries."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def load_entrypoint(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_common_regression", SCRIPT_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RECORD_HOOK = load_entrypoint("record_hook")
RECORD_CLOSEOUT = load_entrypoint("record_closeout")


class RecordCommonTests(unittest.TestCase):
    def test_entrypoints_load_shared_classifier_by_file_path(self) -> None:
        self.assertEqual(
            RECORD_HOOK.normalize_coordination_operation("runtime.wait_agent"),
            RECORD_CLOSEOUT.normalize_coordination_operation("runtime.wait_agent"),
        )
        self.assertEqual(
            RECORD_HOOK.payload_dict({"payload": {"type": "event_msg"}}),
            RECORD_CLOSEOUT.payload_dict({"payload": {"type": "event_msg"}}),
        )

    def test_common_classifiers_keep_hook_and_replay_boundaries_in_sync(self) -> None:
        values = [
            ('{"message":"Wait completed.","timed_out":false}', "completed"),
            ("timeout_ms must be at least 10000", "error"),
            ('{"status":"timed_out"}', "timeout"),
            ({"status": "ok"}, "completed"),
            ({"message": "unrecognized"}, "unknown"),
        ]
        for value, expected in values:
            with self.subTest(value=value):
                self.assertEqual(RECORD_HOOK.classify_wait_outcome(value), expected)
                self.assertEqual(RECORD_CLOSEOUT.classify_wait_outcome(value), expected)

        names = (
            ("spawn_agent", True, "spawn_agent"),
            ("Agent", True, None),
            ("runtime.spawn_agent", True, "spawn_agent"),
            ("send_message", False, "send_message"),
        )
        for name, spawn_expected, operation_expected in names:
            with self.subTest(name=name):
                self.assertEqual(
                    RECORD_HOOK.is_spawn_tool(name), spawn_expected
                )
                self.assertEqual(
                    RECORD_HOOK.normalize_coordination_operation(name), operation_expected
                )
                self.assertEqual(
                    RECORD_CLOSEOUT.is_spawn_tool(name), spawn_expected
                )
                self.assertEqual(
                    RECORD_CLOSEOUT.normalize_coordination_operation(name),
                    operation_expected,
                )

        for value in (None, True, 12, 12.5, 86_400_000, 86_400_001):
            with self.subTest(duration=value):
                self.assertEqual(
                    RECORD_HOOK._wait_duration({"wait_ms": value}, ("wait_ms",)),
                    RECORD_CLOSEOUT._duration_from({"wait_ms": value}, ("wait_ms",)),
                )

        # The historical implementations cap at different points; keep this
        # adapter difference visible rather than changing stored wait metrics.
        fractional_cap = 86_400_000.9
        self.assertEqual(
            RECORD_HOOK._wait_duration({"wait_ms": fractional_cap}, ("wait_ms",)),
            86_400_000,
        )
        self.assertIsNone(
            RECORD_CLOSEOUT._duration_from(
                {"wait_ms": fractional_cap}, ("wait_ms",)
            )
        )
        with self.assertRaises(OverflowError):
            RECORD_HOOK._wait_duration({"wait_ms": float("inf")}, ("wait_ms",))
        self.assertIsNone(
            RECORD_CLOSEOUT._duration_from(
                {"wait_ms": float("inf")}, ("wait_ms",)
            )
        )

    def test_fork_request_missing_evidence_defaults_remain_entrypoint_specific(self) -> None:
        self.assertEqual(
            RECORD_HOOK.sanitize_requested_fork_turns({}),
            (None, "omitted"),
        )
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns({}),
            (None, "not_observed"),
        )
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns({}, legacy=False),
            (None, "omitted"),
        )
        for value in ("none", "all", 3, "3", 0, True, "raw prompt"):
            with self.subTest(value=value):
                self.assertEqual(
                    RECORD_HOOK.sanitize_requested_fork_turns({"fork_turns": value}),
                    RECORD_CLOSEOUT.sanitize_requested_fork_turns(
                        {"fork_turns": value}
                    ),
                )

    def test_live_and_replay_error_and_metric_defaults_stay_distinct(self) -> None:
        error = {"detail": "value must be accepted"}
        self.assertIsNone(RECORD_HOOK.classify_tool_error(error))
        self.assertEqual(RECORD_CLOSEOUT.classify_tool_error(error), "tool-error")

        hook_metrics = RECORD_HOOK.empty_coordination_metrics()
        replay_metrics = RECORD_CLOSEOUT.empty_coordination_metrics()
        self.assertEqual((hook_metrics["observability"], hook_metrics["source"]),
                         ("unknown", "not_observed"))
        self.assertEqual((replay_metrics["observability"], replay_metrics["source"]),
                         ("legacy", "transcript-replay"))
        self.assertEqual(
            hook_metrics["operation_observability"]["wait_agent"], "not_observed"
        )
        self.assertEqual(
            replay_metrics["operation_observability"]["wait_agent"], "legacy"
        )


if __name__ == "__main__":
    unittest.main()
