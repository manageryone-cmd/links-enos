"""Unit tests for the SQLite memory store."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enos.store import (  # noqa: E402
    DEFAULT_DB_DIRNAME,
    DEFAULT_DB_FILENAME,
    MemoryStore,
    default_db_path,
    normalize_tags,
    project_db_path,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MemoryStore(Path(self.tmp.name) / "memory.db")
        self.addCleanup(self.store.close)

    def test_write_and_get_roundtrip(self) -> None:
        record = self.store.write(
            content="Deploy uses nginx on the edge node.",
            title="edge deploy",
            tags=["Deploy", "nginx", "deploy"],
            scope="project:demo",
            kind="fact",
            importance=0.9,
        )
        self.assertTrue(record["created"])
        self.assertTrue(record["id"].startswith("mem_"))
        # Tags are normalised (lowercase, deduped, sorted).
        self.assertEqual(record["tags"], ["deploy", "nginx"])

        fetched = self.store.get(record["id"])
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["content"], "Deploy uses nginx on the edge node.")
        self.assertEqual(fetched["kind"], "fact")
        self.assertAlmostEqual(fetched["importance"], 0.9)
        self.assertFalse(fetched["deleted"])

    def test_write_is_idempotent_and_merges_tags(self) -> None:
        first = self.store.write(content="same body", tags=["a"], scope="s")
        second = self.store.write(content="same body", tags=["b"], scope="s")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["tags"], ["a", "b"])
        self.assertEqual(len(self.store.list()), 1)

    def test_same_content_different_scope_is_distinct(self) -> None:
        a = self.store.write(content="shared fact", scope="alpha")
        b = self.store.write(content="shared fact", scope="beta")
        self.assertNotEqual(a["id"], b["id"])

    def test_update_by_id(self) -> None:
        record = self.store.write(content="draft", scope="s")
        updated = self.store.write(
            content="final", title="final title", memory_id=record["id"]
        )
        self.assertEqual(updated["id"], record["id"])
        self.assertEqual(updated["content"], "final")
        self.assertFalse(updated["created"])

    def test_update_unknown_id_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.write(content="x", memory_id="mem_doesnotexist")

    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.write(content="   ")

    def test_title_derived_from_first_line(self) -> None:
        record = self.store.write(content="# Rollback plan\n\nStep one.", scope="s")
        self.assertEqual(record["title"], "Rollback plan")

    def test_search_free_text(self) -> None:
        self.store.write(content="The staging database password rotates weekly.", tags=["db"])
        self.store.write(content="Nginx reload needs a config test first.", tags=["ops"])
        hits = self.store.search("database password")
        self.assertEqual(len(hits), 1)
        self.assertIn("staging database", hits[0]["excerpt"])

    def test_search_survives_fts_operators(self) -> None:
        self.store.write(content="quoted text", scope="s")
        # Must not raise an FTS5 syntax error.
        for query in ['"unbalanced', "AND OR NOT", "*", "(paren", "a-b:c"]:
            self.store.search(query, scope="s")

    def test_search_tag_filter(self) -> None:
        self.store.write(content="alpha memory", tags=["x", "y"], scope="s")
        self.store.write(content="beta memory", tags=["x"], scope="s")
        hits = self.store.search("memory", tags=["x", "y"], scope="s")
        self.assertEqual(len(hits), 1)
        self.assertIn("alpha", hits[0]["excerpt"])

    def test_search_scope_filter(self) -> None:
        self.store.write(content="scoped memory", scope="only-here")
        self.store.write(content="scoped memory", scope="elsewhere")
        hits = self.store.search("scoped", scope="only-here")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["scope"], "only-here")

    def test_soft_then_hard_delete(self) -> None:
        record = self.store.write(content="ephemeral", scope="s")
        self.assertTrue(self.store.delete(record["id"]))
        self.assertIsNone(self.store.get(record["id"]))
        self.assertEqual(self.store.list(), [])
        # MCP-facing list never shows soft-deleted rows ...
        self.assertEqual(self.store.search("ephemeral"), [])
        # ... but a repeat delete of the same row still finds it.
        self.assertTrue(self.store.delete(record["id"]))
        self.assertFalse(self.store.delete("mem_missing"))

    def test_hard_delete_removes_row(self) -> None:
        record = self.store.write(content="gone", scope="s")
        self.assertTrue(self.store.delete(record["id"], hard=True))
        self.assertEqual(self.store.stats()["deleted"], 0)
        self.assertEqual(self.store.stats()["total"], 0)

    def test_stats_and_export(self) -> None:
        self.store.write(content="one", tags=["t1"], scope="a")
        self.store.write(content="two", tags=["t1", "t2"], scope="b")
        stats = self.store.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["schema_version"], 1)
        self.assertEqual({s["scope"] for s in stats["scopes"]}, {"a", "b"})

        exported = self.store.export()
        self.assertEqual(len(exported), 2)
        self.assertIn("content", exported[0])

    def test_unicode_content(self) -> None:
        text = "部署使用 nginx；数据库每周轮换口令。（迁移：跨平台）"
        record = self.store.write(content=text, tags=["运维"], scope="中文")
        self.assertEqual(self.store.get(record["id"])["content"], text)
        self.assertEqual(len(self.store.search("nginx", scope="中文")), 1)

    def test_database_file_created(self) -> None:
        self.assertTrue(self.store.db_path.exists())
        self.assertEqual(self.store.db_path.name, "memory.db")


class ProjectScopedPathTests(unittest.TestCase):
    """The default database lives in the project, never in the home directory."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"ENOS_MEMORY_DB": ""}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        previous = os.getcwd()
        os.chdir(path)
        self.addCleanup(os.chdir, previous)

    def test_project_db_path_uses_cwd_by_default(self) -> None:
        self.chdir(self.root / "proj")
        expected = Path.cwd() / DEFAULT_DB_DIRNAME / DEFAULT_DB_FILENAME
        self.assertEqual(project_db_path(), expected)
        self.assertEqual(default_db_path(), expected)

    def test_env_var_still_wins(self) -> None:
        self.chdir(self.root / "proj")
        override = self.root / "elsewhere" / "custom.db"
        with mock.patch.dict(os.environ, {"ENOS_MEMORY_DB": str(override)}, clear=False):
            self.assertEqual(default_db_path(), override)

    def test_store_creates_dot_enos_in_cwd(self) -> None:
        project = self.root / "fresh-repo"
        self.chdir(project)
        store = MemoryStore()
        self.addCleanup(store.close)
        store.write(content="project level default", scope="p")

        self.assertEqual(store.db_path, project / ".enos" / "memory.db")
        self.assertTrue((project / ".enos").is_dir())
        self.assertTrue((project / ".enos" / "memory.db").exists())

    def test_two_projects_do_not_share_memories(self) -> None:
        project_a = self.root / "test-project-a"
        project_b = self.root / "test-project-b"

        self.chdir(project_a)
        with MemoryStore() as store_a:
            store_a.write(content="alpha secret", tags=["a"], scope="project")
            db_a = store_a.db_path

        self.chdir(project_b)
        with MemoryStore() as store_b:
            store_b.write(content="beta secret", tags=["b"], scope="project")
            db_b = store_b.db_path
            self.assertEqual(store_b.search("alpha"), [])
            self.assertEqual(len(store_b.search("beta")), 1)

        self.assertNotEqual(db_a, db_b)
        self.assertEqual(db_a, project_a / ".enos" / "memory.db")
        self.assertEqual(db_b, project_b / ".enos" / "memory.db")

        # Re-opening project A must not see project B's memory.
        self.chdir(project_a)
        with MemoryStore() as reopened:
            self.assertEqual(len(reopened.search("alpha")), 1)
            self.assertEqual(reopened.search("beta"), [])


class TagNormalisationTests(unittest.TestCase):
    def test_string_input(self) -> None:
        self.assertEqual(normalize_tags("Deploy, Nginx  deploy"), ["deploy", "nginx"])

    def test_none_and_empty(self) -> None:
        self.assertEqual(normalize_tags(None), [])
        self.assertEqual(normalize_tags(""), [])
        self.assertEqual(normalize_tags([]), [])

    def test_list_input(self) -> None:
        self.assertEqual(normalize_tags(["b", "a", "b"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
