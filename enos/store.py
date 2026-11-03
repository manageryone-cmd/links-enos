"""SQLite-backed memory store for the links-enos MCP server.

Design constraints (these are the reasons for the shape of this file):

* **Standard library only.** No ``sqlalchemy``/``pydantic``/``mcp`` SDK, so the
  store runs identically under a bare interpreter, a PyInstaller one-file
  binary and a slim Docker image.
* **Single portable file.** One SQLite database, movable across machines and
  operatingsystems (path handling goes through :mod:`pathlib`).
* **Idempotent writes.** ``(scope, content_hash)`` is unique, so re-writing the
  same memory refreshes it instead of creating duplicates. This matters when
  agents retry tool calls.
* **Graceful degradation.** Full-text search uses FTS5 when the SQLite build
  ships it, otherwise a ``LIKE`` scan. Never raise on a missing extension.

Database resolution order:

1. explicit ``db_path`` argument / ``--db`` CLI flag
2. ``$ENOS_MEMORY_DB``
3. ``<current working directory>/.enos/memory.db``

The last step is deliberately **project-scoped**: an MCP client that starts the
server inside a repository gets a database for that repository, so memories from
different projects never bleed into each other. Nothing is ever written to the
home directory unless the caller explicitly points there.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA_VERSION = 1
DB_ENV_VAR = "ENOS_MEMORY_DB"
MAX_CONTENT_CHARS = 200_000
MAX_TITLE_CHARS = 300
ALLOWED_KINDS = ("note", "fact", "decision", "preference", "task", "snippet", "reference")
DEFAULT_DB_DIRNAME = ".enos"
DEFAULT_DB_FILENAME = "memory.db"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def utcnow() -> str:
    """Return an RFC3339 UTC timestamp with a trailing ``Z``."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_db_path(start: Optional[Any] = None) -> Path:
    """Return ``<start>/.enos/memory.db`` — ``start`` defaults to the cwd.

    This is the "one database per project" path. It never touches ``$HOME``.
    """
    base = Path(start).expanduser() if start is not None else Path.cwd()
    return base / DEFAULT_DB_DIRNAME / DEFAULT_DB_FILENAME


def default_db_path() -> Path:
    """Resolve the database path from the environment, with a project default.

    ``$ENOS_MEMORY_DB`` wins (useful in containers and CI); otherwise the file
    lives under ``.enos/`` in the process's current working directory, which is
    how each project gets its own memory.
    """
    env = os.environ.get(DB_ENV_VAR)
    if env:
        return Path(env).expanduser()
    return project_db_path()


def normalize_tags(tags: Any) -> List[str]:
    """Normalise tags from a list or a comma/space separated string.

    Tags are lowercased, stripped, de-duplicated and sorted so that the stored
    representation is stable (which keeps the content hash stable too).
    """
    if tags is None:
        return []
    if isinstance(tags, str):
        raw: Iterable[str] = re.split(r"[,\s]+", tags)
    elif isinstance(tags, (list, tuple, set)):
        raw = [str(t) for t in tags]
    else:
        raw = [str(tags)]

    seen: Dict[str, None] = {}
    for item in raw:
        tag = item.strip().lower()
        if not tag:
            continue
        if len(tag) > 64:
            tag = tag[:64]
        seen.setdefault(tag, None)
    return sorted(seen)


def tags_to_column(tags: Sequence[str]) -> str:
    return ",".join(tags)


