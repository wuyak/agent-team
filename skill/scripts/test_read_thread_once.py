#!/usr/bin/env python3
"""Black-box tests for the one-shot Codex thread reader."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("read_thread_once.py")
THREAD_ID = "019fdee4-eecf-7752-a90b-18f542c31b52"


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import sys

initialize = json.loads(sys.stdin.readline())
print(json.dumps({"method": "server/notice", "params": {"ignored": True}}), flush=True)
print(json.dumps({"id": initialize["id"], "result": {"userAgent": "fake/1"}}), flush=True)
initialized = json.loads(sys.stdin.readline())
if initialized.get("method") != "initialized":
    raise SystemExit(3)
request = json.loads(sys.stdin.readline())
if request["method"] == "thread/read":
    result = {"thread": {"id": request["params"]["threadId"], "turns": []}}
elif request["method"] == "thread/turns/list":
    params = request["params"]
    result = {
        "data": [{"id": "turn-1", "items": []}],
        "nextCursor": "next-1",
        "echo": params,
    }
else:
    raise SystemExit(4)
print(json.dumps({"id": request["id"], "result": result}), flush=True)
'''


class ReadThreadOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fake_codex = Path(self.temporary.name) / "codex"
        self.fake_codex.write_text(textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        self.fake_codex.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_reader(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--codex", str(self.fake_codex), *arguments],
            text=True,
            capture_output=True,
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
        echoed = payload["result"]["echo"]
        self.assertEqual(echoed["cursor"], "cursor-1")
        self.assertEqual(echoed["limit"], 3)
        self.assertEqual(echoed["sortDirection"], "asc")
        self.assertEqual(echoed["itemsView"], "summary")

    def test_rejects_noncanonical_thread_id_before_protocol_use(self) -> None:
        result = self.run_reader("metadata", "--thread-id", "not-a-thread")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid Codex thread id", result.stderr)

    def test_limit_is_bounded(self) -> None:
        result = self.run_reader("turns", "--thread-id", THREAD_ID, "--limit", "1000")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
