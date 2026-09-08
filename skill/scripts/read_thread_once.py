#!/usr/bin/env python3
"""Read Codex thread evidence through one short-lived app-server process."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, NoReturn, TextIO


DEFAULT_TIMEOUT = 20.0
MAX_TIMEOUT = 120.0
MAX_TURN_LIMIT = 20


class ThreadReadError(RuntimeError):
    """Expected executable, protocol, timeout, or app-server failure."""


def fail(message: str) -> NoReturn:
    raise ThreadReadError(message)


def resolve_codex(value: str) -> str:
    if os.sep in value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = candidate.resolve()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            fail(f"Codex executable is missing or not executable: {candidate}")
        return str(candidate)
    resolved = shutil.which(value)
    if resolved is None:
        fail(f"Codex executable was not found on PATH: {value}")
    return resolved


def normalize_thread_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid Codex thread id: {value!r}")
    normalized = str(parsed)
    if value.lower() != normalized:
        raise argparse.ArgumentTypeError(
            f"Codex thread id must use canonical UUID form: {normalized}"
        )
    return normalized


class AppServerClient:
    def __init__(self, codex: str, timeout: float) -> None:
        self.timeout = timeout
        self.stderr_tail: deque[str] = deque(maxlen=20)
        try:
            self.process = subprocess.Popen(
                [codex, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            fail(f"could not start Codex app-server: {exc}")
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            self.close()
            fail("Codex app-server did not expose all stdio pipes")
        self.stdout_queue: queue.Queue[str | None] = queue.Queue()
        self.stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self.process.stdout,),
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(self.process.stderr,),
            daemon=True,
        )
        self.stdout_thread.start()
        self.stderr_thread.start()

    def _read_stdout(self, stream: TextIO) -> None:
        try:
            for line in stream:
                self.stdout_queue.put(line)
        finally:
            self.stdout_queue.put(None)

    def _read_stderr(self, stream: TextIO) -> None:
        for line in stream:
            selected = line.rstrip()
            if selected:
                self.stderr_tail.append(selected)

    def _stderr_context(self) -> str:
        if not self.stderr_tail:
            return ""
        return f"; app-server stderr: {' | '.join(self.stderr_tail)}"

    def send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            fail("Codex app-server stdin is closed")
        try:
            self.process.stdin.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            fail(f"could not write to Codex app-server: {exc}{self._stderr_context()}")

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> Any:
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail(f"Codex app-server timed out during {method}{self._stderr_context()}")
            try:
                line = self.stdout_queue.get(timeout=remaining)
            except queue.Empty:
                fail(f"Codex app-server timed out during {method}{self._stderr_context()}")
            if line is None:
                code = self.process.poll()
                fail(
                    f"Codex app-server exited before replying to {method} "
                    f"(exit={code}){self._stderr_context()}"
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                fail(f"Codex app-server emitted invalid JSON: {exc}{self._stderr_context()}")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                fail(
                    f"Codex app-server rejected {method}: "
                    f"{json.dumps(message['error'], ensure_ascii=False, separators=(',', ':'))}"
                )
            if "result" not in message:
                fail(f"Codex app-server reply to {method} has no result")
            return message["result"]

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            1,
            "initialize",
            {
                "clientInfo": {
                    "name": "agent-team-read-thread-once",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        if not isinstance(result, dict):
            fail("Codex app-server initialize result is not an object")
        self.send({"method": "initialized"})
        return result

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None or process.poll() is not None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def __enter__(self) -> "AppServerClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def positive_timeout(value: str) -> float:
    try:
        selected = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if selected <= 0 or selected > MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(f"timeout must be within (0, {MAX_TIMEOUT:g}]")
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read one bounded slice of Codex thread evidence through a short-lived, "
            "read-only app-server client."
        )
    )
    parser.add_argument(
        "--codex",
        default="codex",
        help="Codex executable or command name (default: codex from PATH)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=DEFAULT_TIMEOUT,
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="verify app-server initialization only")

    metadata = subparsers.add_parser("metadata", help="read thread metadata without turns")
    metadata.add_argument("--thread-id", required=True, type=normalize_thread_id)

    turns = subparsers.add_parser("turns", help="read one bounded page of thread turns")
    turns.add_argument("--thread-id", required=True, type=normalize_thread_id)
    turns.add_argument("--cursor")
    turns.add_argument("--limit", type=int, choices=range(1, MAX_TURN_LIMIT + 1), default=5)
    turns.add_argument("--sort", choices=("asc", "desc"), default="desc")
    turns.add_argument(
        "--items-view",
        choices=("notLoaded", "summary", "full"),
        default="summary",
    )
    return parser


def emit(operation: str, result: Any, *, codex: str) -> None:
    print(
        json.dumps(
            {
                "provider": "codex-app-server-stdio",
                "operation": operation,
                "codex": codex,
                "result": result,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def main() -> int:
    args = build_parser().parse_args()
    codex = resolve_codex(args.codex)
    with AppServerClient(codex, args.timeout) as client:
        initialized = client.initialize()
        if args.command == "doctor":
            emit("initialize", {"status": "ok", "server": initialized}, codex=codex)
            return 0
        if args.command == "metadata":
            result = client.request(
                2,
                "thread/read",
                {"threadId": args.thread_id, "includeTurns": False},
            )
            emit("thread/read", result, codex=codex)
            return 0
        params: dict[str, Any] = {
            "threadId": args.thread_id,
            "limit": args.limit,
            "sortDirection": args.sort,
            "itemsView": args.items_view,
        }
        if args.cursor is not None:
            params["cursor"] = args.cursor
        result = client.request(2, "thread/turns/list", params)
        emit("thread/turns/list", result, codex=codex)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ThreadReadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
