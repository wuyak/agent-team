#!/usr/bin/env python3
"""Regression tests for hook-driven incremental Agent Team recording."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("record_hook.py")
AUDITOR = Path(__file__).with_name("record_closeout.py")
_RECORDER_SPEC = importlib.util.spec_from_file_location("record_hook_under_test", SCRIPT)
assert _RECORDER_SPEC is not None and _RECORDER_SPEC.loader is not None
RECORD_HOOK = importlib.util.module_from_spec(_RECORDER_SPEC)
_RECORDER_SPEC.loader.exec_module(RECORD_HOOK)
PARENT_ID = "019fbd55-1111-7222-8333-444455556666"
TURN_ID = "019fbd55-aaaa-7bbb-8ccc-ddddeeeeffff"
SECOND_TURN_ID = "019fbd55-cccc-7ddd-8eee-ffff00001111"
CHILD_ID = "019fbd56-1234-7abc-8def-1234567890ab"
CHILD_TURN_ID = "019fbd56-bbbb-7ccc-8ddd-eeeeffffffff"


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def parent_session_event() -> dict:
    return {
        "timestamp": "2026-08-04T00:59:59Z",
        "type": "session_meta",
        "payload": {"id": PARENT_ID},
    }


def parent_final_event(message: str = "SECRET PARENT FINAL") -> dict:
    return {
        "timestamp": "2026-08-04T01:00:10Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": message}],
            "internal_chat_message_metadata_passthrough": {"turn_id": TURN_ID},
        },
    }


def child_events(
    *, role: str = "explorer", model: str = "gpt-5.6-luna", effort: str = "medium"
) -> list[dict]:
    return [
        {
            "timestamp": "2026-08-04T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": CHILD_ID,
                "parent_thread_id": PARENT_ID,
                "timestamp": "2026-08-04T01:00:00Z",
                "agent_path": "/root/hook-forward-test",
                "agent_nickname": "Hook Explorer",
                "agent_role": role,
                "model_provider": "openai",
                "thread_source": "subagent",
                "cli_version": "0.147.0-test",
                "originator": "Codex Test",
            },
        },
        {
            "timestamp": "2026-08-04T01:00:01Z",
            "type": "turn_context",
            "payload": {
                "model": model,
                "effort": effort,
                "multi_agent_version": "v2",
                "approval_policy": "never",
                "approvals_reviewer": "user",
                "sandbox_policy": {"type": "read-only"},
                "permission_profile": {"type": "disabled"},
                "workspace_roots": ["/secret/workspace-a", "/secret/workspace-b"],
            },
        },
        {
            "timestamp": "2026-08-04T01:00:02Z",
            "type": "event_msg",
            "payload": {"type": "task_started"},
        },
        {
            "timestamp": "2026-08-04T01:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
            },
        },
        {
            "timestamp": "2026-08-04T01:00:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output"},
        },
        {
            "timestamp": "2026-08-04T01:00:05Z",
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
            "timestamp": "2026-08-04T01:00:06Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "last_agent_message": "A raw child result that must not enter hook state.",
            },
        },
    ]


def forked_child_events(
    *, fork_turns: str | int = "all", followup: bool = False
) -> list[dict]:
    """A child rollout with copied parent history and live child turns."""
    del fork_turns  # The transcript shape is the same for all/positive forks.
    events = [
        {
            "timestamp": "2026-08-07T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": PARENT_ID,
                "id": CHILD_ID,
                "forked_from_id": PARENT_ID,
                "parent_thread_id": PARENT_ID,
                "timestamp": "2026-08-07T01:00:00Z",
                "agent_path": "/root/forked-child",
                "agent_nickname": "Forked Child",
                "agent_role": "explorer",
                "model_provider": "openai",
                "thread_source": "subagent",
            },
        },
        # Copied parent session and an entire prior turn.  These values are
        # intentionally extreme so accidental replay is obvious.
        {
            "timestamp": "2026-08-06T23:00:00Z",
            "type": "session_meta",
            "payload": {"id": PARENT_ID, "timestamp": "2026-08-06T23:00:00Z"},
        },
        {
            "timestamp": "2026-08-06T23:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "019fbd54-aaaa-7bbb-8ccc-ddddeeeeffff",
            },
        },
        {
            "timestamp": "2026-08-06T23:00:02Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec"},
        },
        {
            "timestamp": "2026-08-06T23:00:03Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output"},
        },
        {
            "timestamp": "2026-08-06T23:00:04Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 900000000,
                        "cached_input_tokens": 899999000,
                        "output_tokens": 900000,
                        "reasoning_output_tokens": 800000,
                        "total_tokens": 901700000,
                    }
                },
            },
        },
        {
            "timestamp": "2026-08-06T23:00:05Z",
            "type": "event_msg",
            "payload": {"type": "task_complete", "last_agent_message": "old"},
        },
        # The first live child turn uses the child-local UUIDv7 time prefix.
        {
            "timestamp": "2026-08-07T01:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": CHILD_ID},
        },
        {
            "timestamp": "2026-08-07T01:00:02Z",
            "type": "turn_context",
            "payload": {
                "model": "gpt-5.6-luna",
                "effort": "medium",
                "multi_agent_version": "v2",
            },
        },
        {
            "timestamp": "2026-08-07T01:00:03Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec"},
        },
        {
            "timestamp": "2026-08-07T01:00:04Z",
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output"},
        },
        {
            "timestamp": "2026-08-07T01:00:05Z",
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
            "timestamp": "2026-08-07T01:00:06Z",
            "type": "event_msg",
            "payload": {"type": "task_complete", "last_agent_message": "live"},
        },
    ]
    if followup:
        events.extend(
            [
                {
                    "timestamp": "2026-08-07T01:00:07Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "task_started",
                        "turn_id": "019fbd56-cccc-7ddd-8eee-ffff00001111",
                    },
                },
                {
                    "timestamp": "2026-08-07T01:00:08Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call", "name": "exec"},
                },
                {
                    "timestamp": "2026-08-07T01:00:09Z",
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call_output"},
                },
                {
                    "timestamp": "2026-08-07T01:00:10Z",
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "last_agent_message": "followup"},
                },
            ]
        )
    return events


class HookRecorderTests(unittest.TestCase):
    def run_script(
        self, args: list[str], hook_input: dict | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=json.dumps(hook_input) if hook_input is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )

    def ingest(self, root: Path, payload: dict) -> None:
        result = self.run_script(["ingest", "--root", str(root)], payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def pre_spawn(
        self,
        root: Path,
        role: str = "explorer",
        transcript: Path | None = None,
        requested_model: str | None = None,
        fork_turns: str | int | None = None,
        parent_turn_id: str = TURN_ID,
    ) -> None:
        payload = {
            "session_id": PARENT_ID,
            "turn_id": parent_turn_id,
            "cwd": "/tmp/hook-project",
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_use_id": "spawn-call",
            "tool_input": {
                "agent_type": role,
                "message": "SECRET RAW CHILD PROMPT",
            },
        }
        if requested_model is not None:
            payload["tool_input"]["model"] = requested_model
        if fork_turns is not None:
            payload["tool_input"]["fork_turns"] = fork_turns
        if transcript is not None:
            payload["transcript_path"] = str(transcript)
        self.ingest(
            root,
            payload,
        )

    def post_spawn(
        self,
        root: Path,
        role: str = "explorer",
        parent_turn_id: str = TURN_ID,
    ) -> None:
        self.ingest(
            root,
            {
                "session_id": PARENT_ID,
                "turn_id": parent_turn_id,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "PostToolUse",
                "tool_name": "spawn_agent",
                "tool_use_id": "spawn-call",
                "tool_input": {
                    "agent_type": role,
                    "message": "SECRET RAW CHILD PROMPT",
                },
                "tool_response": {
                    "agent_id": CHILD_ID,
                    "nickname": "Hook Explorer",
                },
            },
        )

    def post_spawn_path_only(self, root: Path, role: str = "explorer") -> None:
        self.ingest(
            root,
            {
                "session_id": PARENT_ID,
                "turn_id": TURN_ID,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "PostToolUse",
                "tool_name": "spawn_agent",
                "tool_use_id": "spawn-call",
                "tool_input": {
                    "agent_type": role,
                    "message": "SECRET RAW CHILD PROMPT",
                },
                "tool_response": {"task_name": "/root/hook-forward-test"},
            },
        )

    def touch_second_parent_turn(self, root: Path) -> None:
        self.ingest(
            root,
            {
                "session_id": PARENT_ID,
                "turn_id": SECOND_TURN_ID,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "PreToolUse",
                "tool_name": "collaboration.send_message",
                "tool_use_id": "second-turn-message",
                "tool_input": {
                    "target": "/root/unrelated-agent",
                    "message": "SECOND TURN MESSAGE MUST NOT CAPTURE OLD LIFECYCLE",
                },
            },
        )

    def child_start(
        self,
        root: Path,
        role: str = "explorer",
        hook_turn_id: str = TURN_ID,
    ) -> None:
        self.ingest(
            root,
            {
                "session_id": PARENT_ID,
                "turn_id": hook_turn_id,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "SubagentStart",
                "agent_id": CHILD_ID,
                "agent_type": role,
                "model": "parent-model-must-not-win",
                "permission_mode": "default",
            },
        )

    def child_stop(
        self,
        root: Path,
        transcript: Path,
        role: str = "explorer",
        hook_turn_id: str = TURN_ID,
    ) -> None:
        self.ingest(
            root,
            {
                "session_id": PARENT_ID,
                "turn_id": hook_turn_id,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "SubagentStop",
                "agent_id": CHILD_ID,
                "agent_type": role,
                "model": "parent-model-must-not-win",
                "agent_transcript_path": str(transcript),
                "last_assistant_message": "SECRET RAW FINAL MESSAGE",
                "stop_hook_active": False,
            },
        )

    def finalize(self, root: Path, *extra: str) -> dict:
        return self.finalize_turn(root, TURN_ID, *extra)

    def finalize_turn(self, root: Path, turn_id: str, *extra: str) -> dict:
        result = self.run_script(
            [
                "finalize",
                "--root",
                str(root),
                "--parent-thread-id",
                PARENT_ID,
                "--turn-id",
                turn_id,
                *extra,
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_current_tool_operations_are_sanitized_and_polling_is_excluded(self) -> None:
        for operation in (
            "spawn_agent",
            "followup_task",
            "send_message",
            "interrupt_agent",
        ):
            self.assertEqual(
                RECORD_HOOK.normalize_operation(f"collaboration.{operation}", {}),
                operation,
            )
        for operation in (
            "wait_agent",
            "list_agents",
            "close_agent",
            "resume_agent",
            "send_input",
        ):
            self.assertIsNone(
                RECORD_HOOK.normalize_operation(f"collaboration.{operation}", {})
            )
        for operation in ("wait_agent", "list_agents"):
            self.assertIsNone(
                RECORD_HOOK.sanitize_tool_event(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": f"collaboration.{operation}",
                        "tool_use_id": f"poll-{operation}",
                        "tool_input": {"message": "POLLING BODY"},
                    }
                )
            )

        self.assertEqual(
            RECORD_HOOK.normalize_operation(
                "Agent", {"agent_type": "explorer", "message": "bounded task"}
            ),
            "spawn_agent",
        )
        self.assertEqual(
            RECORD_HOOK.normalize_operation(
                "runtime.spawn_agent", {"agent_type": "explorer", "message": "bounded task"}
            ),
            "spawn_agent",
        )
        self.assertIsNone(RECORD_HOOK.normalize_operation("Agent", {}))
        self.assertEqual(
            RECORD_HOOK.sanitize_tool_event(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Agent",
                    "tool_use_id": "agent-call",
                    "tool_input": {"agent_type": "explorer", "message": "SECRET"},
                }
            )["operation"],
            "spawn_agent",
        )

        message_event = RECORD_HOOK.sanitize_tool_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "collaboration.send_message",
                "tool_use_id": "send-call",
                "tool_input": {
                    "target": "/root/reviewer",
                    "message": "SECRET COMMUNICATION BODY",
                },
            }
        )
        self.assertIsNotNone(message_event)
        assert message_event is not None
        self.assertEqual(message_event["operation"], "send_message")
        self.assertEqual(message_event["target_agent_id"], "/root/reviewer")
        self.assertNotIn("SECRET COMMUNICATION BODY", json.dumps(message_event))

        interrupt_event = RECORD_HOOK.sanitize_tool_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "collaboration.interrupt_agent",
                "tool_use_id": "interrupt-call",
                "tool_input": {"target": "/root/reviewer"},
                "tool_response": {"status": "interrupted"},
            }
        )
        self.assertIsNotNone(interrupt_event)
        assert interrupt_event is not None
        self.assertEqual(interrupt_event["operation"], "interrupt_agent")
        self.assertEqual(interrupt_event["target_agent_id"], "/root/reviewer")
        self.assertEqual(interrupt_event["outcome"], "completed")
        interrupt_metrics = RECORD_HOOK.coordination_metrics_from_hook_events(
            [interrupt_event]
        )
        self.assertEqual(
            interrupt_metrics["operation_counts"]["interrupt_agent"], 1
        )
        self.assertEqual(interrupt_metrics["steering_applied"], "not_observed")

        interrupt_json_event = RECORD_HOOK.sanitize_tool_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "collaboration.interrupt_agent",
                "tool_use_id": "interrupt-json-call",
                "tool_input": {"target": "/root/reviewer"},
                "tool_response": json.dumps({"status": "interrupted"}),
            }
        )
        self.assertIsNotNone(interrupt_json_event)
        assert interrupt_json_event is not None
        self.assertEqual(interrupt_json_event["outcome"], "completed")
        self.assertIsNone(interrupt_json_event["error_code"])

        missing_spawn = RECORD_HOOK.sanitize_tool_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "spawn_agent",
                "tool_use_id": "spawn-missing-result",
                "tool_input": {"agent_type": "explorer"},
                "tool_response": {},
            }
        )
        self.assertIsNotNone(missing_spawn)
        assert missing_spawn is not None
        self.assertEqual(missing_spawn["outcome"], "errored")
        self.assertEqual(missing_spawn["error_code"], "unknown")

        self_binding = RECORD_HOOK.sanitize_tool_event(
            {
                "session_id": PARENT_ID,
                "hook_event_name": "PostToolUse",
                "tool_name": "spawn_agent",
                "tool_use_id": "self-binding",
                "tool_input": {"agent_type": "explorer"},
                "tool_response": {"agent_id": PARENT_ID},
            }
        )
        self.assertIsNotNone(self_binding)
        assert self_binding is not None
        self.assertIsNone(self_binding["agent_id"])
        self.assertTrue(self_binding["self_binding_rejected"])
        self.assertEqual(self_binding["error_code"], "invalid-dispatch")
        self.assertEqual(self_binding["outcome"], "errored")

        replayed = {
            **interrupt_event,
            "observed_at": "later",
            "parent_transcript_offset": 999,
        }
        self.assertEqual(
            RECORD_HOOK.event_key(interrupt_event),
            RECORD_HOOK.event_key(replayed),
        )

    def test_fork_request_is_allowlisted_and_omission_invalidity_are_distinct(self) -> None:
        cases = {
            "none": ("none", "observed"),
            "all": ("all", "observed"),
            3: (3, "observed"),
            "3": (3, "observed"),
            0: (None, "invalid"),
            True: (None, "invalid"),
            "SECRET RAW FORK REQUEST": (None, "invalid"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    RECORD_HOOK.sanitize_requested_fork_turns({"fork_turns": raw}),
                    expected,
                )
        self.assertEqual(
            RECORD_HOOK.sanitize_requested_fork_turns({}), (None, "omitted")
        )
        event = RECORD_HOOK.sanitize_tool_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "spawn_agent",
                "tool_use_id": "fork-call",
                "tool_input": {
                    "agent_type": "explorer",
                    "fork_turns": "SECRET RAW FORK REQUEST",
                    "message": "SECRET RAW CHILD PROMPT",
                },
            }
        )
        assert event is not None
        serialized = json.dumps(event)
        self.assertEqual(event["requested_fork_turns"], None)
        self.assertEqual(event["requested_fork_turns_observability"], "invalid")
        self.assertNotIn("SECRET RAW FORK REQUEST", serialized)
        self.assertNotIn("SECRET RAW CHILD PROMPT", serialized)

    def test_fork_request_observation_mismatch_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "forked-child.jsonl"
            write_jsonl(transcript, forked_child_events())
            self.pre_spawn(root, fork_turns="none")
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            attempt = record["spawn_attempts"][0]
            agent = record["agents"][0]
            self.assertEqual(attempt["requested_fork_turns"], "none")
            self.assertEqual(attempt["requested_fork_turns_observability"], "observed")
            self.assertIs(agent["fork_observed"], True)
            self.assertEqual(agent["fork_observation_source"], "child-session-meta")
            self.assertEqual(agent["forked_from_id"], PARENT_ID)
            anomaly = json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))
            self.assertIn("fork-request-observation-mismatch", anomaly["anomaly_types"])

    def test_fork_request_all_positive_match_actual_fork_and_no_fork_is_proven(self) -> None:
        for requested, transcript_factory, expected_observed in (
            ("all", forked_child_events, True),
            (3, forked_child_events, True),
            ("all", child_events, False),
        ):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                root = work / "records"
                transcript = work / "child.jsonl"
                write_jsonl(transcript, transcript_factory())
                self.pre_spawn(root, fork_turns=requested)
                self.post_spawn(root)
                self.child_start(root)
                self.child_stop(root, transcript)
                output = self.finalize(root)
                record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
                self.assertEqual(record["agents"][0]["fork_observed"], expected_observed)
                anomaly_types = (
                    json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))["anomaly_types"]
                    if output.get("anomaly")
                    else []
                )
                if expected_observed is False:
                    self.assertIn("fork-request-observation-mismatch", anomaly_types)
                else:
                    self.assertNotIn("fork-request-observation-mismatch", anomaly_types)

    def test_coordination_metrics_scan_parent_suffix_without_message_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / "parent.jsonl"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID},
                    }
                ],
            )
            self.pre_spawn(root, transcript=parent)
            suffix = [
                {
                    "timestamp": "2026-08-01T00:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "wait_agent",
                        "call_id": "wait-one",
                        "arguments": json.dumps({"timeout_ms": 1000}),
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "wait-one",
                        "output": {"status": "timeout", "duration_ms": 1000},
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "wait_agent",
                        "call_id": "wait-two",
                        "arguments": json.dumps({"timeout_ms": 2000}),
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:04Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "wait-two",
                        "output": {"status": "timeout", "duration_ms": 2000},
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:05Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "list_agents",
                        "call_id": "list-one",
                        "arguments": "{}",
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:06Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "list-one",
                        "output": {"status": "completed"},
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:07Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "collaboration.send_message",
                        "call_id": "send-one",
                        "arguments": json.dumps(
                            {"target": "/root/reviewer", "message": "SECRET BODY"}
                        ),
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:08Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "send-one",
                        "output": {"status": "completed"},
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:09Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "commentary",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [{"type": "text", "text": "SECRET COMMENTARY BODY"}],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:09.100Z",
                    "type": "response_item",
                    "payload": {
                        "type": "agent_message",
                        "author": "/root/reviewer",
                        "recipient": "/root",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [
                            {"type": "text", "text": "Message Type: MESSAGE\nSECRET CHILD BODY"}
                        ],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:09.200Z",
                    "type": "response_item",
                    "payload": {
                        "type": "agent_message",
                        "author": "/root/reviewer",
                        "recipient": "/root",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [
                            {"type": "text", "text": "Message Type: FINAL_ANSWER\nSECRET CHILD FINAL"}
                        ],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:09.300Z",
                    "type": "response_item",
                    "payload": {
                        "type": "agent_message",
                        "author": "/root/reviewer",
                        "recipient": "/root",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [
                            {"type": "text", "text": "Not a handback\nSECRET CHILD UNKNOWN"}
                        ],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:10Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [{"type": "text", "text": "SECRET FINAL BODY"}],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:11Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "phase": "future_phase",
                        "internal_chat_message_metadata_passthrough": {
                            "turn_id": TURN_ID
                        },
                        "content": [{"type": "text", "text": "SECRET UNKNOWN BODY"}],
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:11.500Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "phase": "commentary",
                        "message": "SECRET UNIDENTIFIED EVENT BODY",
                    },
                },
                {
                    "timestamp": "2026-08-01T00:00:12Z",
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": TURN_ID},
                },
            ]
            with parent.open("a", encoding="utf-8") as handle:
                for event in suffix:
                    handle.write(json.dumps(event) + "\n")
            self.post_spawn(root)
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            metrics = record["coordination_metrics"]
            self.assertEqual(metrics["operation_counts"]["wait_agent"], 2)
            self.assertEqual(metrics["operation_counts"]["list_agents"], 1)
            self.assertEqual(metrics["operation_counts"]["send_message"], 1)
            self.assertEqual(metrics["wait_outcomes"]["timeout"], 2)
            self.assertEqual(metrics["requested_wait_ms"], 3000)
            self.assertEqual(metrics["observed_wait_ms"], 3000)
            self.assertEqual(metrics["max_consecutive_timeout_without_agent_update"], 2)
            self.assertEqual(
                metrics["parent_message_phase_counts"],
                {"commentary": 1, "final_answer": 1, "unknown": 1},
            )
            self.assertEqual(metrics["parent_message_observability"], "observed")
            self.assertEqual(
                metrics["child_handback_counts"],
                {"message": 1, "final_answer": 1, "unknown": 1},
            )
            self.assertEqual(metrics["child_handback_observability"], "observed")
            self.assertEqual(metrics["steering_applied"], "not_observed")
            self.assertEqual(metrics["observability"], "observed")
            first_bytes = record["collector"]["parent_transcript_bytes_processed"]
            second = self.finalize(root)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["routine"], output["routine"])
            self.assertEqual(
                len(list((root / "routine").glob("*.json"))),
                1,
            )
            second_record = json.loads(
                Path(second["routine"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                second_record["collector"]["parent_transcript_bytes_processed"],
                first_bytes,
            )
            self.assertEqual(
                second_record["coordination_metrics"]["operation_counts"],
                metrics["operation_counts"],
            )
            state_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "hook-state").rglob("*.json*")
                if path.is_file()
            )
            self.assertNotIn("SECRET BODY", state_text)
            self.assertNotIn("SECRET COMMENTARY BODY", state_text)
            self.assertNotIn("SECRET FINAL BODY", state_text)
            self.assertNotIn("SECRET UNKNOWN BODY", state_text)
            self.assertNotIn("SECRET UNIDENTIFIED EVENT BODY", state_text)
            self.assertNotIn("SECRET CHILD BODY", state_text)
            self.assertNotIn("SECRET CHILD FINAL", state_text)
            self.assertNotIn("SECRET CHILD UNKNOWN", state_text)

    def test_coordination_without_parent_path_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            self.pre_spawn(root)
            self.post_spawn(root)
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            metrics = record["coordination_metrics"]
            self.assertEqual(metrics["observability"], "unknown")
            self.assertEqual(metrics["operation_observability"]["wait_agent"], "not_observed")

    def test_dry_run_does_not_write_hook_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / "parent.jsonl"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID},
                    }
                ],
            )
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T00:00:01Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": "wait_agent",
                                "call_id": "wait-dry-run",
                                "arguments": '{"timeout_ms":1000}',
                            },
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T00:00:02Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "wait-dry-run",
                                "output": '{"status":"completed"}',
                            },
                        }
                    )
                    + "\n"
                )
            self.post_spawn(root)
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            result = self.finalize(root, "--dry-run")
            self.assertFalse(result.get("idempotent", False))
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_stale_coordination_cache_reclassifies_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / "parent.jsonl"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID},
                    }
                ],
            )
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                for event in (
                    {
                        "timestamp": "2026-08-01T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "wait_agent",
                            "call_id": "wait-stale",
                            "arguments": '{"timeout_ms":1000}',
                        },
                    },
                    {
                        "timestamp": "2026-08-01T00:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "wait-stale",
                            "output": "timeout_ms must be at least 10000",
                        },
                    },
                ):
                    handle.write(json.dumps(event) + "\n")
            self.post_spawn(root)
            dirs = RECORD_HOOK.state_dirs(root)
            events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            )
            _, first_stats = RECORD_HOOK.recover_parent_tail(
                root, PARENT_ID, TURN_ID, events
            )
            cursor_path = RECORD_HOOK.parent_cursor_path(dirs, PARENT_ID, TURN_ID)
            cursor = RECORD_HOOK.read_json(cursor_path)
            cursor["coordination_metrics"]["version"] = 3
            cursor["coordination_metrics"]["wait_outcomes"] = {
                "timeout": 1,
                "completed": 0,
                "error": 0,
                "unknown": 0,
            }
            cursor["coordination_calls"] = {
                "wait-stale": {"operation": "wait_agent", "outcome_recorded": True}
            }
            RECORD_HOOK.atomic_json(cursor_path, cursor)
            _, refreshed = RECORD_HOOK.recover_parent_tail(
                root, PARENT_ID, TURN_ID, events, persist=False
            )
            self.assertEqual(refreshed["coordination_metrics"]["version"], 4)
            self.assertEqual(
                refreshed["coordination_metrics"]["wait_outcomes"]["error"],
                1,
            )
            self.assertEqual(
                refreshed["coordination_metrics"]["wait_outcomes"]["timeout"],
                0,
            )

    def test_closed_stale_cursor_persists_classifier_upgrade_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / "parent.jsonl"
            write_jsonl(
                parent,
                [{"timestamp": "2026-08-01T00:00:00Z", "type": "session_meta", "payload": {"id": PARENT_ID}}],
            )
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                for event in (
                    {
                        "timestamp": "2026-08-01T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "wait_agent",
                            "call_id": "closed-stale-wait",
                            "arguments": '{"timeout_ms":1000}',
                        },
                    },
                    {
                        "timestamp": "2026-08-01T00:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "closed-stale-wait",
                            "output": "timeout_ms must be at least 10000",
                        },
                    },
                ):
                    handle.write(json.dumps(event) + "\n")
            self.post_spawn(root)
            dirs = RECORD_HOOK.state_dirs(root)
            events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            )
            RECORD_HOOK.recover_parent_tail(root, PARENT_ID, TURN_ID, events)
            cursor_path = RECORD_HOOK.parent_cursor_path(dirs, PARENT_ID, TURN_ID)
            cursor = RECORD_HOOK.read_json(cursor_path)
            cursor["coordination_metrics"]["version"] = 3
            cursor["closed"] = True
            cursor["recovery_window_closed"] = True
            RECORD_HOOK.atomic_json(cursor_path, cursor)
            with mock.patch.object(
                RECORD_HOOK,
                "consume_parent_coordination_event",
                wraps=RECORD_HOOK.consume_parent_coordination_event,
            ) as consume:
                _, stats = RECORD_HOOK.recover_parent_tail(
                    root, PARENT_ID, TURN_ID, events, persist=True
                )
            self.assertEqual(stats["new_bytes_processed"], 0)
            upgraded = RECORD_HOOK.read_json(cursor_path)
            self.assertEqual(upgraded["coordination_metrics"]["version"], 4)
            self.assertGreater(consume.call_count, 0)
            after_upgrade = cursor_path.read_bytes()
            with mock.patch.object(
                RECORD_HOOK,
                "consume_parent_coordination_event",
                wraps=RECORD_HOOK.consume_parent_coordination_event,
            ) as consume_again:
                _, second_stats = RECORD_HOOK.recover_parent_tail(
                    root, PARENT_ID, TURN_ID, events, persist=True
                )
            self.assertEqual(second_stats["new_bytes_processed"], 0)
            self.assertEqual(consume_again.call_count, 0)
            self.assertEqual(cursor_path.read_bytes(), after_upgrade)

    def test_wait_outcome_classifier_prioritizes_explicit_error_and_completion(self) -> None:
        self.assertEqual(
            RECORD_HOOK.classify_wait_outcome(
                '{"message":"Wait completed.","timed_out":false}'
            ),
            "completed",
        )
        self.assertEqual(
            RECORD_HOOK.classify_wait_outcome("timeout_ms must be at least 10000"),
            "error",
        )
        self.assertEqual(
            RECORD_HOOK.classify_wait_outcome('{"status":"timed_out"}'),
            "timeout",
        )

    def test_followup_binding_requires_successful_posttooluse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            common = {
                "session_id": PARENT_ID,
                "turn_id": SECOND_TURN_ID,
                "cwd": "/tmp/hook-project",
                "tool_name": "collaboration.followup_task",
                "tool_use_id": "followup-call",
                "tool_input": {
                    "target": CHILD_ID,
                    "message": "FOLLOWUP BODY MUST NOT BE STORED",
                },
            }
            self.ingest(root, {**common, "hook_event_name": "PreToolUse"})
            self.assertIsNone(
                RECORD_HOOK.bound_parent_turn(root, CHILD_ID, PARENT_ID)
            )
            self.ingest(
                root,
                {
                    **common,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"isError": True, "message": "failed"},
                },
            )
            self.assertIsNone(
                RECORD_HOOK.bound_parent_turn(root, CHILD_ID, PARENT_ID)
            )

            success = {
                **common,
                "tool_use_id": "followup-success",
                "hook_event_name": "PostToolUse",
                "tool_response": {"status": "completed"},
            }
            self.ingest(root, success)
            self.assertEqual(
                RECORD_HOOK.bound_parent_turn(root, CHILD_ID, PARENT_ID),
                SECOND_TURN_ID,
            )

    def test_late_old_stop_uses_child_turn_binding_after_followup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.ingest(
                root,
                {
                    "session_id": PARENT_ID,
                    "turn_id": SECOND_TURN_ID,
                    "cwd": "/tmp/hook-project",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "collaboration.followup_task",
                    "tool_use_id": "followup-success",
                    "tool_input": {
                        "target": CHILD_ID,
                        "message": "FOLLOWUP BODY MUST NOT BE STORED",
                    },
                    "tool_response": {"status": "completed"},
                },
            )
            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            dirs = RECORD_HOOK.state_dirs(root)
            first_events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            )
            second_events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, SECOND_TURN_ID)
            )
            self.assertEqual(
                sum(event.get("kind") == "subagent_stop" for event in first_events),
                1,
            )
            self.assertFalse(
                any(event.get("kind") == "subagent_stop" for event in second_events)
            )

    def test_followup_does_not_claim_preexisting_unbound_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            self.pre_spawn(root)
            self.post_spawn_path_only(root)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.ingest(
                root,
                {
                    "session_id": PARENT_ID,
                    "turn_id": SECOND_TURN_ID,
                    "cwd": "/tmp/hook-project",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "collaboration.followup_task",
                    "tool_use_id": "followup-success",
                    "tool_input": {
                        "target": CHILD_ID,
                        "message": "FOLLOWUP BODY MUST NOT BE STORED",
                    },
                    "tool_response": {"status": "completed"},
                },
            )

            self.assertTrue(
                RECORD_HOOK.has_pending_lifecycle(root, PARENT_ID, CHILD_ID)
            )
            self.assertIsNone(
                RECORD_HOOK.bound_parent_turn(root, CHILD_ID, PARENT_ID)
            )
            dirs = RECORD_HOOK.state_dirs(root)
            second_events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, SECOND_TURN_ID)
            )
            self.assertFalse(
                any(event.get("kind") == "subagent_start" for event in second_events)
            )

    def test_hook_storage_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            dirs = RECORD_HOOK.state_dirs(root)
            RECORD_HOOK.append_event(
                root,
                PARENT_ID,
                TURN_ID,
                {
                    "kind": "tool_pre",
                    "operation": "spawn_agent",
                    "observed_at": "2026-08-01T01:00:00Z",
                },
            )
            RECORD_HOOK.atomic_json(
                dirs["path_bindings"] / "binding.json", {"ok": True}
            )
            RECORD_HOOK.write_new(dirs["routine"] / "record.json", {"ok": True})

            for path in (root, *root.rglob("*")):
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))

    def test_atomic_json_fsyncs_parent_directory_after_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "state"
            parent.mkdir()
            target = parent / "cursor.json"
            calls: list[Path] = []
            original = RECORD_HOOK.fsync_directory
            RECORD_HOOK.fsync_directory = lambda path: calls.append(path)
            try:
                RECORD_HOOK.atomic_json(target, {"offset": 7})
            finally:
                RECORD_HOOK.fsync_directory = original
            self.assertEqual(calls, [parent])

    def test_event_marker_is_fsynced_before_index_cursor_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            calls: list[Path] = []
            original = RECORD_HOOK.fsync_directory

            def observe(path: Path) -> None:
                calls.append(path)
                original(path)

            RECORD_HOOK.fsync_directory = observe
            try:
                RECORD_HOOK.append_event(
                    root,
                    PARENT_ID,
                    TURN_ID,
                    {
                        "kind": "tool_pre",
                        "operation": "spawn_agent",
                        "tool_use_id": "ordered-call",
                        "observed_at": "2026-08-01T01:00:00Z",
                    },
                )
            finally:
                RECORD_HOOK.fsync_directory = original
            dirs = RECORD_HOOK.state_dirs(root)
            marker_dir = RECORD_HOOK.event_key_path(
                dirs,
                PARENT_ID,
                TURN_ID,
                RECORD_HOOK.event_key(
                    {
                        "kind": "tool_pre",
                        "operation": "spawn_agent",
                        "tool_use_id": "ordered-call",
                        "observed_at": "2026-08-01T01:00:00Z",
                    }
                ),
            ).parent
            cursor_dir = RECORD_HOOK.event_key_cursor_path(
                dirs, PARENT_ID, TURN_ID
            ).parent
            marker_positions = [index for index, path in enumerate(calls) if path == marker_dir]
            cursor_positions = [index for index, path in enumerate(calls) if path == cursor_dir]
            self.assertTrue(marker_positions)
            self.assertTrue(cursor_positions)
            self.assertLess(max(marker_positions), max(cursor_positions))

    def test_parent_recovery_accepts_unique_path_to_stop_binding(self) -> None:
        events = [
            {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "path-call",
            },
            {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "path-call",
                "outcome": "started",
                "child_ref": "/root/path-child",
            },
            {
                "kind": "subagent_start",
                "agent_id": CHILD_ID,
            },
            {
                "kind": "subagent_stop",
                "agent_id": CHILD_ID,
                "metrics": {
                    "agent_id": CHILD_ID,
                    "agent_path": "/root/path-child",
                },
            },
        ]
        self.assertFalse(RECORD_HOOK.needs_parent_recovery(events))

    def test_parent_recovery_does_not_let_another_start_mask_unbound_spawn(self) -> None:
        known_id = "019fbd56-9999-7abc-8def-1234567890ab"
        events = [
            {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "missing-call",
            },
            {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "missing-call",
                "outcome": "started",
                "child_ref": "/root/missing-child",
            },
            {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "known-call",
            },
            {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "known-call",
                "outcome": "started",
                "agent_id": known_id,
                "child_ref": "/root/known-child",
            },
            {
                "kind": "subagent_start",
                "agent_id": known_id,
            },
            {
                "kind": "subagent_stop",
                "agent_id": known_id,
                "metrics": {
                    "agent_id": known_id,
                    "agent_path": "/root/known-child",
                },
            },
        ]
        self.assertTrue(RECORD_HOOK.needs_parent_recovery(events))

    def test_interleaved_lifecycle_uses_path_identity_not_event_order(self) -> None:
        ids = {
            "alpha": "019fbd56-0000-7abc-8def-1234567890ab",
            "beta": "019fbd56-0001-7abc-8def-1234567890ab",
            "gamma": "019fbd56-0002-7abc-8def-1234567890ab",
        }
        paths = {name: f"/root/{name}-task" for name in ids}
        events: list[dict] = []
        for index, name in enumerate(ids):
            call_id = f"call-{name}"
            events.extend(
                [
                    {
                        "kind": "tool_pre",
                        "operation": "spawn_agent",
                        "tool_use_id": call_id,
                        "task_name": name,
                        "requested_role": "explorer",
                        "observed_at": f"2026-08-01T01:0{index}:00Z",
                    },
                    {
                        "kind": "tool_post",
                        "operation": "spawn_agent",
                        "tool_use_id": call_id,
                        "task_name": name,
                        "child_ref": paths[name],
                        "outcome": "started",
                        "observed_at": f"2026-08-01T01:0{index}:01Z",
                    },
                    {
                        "kind": "subagent_start",
                        "agent_id": ids[name],
                        "role": "explorer",
                        "observed_at": f"2026-08-01T01:0{index}:02Z",
                    },
                ]
            )

        # Completion order is beta, gamma, alpha, intentionally different
        # from both spawn and start order.
        for offset, name in enumerate(("beta", "gamma", "alpha"), start=3):
            events.append(
                {
                    "kind": "subagent_stop",
                    "agent_id": ids[name],
                    "role": "explorer",
                    "observed_at": f"2026-08-01T01:0{offset}:00Z",
                    "metrics": {
                        "agent_id": ids[name],
                        "agent_path": paths[name],
                        "role": "explorer",
                        "status": "completed",
                        "turn_count": 1,
                    },
                }
            )

        attempts = RECORD_HOOK.connected_attempts(events)
        agents = RECORD_HOOK.build_agents(events, attempts, {})
        attempt_by_name = {item["task_name"]: item for item in attempts}
        agent_by_id = {item["agent_id"]: item for item in agents}
        for name, agent_id in ids.items():
            self.assertEqual(attempt_by_name[name]["agent_id"], agent_id)
            self.assertEqual(attempt_by_name[name]["binding"], "agent-path")
            self.assertEqual(agent_by_id[agent_id]["agent_path"], paths[name])
            self.assertEqual(agent_by_id[agent_id]["task_name"], name)

    def test_ambiguous_path_binding_remains_unbound(self) -> None:
        shared_path = "/root/shared-task"
        events = [
            {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "call-one",
                "task_name": "one",
                "observed_at": "2026-08-01T01:00:00Z",
            },
            {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "call-one",
                "task_name": "one",
                "child_ref": shared_path,
                "outcome": "started",
                "observed_at": "2026-08-01T01:00:01Z",
            },
            {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "call-two",
                "task_name": "two",
                "observed_at": "2026-08-01T01:00:02Z",
            },
            {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "call-two",
                "task_name": "two",
                "child_ref": shared_path,
                "outcome": "started",
                "observed_at": "2026-08-01T01:00:03Z",
            },
        ]
        for agent_id in (
            "019fbd56-0010-7abc-8def-1234567890ab",
            "019fbd56-0011-7abc-8def-1234567890ab",
        ):
            events.append(
                {
                    "kind": "subagent_start",
                    "agent_id": agent_id,
                    "role": "explorer",
                    "observed_at": "2026-08-01T01:00:04Z",
                }
            )
            events.append(
                {
                    "kind": "subagent_stop",
                    "agent_id": agent_id,
                    "role": "explorer",
                    "observed_at": "2026-08-01T01:00:05Z",
                    "metrics": {
                        "agent_id": agent_id,
                        "agent_path": shared_path,
                        "role": "explorer",
                        "status": "completed",
                        "turn_count": 1,
                    },
                }
            )
        attempts = RECORD_HOOK.connected_attempts(events)
        self.assertEqual([item.get("agent_id") for item in attempts], [None, None])
        self.assertTrue(all(item.get("binding") == "ambiguous" for item in attempts))

    def test_ambiguous_direct_agent_id_candidates_are_retained(self) -> None:
        agent_id = "019fbd56-0020-7abc-8def-1234567890ab"
        events = []
        for index, task_name in enumerate(("one", "two")):
            events.extend(
                [
                    {
                        "kind": "tool_pre",
                        "operation": "spawn_agent",
                        "tool_use_id": f"call-{task_name}",
                        "task_name": task_name,
                        "observed_at": f"2026-08-01T01:01:0{index}Z",
                    },
                    {
                        "kind": "tool_post",
                        "operation": "spawn_agent",
                        "tool_use_id": f"call-{task_name}",
                        "task_name": task_name,
                        "child_ref": agent_id,
                        "agent_id": agent_id,
                        "outcome": "started",
                        "observed_at": f"2026-08-01T01:01:1{index}Z",
                    },
                ]
            )
        events.extend(
            [
                {
                    "kind": "subagent_start",
                    "agent_id": agent_id,
                    "role": "explorer",
                    "observed_at": "2026-08-01T01:01:20Z",
                },
                {
                    "kind": "subagent_stop",
                    "agent_id": agent_id,
                    "role": "explorer",
                    "observed_at": "2026-08-01T01:01:30Z",
                    "metrics": {
                        "agent_id": agent_id,
                        "agent_path": "/root/shared-id",
                        "role": "explorer",
                        "status": "completed",
                        "turn_count": 1,
                    },
                },
            ]
        )
        attempts = RECORD_HOOK.connected_attempts(events)
        self.assertEqual([item.get("agent_id") for item in attempts], [None, None])
        self.assertTrue(all(item.get("binding") == "ambiguous" for item in attempts))
        self.assertTrue(
            all(item.get("binding_candidates") == [agent_id] for item in attempts)
        )

    def test_binding_anomaly_waits_for_terminal_lifecycle(self) -> None:
        attempt = {
            "call_id": "call-unbound",
            "task_name": "unbound-task",
            "outcome": "started",
            "binding": "unbound",
            "agent_id": None,
        }
        terminal_agent = {
            "agent_id": CHILD_ID,
            "role": "explorer",
            "status": "completed",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "medium",
            "nested_agent_calls": 0,
        }
        types, evidence = RECORD_HOOK.automatic_anomaly_types(
            [attempt], [terminal_agent], False
        )
        self.assertIn("agent-binding-uncertainty", types)
        self.assertIn("call-unbound", " ".join(evidence))

        running_agent = {**terminal_agent, "status": "unknown"}
        types, _ = RECORD_HOOK.automatic_anomaly_types(
            [attempt], [running_agent], False
        )
        self.assertNotIn("agent-binding-uncertainty", types)
        self.assertIn("child-lifecycle-incomplete", types)

        failed_attempt = {**attempt, "outcome": "errored", "error": "tool-error"}
        types, _ = RECORD_HOOK.automatic_anomaly_types(
            [failed_attempt], [terminal_agent], False
        )
        self.assertNotIn("agent-binding-uncertainty", types)

        malformed_ambiguous = {**attempt, "agent_id": CHILD_ID, "binding": "ambiguous"}
        types, _ = RECORD_HOOK.automatic_anomaly_types(
            [malformed_ambiguous], [terminal_agent], False
        )
        self.assertIn("agent-binding-uncertainty", types)

    def test_hook_path_records_facts_without_raw_prompt_or_parent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 4)
            self.assertEqual(record["collection_mode"], "codex-hooks-incremental")
            self.assertEqual(record["project"], "/tmp/hook-project")
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["attempt_count"], 1)
            self.assertIsNone(record["assessment"])
            self.assertEqual(record["collector"]["parent_transcript_bytes_processed"], 0)
            agent = record["agents"][0]
            self.assertEqual(agent["model"], "gpt-5.6-luna")
            self.assertEqual(agent["reasoning_effort"], "medium")
            self.assertEqual(agent["status"], "completed")
            self.assertEqual(agent["turn_count"], 1)
            self.assertEqual(agent["tool_calls"], 1)
            self.assertEqual(agent["token_usage"]["total_tokens"], 130)
            self.assertEqual(
                agent["runtime_resolution"]["expected"]["service_tier"],
                "standard",
            )
            self.assertEqual(
                agent["runtime_resolution"]["match"]["service_tier"],
                "not_observed",
            )
            self.assertEqual(
                agent["effective_authority"]["sandbox_mode"], "read-only"
            )
            self.assertEqual(
                agent["effective_authority"]["workspace_root_count"], 2
            )
            self.assertEqual(
                agent["runtime_provenance"]["cli_version"], "0.147.0-test"
            )
            self.assertNotIn("/secret/workspace-a", json.dumps(record))
            self.assertEqual(agent["metric_snapshot"]["sequence"], 1)
            self.assertEqual(agent["metric_snapshot"]["delta"]["tool_calls"], 1)
            self.assertIsNone(agent["result_summary"])
            self.assertIsNone(output["anomaly"])

            state_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "hook-state").rglob("*.json*")
                if path.is_file()
            )
            self.assertNotIn("SECRET RAW CHILD PROMPT", state_text)
            self.assertNotIn("SECRET RAW FINAL MESSAGE", state_text)
            self.assertNotIn("A raw child result", state_text)

            second = self.finalize(root)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["routine"], output["routine"])
            self.assertEqual(len(list((root / "routine").glob("*.json"))), 1)
            audit = subprocess.run(
                [sys.executable, str(AUDITOR), "audit", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["valid"])

    def test_stop_hook_auto_finalizes_only_turns_with_agent_evidence(self) -> None:
        stop_payload = {
            "session_id": PARENT_ID,
            "turn_id": TURN_ID,
            "cwd": "/tmp/hook-project",
            "hook_event_name": "Stop",
            "last_assistant_message": "SECRET PARENT FINAL",
            "stop_hook_active": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ordinary-records"
            result = self.run_script(
                ["auto-finalize", "--root", str(root)], stop_payload
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertFalse(root.exists())

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "delegated-records"
            transcript = work / "child.jsonl"
            parent = work / "parent.jsonl"
            write_jsonl(transcript, child_events())
            write_jsonl(parent, [parent_session_event()])
            self.pre_spawn(root, transcript=parent)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(parent_final_event()) + "\n")
            stop_payload["transcript_path"] = str(parent)
            result = self.run_script(
                ["auto-finalize", "--root", str(root)], stop_payload
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            routines = list((root / "routine").glob("*.json"))
            self.assertEqual(len(routines), 1)
            record = json.loads(routines[0].read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], 4)
            self.assertEqual(record["finalization"]["trigger"], "parent-stop-hook")
            self.assertTrue(record["finalization"]["automatic"])
            self.assertTrue(record["finalization"]["provenance_verified"])
            self.assertNotIn("SECRET PARENT FINAL", json.dumps(record))

            replay = {
                **stop_payload,
                "last_assistant_message": "A DIFFERENT SIMULATED MESSAGE",
            }
            second = self.run_script(
                ["auto-finalize", "--root", str(root)], replay
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(list((root / "routine").glob("*.json"))), 1)
            journal = RECORD_HOOK.turn_journal(
                RECORD_HOOK.state_dirs(root), PARENT_ID, TURN_ID
            )
            parent_stops = [
                event
                for event in RECORD_HOOK.load_journal(journal)
                if event.get("kind") == "parent_stop"
            ]
            self.assertEqual(len(parent_stops), 1)

    def test_simulated_stop_before_final_response_cannot_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            child = work / "child.jsonl"
            parent = work / "parent.jsonl"
            write_jsonl(child, child_events())
            write_jsonl(parent, [parent_session_event()])
            self.pre_spawn(root, transcript=parent)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, child)
            result = self.run_script(
                ["auto-finalize", "--root", str(root)],
                {
                    "session_id": PARENT_ID,
                    "turn_id": TURN_ID,
                    "cwd": "/tmp/hook-project",
                    "hook_event_name": "Stop",
                    "transcript_path": str(parent),
                    "last_assistant_message": "SIMULATED BEFORE FINAL",
                    "stop_hook_active": False,
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("before the final assistant response", result.stderr)
            self.assertEqual(len(list((root / "routine").glob("*.json"))), 0)
            journal = RECORD_HOOK.turn_journal(
                RECORD_HOOK.state_dirs(root), PARENT_ID, TURN_ID
            )
            self.assertFalse(
                any(
                    event.get("kind") == "parent_stop"
                    for event in RECORD_HOOK.load_journal(journal)
                )
            )

    def test_production_manual_finalize_requires_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            production = Path(directory) / "records"
            with mock.patch.object(RECORD_HOOK, "DEFAULT_ROOT", production):
                with self.assertRaisesRegex(
                    ValueError, "production records may only be written"
                ):
                    RECORD_HOOK.enforce_manual_finalize_scope(production, False)
                RECORD_HOOK.enforce_manual_finalize_scope(production, True)

    def test_parent_stop_defers_until_terminal_and_writes_one_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            child = work / "child.jsonl"
            parent = work / "parent.jsonl"
            write_jsonl(child, child_events()[:-1])
            write_jsonl(parent, [parent_session_event()])
            self.pre_spawn(root, transcript=parent)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, child)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(parent_final_event()) + "\n")
            stop_payload = {
                "session_id": PARENT_ID,
                "turn_id": TURN_ID,
                "cwd": "/tmp/hook-project",
                "hook_event_name": "Stop",
                "transcript_path": str(parent),
                "last_assistant_message": "SECRET PARENT FINAL",
                "stop_hook_active": False,
            }
            stop = self.run_script(
                ["auto-finalize", "--root", str(root)], stop_payload
            )
            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertEqual(len(list((root / "routine").glob("*.json"))), 0)

            with child.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(child_events()[-1]) + "\n")
            self.child_stop(root, child)

            routines = list((root / "routine").glob("*.json"))
            self.assertEqual(len(routines), 1)
            record = json.loads(routines[0].read_text(encoding="utf-8"))
            self.assertEqual(record["source"], "live")
            self.assertEqual(record["supersedes"], [])
            self.assertEqual(record["agents"][0]["status"], "completed")
            self.assertEqual(record["finalization"]["trigger"], "parent-stop-hook")
            anomaly_path = root / "anomalies" / routines[0].name
            if anomaly_path.is_file():
                anomaly = json.loads(anomaly_path.read_text(encoding="utf-8"))
                self.assertNotIn("record-correction", anomaly["anomaly_types"])

    def test_reused_child_records_expose_non_overlapping_metric_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            first = self.finalize(root)
            first_record = json.loads(
                Path(first["routine"]).read_text(encoding="utf-8")
            )

            with transcript.open("a", encoding="utf-8") as handle:
                for event in forked_child_events(followup=True)[-4:]:
                    handle.write(json.dumps(event) + "\n")
            self.pre_spawn(root, parent_turn_id=SECOND_TURN_ID)
            self.post_spawn(root, parent_turn_id=SECOND_TURN_ID)
            self.child_start(root, hook_turn_id=SECOND_TURN_ID)
            self.child_stop(root, transcript, hook_turn_id=SECOND_TURN_ID)
            second = self.finalize_turn(root, SECOND_TURN_ID)
            second_record = json.loads(
                Path(second["routine"]).read_text(encoding="utf-8")
            )

            first_snapshot = first_record["agents"][0]["metric_snapshot"]
            second_snapshot = second_record["agents"][0]["metric_snapshot"]
            self.assertEqual(first_snapshot["sequence"], 1)
            self.assertEqual(first_snapshot["delta"]["tool_calls"], 1)
            self.assertEqual(second_snapshot["sequence"], 2)
            self.assertEqual(second_snapshot["delta"]["turn_count"], 1)
            self.assertEqual(second_snapshot["delta"]["tool_calls"], 1)
            self.assertEqual(second_snapshot["delta"]["tool_outputs"], 1)
            self.assertEqual(second_snapshot["delta"]["event_count"], 4)
            self.assertEqual(
                first_snapshot["delta"]["tool_calls"]
                + second_snapshot["delta"]["tool_calls"],
                second_snapshot["cumulative"]["tool_calls"],
            )

    def test_terminal_error_is_stored_as_code_without_secret_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            events = child_events()
            events[-1] = {
                "timestamp": "2026-08-01T01:00:06Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_failed",
                    "error": "SECRET RAW CHILD TERMINAL ERROR",
                },
            }
            write_jsonl(transcript, events)
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            output = self.finalize(root)
            routine_text = Path(output["routine"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET RAW CHILD TERMINAL ERROR", routine_text)
            record = json.loads(routine_text)
            self.assertEqual(record["agents"][0]["error"], "tool-error")
            if output.get("anomaly"):
                anomaly_text = Path(output["anomaly"]).read_text(encoding="utf-8")
                self.assertNotIn("SECRET RAW CHILD TERMINAL ERROR", anomaly_text)
            state_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "hook-state").rglob("*.json*")
                if path.is_file()
            )
            self.assertNotIn("SECRET RAW CHILD TERMINAL ERROR", state_text)

    def test_complete_hooks_with_parent_path_skip_parent_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            child = work / f"rollout-child-{CHILD_ID}.jsonl"
            write_jsonl(
                parent,
                [{"timestamp": "2026-08-01T00:59:59Z", "type": "session_meta", "payload": {"id": PARENT_ID}}],
            )
            write_jsonl(child, child_events())
            self.pre_spawn(root, transcript=parent)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, child)
            dirs = RECORD_HOOK.state_dirs(root)
            events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            )
            _, stats = RECORD_HOOK.recover_parent_tail(
                root, PARENT_ID, TURN_ID, events
            )
            self.assertFalse(stats["used"])
            self.assertEqual(stats["bytes_processed"], 0)
            self.assertEqual(stats["new_bytes_processed"], 0)
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["collector"]["parent_transcript_bytes_processed"], 0)

    def test_transcript_cursor_only_processes_appended_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            first = child_events()[:-1]
            first.append(
                {
                    "timestamp": "2026-08-01T01:00:06Z",
                    "type": "event_msg",
                    "payload": {"type": "turn_aborted", "reason": "interrupted"},
                }
            )
            write_jsonl(transcript, first)
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            first_size = transcript.stat().st_size

            appended = [
                {
                    "timestamp": "2026-08-01T01:00:07Z",
                    "type": "event_msg",
                    "payload": {"type": "task_started"},
                },
                {
                    "timestamp": "2026-08-01T01:00:09Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": "Second turn complete.",
                    },
                },
            ]
            with transcript.open("a", encoding="utf-8") as handle:
                for event in appended:
                    handle.write(json.dumps(event) + "\n")
            self.child_stop(root, transcript)
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            agent = record["agents"][0]
            self.assertEqual(agent["turn_count"], 2)
            self.assertEqual(agent["status"], "completed")
            self.assertEqual(agent["collection_passes"], 2)
            self.assertEqual(agent["transcript_bytes_processed"], transcript.stat().st_size)
            self.assertGreater(agent["transcript_bytes_processed"], first_size)
            self.assertEqual(agent["event_count"], len(first) + len(appended))

    def test_forked_all_and_positive_history_skip_parent_metrics_and_count_followup(self) -> None:
        for fork_turns in ("all", 3):
            with self.subTest(fork_turns=fork_turns), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                root = work / "records"
                transcript = work / f"child-{fork_turns}.jsonl"
                write_jsonl(transcript, forked_child_events(fork_turns=fork_turns))
                self.pre_spawn(root, fork_turns=fork_turns)
                self.post_spawn(root)
                self.child_start(root)
                self.child_stop(root, transcript)

                first = self.finalize(root)
                record = json.loads(Path(first["routine"]).read_text(encoding="utf-8"))
                agent = record["agents"][0]
                self.assertEqual(agent["turn_count"], 1)
                self.assertEqual(agent["tool_calls"], 1)
                self.assertEqual(agent["tool_outputs"], 1)
                self.assertEqual(agent["token_usage"]["total_tokens"], 130)
                self.assertEqual(agent["event_count"], 7)
                self.assertEqual(record["collector"]["child_metric_boundary_version"], 1)
                self.assertEqual(record["collector"]["zero_yield_policy_version"], 1)

                with transcript.open("a", encoding="utf-8") as handle:
                    for event in forked_child_events(fork_turns=fork_turns, followup=True)[-4:]:
                        handle.write(json.dumps(event) + "\n")
                self.child_stop(root, transcript)
                second = self.finalize(root)
                corrected = json.loads(Path(second["routine"]).read_text(encoding="utf-8"))
                agent = corrected["agents"][0]
                self.assertEqual(agent["turn_count"], 2)
                self.assertEqual(agent["tool_calls"], 2)
                self.assertEqual(agent["tool_outputs"], 2)
                self.assertEqual(agent["event_count"], 11)

    def test_completed_zero_yield_child_is_anomaly(self) -> None:
        agent = {
            "agent_id": CHILD_ID,
            "role": "explorer",
            "status": "completed",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "medium",
            "tool_calls": 0,
            "final_message_chars": 0,
            "nested_agent_calls": 0,
        }
        types, evidence = RECORD_HOOK.automatic_anomaly_types([], [agent], False)
        self.assertIn("zero-yield-child", types)
        self.assertIn(CHILD_ID, " ".join(evidence))

    def test_completed_child_with_tool_activity_and_no_message_is_delivery_failure(self) -> None:
        agent = {
            "agent_id": CHILD_ID,
            "actual_role": "explorer",
            "role": "explorer",
            "status": "completed",
            "tool_calls": 1,
            "final_message_chars": 0,
        }
        types, evidence = RECORD_HOOK.automatic_anomaly_types([], [agent], False)
        self.assertIn("child-delivery-failure", types)
        self.assertIn(CHILD_ID, " ".join(evidence))

    def test_child_record_keeps_requested_and_actual_roles_and_unobserved_tier(self) -> None:
        attempts = [
            {
                "agent_id": CHILD_ID,
                "child_ref": "/root/role-test",
                "requested_role": "worker",
                "binding": "agent-id",
            }
        ]
        events = [
            {
                "kind": "subagent_start",
                "agent_id": CHILD_ID,
                "role": "worker",
                "observed_at": "2026-08-01T01:00:00Z",
            },
            {
                "kind": "subagent_stop",
                "agent_id": CHILD_ID,
                "metrics": {
                    "agent_id": CHILD_ID,
                    "agent_path": "/root/role-test",
                    "actual_role": None,
                    "role": None,
                    "metric_scope": "unobserved",
                    "metric_validity": "unobserved",
                    "metric_observability": "missing",
                    "status": "completed",
                    "turn_count": 1,
                    "tool_calls": 1,
                    "service_tier": None,
                    "service_tier_source": "not_observed",
                },
            },
        ]
        agent = RECORD_HOOK.build_agents(
            events, attempts, {}, PARENT_ID, TURN_ID
        )[0]
        self.assertEqual(agent["requested_role"], "worker")
        self.assertIsNone(agent["actual_role"])
        self.assertIsNone(agent["role"])
        self.assertEqual(agent["requested_role_source"], "spawn-request")
        self.assertEqual(agent["role_binding_source"], "not_observed")
        self.assertIsNone(agent["service_tier"])
        self.assertEqual(agent["service_tier_observability"], "not_observed")
        self.assertEqual(agent["metric_validity"], "unobserved")

    def test_observed_service_tier_aliases_are_validated_without_inference(self) -> None:
        base = {
            "agent_id": CHILD_ID,
            "actual_role": "explorer",
            "role": "explorer",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "medium",
            "started_at": "2026-08-12T07:00:00Z",
        }
        self.assertIsNone(
            RECORD_HOOK.service_tier_mismatch({**base, "service_tier": "fast"})
        )
        self.assertIsNone(
            RECORD_HOOK.service_tier_mismatch({**base, "service_tier": "priority"})
        )
        self.assertIsNone(
            RECORD_HOOK.service_tier_mismatch(
                {**base, "service_tier": None, "service_tier_source": "not_observed"}
            )
        )
        mismatch = RECORD_HOOK.service_tier_mismatch(
            {**base, "service_tier": "standard"}
        )
        self.assertIsNotNone(mismatch)
        self.assertIsNone(
            RECORD_HOOK.service_tier_mismatch(
                {
                    **base,
                    "started_at": "2026-08-12T00:00:00Z",
                    "service_tier": "default",
                }
            )
        )
        types, _ = RECORD_HOOK.automatic_anomaly_types(
            [], [{**base, "status": "completed", "service_tier": "standard"}], False
        )
        self.assertIn("runtime-capability-mismatch", types)

    def test_ordinary_child_lookup_uses_parent_and_uuid_date_without_global_rglob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            sessions = work / ".codex" / "sessions"
            compact = CHILD_ID.replace("-", "")
            child_time = RECORD_HOOK.datetime.fromtimestamp(
                int(compact[:12], 16) / 1000, RECORD_HOOK.timezone.utc
            )
            day_dir = sessions / child_time.strftime("%Y") / child_time.strftime("%m") / child_time.strftime("%d")
            day_dir.mkdir(parents=True)
            child = day_dir / f"rollout-{CHILD_ID}.jsonl"
            child.write_text("{}\n", encoding="utf-8")
            parent = work / "parent.jsonl"
            parent.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(RECORD_HOOK.Path, "home", return_value=work), mock.patch.object(
                RECORD_HOOK.Path,
                "rglob",
                side_effect=AssertionError("ordinary lookup must not rglob sessions"),
            ):
                found = RECORD_HOOK.locate_child_transcript(CHILD_ID, str(parent))
            self.assertEqual(found, child.resolve())

    def test_uuid_boundary_does_not_trust_copied_later_id_with_earlier_or_missing_timestamp(self) -> None:
        for missing_timestamp in (False, True):
            with self.subTest(missing_timestamp=missing_timestamp), tempfile.TemporaryDirectory() as directory:
                events = forked_child_events()
                live_task = next(
                    event
                    for event in events
                    if event.get("type") == "event_msg"
                    and event.get("payload", {}).get("type") == "task_started"
                    and event.get("payload", {}).get("turn_id") == CHILD_ID
                )
                live_task["payload"]["turn_id"] = "019fbd57-0000-7abc-8def-1234567890ab"
                if missing_timestamp:
                    live_task.pop("timestamp", None)
                else:
                    live_task["timestamp"] = "2026-08-06T23:00:06Z"
                transcript = Path(directory) / "forked-child.jsonl"
                write_jsonl(transcript, events)
                boundary = RECORD_HOOK.fork_metric_boundary(transcript)
                self.assertTrue(boundary is None or boundary[2] != "uuidv7-time")

    def test_fork_boundary_probe_does_not_materialize_transcript_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            transcript = work / "forked-child.jsonl"
            write_jsonl(transcript, forked_child_events())
            with mock.patch.object(
                RECORD_HOOK,
                "transcript_records",
                side_effect=AssertionError("fork probing must stay streaming"),
                create=True,
            ):
                metrics = RECORD_HOOK.harvest_transcript(work / "records", transcript)
            self.assertEqual(metrics["turn_count"], 1)
            self.assertEqual(metrics["tool_calls"], 1)

    def test_child_scoped_lifecycle_turn_is_routed_to_parent_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["turn_id"], TURN_ID)
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["agents"][0]["agent_id"], CHILD_ID)
            self.assertEqual(record["agents"][0]["status"], "completed")
            self.assertEqual(
                list((root / "hook-state" / "agents").glob("*.json")), []
            )

    def test_delayed_direct_id_lifecycle_does_not_follow_newer_parent_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn(root)
            self.touch_second_parent_turn(root)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["turn_id"], TURN_ID)
            self.assertEqual(record["agents"][0]["status"], "completed")
            dirs = RECORD_HOOK.state_dirs(root)
            second_events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, SECOND_TURN_ID)
            )
            self.assertFalse(
                any(
                    event.get("kind") in {"subagent_start", "subagent_stop"}
                    for event in second_events
                )
            )

    def test_delayed_path_only_lifecycle_resolves_without_arrival_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn_path_only(root)
            self.touch_second_parent_turn(root)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["turn_id"], TURN_ID)
            self.assertEqual(record["spawn_attempts"][0]["binding"], "agent-path")
            self.assertEqual(record["agents"][0]["status"], "completed")
            dirs = RECORD_HOOK.state_dirs(root)
            second_events = RECORD_HOOK.load_journal(
                RECORD_HOOK.turn_journal(dirs, PARENT_ID, SECOND_TURN_ID)
            )
            self.assertFalse(
                any(
                    event.get("kind") in {"subagent_start", "subagent_stop"}
                    for event in second_events
                )
            )
            self.assertEqual(
                list((root / "hook-state" / "pending-lifecycle").rglob("*.json")),
                [],
            )

    def test_duplicate_path_candidates_remain_pending_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root)
            self.post_spawn_path_only(root)
            for event_name, response in (
                ("PreToolUse", None),
                ("PostToolUse", {"task_name": "/root/hook-forward-test"}),
            ):
                payload = {
                    "session_id": PARENT_ID,
                    "turn_id": SECOND_TURN_ID,
                    "cwd": "/tmp/hook-project",
                    "hook_event_name": event_name,
                    "tool_name": "spawn_agent",
                    "tool_use_id": "second-spawn",
                    "tool_input": {
                        "agent_type": "explorer",
                        "message": "SECOND SPAWN BODY MUST NOT BE STORED",
                    },
                }
                if response is not None:
                    payload["tool_response"] = response
                self.ingest(root, payload)
            self.child_start(root, hook_turn_id=CHILD_TURN_ID)
            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            self.assertIsNone(
                RECORD_HOOK.parent_turn_from_path_binding(
                    root, PARENT_ID, "/root/hook-forward-test"
                )
            )
            dirs = RECORD_HOOK.state_dirs(root)
            for parent_turn_id in (TURN_ID, SECOND_TURN_ID):
                events = RECORD_HOOK.load_journal(
                    RECORD_HOOK.turn_journal(dirs, PARENT_ID, parent_turn_id)
                )
                self.assertFalse(
                    any(
                        event.get("kind") in {"subagent_start", "subagent_stop"}
                        for event in events
                    )
                )
            self.assertEqual(
                len(list((root / "hook-state" / "pending-lifecycle").rglob("*.json"))),
                1,
            )

    def test_nonterminal_closeout_is_anomaly_then_late_terminal_corrects_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events()[:-1])
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)

            first = self.finalize(root)
            first_record = json.loads(
                Path(first["routine"]).read_text(encoding="utf-8")
            )
            first_anomaly = json.loads(
                Path(first["anomaly"]).read_text(encoding="utf-8")
            )
            self.assertEqual(first_record["agents"][0]["status"], "unknown")
            self.assertEqual(first_record["collector"]["lifecycle_policy_version"], 1)
            self.assertIn(
                "child-lifecycle-incomplete", first_anomaly["anomaly_types"]
            )

            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(child_events()[-1]) + "\n")
            self.child_stop(root, transcript)
            second = self.finalize(root)
            second_record = json.loads(
                Path(second["routine"]).read_text(encoding="utf-8")
            )
            second_anomaly = json.loads(
                Path(second["anomaly"]).read_text(encoding="utf-8")
            )
            self.assertEqual(second_record["source"], "correction")
            self.assertEqual(second_record["supersedes"], [first_record["record_id"]])
            self.assertEqual(second_record["agents"][0]["status"], "completed")
            self.assertNotIn(
                "child-lifecycle-incomplete", second_anomaly["anomaly_types"]
            )
            self.assertIn("record-correction", second_anomaly["anomaly_types"])

            audit = subprocess.run(
                [sys.executable, str(AUDITOR), "audit", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["valid"])

    def test_replayed_hook_events_are_semantically_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            for _ in range(2):
                self.pre_spawn(root)
                self.post_spawn(root)
                self.child_start(root, hook_turn_id=CHILD_TURN_ID)
                self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)

            first = self.finalize(root)
            record = json.loads(Path(first["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["collector"]["hook_event_count"], 4)
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["agents"][0]["status"], "completed")

            self.child_stop(root, transcript, hook_turn_id=CHILD_TURN_ID)
            second = self.finalize(root)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["routine"], first["routine"])

    def test_journal_dedup_uses_persistent_key_index_after_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            event = {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "indexed-call",
                "observed_at": "2026-08-01T01:00:00Z",
            }
            RECORD_HOOK.append_event(root, PARENT_ID, TURN_ID, event)
            original = RECORD_HOOK.load_journal

            def fail_if_scanning(*args, **kwargs):
                raise AssertionError("append must not scan the full journal")

            RECORD_HOOK.load_journal = fail_if_scanning
            try:
                RECORD_HOOK.append_event(root, PARENT_ID, TURN_ID, event)
            finally:
                RECORD_HOOK.load_journal = original
            dirs = RECORD_HOOK.state_dirs(root)
            journal = RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            self.assertEqual(len(RECORD_HOOK.load_journal(journal)), 1)
            key_dir = dirs["event_keys"] / RECORD_HOOK.turn_key(PARENT_ID, TURN_ID)
            self.assertTrue(any(path.name != ".initialized" for path in key_dir.iterdir()))

    def test_journal_index_recovers_durable_suffix_after_marker_crash_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            first = {
                "kind": "tool_pre",
                "operation": "spawn_agent",
                "tool_use_id": "first-call",
                "observed_at": "2026-08-01T01:00:00Z",
            }
            late = {
                "kind": "tool_post",
                "operation": "spawn_agent",
                "tool_use_id": "late-call",
                "outcome": "started",
                "child_ref": "/root/late-child",
                "observed_at": "2026-08-01T01:00:01Z",
            }
            RECORD_HOOK.append_event(root, PARENT_ID, TURN_ID, first)
            dirs = RECORD_HOOK.state_dirs(root)
            journal = RECORD_HOOK.turn_journal(dirs, PARENT_ID, TURN_ID)
            cursor = RECORD_HOOK.event_key_cursor_path(dirs, PARENT_ID, TURN_ID)
            before = RECORD_HOOK.read_json(cursor)
            late_key = RECORD_HOOK.event_key(late)
            # Simulate a process dying after journal fsync but before index
            # marker/cursor commit.
            with journal.open("a", encoding="utf-8") as handle:
                json.dump(
                    {
                        "parent_thread_id": PARENT_ID,
                        "turn_id": TURN_ID,
                        **late,
                        "event_key": late_key,
                    },
                    handle,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            marker = RECORD_HOOK.event_key_path(dirs, PARENT_ID, TURN_ID, late_key)
            self.assertFalse(marker.exists())
            RECORD_HOOK.append_event(root, PARENT_ID, TURN_ID, late)
            events = RECORD_HOOK.load_journal(journal)
            self.assertEqual(
                sum(item.get("event_key") == late_key for item in events), 1
            )
            after = RECORD_HOOK.read_json(cursor)
            self.assertEqual(after["offset"], journal.stat().st_size)
            self.assertGreater(after["offset"], before["offset"])
            self.assertTrue(marker.is_file())

    def test_parent_tail_accepts_late_same_turn_evidence_after_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            child = work / f"rollout-child-{CHILD_ID}.jsonl"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T00:59:59Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID},
                    }
                ],
            )
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:10Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": TURN_ID},
                        }
                    )
                    + "\n"
                )
            write_jsonl(child, child_events())
            first = self.finalize(root)
            first_record = json.loads(Path(first["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(first_record["child_count"], 0)

            with parent.open("a", encoding="utf-8") as handle:
                for event in (
                    {
                        "timestamp": "2026-08-01T01:00:11Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "spawn-call",
                            "output": json.dumps(
                                {"agent_id": CHILD_ID, "task_name": "/root/hook-forward-test"}
                            ),
                        },
                    },
                    {
                        "timestamp": "2026-08-01T01:00:12Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "sub_agent_activity",
                            "event_id": "spawn-call",
                            "occurred_at_ms": 1785546012000,
                            "agent_thread_id": CHILD_ID,
                            "agent_path": "/root/hook-forward-test",
                            "kind": "started",
                        },
                    },
                ):
                    handle.write(json.dumps(event) + "\n")

            second = self.finalize(root)
            self.assertFalse(second["idempotent"])
            record = json.loads(Path(second["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["source"], "correction")
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["spawn_attempts"][0]["outcome"], "started")
            self.assertEqual(record["agents"][0]["agent_id"], CHILD_ID)

    def test_parent_tail_ignores_later_parent_turn_without_false_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            write_jsonl(parent, [])
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:10Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": TURN_ID},
                        }
                    )
                    + "\n"
                )
            first = self.finalize(root)
            with parent.open("a", encoding="utf-8") as handle:
                for event in (
                    {
                        "timestamp": "2026-08-01T01:01:00Z",
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": SECOND_TURN_ID},
                    },
                    {
                        "timestamp": "2026-08-01T01:01:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "spawn-call",
                            "output": json.dumps(
                                {"agent_id": CHILD_ID, "task_name": "/root/later-turn"}
                            ),
                        },
                    },
                ):
                    handle.write(json.dumps(event) + "\n")
            second = self.finalize(root)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["routine"], first["routine"])

    def test_legacy_closed_parent_cursor_reopens_for_late_same_turn_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            child = work / f"rollout-child-{CHILD_ID}.jsonl"
            write_jsonl(
                parent,
                [{"timestamp": "2026-08-01T00:59:59Z", "type": "session_meta", "payload": {"id": PARENT_ID}}],
            )
            captured_offset = parent.stat().st_size
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:10Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": TURN_ID},
                        }
                    )
                    + "\n"
                )
            terminal_offset = parent.stat().st_size
            dirs = RECORD_HOOK.state_dirs(root)
            RECORD_HOOK.atomic_json(
                RECORD_HOOK.parent_cursor_path(dirs, PARENT_ID, TURN_ID),
                {
                    "transcript_path": str(parent.resolve()),
                    "start_offset": captured_offset,
                    "offset": terminal_offset,
                    "file_size": terminal_offset,
                    "passes": 1,
                    "invalid_line_count": 0,
                    "closed": True,
                    "tool_results": {},
                    "activities": {},
                },
            )
            write_jsonl(child, child_events())
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:11Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "spawn-call",
                                "output": json.dumps(
                                    {"agent_id": CHILD_ID, "task_name": "/root/hook-forward-test"}
                                ),
                            },
                        }
                    )
                    + "\n"
                )
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["agents"][0]["agent_id"], CHILD_ID)
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:01:00Z",
                            "type": "event_msg",
                            "payload": {"type": "task_started", "turn_id": SECOND_TURN_ID},
                        }
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:01:01Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "spawn-call",
                                "output": json.dumps({"agent_id": "later-turn-id"}),
                            },
                        }
                    )
                    + "\n"
                )
            self.assertTrue(self.finalize(root)["idempotent"])

    def test_open_cursor_supplies_transcript_path_when_hooks_omit_new_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            child = work / f"rollout-child-{CHILD_ID}.jsonl"
            write_jsonl(
                parent,
                [{"timestamp": "2026-08-01T00:59:59Z", "type": "session_meta", "payload": {"id": PARENT_ID}}],
            )
            captured_offset = parent.stat().st_size
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:10Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": TURN_ID},
                        }
                    )
                    + "\n"
                )
            terminal_offset = parent.stat().st_size
            write_jsonl(child, child_events())
            RECORD_HOOK.append_event(
                root,
                PARENT_ID,
                TURN_ID,
                {
                    "kind": "tool_pre",
                    "operation": "spawn_agent",
                    "tool_use_id": "spawn-call",
                    "observed_at": "2026-08-01T01:00:00Z",
                },
            )
            dirs = RECORD_HOOK.state_dirs(root)
            RECORD_HOOK.atomic_json(
                RECORD_HOOK.parent_cursor_path(dirs, PARENT_ID, TURN_ID),
                {
                    "transcript_path": str(parent.resolve()),
                    "start_offset": captured_offset,
                    "offset": terminal_offset,
                    "file_size": terminal_offset,
                    "passes": 1,
                    "invalid_line_count": 0,
                    "closed": False,
                    "recovery_window_closed": False,
                    "terminal_seen": True,
                    "terminal_turn_id": TURN_ID,
                    "tool_results": {},
                    "activities": {},
                },
            )
            with parent.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": "2026-08-01T01:00:11Z",
                            "type": "response_item",
                            "payload": {
                                "type": "function_call_output",
                                "call_id": "spawn-call",
                                "output": json.dumps(
                                    {"agent_id": CHILD_ID, "task_name": "/root/hook-forward-test"}
                                ),
                            },
                        }
                    )
                    + "\n"
                )
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["agents"][0]["agent_id"], CHILD_ID)
            self.assertFalse(record["collector"]["parent_legacy_full_scan"])
            self.assertGreater(record["collector"]["parent_transcript_bytes_processed"], 0)

    def test_finalize_refreshes_terminal_bytes_written_after_subagent_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events()[:-1])
            self.pre_spawn(root)
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(child_events()[-1]) + "\n")

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            agent = record["agents"][0]
            self.assertEqual(agent["status"], "completed")
            self.assertEqual(agent["collection_passes"], 2)
            self.assertEqual(
                agent["transcript_bytes_processed"], transcript.stat().st_size
            )

    def test_runtime_mismatch_is_automatically_classified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(
                transcript,
                child_events(role="worker_xhigh", model="gpt-5.6-luna", effort="xhigh"),
            )
            self.pre_spawn(root, role="worker_xhigh")
            self.post_spawn(root, role="worker_xhigh")
            self.child_start(root, role="worker_xhigh")
            self.child_stop(root, transcript, role="worker_xhigh")
            output = self.finalize(root)
            anomaly = json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))
            self.assertIn("runtime-capability-mismatch", anomaly["anomaly_types"])
            self.assertIn("gpt-5.6-sol/xhigh", anomaly["evidence"])

    def test_worker_xhigh_legacy_runtime_is_time_bounded(self) -> None:
        before = {
            "role": "worker_xhigh",
            "started_at": "2026-08-03T08:54:21Z",
        }
        after = {
            "role": "worker_xhigh",
            "started_at": "2026-08-03T08:54:23Z",
        }
        self.assertIn(
            ("gpt-5.6-terra", "max"),
            RECORD_HOOK.expected_role_runtimes(before),
        )
        self.assertEqual(
            RECORD_HOOK.expected_role_runtimes(after),
            (("gpt-5.6-sol", "xhigh"),),
        )

    def test_explicit_requested_runtime_mismatch_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            transcript = work / "child.jsonl"
            write_jsonl(transcript, child_events())
            self.pre_spawn(root, requested_model="gpt-5.6-sol")
            self.post_spawn(root)
            self.child_start(root)
            self.child_stop(root, transcript)
            output = self.finalize(root)
            anomaly = json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))
            self.assertIn("runtime-capability-mismatch", anomaly["anomaly_types"])
            self.assertIn(
                "dispatch requested gpt-5.6-sol/unspecified, observed gpt-5.6-luna/medium",
                anomaly["evidence"],
            )

    def test_failure_only_dispatch_is_recorded_without_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "records"
            self.pre_spawn(root)
            self.ingest(
                root,
                {
                    "session_id": PARENT_ID,
                    "turn_id": TURN_ID,
                    "cwd": "/tmp/hook-project",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "spawn_agent",
                    "tool_use_id": "spawn-call",
                    "tool_input": {
                        "agent_type": "explorer",
                        "message": "SECRET RAW CHILD PROMPT",
                    },
                    "tool_response": "Unknown model SECRET RAW DISPATCH ERROR. Available models: sol.",
                },
            )
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            anomaly = json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"], "dispatch_failed")
            self.assertEqual(record["child_count"], 0)
            self.assertEqual(record["spawn_attempts"][0]["error"], "unknown-model")
            self.assertIn("dispatch-failure", anomaly["anomaly_types"])
            self.assertNotIn("SECRET RAW DISPATCH ERROR", json.dumps(record))
            self.assertNotIn("SECRET RAW DISPATCH ERROR", json.dumps(anomaly))
            self.assertNotIn("SECRET RAW DISPATCH ERROR", json.dumps(output))
            state_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "hook-state").rglob("*.json*")
                if path.is_file()
            )
            self.assertNotIn("SECRET RAW DISPATCH ERROR", state_text)

    def test_incremental_parent_tail_recovers_current_runtime_hook_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            child = work / f"rollout-child-{CHILD_ID}.jsonl"
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T00:59:59Z",
                        "type": "session_meta",
                        "payload": {"id": PARENT_ID},
                    }
                ],
            )
            captured_offset = parent.stat().st_size
            self.pre_spawn(root, transcript=parent)
            with parent.open("a", encoding="utf-8") as handle:
                for event in (
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "spawn-call",
                            "output": '{"task_name":"/root/hook-forward-test"}',
                        },
                    },
                    {
                        "timestamp": "2026-08-01T01:00:00.100Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "sub_agent_activity",
                            "event_id": "spawn-call",
                            "occurred_at_ms": 1785546000100,
                            "agent_thread_id": CHILD_ID,
                            "agent_path": "/root/hook-forward-test",
                            "kind": "started",
                        },
                    },
                    {
                        "timestamp": "2026-08-01T01:00:10Z",
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": TURN_ID},
                    },
                ):
                    handle.write(json.dumps(event) + "\n")
            write_jsonl(child, child_events())

            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            self.assertEqual(record["child_count"], 1)
            self.assertEqual(record["agents"][0]["agent_id"], CHILD_ID)
            self.assertEqual(record["agents"][0]["model"], "gpt-5.6-luna")
            self.assertEqual(record["agents"][0]["status"], "completed")
            self.assertEqual(record["spawn_attempts"][0]["outcome"], "started")
            collector = record["collector"]
            self.assertEqual(
                collector["transcript_strategy"],
                "hook-events-plus-incremental-parent-tail",
            )
            self.assertEqual(
                collector["parent_transcript_bytes_processed"],
                parent.stat().st_size - captured_offset,
            )
            self.assertFalse(collector["parent_legacy_full_scan"])
            second = self.finalize(root)
            self.assertTrue(second["idempotent"])
            self.assertEqual(second["routine"], output["routine"])
            audit = subprocess.run(
                [sys.executable, str(AUDITOR), "audit", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["valid"])

    def test_incremental_parent_tail_recovers_missing_failed_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "records"
            parent = work / f"rollout-parent-{PARENT_ID}.jsonl"
            write_jsonl(parent, [])
            self.pre_spawn(root, transcript=parent)
            write_jsonl(
                parent,
                [
                    {
                        "timestamp": "2026-08-01T01:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "spawn-call",
                            "output": "Unknown model gpt-5.6-luna. Available models: sol.",
                        },
                    },
                    {
                        "timestamp": "2026-08-01T01:00:01Z",
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": TURN_ID},
                    },
                ],
            )
            output = self.finalize(root)
            record = json.loads(Path(output["routine"]).read_text(encoding="utf-8"))
            anomaly = json.loads(Path(output["anomaly"]).read_text(encoding="utf-8"))
            self.assertEqual(record["outcome"], "dispatch_failed")
            self.assertEqual(record["spawn_attempts"][0]["error"], "unknown-model")
            self.assertIn("dispatch-failure", anomaly["anomaly_types"])


if __name__ == "__main__":
    unittest.main()
