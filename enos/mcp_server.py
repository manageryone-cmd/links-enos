#!/usr/bin/env python3
"""links-enos MCP memory server — JSON-RPC 2.0 over stdio.

Launch:

    python3 -m enos.mcp_server

The process speaks the MCP stdio transport: newline-delimited JSON on stdin,
newline-delimited JSON on stdout. Nothing else is ever written to stdout —
diagnostics go to stderr — because a single stray ``print()`` corrupts the
stream for every client (Cline, Claude Code, Cursor, ...).

Implemented methods
-------------------
``initialize`` / ``notifications/initialized`` / ``ping`` / ``tools/list`` /
``tools/call`` / ``resources/list`` / ``prompts/list`` / ``logging/setLevel``

No third-party packages are required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, Iterable, List, Optional

try:  # normal package import
    from enos import __version__
    from enos.store import DB_ENV_VAR, MemoryStore, default_db_path
except ImportError:  # executed directly, e.g. `python3 enos/mcp_server.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from enos import __version__  # type: ignore[no-redef]
    from enos.store import DB_ENV_VAR, MemoryStore, default_db_path  # type: ignore[no-redef]

SERVER_NAME = "enos-memory"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]

LOG_PREFIX = "[links-enos]"


def log(message: str) -> None:
    """Write a diagnostic line to stderr (never stdout)."""
    sys.stderr.write("%s %s\n" % (LOG_PREFIX, message))
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
class StdioTransport:
    """Read/write MCP messages on a byte stream.

    Handles the canonical newline-delimited framing and, defensively, the
    ``Content-Length: N`` framing some older clients still emit.
    """

    def __init__(self, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = stdin if stdin is not None else sys.stdin.buffer
        self.stdout = stdout if stdout is not None else sys.stdout.buffer

    def read(self) -> Iterable[Optional[Dict[str, Any]]]:
        while True:
            raw = self.stdin.readline()
            if not raw:
                return
            line = raw.strip()
            if not line:
                continue

            if line[:15].lower() == b"content-length:":
                body = self._read_content_length(line)
                if body is None:
                    continue
            else:
                body = line

            try:
                message = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                log("ignoring malformed JSON input: %s" % exc)
                continue

            if isinstance(message, list):  # JSON-RPC batch
                for item in message:
                    if isinstance(item, dict):
                        yield item
            elif isinstance(message, dict):
                yield message

    def _read_content_length(self, header_line: bytes) -> Optional[bytes]:
        try:
            length = int(header_line.split(b":", 1)[1].strip())
        except (IndexError, ValueError):
            log("ignoring malformed Content-Length header")
            return None
        # Drain the remaining headers up to the blank separator line.
        while True:
            header = self.stdin.readline()
            if not header or header.strip() == b"":
                break
        return self.stdin.read(length)

    def send(self, message: Dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self.stdout.write(payload.encode("utf-8") + b"\n")
        self.stdout.flush()


# --------------------------------------------------------------------------- #
# tool catalogue
# --------------------------------------------------------------------------- #
def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "memory_write",
        "description": (
            "Persist a durable memory (fact, decision, preference, snippet). "
            "Idempotent: writing the same content in the same scope refreshes the "
            "existing entry instead of duplicating it. Returns the stored record id."
        ),
        "inputSchema": _schema(
            {
                "content": {"type": "string", "description": "The memory body (Markdown ok)."},
                "title": {"type": "string", "description": "Short label; derived from content if omitted."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Retrieval tags, e.g. ['deploy','nginx'].",
                },
                "scope": {"type": "string", "description": "Namespace, e.g. 'project:demo'. Defaults to the server scope."},
                "kind": {
                    "type": "string",
                    "enum": ["note", "fact", "decision", "preference", "task", "snippet", "reference"],
                    "description": "Memory category (default 'note').",
                },
                "importance": {"type": "number", "minimum": 0, "maximum": 1, "description": "0..1 ranking hint (default 0.5)."},
                "id": {"type": "string", "description": "Update this existing memory id instead of creating one."},
            },
            ["content"],
        ),
    },
    {
        "name": "memory_search",
        "description": "Full-text search across memories (FTS5 when available, LIKE fallback).",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Keywords to look for."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Max results (default 10)."},
                "scope": {"type": "string", "description": "Restrict to one scope."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Require all of these tags."},
            },
            ["query"],
        ),
    },
    {
        "name": "memory_get",
        "description": "Fetch one memory by id, including its full content.",
        "inputSchema": _schema({"id": {"type": "string"}}, ["id"]),
    },
    {
        "name": "memory_list",
        "description": "List recent memories, newest/most important first.",
        "inputSchema": _schema(
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Default 20."},
                "offset": {"type": "integer", "minimum": 0, "description": "Pagination offset."},
                "scope": {"type": "string"},
            }
        ),
    },
    {
        "name": "memory_delete",
        "description": "Delete a memory. Soft delete by default; hard=true removes the row permanently.",
        "inputSchema": _schema(
            {"id": {"type": "string"}, "hard": {"type": "boolean", "description": "Permanent delete (default false)."}},
            ["id"],
        ),
    },
    {
        "name": "memory_context",
        "description": (
            "Build a compact Markdown context block from memories relevant to a query — "
            "drop-in text for a system prompt or handoff note."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 8."},
                "scope": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "description": "Default 4000."},
            },
            ["query"],
        ),
    },
    {
        "name": "memory_stats",
        "description": "Storage statistics: database path, entry counts per scope, top tags, FTS availability.",
        "inputSchema": _schema({}),
    },
    {
        "name": "memory_export",
        "description": "Export memories as portable JSON (backup / migration between machines).",
        "inputSchema": _schema({"scope": {"type": "string", "description": "Optional single-scope export."}}),
    },
]


# --------------------------------------------------------------------------- #
# server
# --------------------------------------------------------------------------- #
class McpServer:
    """Minimal, spec-compliant MCP server over stdio."""

    def __init__(self, store: MemoryStore, default_scope: str = "global") -> None:
        self.store = store
        self.default_scope = default_scope
        self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self._shutdown_requested = False
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "initialize": self._on_initialize,
            "ping": self._on_ping,
            "tools/list": self._on_tools_list,
            "tools/call": self._on_tools_call,
            "resources/list": self._on_resources_list,
            "resources/templates/list": lambda params: {"resourceTemplates": []},
            "prompts/list": self._on_prompts_list,
            "logging/setLevel": lambda params: {},
            "shutdown": self._on_shutdown,
        }

    # -- dispatch --------------------------------------------------------- #
    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message

        if not isinstance(method, str):
            if is_notification:
                return None
            return self._error(msg_id, -32600, "Invalid Request: missing method")

        if method.startswith("notifications/"):
            return None  # notifications never get a response

        handler = self._handlers.get(method)
        if handler is None:
            if is_notification:
                return None
            return self._error(msg_id, -32601, "Method not found: %s" % method)

        try:
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = handler(params)
        except ValueError as exc:
            if is_notification:
                return None
            return self._error(msg_id, -32602, str(exc))
        except KeyError as exc:
            if is_notification:
                return None
            return self._error(msg_id, -32602, str(exc).strip("'"))
        except Exception as exc:  # noqa: BLE001 - never kill the server loop
            log("internal error in %s: %s" % (method, exc))
            traceback.print_exc(file=sys.stderr)
            if is_notification:
                return None
            return self._error(msg_id, -32603, "Internal error: %s" % exc)

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    # -- MCP lifecycle ---------------------------------------------------- #
    def _on_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = requested
        else:
            self.protocol_version = DEFAULT_PROTOCOL_VERSION

        client = params.get("clientInfo") or {}
        log(
            "initialize from %s %s (protocol %s)"
            % (client.get("name", "unknown"), client.get("version", "?"), self.protocol_version)
        )
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "links-enos durable memory. Use memory_write to store facts/decisions "
                "worth keeping across sessions, memory_search/memory_context to "
                "recall them, and memory_stats to inspect storage. Scopes are "
                "namespaces (e.g. 'project:demo'); the default scope is "
                "'%s'." % self.default_scope
            ),
        }

    def _on_ping(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def _on_shutdown(self, params: Dict[str, Any]) -> None:
        self._shutdown_requested = True
        return None

    # -- resources / prompts (declared, empty) ---------------------------- #
    def _on_resources_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"resources": []}

    def _on_prompts_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"prompts": []}

    # -- tools ------------------------------------------------------------ #
    def _on_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"tools": TOOL_DEFINITIONS}

    def _on_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call requires a tool name")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")

        handler = {
            "memory_write": self._tool_write,
            "memory_search": self._tool_search,
            "memory_get": self._tool_get,
            "memory_list": self._tool_list,
            "memory_delete": self._tool_delete,
            "memory_context": self._tool_context,
            "memory_stats": self._tool_stats,
            "memory_export": self._tool_export,
        }.get(name)

        if handler is None:
            raise ValueError("unknown tool: %s" % name)

        try:
            payload = handler(arguments)
        except (ValueError, KeyError) as exc:
            return _tool_error(str(exc).strip("'"))
        except Exception as exc:  # noqa: BLE001
            log("tool %s failed: %s" % (name, exc))
            traceback.print_exc(file=sys.stderr)
            return _tool_error("internal error: %s" % exc)

        return _tool_ok(payload)

    # -- tool implementations --------------------------------------------- #
    def _tool_write(self, args: Dict[str, Any]) -> Dict[str, Any]:
        record = self.store.write(
            content=args.get("content", ""),
            title=args.get("title"),
            tags=args.get("tags"),
            scope=args.get("scope") or self.default_scope,
            kind=args.get("kind", "note"),
            importance=args.get("importance", 0.5),
            memory_id=args.get("id"),
        )
        return {
            "id": record["id"],
            "scope": record["scope"],
            "title": record["title"],
            "tags": record["tags"],
            "kind": record["kind"],
            "importance": record["importance"],
            "created": record["created"],
            "updated_at": record["updated_at"],
        }

    def _tool_search(self, args: Dict[str, Any]) -> Any:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        return {
            "query": query,
            "results": self.store.search(
                query,
                limit=args.get("limit", 10),
                scope=args.get("scope"),
                tags=args.get("tags"),
            ),
        }

    def _tool_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        memory_id = args.get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("id must be a non-empty string")
        record = self.store.get(memory_id)
        if record is None:
            raise ValueError("memory not found: %s" % memory_id)
        return record

    def _tool_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "results": self.store.list(
                limit=args.get("limit", 20),
                offset=args.get("offset", 0),
                scope=args.get("scope"),
            )
        }

    def _tool_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        memory_id = args.get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise ValueError("id must be a non-empty string")
        removed = self.store.delete(memory_id, hard=bool(args.get("hard", False)))
        if not removed:
            raise ValueError("memory not found: %s" % memory_id)
        return {"id": memory_id, "deleted": True, "hard": bool(args.get("hard", False))}

    def _tool_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        limit = int(args.get("limit", 8))
        max_chars = int(args.get("max_chars", 4000))
        hits = self.store.search(query, limit=limit, scope=args.get("scope"))
        if not hits:
            return {"context": "", "count": 0}

        lines = ["## Relevant memories (%s)" % query, ""]
        for hit in hits:
            tags = ", ".join(hit["tags"])
            header = "- **%s**" % (hit["title"] or hit["id"])
            if tags:
                header += " [%s]" % tags
            body = self.store.get(hit["id"])
            excerpt = (body or {}).get("content") or hit["excerpt"]
            lines.append(header)
            lines.append("  " + excerpt.replace("\n", "\n  "))
            lines.append("")
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n…(truncated)"
        return {"context": text, "count": len(hits)}

    def _tool_stats(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.store.stats()

    def _tool_export(self, args: Dict[str, Any]) -> Dict[str, Any]:
        records = self.store.export(scope=args.get("scope"))
        return {"count": len(records), "memories": records}


def _tool_ok(payload: Any) -> Dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
        "isError": False,
    }


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enos.mcp_server",
        description="links-enos MCP memory server (stdio transport).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to the SQLite file. Default: $%s, else "
            "<cwd>/.enos/memory.db — i.e. one database per project, created on "
            "first launch." % DB_ENV_VAR
        ),
    )
    parser.add_argument(
        "--scope",
        default=os.environ.get("ENOS_DEFAULT_SCOPE", "global"),
        help="Default scope for writes that omit one (default: global).",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Run a round-trip check against the database, print JSON, exit.",
    )
    return parser


def run_selftest(store: MemoryStore) -> int:
    """Non-MCP self check used by installers and CI."""
    marker = "enos selftest %s" % os.getpid()
    record = store.write(content=marker, title="selftest", tags=["selftest"], scope="__selftest__")
    found = store.search("selftest", scope="__selftest__")
    store.delete(record["id"], hard=True)
    ok = bool(found)
    payload = {
        "ok": ok,
        "version": __version__,
        "db_path": str(store.db_path),
        "fts5": store.stats()["fts5"],
        "python": sys.version.split()[0],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        sys.stdout.write("%s %s\n" % (SERVER_NAME, __version__))
        return 0

    # Explicit --db wins; otherwise resolve $ENOS_MEMORY_DB, otherwise create
    # .enos/memory.db inside the directory the client started us in. Resolving
    # here (instead of inside the store) keeps the caller-visible path explicit
    # for logging and for the selftest payload.
    db_path = args.db if args.db else default_db_path()

    try:
        store = MemoryStore(db_path)
    except Exception as exc:  # noqa: BLE001
        log("failed to open memory database at %s: %s" % (db_path, exc))
        return 2

    if args.selftest:
        try:
            return run_selftest(store)
        finally:
            store.close()

    transport = StdioTransport()
    server = McpServer(store, default_scope=args.scope)
    log("ready · v%s · db=%s · fts5=%s" % (__version__, store.db_path, store.stats()["fts5"]))

    try:
        for message in transport.read():
            if message is None:
                continue
            response = server.handle(message)
            if response is not None:
                transport.send(response)
            if server._shutdown_requested:
                break
    except KeyboardInterrupt:
        log("interrupted")
    except BrokenPipeError:
        pass  # client closed the pipe; normal on shutdown
    finally:
        store.close()
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
