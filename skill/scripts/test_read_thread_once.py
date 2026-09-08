#!/usr/bin/env python3
"""Black-box tests for the one-shot Codex thread reader."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("read_thread_once.py")
THREAD_ID = "019fdee4-eecf-7752-a90b-18f542c31b52"


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys
import time


MODE = os.environ.get("FAKE_MODE", "ok")


def send(message):
    print(json.dumps(message), flush=True)


if sys.argv[1:] != ["app-server", "--stdio"]:
    raise SystemExit(2)


if MODE == "exit-immediately":
    raise SystemExit(0)

initialize = json.loads(sys.stdin.readline())
send({"method": "server/notice", "params": {"ignored": True}})
if MODE == "invalid-init":
    send({"id": initialize["id"], "result": []})
else:
    send({"id": initialize["id"], "result": {"userAgent": "fake/1"}})
initialized = json.loads(sys.stdin.readline())
if initialized.get("method") != "initialized":
    raise SystemExit(3)
request = json.loads(sys.stdin.readline())
if MODE == "timeout":
    print("fake timeout", file=sys.stderr, flush=True)
    time.sleep(5)
    raise SystemExit(0)
if MODE == "invalid-json":
    print("not-json", flush=True)
    raise SystemExit(0)
if MODE == "protocol-error":
    print("fake protocol failure", file=sys.stderr, flush=True)
    time.sleep(0.02)
    send({"id": request["id"], "error": {"code": "bad", "message": "nope"}})
    raise SystemExit(0)
if MODE == "wrong-id":
    send({"id": request["id"] + 100, "result": {"ignored": True}})
if request["method"] == "thread/read":
    thread_id = request["params"]["threadId"]
    if MODE == "mismatch":
        thread_id = "00000000-0000-7000-8000-000000000000"
    if MODE == "missing-thread":
        result = {"metadata": {}}
    else:
        result = {"thread": {"id": thread_id, "turns": []}}
elif request["method"] == "thread/turns/list":
    params = request["params"]
    result = {
        "data": [{"id": "turn-1", "items": []}],
        "nextCursor": "next-1",
        "echo": params,
    }
else:
    raise SystemExit(4)
send({"id": request["id"], "result": result})
'''


class ReadThreadOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fake_codex = Path(self.temporary.name) / "codex"
        self.fake_codex.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        self.fake_codex.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_reader(
        self, *arguments: str, mode: str = "ok"
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["FAKE_MODE"] = mode
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--codex", str(self.fake_codex), *arguments],
            text=True,
            capture_output=True,
            env=environment,
            timeout=10,
            check=False,
        )

    def test_doctor_uses_one_short_lived_app_server(self) -> None:
        result = self.run_reader("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["provider"], "codex-app-server-stdio")
        self.assertEqual(payload["operation"], "initialize")
        self.assertEqual(payload["result"]["status"], "ok")

    def test_metadata_excludes_turns_from_thread_read(self) -> None:
        result = self.run_reader("metadata", "--thread-id", THREAD_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["operation"], "thread/read")
        self.assertEqual(payload["result"]["thread"]["id"], THREAD_ID)

    def test_metadata_rejects_unexpected_thread_identity(self) -> None:
        result = self.run_reader(
            "metadata", "--thread-id", THREAD_ID, mode="mismatch"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unexpected thread", result.stderr)
        self.assertIn(THREAD_ID, result.stderr)

    def test_metadata_rejects_missing_thread_metadata(self) -> None:
        result = self.run_reader(
            "metadata", "--thread-id", THREAD_ID, mode="missing-thread"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no thread metadata", result.stderr)

    def test_turn_page_preserves_cursor_and_bounded_options(self) -> None:
        result = self.run_reader(
            "turns",
            "--thread-id",
            THREAD_ID,
            "--cursor",
            "cursor-1",
            "--limit",
            "3",
            "--sort",
            "asc",
            "--items-view",
            "summary",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["nextCursor"], "next-1")
        echoed = payload["result"]["echo"]
        self.assertEqual(echoed["cursor"], "cursor-1")
        self.assertEqual(echoed["limit"], 3)
        self.assertEqual(echoed["sortDirection"], "asc")
        self.assertEqual(echoed["itemsView"], "summary")

    def test_rejects_invalid_thread_id_before_protocol_use(self) -> None:
        result = self.run_reader("metadata", "--thread-id", "not-a-thread")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid Codex thread id", result.stderr)

    def test_normalizes_uuid_spellings_before_protocol_use(self) -> None:
        result = self.run_reader(
            "metadata", "--thread-id", "{" + THREAD_ID.upper() + "}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["thread"]["id"], THREAD_ID)

    def test_limit_is_bounded(self) -> None:
        result = self.run_reader("turns", "--thread-id", THREAD_ID, "--limit", "1000")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_ignores_notifications_and_unmatched_responses(self) -> None:
        result = self.run_reader(
            "turns", "--thread-id", THREAD_ID, mode="wrong-id"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"]["data"][0]["id"], "turn-1")

    def test_timeout_reports_request_and_closes_child(self) -> None:
        result = self.run_reader(
            "--timeout",
            "1.0",
            "metadata",
            "--thread-id",
            THREAD_ID,
            mode="timeout",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out during thread/read", result.stderr)

    def test_protocol_error_includes_structured_error_and_stderr(self) -> None:
        result = self.run_reader(
            "metadata", "--thread-id", THREAD_ID, mode="protocol-error"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("rejected thread/read", result.stderr)
        self.assertIn('{"code":"bad","message":"nope"}', result.stderr)
        self.assertIn("app-server stderr: fake protocol failure", result.stderr)

    def test_malformed_json_is_reported(self) -> None:
        result = self.run_reader(
            "metadata", "--thread-id", THREAD_ID, mode="invalid-json"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("emitted invalid JSON", result.stderr)

    def test_close_closes_pipes_when_child_exited_before_context_exit(self) -> None:
        spec = importlib.util.spec_from_file_location("read_thread_once", SCRIPT)
        if spec is None or spec.loader is None:
            self.fail("could not import reader module")
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        with patch.dict(os.environ, {"FAKE_MODE": "exit-immediately"}):
            client = reader.AppServerClient(str(self.fake_codex), timeout=1.0)
        client.process.wait(timeout=2.0)
        client.close()
        self.assertTrue(client.process.stdin.closed)
        self.assertTrue(client.process.stdout.closed)
        self.assertTrue(client.process.stderr.closed)
        client.close()


if __name__ == "__main__":
    unittest.main()
