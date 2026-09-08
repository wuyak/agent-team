#!/usr/bin/env python3
"""Focused tests for schema-v2 Agent Team closeout recording."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("record_closeout.py")
_CLOSEOUT_SPEC = importlib.util.spec_from_file_location("record_closeout_under_test", SCRIPT)
assert _CLOSEOUT_SPEC is not None and _CLOSEOUT_SPEC.loader is not None
RECORD_CLOSEOUT = importlib.util.module_from_spec(_CLOSEOUT_SPEC)
_CLOSEOUT_SPEC.loader.exec_module(RECORD_CLOSEOUT)
PARENT_ID = "parent-thread"
TURN_ID = "turn-with-team"
CHILD_ID = "child-session"


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def parent_events(include_success: bool = True) -> list[dict]:
    events = [
        {
            "timestamp": "2026-08-04T01:00:00Z",
            "type": "session_meta",
            "payload": {"id": PARENT_ID, "session_id": PARENT_ID},
        },
        {
            "timestamp": "2026-08-04T01:01:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": "failed-call",
                "arguments": json.dumps(
                    {
                        "agent_type": "explorer",
                        "task_name": "catalog-review",
                    }
                ),
                "internal_chat_message_metadata_passthrough": {"turn_id": TURN_ID},
            },
        },
        {
            "timestamp": "2026-08-04T01:01:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "failed-call",
                "output": (
                    "Unknown model gpt-5.6-luna for spawn_agent. "
                    "Available models: gpt-5.6-sol"
                ),
            },
        },
    ]
    if include_success:
        events.extend(
            [
                {
                    "timestamp": "2026-08-04T01:02:00Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "call_id": "success-call",
                        "arguments": json.dumps(
                            {
                                "agent_type": "explorer",
                                "task_name": "catalog-review",
                            }
                        ),
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                    },
                },
                {
                    "timestamp": "2026-08-04T01:02:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "success-call",
                        "output": json.dumps({"task_name": "/root/catalog-review"}),
                    },
                },
            ]
        )
    return events


def child_events(parent_id: str = PARENT_ID) -> list[dict]:
    return [
        {
            "timestamp": "2026-08-04T01:02:01Z",
            "type": "session_meta",
            "payload": {
                "id": CHILD_ID,
                "parent_thread_id": parent_id,
                "timestamp": "2026-08-04T01:02:01Z",
                "agent_path": "/root/catalog-review",
                "agent_nickname": "Luna Explorer",
                "agent_role": "explorer",
                "model_provider": "example-provider",
            },
        },
        {
            "timestamp": "2026-08-04T01:02:01Z",
            "type": "turn_context",
            "payload": {
                "model": "gpt-5.6-luna",
                "effort": "medium",
                "multi_agent_version": "v2",
            },
        },
        {
            "timestamp": "2026-08-04T01:02:02Z",
            "type": "event_msg",
            "payload": {"type": "task_started"},
        },
        {
            "timestamp": "2026-08-04T01:02:03Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "status": "completed",
            },
        },
        {
            "timestamp": "2026-08-04T01:02:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output"},
        },
        {
            "timestamp": "2026-08-04T01:02:05Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 30,
                        "reasoning_output_tokens": 10,
                        "total_tokens": 130,
                    }
                },
            },
        },
        {
            "timestamp": "2026-08-04T01:02:06Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "duration_ms": 4000,
                "last_agent_message": "Found the catalog mismatch.",
            },
        },
    ]


class RecordCloseoutTests(unittest.TestCase):
    def run_script(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def common_args(self, root: Path, parent: Path) -> list[str]:
        return [
            "record",
            "--root",
            str(root),
            "--parent-thread-id",
            PARENT_ID,
            "--turn-id",
            TURN_ID,
            "--project",
            "/tmp/example-project",
            "--task-signature",
            "catalog-contract-review",
            "--parent-session",
            str(parent),
            "--role-fit",
            "clear",
            "--upgrade",
            "none",
            "--team-value",
            "positive",
            "--evidence",
            "Explorer isolated the model catalog mismatch.",
        ]

    def test_wait_outcome_classifier_matches_hook_boundaries(self) -> None:
        self.assertEqual(
            RECORD_CLOSEOUT.classify_wait_outcome(
                '{"message":"Wait completed.","timed_out":false}'
            ),
            "completed",
        )
        self.assertEqual(
            RECORD_CLOSEOUT.classify_wait_outcome(
                "timeout_ms must be at least 10000"
            ),
            "error",
        )
        self.assertEqual(
            RECORD_CLOSEOUT.classify_wait_outcome('{"status":"timed_out"}'),
            "timeout",
        )

    def test_replay_fork_request_is_optional_and_child_observation_is_preserved(self) -> None:
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns({}),
            (None, "not_observed"),
        )
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns({"fork_turns": "none"}),
            ("none", "observed"),
        )
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns({"fork_turns": 4}),
            (4, "observed"),
        )
        self.assertEqual(
            RECORD_CLOSEOUT.sanitize_requested_fork_turns(
                {"fork_turns": "SECRET RAW PROMPT"}
            ),
            (None, "invalid"),
        )
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / "child.jsonl"
            write_jsonl(child, child_events())
            summary = RECORD_CLOSEOUT.summarize_agent_session(
                child,
                PARENT_ID,
                {CHILD_ID: "bounded child evidence"},
                {},
            )
            self.assertIs(summary["fork_observed"], False)
            self.assertEqual(summary["fork_observation_source"], "child-session-meta")
            self.assertIsNone(summary["forked_from_id"])

    def test_replay_fork_mismatch_anomaly_uses_only_proven_evidence(self) -> None:
        attempt = {
            "agent_id": CHILD_ID,
            "requested_fork_turns": "all",
            "requested_fork_turns_observability": "observed",
        }
        agent = {"agent_id": CHILD_ID, "fork_observed": False}
        issues = RECORD_CLOSEOUT.fork_request_observation_mismatch_messages(
            [attempt], [agent]
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("fork_turns=all", issues[0])
        unknown = RECORD_CLOSEOUT.fork_request_observation_mismatch_messages(
            [
                {
                    "agent_id": CHILD_ID,
                    "requested_fork_turns": "none",
                    "requested_fork_turns_observability": "not_observed",
                }
            ],
            [{"agent_id": CHILD_ID, "fork_observed": None}],
        )
        self.assertEqual(unknown, [])

    def test_spawn_tool_name_variants_are_shared_by_parent_and_nested_counts(self) -> None:
        self.assertTrue(RECORD_CLOSEOUT.is_spawn_tool("spawn_agent"))
        self.assertTrue(RECORD_CLOSEOUT.is_spawn_tool("Agent"))
        self.assertTrue(RECORD_CLOSEOUT.is_spawn_tool("collaboration.spawn_agent"))
        self.assertFalse(RECORD_CLOSEOUT.is_spawn_tool("send_message"))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            parent_payload = [
                {
                    "timestamp": "2026-08-01T01:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": PARENT_ID, "session_id": PARENT_ID},
                }
            ]
            for index, name in enumerate(
                ("spawn_agent", "Agent", "runtime.spawn_agent"), start=1
            ):
                call_id = f"call-{index}"
                parent_payload.extend(
                    [
                        {
                            "timestamp": f"2026-08-01T01:0{index}:00Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": name,
                                "call_id": call_id,
                                "arguments": json.dumps(
                                    {"agent_type": "explorer", "task_name": f"task-{index}"}
                                ),
                                "internal_chat_message_metadata_passthrough": {
                                    "turn_id": TURN_ID
                                },
                            },
                        },
                        {
                            "timestamp": f"2026-08-01T01:0{index}:01Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps({"task_name": f"/root/task-{index}"}),
                            },
                        },
                    ]
                )
            child_payload = child_events()
            child_payload.extend(
                [
                    {
                        "timestamp": "2026-08-04T01:02:07Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "runtime.spawn_agent",
                            "call_id": "nested-one",
                        },
                    },
                    {
                        "timestamp": "2026-08-01T01:02:08Z",
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "name": "Agent",
                            "call_id": "nested-two",
                        },
                    },
                ]
            )
            write_jsonl(parent, parent_payload)
            write_jsonl(child, child_payload)
            attempts = RECORD_CLOSEOUT.parse_parent_attempts(parent, PARENT_ID, TURN_ID)
            self.assertEqual(len(attempts), 3)
            self.assertTrue(all(item["outcome"] == "started" for item in attempts))
            summary = RECORD_CLOSEOUT.summarize_agent_session(
                child,
                PARENT_ID,
                {CHILD_ID: "nested names were recorded"},
                {},
            )
            self.assertEqual(summary["nested_agent_calls"], 2)

    def test_forked_parent_history_is_excluded_but_followups_are_counted(self) -> None:
        child_id = "0198a000-0000-0000-0000-000000000001"
        parent_turn_id = "01989fff-0000-0000-0000-000000000001"
        followup_turn_id = "0198a001-0000-0000-0000-000000000001"
        events = [
            {
                "timestamp": "2026-08-01T01:02:01Z",
                "type": "session_meta",
                "payload": {
                    "id": child_id,
                    "forked_from_id": PARENT_ID,
                    "parent_thread_id": PARENT_ID,
                    "timestamp": "2026-08-01T01:02:01Z",
                    "agent_path": "/root/catalog-review",
                    "agent_role": "explorer",
                },
            },
            # This turn_context and task are copied parent history.
            {
                "timestamp": "2026-08-01T01:01:00Z",
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                    "multi_agent_version": "v2",
                },
            },
            {
                "timestamp": "2026-08-01T01:01:01Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": parent_turn_id},
            },
            {
                "timestamp": "2026-08-01T01:01:02Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "status": "completed",
                },
            },
            {
                "timestamp": "2026-08-01T01:01:03Z",
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output"},
            },
            {
                "timestamp": "2026-08-01T01:01:04Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 999}},
                },
            },
            {
                "timestamp": "2026-08-01T01:01:05Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "Copied parent answer.",
                },
            },
            # Live child task and a later follow-up turn.
            {
                "timestamp": "2026-08-01T01:02:02Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": child_id},
            },
            {
                "timestamp": "2026-08-01T01:02:03Z",
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                    "multi_agent_version": "v2",
                },
            },
            {
                "timestamp": "2026-08-01T01:02:04Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "status": "completed",
                },
            },
            {
                "timestamp": "2026-08-01T01:02:05Z",
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output"},
            },
            {
                "timestamp": "2026-08-01T01:02:06Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 111}},
                },
            },
            {
                "timestamp": "2026-08-01T01:02:07Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "Child answer.",
                },
            },
            {
                "timestamp": "2026-08-01T01:02:08Z",
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": followup_turn_id},
            },
            {
                "timestamp": "2026-08-01T01:02:09Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "status": "completed",
                },
            },
            {
                "timestamp": "2026-08-01T01:02:10Z",
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output"},
            },
            {
                "timestamp": "2026-08-01T01:02:11Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 222}},
                },
            },
            {
                "timestamp": "2026-08-01T01:02:12Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "Follow-up answer.",
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / "forked-child.jsonl"
            write_jsonl(child, events)
            summary = RECORD_CLOSEOUT.summarize_agent_session(
                child,
                PARENT_ID,
                {child_id: "The follow-up was included."},
                {},
            )
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["turn_count"], 2)
        self.assertEqual(summary["tool_calls"], 2)
        self.assertEqual(summary["tool_outputs"], 2)
        self.assertEqual(summary["event_count"], 12)
        self.assertEqual(summary["token_usage"]["total_tokens"], 222)
        self.assertEqual(summary["final_message_chars"], len("Follow-up answer."))

    def test_uuid_boundary_requires_child_and_candidate_timestamps(self) -> None:
        child_id = "0198a000-0000-0000-0000-000000000001"
        events = [
            {
                "timestamp": "2026-08-01T01:02:01Z",
                "type": "session_meta",
                "payload": {
                    "id": child_id,
                    "forked_from_id": PARENT_ID,
                    "parent_thread_id": PARENT_ID,
                    "timestamp": "2026-08-01T01:02:01Z",
                },
            },
            {
                "timestamp": "2026-08-01T01:01:01Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "0198a001-0000-0000-0000-000000000001",
                },
            },
        ]
        info = RECORD_CLOSEOUT.fork_metric_boundary_info(events)
        self.assertTrue(info is None or info[1] != "uuidv7-time")
        events[1].pop("timestamp")
        self.assertIsNone(RECORD_CLOSEOUT.fork_metric_boundary_info(events))

    def test_parent_message_phase_requires_response_identity(self) -> None:
        events = [
            {
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "phase": "commentary",
                    "message": "SECRET UNIDENTIFIED EVENT BODY",
                },
            },
            *[
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": phase,
                        "internal_chat_message_metadata_passthrough": {"turn_id": TURN_ID},
                        "content": [{"type": "text", "text": f"SECRET {phase}"}],
                    },
                }
                for phase in ("commentary", "final_answer", "future_phase")
            ],
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "text", "text": "SECRET NO ID"}],
                },
            },
        ]
        metrics = RECORD_CLOSEOUT.parse_parent_coordination(events, TURN_ID)
        self.assertEqual(
            metrics["parent_message_phase_counts"],
            {"commentary": 1, "final_answer": 1, "unknown": 1},
        )
        self.assertEqual(metrics["parent_message_observability"], "legacy")

    def test_closeout_storage_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            routine, anomalies = RECORD_CLOSEOUT.storage(root)
            RECORD_CLOSEOUT.write_new(routine / "routine.json", {"ok": True})
            RECORD_CLOSEOUT.write_new(anomalies / "anomaly.json", {"ok": True})

            for path in (root, *root.rglob("*")):
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))

    def test_interleaved_child_sessions_bind_attempts_by_stable_path(self) -> None:
        ids = {
            "alpha": "child-alpha",
            "beta": "child-beta",
            "gamma": "child-gamma",
        }
        paths = {name: f"/root/{name}-task" for name in ids}
        agents = [
            {
                "agent_id": ids[name],
                "agent_path": paths[name],
                "task_name": name,
                "role": "explorer",
                "started_at": f"2026-08-01T01:0{index}:00Z",
                "status": "completed",
            }
            for index, name in enumerate(("beta", "gamma", "alpha"))
        ]
        attempts = [
            {
                "attempted_at": f"2026-08-01T01:0{index}:00Z",
                "call_id": f"call-{name}",
                "task_name": name,
                "requested_role": "explorer",
                "outcome": "started",
                "child_ref": paths[name],
            }
            for index, name in enumerate(ids)
        ]
        connected = RECORD_CLOSEOUT.connect_attempts_to_agents(attempts, agents)
        by_name = {item["task_name"]: item for item in connected}
        for name, agent_id in ids.items():
            self.assertEqual(by_name[name]["agent_id"], agent_id)
            self.assertEqual(by_name[name]["binding"], "agent-path")
        self.assertEqual(len(connected), 3)

    def test_duplicate_child_path_is_left_ambiguous(self) -> None:
        agents = [
            {
                "agent_id": "child-one",
                "agent_path": "/root/shared-task",
                "task_name": "one",
                "role": "explorer",
                "started_at": "2026-08-01T01:00:00Z",
            },
            {
                "agent_id": "child-two",
                "agent_path": "/root/shared-task",
                "task_name": "two",
                "role": "explorer",
                "started_at": "2026-08-01T01:00:01Z",
            },
        ]
        attempts = [
            {
                "attempted_at": "2026-08-01T01:00:02Z",
                "call_id": "call-shared",
                "task_name": "shared",
                "requested_role": "explorer",
                "outcome": "started",
                "child_ref": "/root/shared-task",
            }
        ]
        connected = RECORD_CLOSEOUT.connect_attempts_to_agents(attempts, agents)
        ambiguous = next(item for item in connected if item.get("call_id") == "call-shared")
        self.assertIsNone(ambiguous.get("agent_id"))
        self.assertEqual(ambiguous["binding"], "ambiguous")
        self.assertEqual(
            ambiguous["binding_candidates"], ["child-one", "child-two"]
        )
        self.assertEqual(
            {
                item["agent_id"]
                for item in connected
                if item.get("binding") == "agent-session"
            },
            {"child-one", "child-two"},
        )

    def test_duplicate_direct_agent_id_is_left_ambiguous_with_candidate(self) -> None:
        agents = [
            {
                "agent_id": "child-one",
                "agent_path": "/root/one-task",
                "task_name": "one",
                "role": "explorer",
                "started_at": "2026-08-01T01:00:00Z",
            }
        ]
        attempts = [
            {
                "attempted_at": f"2026-08-01T01:00:0{index}Z",
                "call_id": f"call-{index}",
                "task_name": f"task-{index}",
                "requested_role": "explorer",
                "outcome": "started",
                "child_ref": "child-one",
            }
            for index in (1, 2)
        ]
        connected = RECORD_CLOSEOUT.connect_attempts_to_agents(attempts, agents)
        for attempt in (
            item for item in connected if item.get("call_id") in {"call-1", "call-2"}
        ):
            self.assertIsNone(attempt.get("agent_id"))
            self.assertEqual(attempt["binding"], "ambiguous")
            self.assertEqual(attempt["binding_candidates"], ["child-one"])

    def test_closeout_derives_binding_uncertainty_anomaly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child_one = work / "child-one.jsonl"
            child_two = work / "child-two.jsonl"
            shared_path = "/root/shared-task"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID, "session_id": PARENT_ID},
                    },
                    *[
                        {
                            "timestamp": f"2026-08-01T01:0{index}:00Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": "spawn_agent",
                                "call_id": f"call-{name}",
                                "arguments": json.dumps(
                                    {
                                        "agent_type": "explorer",
                                        "task_name": name,
                                    }
                                ),
                                "internal_chat_message_metadata_passthrough": {
                                    "turn_id": TURN_ID
                                },
                            },
                        }
                        for index, name in enumerate(("one", "two"), start=1)
                    ],
                    *[
                        {
                            "timestamp": f"2026-08-01T01:0{index}:01Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": f"call-{name}",
                                "output": json.dumps({"task_name": shared_path}),
                            },
                        }
                        for index, name in enumerate(("one", "two"), start=1)
                    ],
                ],
            )
            for target, child_id in (
                (child_one, "child-one"),
                (child_two, "child-two"),
            ):
                events = child_events()
                events[0]["payload"]["id"] = child_id
                events[0]["payload"]["agent_path"] = shared_path
                write_jsonl(target, events)
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child_one),
                    "--agent-session",
                    str(child_two),
                    "--agent-summary",
                    "child-one=First child completed.",
                    "--agent-summary",
                    "child-two=Second child completed.",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["routine"]["outcome"], "completed")
            self.assertIn(
                "agent-binding-uncertainty",
                payload["anomaly"]["anomaly_types"],
            )

    def test_correction_record_preserves_attempts_and_agent_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "legacy-record.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "record_id": "legacy-record",
                        "task_signature": "catalog-contract-review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            write_jsonl(parent, parent_events())
            write_jsonl(child, child_events())
            result = self.run_script(
                [
                    *self.common_args(root, parent),
                    "--source",
                    "correction",
                    "--supersedes",
                    "legacy-record",
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=Mapped the conflicting model catalogs.",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 3)
            self.assertEqual(record["collection_mode"], "transcript-replay-fallback")
            self.assertEqual(record["assessment"]["source"], "parent")
            self.assertEqual(record["source"], "correction")
            self.assertEqual(record["supersedes"], ["legacy-record"])
            self.assertEqual(record["project"], "/tmp/example-project")
            self.assertEqual(record["parent_thread_id"], PARENT_ID)
            self.assertEqual(record["turn_id"], TURN_ID)
            self.assertEqual(record["attempt_count"], 2)
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["role_counts"], {"explorer": 1})
            self.assertEqual(record["coordination_metrics"]["observability"], "legacy")
            self.assertEqual(record["coordination_metrics"]["version"], 4)
            self.assertEqual(
                record["coordination_metrics"]["steering_applied"],
                "not_observed",
            )
            self.assertEqual(
                record["coordination_metrics"]["parent_message_observability"],
                "legacy",
            )
            self.assertEqual(
                record["coordination_metrics"]["child_handback_observability"],
                "legacy",
            )
            self.assertEqual(
                [attempt["outcome"] for attempt in record["spawn_attempts"]],
                ["rejected", "started"],
            )
            agent = record["agents"][0]
            self.assertEqual(agent["model"], "gpt-5.6-luna")
            self.assertEqual(agent["reasoning_effort"], "medium")
            self.assertEqual(agent["status"], "completed")
            self.assertEqual(agent["turn_count"], 1)
            self.assertEqual(agent["duration_ms"], 4000)
            self.assertEqual(agent["tool_calls"], 1)
            self.assertEqual(agent["token_usage"]["total_tokens"], 130)
            self.assertEqual(agent["result_summary"], "Mapped the conflicting model catalogs.")
            audit = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(audit.returncode, 0, audit.stderr)
            audit_payload = json.loads(audit.stdout)
            self.assertTrue(audit_payload["valid"])
            self.assertEqual(audit_payload["routine_count"], 2)
            self.assertEqual(audit_payload["active_count"], 1)
            self.assertEqual(audit_payload["superseded_count"], 1)
            self.assertEqual(
                audit_payload["superseded_records"][0]["record_id"], "legacy-record"
            )

    def test_multi_turn_session_uses_full_lifecycle_and_latest_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            events = child_events()
            events.extend(
                [
                    {
                        "timestamp": "2026-08-01T01:02:07Z",
                        "type": "event_msg",
                        "payload": {"type": "task_started"},
                    },
                    {
                        "timestamp": "2026-08-04T01:02:10Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "turn_aborted",
                            "duration_ms": 3000,
                            "reason": "interrupted",
                        },
                    },
                ]
            )
            write_jsonl(parent, parent_events())
            write_jsonl(child, events)
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=The follow-up turn was interrupted.",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            agent = json.loads(result.stdout)["routine"]["agents"][0]
            self.assertEqual(agent["status"], "interrupted")
            self.assertEqual(agent["turn_count"], 2)
            self.assertEqual(agent["started_at"], "2026-08-04T01:02:02Z")
            self.assertEqual(agent["ended_at"], "2026-08-04T01:02:10Z")
            self.assertEqual(agent["duration_ms"], 8000)
            self.assertEqual(agent["final_message_chars"], 0)

    def test_failure_only_dry_run_records_dispatch_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / "parent.jsonl"
            events = parent_events(include_success=False)
            events[1]["payload"]["output"] = (
                "Unknown model SECRET RAW REPLAY DISPATCH ERROR. "
                "Available models: gpt-5.6-sol"
            )
            write_jsonl(parent, events)
            result = self.run_script(
                [
                    *self.common_args(root, parent),
                    "--source",
                    "backfill",
                    "--team-value",
                    "negative",
                    "--anomaly-type",
                    "dispatch-failure",
                    "--anomaly-type",
                    "runtime-capability-mismatch",
                    "--anomaly-summary",
                    "The configured role model was unavailable.",
                    "--anomaly-evidence",
                    "The native spawn output rejected Luna before returning a child.",
                    "--anomaly-impact",
                    "The parent completed the review without the planned child.",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            record = payload["routine"]
            self.assertEqual(record["outcome"], "dispatch_failed")
            self.assertEqual(record["attempt_count"], 1)
            self.assertEqual(record["child_count"], 0)
            self.assertFalse(root.exists())
            self.assertEqual(
                payload["anomaly"]["anomaly_types"],
                ["dispatch-failure", "runtime-capability-mismatch"],
            )
            self.assertNotIn("SECRET RAW REPLAY DISPATCH ERROR", result.stdout)

    def test_replay_record_marks_zero_yield_child_and_policy_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            events = [
                event
                for event in child_events()
                if not (
                    event.get("type") == "response_item"
                    and event.get("payload", {}).get("type")
                    in {"custom_tool_call", "custom_tool_call_output"}
                )
            ]
            events[-1]["payload"]["last_agent_message"] = ""
            write_jsonl(parent, parent_events())
            write_jsonl(child, events)
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=The child yielded no observable work.",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["routine"]["collector"]["child_metric_boundary_version"], 1
            )
            self.assertEqual(payload["routine"]["collector"]["zero_yield_policy_version"], 1)
            self.assertEqual(payload["anomaly"]["anomaly_types"], ["zero-yield-child"])

    def test_agent_summary_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            write_jsonl(parent, parent_events())
            write_jsonl(child, child_events())
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing --agent-summary", result.stderr)

    def test_native_role_runtime_mismatch_requires_anomaly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            events = child_events()
            events[0]["payload"]["agent_role"] = "worker_xhigh"
            events[1]["payload"]["model"] = "gpt-5.6-luna"
            events[1]["payload"]["effort"] = "xhigh"
            write_jsonl(parent, parent_events())
            write_jsonl(child, events)
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=Observed the runtime contract.",
                    "--dry-run",
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime mismatch requires --anomaly-type runtime-capability-mismatch",
                result.stderr,
            )

    def test_worker_xhigh_legacy_terra_is_accepted_only_before_cutover(self) -> None:
        legacy = {
            "agent_id": CHILD_ID,
            "role": "worker_xhigh",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "max",
            "started_at": "2026-08-03T08:54:21Z",
        }
        current = {**legacy, "started_at": "2026-08-03T08:54:23Z"}
        self.assertEqual(RECORD_CLOSEOUT.runtime_contract_mismatches([legacy]), [])
        self.assertTrue(RECORD_CLOSEOUT.runtime_contract_mismatches([current]))

    def test_child_parent_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            write_jsonl(parent, parent_events())
            write_jsonl(child, child_events(parent_id="different-parent"))
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=Mapped the catalog.",
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected parent", result.stderr)

    def test_correction_target_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            write_jsonl(parent, parent_events())
            write_jsonl(child, child_events())
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--source",
                    "correction",
                    "--supersedes",
                    "missing-record",
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=Mapped the catalog.",
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supersedes missing record", result.stderr)

    def test_audit_rejects_unclassified_native_runtime_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "mismatch.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_id": "mismatch",
                        "source": "backfill",
                        "supersedes": [],
                        "parent_thread_id": PARENT_ID,
                        "turn_id": TURN_ID,
                        "child_count": 1,
                        "attempt_count": 0,
                        "role_counts": {"worker_xhigh": 1},
                        "spawn_attempts": [],
                        "agents": [
                            {
                                "agent_id": CHILD_ID,
                                "role": "worker_xhigh",
                                "model": "gpt-5.6-luna",
                                "reasoning_effort": "xhigh",
                                "turn_count": 1,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["valid"])
            self.assertTrue(
                any(
                    "unclassified native-role runtime mismatch" in error
                    for error in payload["errors"]
                )
            )

    def test_audit_requires_zero_yield_anomaly_after_policy_cutover(self) -> None:
        def routine_payload(record_id: str, *, zero_policy: int | None = 1) -> dict:
            collector = {
                "lifecycle_policy_version": 1,
                "child_metric_boundary_version": 1,
                "parent_transcript_bytes_processed": 0,
                "child_transcript_bytes_processed": 0,
                "transcript_strategy": "hook-events-plus-child-byte-cursors",
                "raw_prompts_stored": False,
                "raw_messages_stored": False,
            }
            if zero_policy is not None:
                collector["zero_yield_policy_version"] = zero_policy
            return {
                "schema_version": 3,
                "record_id": record_id,
                "source": "live",
                "supersedes": [],
                "project": "/tmp/example-project",
                "parent_thread_id": PARENT_ID,
                "turn_id": TURN_ID,
                "collection_mode": "codex-hooks-incremental",
                "child_count": 1,
                "attempt_count": 1,
                "role_counts": {"explorer": 1},
                "spawn_attempts": [
                    {
                        "call_id": "call-child",
                        "task_name": "child-task",
                        "outcome": "started",
                        "agent_id": CHILD_ID,
                        "binding": "agent-id",
                    }
                ],
                "agents": [
                    {
                        "agent_id": CHILD_ID,
                        "role": "explorer",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "medium",
                        "status": "completed",
                        "turn_count": 1,
                        "tool_calls": 0,
                        "final_message_chars": 0,
                        "nested_agent_calls": 0,
                    }
                ],
                "collector": collector,
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "current-records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "current.json").write_text(
                json.dumps(routine_payload("current")) + "\n", encoding="utf-8"
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                any(
                    "zero-yield-child anomaly" in error
                    for error in json.loads(result.stdout)["errors"]
                )
            )

            anomalies = root / "anomalies"
            anomalies.mkdir()
            (anomalies / "current.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "record_id": "current",
                        "related_routine": "current.json",
                        "anomaly_types": ["zero-yield-child"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy-records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "legacy.json").write_text(
                json.dumps(routine_payload("legacy", zero_policy=None)) + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

    def test_audit_validates_child_metric_boundary_marker_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            payload = {
                "schema_version": 2,
                "record_id": "bad-marker",
                "source": "backfill",
                "supersedes": [],
                "parent_thread_id": PARENT_ID,
                "turn_id": TURN_ID,
                "child_count": 1,
                "attempt_count": 0,
                "role_counts": {"explorer": 1},
                "spawn_attempts": [],
                "agents": [
                    {
                        "agent_id": CHILD_ID,
                        "role": "explorer",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "medium",
                        "status": "completed",
                        "turn_count": 1,
                        "tool_calls": 1,
                        "final_message_chars": 4,
                        "nested_agent_calls": 0,
                    }
                ],
                "collector": {"child_metric_boundary_version": "1"},
            }
            (routine / "bad-marker.json").write_text(
                json.dumps(payload) + "\n", encoding="utf-8"
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                any(
                    "child_metric_boundary_version marker" in error
                    for error in json.loads(result.stdout)["errors"]
                )
            )

    def test_audit_requires_terminal_binding_uncertainty_anomaly(self) -> None:
        self.assertEqual(
            RECORD_CLOSEOUT.binding_uncertainty_messages(
                [{"outcome": "errored", "agent_id": None}],
                [{"status": "completed"}],
            ),
            [],
        )
        self.assertEqual(
            RECORD_CLOSEOUT.binding_uncertainty_messages(
                [{"outcome": "started", "agent_id": None, "binding": "unbound"}],
                [{"status": "unknown"}],
            ),
            [],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "binding.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_id": "binding",
                        "source": "backfill",
                        "supersedes": [],
                        "parent_thread_id": PARENT_ID,
                        "turn_id": TURN_ID,
                        "child_count": 1,
                        "attempt_count": 1,
                        "role_counts": {"explorer": 1},
                        "spawn_attempts": [
                            {
                                "call_id": "call-unbound",
                                "task_name": "unbound-task",
                                "outcome": "started",
                                "agent_id": None,
                                "binding": "ambiguous",
                                "binding_candidates": [CHILD_ID],
                            }
                        ],
                        "agents": [
                            {
                                "agent_id": CHILD_ID,
                                "role": "explorer",
                                "model": "gpt-5.6-luna",
                                "reasoning_effort": "medium",
                                "status": "completed",
                                "turn_count": 1,
                                "nested_agent_calls": 0,
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["valid"])
            self.assertTrue(
                any(
                    "agent-binding-uncertainty anomaly" in error
                    for error in payload["errors"]
                )
            )

            anomalies = root / "anomalies"
            anomalies.mkdir()
            (anomalies / "binding.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_id": "binding",
                        "related_routine": "binding.json",
                        "anomaly_types": ["agent-binding-uncertainty"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

    def test_audit_requires_lifecycle_anomaly_only_after_policy_cutover(self) -> None:
        def routine_payload(record_id: str, *, policy: bool) -> dict:
            payload = {
                "schema_version": 2,
                "record_id": record_id,
                "source": "backfill",
                "supersedes": [],
                "parent_thread_id": PARENT_ID,
                "turn_id": TURN_ID,
                "child_count": 1,
                "attempt_count": 1,
                "role_counts": {"explorer": 1},
                "spawn_attempts": [
                    {
                        "call_id": "call-child",
                        "task_name": "child-task",
                        "outcome": "started",
                        "agent_id": CHILD_ID,
                        "binding": "agent-id",
                    }
                ],
                "agents": [
                    {
                        "agent_id": CHILD_ID,
                        "role": "explorer",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "medium",
                        "status": "unknown",
                        "turn_count": 1,
                        "nested_agent_calls": 0,
                    }
                ],
            }
            if policy:
                payload["collector"] = {"lifecycle_policy_version": 1}
            return payload

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy-records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "legacy.json").write_text(
                json.dumps(routine_payload("legacy", policy=False)) + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "current-records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "current.json").write_text(
                json.dumps(routine_payload("current", policy=True)) + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                any(
                    "child-lifecycle-incomplete anomaly" in error
                    for error in json.loads(result.stdout)["errors"]
                )
            )

            anomalies = root / "anomalies"
            anomalies.mkdir()
            (anomalies / "current.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_id": "current",
                        "related_routine": "current.json",
                        "anomaly_types": ["child-lifecycle-incomplete"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["valid"])

    def test_coverage_reconcile_ignores_root_sessions_and_tracks_unmatched_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            (root / "routine").mkdir(parents=True)
            child = work / "child.jsonl"
            write_jsonl(
                child,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/catalog-review",
                            "agent_role": "explorer",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            root_session = work / "root.jsonl"
            write_jsonl(
                root_session,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "root-thread"},
                    }
                ],
            )
            record = {
                "schema_version": 3,
                "record_id": "record-1",
                "recorded_at": "2026-08-01T01:00:00Z",
                "observed_started_at": "2026-08-01T01:00:00Z",
                "parent_thread_id": PARENT_ID,
                "turn_id": TURN_ID,
                "supersedes": [],
                "agents": [
                    {
                        "agent_id": CHILD_ID,
                        "child_session_id": CHILD_ID,
                        "agent_path": "/root/catalog-review",
                        "actual_role": "explorer",
                        "metric_validity": "valid",
                    },
                    {
                        "agent_id": "unmatched-child",
                        "child_session_id": "unmatched-child",
                        "agent_path": "/root/unmatched",
                        "actual_role": "explorer",
                        "metric_validity": "valid",
                    },
                ],
            }
            (root / "routine" / "record-1.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            result = RECORD_CLOSEOUT.reconcile_coverage(
                root,
                session_paths=[child, root_session],
                since="2026-08-01T00:00:00Z",
                until="2026-08-01T02:00:00Z",
            )
            self.assertEqual(result["session_evidence_count"], 1)
            self.assertEqual(result["missing_structured_records"], [])
            self.assertEqual(
                [item["child_session_id"] for item in result["unmatched_structured_records"]],
                ["unmatched-child"],
            )
            self.assertFalse(result["valid"])

    def test_coverage_reconcile_distinguishes_reuse_from_duplicate_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            child = work / "child.jsonl"
            write_jsonl(
                child,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/reused-owner",
                            "agent_role": "explorer",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )

            def write_record(record_id: str, turn_id: str) -> None:
                payload = {
                    "schema_version": 3,
                    "record_id": record_id,
                    "recorded_at": "2026-08-01T01:00:00Z",
                    "observed_started_at": "2026-08-01T01:00:00Z",
                    "parent_thread_id": PARENT_ID,
                    "turn_id": turn_id,
                    "supersedes": [],
                    "agents": [
                        {
                            "agent_id": CHILD_ID,
                            "child_session_id": CHILD_ID,
                            "agent_path": "/root/reused-owner",
                            "actual_role": "explorer",
                            "metric_validity": "valid",
                        }
                    ],
                }
                (routine / f"{record_id}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )

            write_record("record-turn-1", "turn-1")
            write_record("record-turn-2", "turn-2")
            result = RECORD_CLOSEOUT.reconcile_coverage(root, session_paths=[child])
            self.assertEqual(result["duplicate_structured_records"], [])
            self.assertEqual(len(result["reused_child_records"]), 1)
            self.assertEqual(
                result["reused_child_records"][0]["parent_turn_ids"],
                ["turn-1", "turn-2"],
            )
            self.assertTrue(result["valid"])

    def test_coverage_reconcile_rejects_duplicate_same_turn_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            child = work / "child.jsonl"
            write_jsonl(
                child,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/duplicate-owner",
                            "agent_role": "explorer",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            for record_id in ("record-a", "record-b"):
                payload = {
                    "schema_version": 3,
                    "record_id": record_id,
                    "recorded_at": "2026-08-01T01:00:00Z",
                    "observed_started_at": "2026-08-01T01:00:00Z",
                    "parent_thread_id": PARENT_ID,
                    "turn_id": TURN_ID,
                    "supersedes": [],
                    "agents": [
                        {
                            "agent_id": CHILD_ID,
                            "child_session_id": CHILD_ID,
                            "agent_path": "/root/duplicate-owner",
                            "actual_role": "explorer",
                            "metric_validity": "valid",
                        }
                    ],
                }
                (routine / f"{record_id}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            result = RECORD_CLOSEOUT.reconcile_coverage(root, session_paths=[child])
            self.assertEqual(len(result["duplicate_structured_records"]), 1)
            self.assertEqual(
                result["duplicate_structured_records"][0]["parent_turn_id"], TURN_ID
            )
            self.assertEqual(result["reused_child_records"], [])
            self.assertFalse(result["valid"])

    def test_coverage_reconcile_accepts_exact_path_for_cross_window_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            child = work / "older-child.jsonl"
            write_jsonl(
                child,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/reused-owner",
                            "agent_role": "explorer",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            record = {
                "schema_version": 3,
                "record_id": "record-followup",
                "recorded_at": "2026-08-02T01:00:00Z",
                "observed_started_at": "2026-08-02T01:00:00Z",
                "parent_thread_id": PARENT_ID,
                "parent_turn_id": "turn-followup",
                "supersedes": [],
                "agents": [
                    {
                        "agent_id": CHILD_ID,
                        "child_session_id": CHILD_ID,
                        "agent_path": "/root/reused-owner",
                        "actual_role": "explorer",
                        "metric_validity": "valid",
                        "session_path": str(child.resolve()),
                    }
                ],
            }
            (routine / "record-followup.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            result = RECORD_CLOSEOUT.reconcile_coverage(
                root,
                session_paths=[child],
                since="2026-08-02T00:00:00Z",
                until="2026-08-02T23:59:59Z",
            )
            self.assertEqual(result["session_evidence_count"], 1)
            self.assertEqual(result["unmatched_structured_records"], [])
            self.assertEqual(len(result["out_of_window_session_evidence"]), 1)
            self.assertEqual(
                result["out_of_window_session_evidence"][0]["parent_turn_id"],
                "turn-followup",
            )
            self.assertTrue(result["valid"])

    def test_replay_record_marks_delivery_failure_separately_from_zero_yield(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            parent = work / "parent.jsonl"
            child = work / "child.jsonl"
            write_jsonl(parent, parent_events(include_success=True))
            events = child_events()
            events[-1]["payload"].pop("last_agent_message", None)
            write_jsonl(child, events)
            result = self.run_script(
                [
                    *self.common_args(work / "records", parent),
                    "--agent-session",
                    str(child),
                    "--agent-summary",
                    f"{CHILD_ID}=Child work completed but no answer was delivered.",
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["anomaly"]["anomaly_types"], ["child-delivery-failure"]
            )

    def test_replay_role_sources_match_hook_enums_and_tier_aliases(self) -> None:
        agent = {
            "agent_id": CHILD_ID,
            "role": None,
            "actual_role": None,
            "actual_role_source": "not_observed",
            "service_tier": None,
            "status": "completed",
        }
        attempts = [
            {
                "agent_id": CHILD_ID,
                "child_ref": CHILD_ID,
                "requested_role": "explorer",
                "binding": "agent-id",
            }
        ]
        RECORD_CLOSEOUT.enrich_child_identity(
            attempts, [agent], PARENT_ID, TURN_ID
        )
        self.assertEqual(agent["requested_role_source"], "spawn-request")
        self.assertEqual(agent["role_binding_source"], "not_observed")
        self.assertIsNone(agent["actual_role"])
        self.assertIsNone(
            RECORD_CLOSEOUT.service_tier_mismatch(
                {
                    **agent,
                    "actual_role": "explorer",
                    "service_tier": "priority",
                    "started_at": "2026-08-12T07:00:00Z",
                }
            )
        )
        self.assertIsNotNone(
            RECORD_CLOSEOUT.service_tier_mismatch(
                {**agent, "actual_role": "worker_xhigh", "service_tier": "fast"}
            )
        )
        mixed = {
            "agent_id": CHILD_ID,
            "agent_path": "/root/mixed",
            "role": None,
            "actual_role": None,
            "status": "completed",
        }
        mixed_attempts = [
            {
                "agent_id": CHILD_ID,
                "child_ref": CHILD_ID,
                "requested_role": "explorer",
                "binding": "agent-id",
            },
            {
                "agent_id": CHILD_ID,
                "child_ref": "/root/mixed",
                "requested_role": "explorer",
                "binding": "agent-path",
            },
        ]
        RECORD_CLOSEOUT.enrich_child_identity(
            mixed_attempts, [mixed], PARENT_ID, TURN_ID
        )
        self.assertEqual(mixed["requested_role_source"], "spawn-request")

    def test_audit_requires_anomaly_for_observed_service_tier_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            routine = root / "routine"
            routine.mkdir(parents=True)
            (routine / "tier-mismatch.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_id": "tier-mismatch",
                        "source": "backfill",
                        "supersedes": [],
                        "parent_thread_id": PARENT_ID,
                        "turn_id": TURN_ID,
                        "child_count": 1,
                        "attempt_count": 1,
                        "role_counts": {"explorer": 1},
                        "spawn_attempts": [
                            {
                                "outcome": "started",
                                "agent_id": CHILD_ID,
                                "binding": "agent-id",
                            }
                        ],
                        "agents": [
                            {
                                "agent_id": CHILD_ID,
                                "role": "explorer",
                                "actual_role": "explorer",
                                "model": "gpt-5.6-luna",
                                "reasoning_effort": "medium",
                                "service_tier": "standard",
                                "started_at": "2026-08-12T07:00:00Z",
                                "turn_count": 1,
                                "status": "completed",
                                "nested_agent_calls": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_script(["audit", "--root", str(root)])
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                any(
                    "unclassified native-role runtime mismatch" in error
                    for error in json.loads(result.stdout)["errors"]
                )
            )

    def test_coverage_reconcile_keeps_explicit_child_header_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            malformed = work / "malformed-child.jsonl"
            malformed.write_text("not-json\n", encoding="utf-8")
            bad_timestamp = work / "bad-timestamp-child.jsonl"
            write_jsonl(
                bad_timestamp,
                [
                    {
                        "timestamp": "not-a-time",
                        "type": "session_meta",
                        "payload": {
                            "id": CHILD_ID,
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/bad-time",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            result = RECORD_CLOSEOUT.reconcile_coverage(
                work / "records", session_paths=[malformed, bad_timestamp]
            )
            reasons = " ".join(
                str(item.get("reason"))
                for item in result["invalid_metric_identifiers"]
            )
            self.assertIn("malformed JSON", reasons)
            self.assertIn("missing child session id", reasons)
            self.assertIn("invalid session timestamp", reasons)

    def test_coverage_reconcile_scopes_header_errors_to_three_day_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            sessions = work / "sessions"
            old_path = sessions / "2026" / "07" / "20" / "old-child.jsonl"
            old_path.parent.mkdir(parents=True)
            write_jsonl(
                old_path,
                [
                    {
                        "timestamp": "2026-07-20T01:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "old-child",
                            "agent_path": "/root/old-child",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            current_path = sessions / "2026" / "08" / "02" / "bad-current.jsonl"
            current_path.parent.mkdir(parents=True)
            write_jsonl(
                current_path,
                [
                    {
                        "timestamp": "not-a-time",
                        "type": "session_meta",
                        "payload": {
                            "id": "bad-current",
                            "parent_thread_id": PARENT_ID,
                            "agent_path": "/root/bad-current",
                            "thread_source": "subagent",
                        },
                    }
                ],
            )
            result = RECORD_CLOSEOUT.reconcile_coverage(
                work / "records",
                sessions_root=sessions,
                since="2026-08-01T00:00:00Z",
                until="2026-08-03T23:59:59Z",
            )
            paths = {
                item.get("session_path")
                for item in result["invalid_metric_identifiers"]
            }
            self.assertIn(str(current_path.resolve()), paths)
            self.assertNotIn(str(old_path.resolve()), paths)

    def test_session_meta_source_subagent_thread_spawn_is_joinable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory) / "child.jsonl"
            write_jsonl(
                child,
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "child-source",
                            "source": {
                                "subagent": {
                                    "thread_spawn": {
                                        "parent_thread_id": PARENT_ID,
                                        "agent_path": "/root/source-child",
                                        "agent_role": "explorer",
                                    }
                                }
                            },
                        },
                    }
                ],
            )
            evidence = RECORD_CLOSEOUT._read_session_meta_only(child)
            self.assertEqual(evidence["parent_thread_id"], PARENT_ID)
            self.assertEqual(evidence["agent_path"], "/root/source-child")
            self.assertEqual(evidence["actual_role"], "explorer")


if __name__ == "__main__":
    unittest.main()
