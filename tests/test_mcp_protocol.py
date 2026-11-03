"""End-to-end tests of the stdio MCP protocol.

These spawn the real process exactly as an MCP client would
(``python3 -m enos.mcp_server``) and assert on the JSON-RPC traffic.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ServerProcess:
    """A running ``python -m enos.mcp_server`` with a line-oriented queue."""

    def __init__(self, db_path: Path) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "enos.mcp_server", "--db", str(db_path)],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr: list = []
        self._reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._reader.start()
        self._err_reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._err_reader.start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line)

    def send(self, payload: Dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def read(self, timeout: float = 15.0) -> Dict[str, Any]:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(
                "timed out waiting for a response; stderr=%r" % "".join(self._stderr)
            )
        if line is None:
            raise AssertionError("server closed stdout; stderr=%r" % "".join(self._stderr))
        return json.loads(line)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, msg_id: int = 1) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        return self.read()

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)

    def call_tool(self, name: str, arguments: Dict[str, Any], msg_id: int = 1) -> Dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments}, msg_id)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()


class McpProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "memory.db"
        self.server = ServerProcess(self.db)
        self.addCleanup(self.server.close)

    def _initialize(self, protocol: str = "2024-11-05") -> Dict[str, Any]:
        response = self.server.request(
            "initialize",
            {"protocolVersion": protocol, "clientInfo": {"name": "unittest", "version": "1"}},
        )
        self.server.notify("notifications/initialized")
        return response

    # -- lifecycle -------------------------------------------------------- #
    def test_initialize_handshake(self) -> None:
        response = self._initialize()
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "enos-memory")
        self.assertIn("tools", result["capabilities"])
        self.assertTrue(result["instructions"])

    def test_initialize_echoes_supported_version(self) -> None:
        response = self._initialize("2025-06-18")
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_falls_back_on_unknown_version(self) -> None:
        response = self._initialize("1999-01-01")
        self.assertIn(response["result"]["protocolVersion"], ("2024-11-05", "2025-03-26", "2025-06-18"))

    def test_ping(self) -> None:
        self._initialize()
        response = self.server.request("ping", msg_id=7)
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["result"], {})

    def test_notifications_are_silent(self) -> None:
        self._initialize()
        self.server.notify("notifications/whatever", {"x": 1})
        # The next message on stdout must be the ping response, not a reply
        # to the notification.
        response = self.server.request("ping", msg_id=42)
        self.assertEqual(response["id"], 42)

    # -- tools ------------------------------------------------------------ #
    def test_tools_list(self) -> None:
        self._initialize()
        tools = self.server.request("tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        expected = {
            "memory_write", "memory_search", "memory_get", "memory_list",
            "memory_delete", "memory_context", "memory_stats", "memory_export",
        }
        self.assertEqual(names, expected)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])

    def test_write_search_get_roundtrip(self) -> None:
        self._initialize()
        write = self.server.call_tool(
            "memory_write",
            {"content": "links-enos stores memories in SQLite.", "tags": ["arch"], "scope": "test"},
            msg_id=2,
        )
        self.assertFalse(write["result"]["isError"])
        payload = write["result"]["structuredContent"]
        memory_id = payload["id"]
        self.assertTrue(payload["created"])
        self.assertEqual(payload["scope"], "test")

        search = self.server.call_tool(
            "memory_search", {"query": "SQLite", "scope": "test"}, msg_id=3
        )
        results = search["result"]["structuredContent"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], memory_id)

        fetched = self.server.call_tool("memory_get", {"id": memory_id}, msg_id=4)
        self.assertEqual(
            fetched["result"]["structuredContent"]["content"],
            "links-enos stores memories in SQLite.",
        )

    def test_tool_text_content_is_json(self) -> None:
        self._initialize()
        response = self.server.call_tool("memory_stats", {}, msg_id=5)
        content = response["result"]["content"]
        self.assertEqual(content[0]["type"], "text")
        parsed = json.loads(content[0]["text"])
        self.assertIn("db_path", parsed)

    def test_unknown_tool_returns_protocol_error(self) -> None:
        # Per the MCP spec an unknown tool name is a *protocol* error (-32602),
        # not a tool execution error.
        self._initialize()
        response = self.server.call_tool("nope", {}, msg_id=6)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("unknown tool", response["error"]["message"])

    def test_missing_required_argument_is_reported(self) -> None:
        self._initialize()
        response = self.server.call_tool("memory_search", {}, msg_id=7)
        self.assertTrue(response["result"]["isError"])

    def test_delete_flow(self) -> None:
        self._initialize()
        memory_id = self.server.call_tool(
            "memory_write", {"content": "temp", "scope": "test"}, msg_id=8
        )["result"]["structuredContent"]["id"]
        deleted = self.server.call_tool("memory_delete", {"id": memory_id}, msg_id=9)
        self.assertTrue(deleted["result"]["structuredContent"]["deleted"])
        missing = self.server.call_tool("memory_get", {"id": memory_id}, msg_id=10)
        self.assertTrue(missing["result"]["isError"])

    def test_memory_context_block(self) -> None:
        self._initialize()
        self.server.call_tool(
            "memory_write",
            {"content": "Rollback: restore the pre-upgrade dump then restart.", "scope": "ops"},
            msg_id=11,
        )
        response = self.server.call_tool(
            "memory_context", {"query": "rollback", "scope": "ops"}, msg_id=12
        )
        context = response["result"]["structuredContent"]["context"]
        self.assertIn("## Relevant memories", context)
        self.assertIn("pre-upgrade dump", context)

    def test_export(self) -> None:
        self._initialize()
        self.server.call_tool("memory_write", {"content": "keep me", "scope": "ops"}, msg_id=13)
        export = self.server.call_tool("memory_export", {}, msg_id=14)
        payload = export["result"]["structuredContent"]
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["memories"][0]["content"], "keep me")

    # -- protocol errors -------------------------------------------------- #
    def test_unknown_method(self) -> None:
        response = self.server.request("does/not/exist", msg_id=15)
        self.assertEqual(response["error"]["code"], -32601)

    def test_malformed_json_does_not_kill_server(self) -> None:
        assert self.server.proc.stdin is not None
        self.server.proc.stdin.write("{not json}\n")
        self.server.proc.stdin.flush()
        response = self.server.request("ping", msg_id=16)
        self.assertEqual(response["result"], {})

    # -- stream hygiene --------------------------------------------------- #
    def test_stdout_is_pure_jsonrpc(self) -> None:
        self._initialize()
        for index in range(6):
            response = self.server.call_tool(
                "memory_write", {"content": "entry %d" % index, "scope": "pure"}, msg_id=100 + index
            )
            self.assertEqual(response["jsonrpc"], "2.0")

        # Drain anything buffered and validate every line.
        lines = []
        while not self.server._lines.empty():
            item = self.server._lines.get_nowait()
            if item:
                lines.append(item)
        for line in lines:
            json.loads(line)

    def test_selftest_flag_bypasses_mcp(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        completed = subprocess.run(
            [sys.executable, "-m", "enos.mcp_server", "--db", str(self.db), "--selftest"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