def column_to_tags(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [t for t in value.split(",") if t]


def content_hash(scope: str, title: str, content: str) -> str:
    """Stable dedupe key for a memory within a scope."""
    payload = "\x1f".join((scope.strip().lower(), title.strip(), content.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def new_memory_id() -> str:
    return "mem_" + uuid.uuid4().hex[:12]


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 query.

    Every token is quoted, so user input cannot inject FTS operators and cause
    ``sqlite3.OperationalError: fts5: syntax error``.
    """
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    tokens = [t.replace('"', '""') for t in tokens if t.strip('"')]
    if not tokens:
        return ""
    return " AND ".join('"%s"' % t for t in tokens[:32])


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #
class MemoryStore:
    """A small, dependency-free persistent memory store."""

    def __init__(self, db_path: Optional[Any] = None, create: bool = True) -> None:
        self.db_path = Path(db_path).expanduser() if db_path else default_db_path()
        self._fts_enabled = False
        if create:
            # ``.enos/`` may not exist in a fresh checkout: create it up front so
            # the first write does not fail on a missing directory. This is a
            # no-op when the directory is already there.
            os.makedirs(str(self.db_path.parent), exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._init_schema()

    # -- lifecycle -------------------------------------------------------- #
    def _configure(self) -> None:
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA busy_timeout=5000",
            "PRAGMA foreign_keys=ON",
        ):
            try:
                self._conn.execute(pragma)
            except sqlite3.Error:
                # Read-only or exotic filesystems (network shares, some
                # containers) may reject WAL. The server must still start.
                continue

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                uid           TEXT    NOT NULL UNIQUE,
                scope         TEXT    NOT NULL DEFAULT 'global',
                title         TEXT    NOT NULL DEFAULT '',
                content       TEXT    NOT NULL,
                tags          TEXT    NOT NULL DEFAULT '',
                kind          TEXT    NOT NULL DEFAULT 'note',
                importance    REAL    NOT NULL DEFAULT 0.5,
                content_hash  TEXT    NOT NULL,
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL,
                deleted_at    TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_scope_hash
                ON memories(scope, content_hash);
            CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(scope, deleted_at);
            CREATE INDEX IF NOT EXISTS idx_memories_updated
                ON memories(updated_at DESC);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._init_fts()
        self._conn.commit()

    def _init_fts(self) -> None:
        """Create the FTS5 index when the local SQLite build supports it."""
        try:
            self._conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    title, content, tags,
                    content='memories',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            self._conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES ('delete', old.id, old.title, old.content, old.tags);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES ('delete', old.id, old.title, old.content, old.tags);
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;
                """
            )
            self._fts_enabled = True
        except sqlite3.Error:
            self._fts_enabled = False

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- serialisation ----------------------------------------------------- #
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["uid"],
            "scope": row["scope"],
            "title": row["title"],
            "content": row["content"],
            "tags": column_to_tags(row["tags"]),
            "kind": row["kind"],
            "importance": row["importance"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted": row["deleted_at"] is not None,
        }

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> Dict[str, Any]:
        content = row["content"] or ""
        excerpt = content if len(content) <= 400 else content[:400].rstrip() + "…"
        return {
            "id": row["uid"],
            "scope": row["scope"],
            "title": row["title"],
            "excerpt": excerpt,
            "tags": column_to_tags(row["tags"]),
            "kind": row["kind"],
            "importance": row["importance"],
            "updated_at": row["updated_at"],
        }

    # -- writes ------------------------------------------------------------ #
    def write(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Any = None,
        scope: str = "global",
        kind: str = "note",
        importance: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or refresh a memory.

        The write is idempotent on ``(scope, content_hash)``: repeating the same
        write only bumps ``updated_at`` (and merges tags). Passing ``memory_id``
        targets an existing memory explicitly and edits it in place.
        """
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError("content exceeds %d characters" % MAX_CONTENT_CHARS)

        scope = (scope or "global").strip() or "global"
        kind = (kind or "note").strip().lower()
        if kind not in ALLOWED_KINDS:
            kind = "note"

        title = (title or "").strip() or _derive_title(content)
        title = title[:MAX_TITLE_CHARS]

        try:
            importance = float(importance)
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))

        tag_list = normalize_tags(tags)
        tag_column = tags_to_column(tag_list)
        digest = content_hash(scope, title, content)
        now = utcnow()

        cur = self._conn.cursor()
        try:
            if memory_id:
                row = cur.execute(
                    "SELECT * FROM memories WHERE uid = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("memory not found: %s" % memory_id)
                cur.execute(
                    """
                    UPDATE memories
                       SET title = ?, content = ?, tags = ?, kind = ?,
                           importance = ?, content_hash = ?, updated_at = ?,
                           deleted_at = NULL
                     WHERE uid = ?
                    """,
                    (title, content, tag_column, kind, importance, digest, now, memory_id),
                )
                created = False
                uid = memory_id
            else:
                existing = cur.execute(
                    "SELECT * FROM memories WHERE scope = ? AND content_hash = ?",
                    (scope, digest),
                ).fetchone()
                if existing is not None:
                    merged = sorted(
                        set(column_to_tags(existing["tags"])) | set(tag_list)
                    )
                    cur.execute(
                        """
                        UPDATE memories
                           SET title = ?, tags = ?, kind = ?,
                               importance = MAX(importance, ?),
                               updated_at = ?, deleted_at = NULL
                         WHERE uid = ?
                        """,
                        (title, tags_to_column(merged), kind, importance, now, existing["uid"]),
                    )
                    created = False
                    uid = existing["uid"]
                else:
                    uid = new_memory_id()
                    cur.execute(
                        """
                        INSERT INTO memories
                            (uid, scope, title, content, tags, kind, importance,
                             content_hash, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (uid, scope, title, content, tag_column, kind, importance,
                         digest, now, now),
                    )
                    created = True
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        record = self.get(uid)
        assert record is not None
        record["created"] = created
        return record

    def delete(self, memory_id: str, hard: bool = False) -> bool:
        """Soft-delete (default) or hard-delete a memory."""
        row = self._conn.execute(
            "SELECT uid FROM memories WHERE uid = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return False
        if hard:
            self._conn.execute("DELETE FROM memories WHERE uid = ?", (memory_id,))
        else:
            self._conn.execute(
                "UPDATE memories SET deleted_at = ?, updated_at = ? WHERE uid = ?",
                (utcnow(), utcnow(), memory_id),
            )
        self._conn.commit()
        return True

    # -- reads ------------------------------------------------------------- #
    def get(self, memory_id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        if not memory_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM memories WHERE uid = ?", (memory_id,)
        ).fetchone()
        if row is None:
            # Convenience: accept a numeric rowid as well as the public uid.
            try:
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (int(memory_id),)
                ).fetchone()
            except (TypeError, ValueError):
                row = None
        if row is None:
            return None
        if row["deleted_at"] is not None and not include_deleted:
            return None
        return self._row_to_dict(row)

    def search(
        self,
        query: str,
        limit: int = 10,
        scope: Optional[str] = None,
        tags: Any = None,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = _clamp(limit, 1, 100, 10)
        tag_list = normalize_tags(tags)

        rows = self._search_fts(query, limit, scope, tag_list, include_deleted)
        if not rows:
            rows = self._search_like(query, limit, scope, tag_list, include_deleted)
        return [self._row_to_summary(row) for row in rows]

    def _where(
        self,
        scope: Optional[str],
        tag_list: Sequence[str],
        include_deleted: bool,
    ) -> tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if not include_deleted:
            clauses.append("m.deleted_at IS NULL")
        if scope:
            clauses.append("m.scope = ?")
            params.append(scope)
        for tag in tag_list:
            clauses.append("(',' || m.tags || ',') LIKE ?")
            params.append("%%,%s,%%" % tag)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def _search_fts(
        self,
        query: str,
        limit: int,
        scope: Optional[str],
        tag_list: Sequence[str],
        include_deleted: bool,
    ) -> List[sqlite3.Row]:
        if not self._fts_enabled:
            return []
        match = _fts_query(query)
        if not match:
            return []
        where, params = self._where(scope, tag_list, include_deleted)
        sql = (
            "SELECT m.* FROM memories_fts f "
            "JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ?"
            + (where.replace(" WHERE ", " AND ") if where else "")
            + " ORDER BY bm25(memories_fts), m.importance DESC LIMIT ?"
        )
        try:
            return self._conn.execute(sql, [match] + params + [limit]).fetchall()
        except sqlite3.Error:
            return []

    def _search_like(
        self,
        query: str,
        limit: int,
        scope: Optional[str],
        tag_list: Sequence[str],
        include_deleted: bool,
    ) -> List[sqlite3.Row]:
        where, params = self._where(scope, tag_list, include_deleted)
        like = "%" + query.replace("%", r"\%").replace("_", r"\_") + "%"
        condition = (
            "(m.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\' "
            "OR m.tags LIKE ? ESCAPE '\\')"
        )
        if where:
            where = where + " AND " + condition
        else:
            where = " WHERE " + condition
        sql = (
            "SELECT m.* FROM memories m"
            + where
            + " ORDER BY m.importance DESC, m.updated_at DESC LIMIT ?"
        )
        return self._conn.execute(sql, params + [like, like, like, limit]).fetchall()

    def list(
        self,
        limit: int = 20,
        offset: int = 0,
        scope: Optional[str] = None,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        limit = _clamp(limit, 1, 200, 20)
        offset = _clamp(offset, 0, 10_000_000, 0)
        clauses: List[str] = []
        params: List[Any] = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            "SELECT * FROM memories"
            + where
            + " ORDER BY importance DESC, updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def stats(self) -> Dict[str, Any]:
        total = self._conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE deleted_at IS NULL"
        ).fetchone()["c"]
        deleted = self._conn.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE deleted_at IS NOT NULL"
        ).fetchone()["c"]
        scopes = [
            {"scope": row["scope"], "count": row["c"]}
            for row in self._conn.execute(
                "SELECT scope, COUNT(*) AS c FROM memories "
                "WHERE deleted_at IS NULL GROUP BY scope ORDER BY c DESC"
            ).fetchall()
        ]
        tags: Dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT tags FROM memories WHERE deleted_at IS NULL AND tags != ''"
        ).fetchall():
            for tag in column_to_tags(row["tags"]):
                tags[tag] = tags.get(tag, 0) + 1
        top_tags = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        return {
            "db_path": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
            "fts5": self._fts_enabled,
            "total": total,
            "deleted": deleted,
            "scopes": scopes,
            "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        }

    def export(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """Dump memories as plain JSON-able dicts (portable backup/migration)."""
        params: List[Any] = []
        where = " WHERE deleted_at IS NULL"
        if scope:
            where += " AND scope = ?"
            params.append(scope)
        rows = self._conn.execute(
            "SELECT * FROM memories" + where + " ORDER BY id ASC", params
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def dumps(self) -> str:
        return json.dumps(self.export(), ensure_ascii=False, indent=2)


def _derive_title(content: str) -> str:
    """Use the first non-empty line (trimmed) as a title."""
    for line in content.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return candidate[:MAX_TITLE_CHARS]
    return "untitled"
