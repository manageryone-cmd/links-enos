"""Tests for the multi-client installer (config merge, backup, uninstall)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_mcp  # noqa: E402


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        self.db = Path(self.tmp.name) / "data" / "memory.db"

        patcher = mock.patch.dict(
            os.environ,
            {"HOME": str(self.home), "USERPROFILE": str(self.home), "ENOS_MEMORY_DB": ""},
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- helpers ---------------------------------------------------------- #
    def chdir(self, path: Path) -> None:
        previous = os.getcwd()
        os.chdir(str(path))
        self.addCleanup(os.chdir, previous)

    def cli(self, *args: str) -> int:
        return install_mcp.main(
            ["--no-verify", "--yes", "--project-dir", str(self.project), "--db", str(self.db), *args]
        )

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    # -- launch specs ----------------------------------------------------- #
    def test_python_launch_spec(self) -> None:
        spec = install_mcp.build_launch("python", self.db)
        self.assertEqual(spec.args[:2], ["-m", "enos.mcp_server"])
        self.assertIn("--db", spec.args)
        self.assertEqual(spec.env["PYTHONPATH"], str(install_mcp.PROJECT_ROOT))
        config = spec.as_client_config()
        self.assertIn("command", config)
        self.assertIn("enos.mcp_server", config["args"])

    def test_python_launch_without_db_omits_the_flag(self) -> None:
        spec = install_mcp.build_launch("python")
        self.assertEqual(spec.args, ["-m", "enos.mcp_server"])
        self.assertNotIn("--db", spec.args)

    def test_uvx_launch_without_db_omits_the_flag(self) -> None:
        spec = install_mcp.build_launch("uvx")
        self.assertNotIn("--db", spec.args)

    def test_default_db_path_is_project_scoped(self) -> None:
        self.chdir(self.project)
        expected = Path(os.getcwd()) / ".enos" / "memory.db"
        self.assertEqual(install_mcp.default_db_path(), expected)
        self.assertNotIn(str(self.home), str(install_mcp.default_db_path()))

    def test_docker_launch_spec(self) -> None:
        spec = install_mcp.build_launch("docker", self.db, image="links-enos:latest")
        self.assertEqual(spec.command, "docker")
        self.assertEqual(spec.args[0], "run")
        self.assertIn("-i", spec.args)          # -i is mandatory for the transport
        self.assertNotIn("-t", spec.args)       # -t would corrupt the stream
        self.assertIn("-v", spec.args)
        self.assertEqual(spec.args[-1], "links-enos:latest")
        self.assertIn("ENOS_MEMORY_DB=/data/memory.db", spec.args)

    def test_docker_default_mount_is_project_scoped(self) -> None:
        self.chdir(self.project)
        spec = install_mcp.build_launch("docker")
        volume = spec.args[spec.args.index("-v") + 1]
        self.assertEqual(volume, "%s:/data" % (Path(os.getcwd()) / ".enos"))
        self.assertNotIn(str(self.home), " ".join(spec.args))

    def test_docker_workspace_launch_uses_the_variable(self) -> None:
        spec = install_mcp.build_launch("docker", self.db)
        ws = install_mcp.workspace_launch(spec)
        volume = ws.args[ws.args.index("-v") + 1]
        self.assertEqual(volume, "${workspaceFolder}/.enos:/data")
        # The in-container path must not change, only the host mount.
        self.assertIn("ENOS_MEMORY_DB=/data/memory.db", ws.args)

    def test_uvx_launch_spec(self) -> None:
        spec = install_mcp.build_launch("uvx", self.db)
        self.assertEqual(spec.command, "uvx")
        self.assertIn("links-enos", spec.args)

    def test_binary_launch_requires_existing_file(self) -> None:
        with self.assertRaises(SystemExit):
            install_mcp.build_launch("binary", self.db, binary=Path("/nonexistent/links-enos"))

    # -- JSON clients ----------------------------------------------------- #
    def test_cursor_config_written(self) -> None:
        self.assertEqual(self.cli("--target", "cursor"), 0)
        config = self.read_json(self.home / ".cursor" / "mcp.json")
        entry = config["mcpServers"]["enos"]
        self.assertTrue(entry["args"][0:2] == ["-m", "enos.mcp_server"])
        self.assertEqual(entry["env"]["PYTHONPATH"], str(install_mcp.PROJECT_ROOT))

    def test_existing_servers_are_preserved(self) -> None:
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}, "theme": "dark"}),
            encoding="utf-8",
        )

        self.assertEqual(self.cli("--target", "cursor"), 0)
        config = self.read_json(path)
        self.assertIn("github", config["mcpServers"])
        self.assertIn("enos", config["mcpServers"])
        self.assertEqual(config["theme"], "dark")

        backups = list(path.parent.glob("mcp.json.bak-*"))
        self.assertEqual(len(backups), 1)

    def test_repeated_install_is_stable(self) -> None:
        self.cli("--target", "cursor")
        first = (self.home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        self.cli("--target", "cursor")
        second = (self.home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(len(self.read_json(self.home / ".cursor" / "mcp.json")["mcpServers"]), 1)

    def test_no_backup_flag(self) -> None:
        self.cli("--target", "cursor")
        (self.home / ".cursor" / "mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
        install_mcp.main(
            ["--no-verify", "--yes", "--target", "cursor", "--no-backup", "--db", str(self.db)]
        )
        self.assertEqual(list((self.home / ".cursor").glob("*.bak-*")), [])

    def test_custom_server_name(self) -> None:
        install_mcp.main(
            ["--no-verify", "--yes", "--target", "cursor", "--server-name", "team-memory",
             "--db", str(self.db)]
        )
        config = self.read_json(self.home / ".cursor" / "mcp.json")
        self.assertIn("team-memory", config["mcpServers"])

    def test_vscode_uses_servers_key_and_stdio_type(self) -> None:
        self.assertEqual(self.cli("--target", "vscode"), 0)
        config = self.read_json(self.project / ".vscode" / "mcp.json")
        self.assertNotIn("mcpServers", config)
        entry = config["servers"]["enos"]
        self.assertEqual(entry["type"], "stdio")

    def test_uninstall_removes_only_our_entry(self) -> None:
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}}), encoding="utf-8"
        )
        self.cli("--target", "cursor")
        self.cli("--target", "cursor", "--uninstall")
        config = self.read_json(path)
        self.assertNotIn("enos", config["mcpServers"])
        self.assertIn("github", config["mcpServers"])

    def test_uninstall_deletes_file_when_it_becomes_empty(self) -> None:
        self.cli("--target", "cursor")
        self.cli("--target", "cursor", "--uninstall")
        self.assertFalse((self.home / ".cursor" / "mcp.json").exists())

    def test_invalid_existing_json_is_not_clobbered(self) -> None:
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir(parents=True)
        path.write_text("{ this is not json", encoding="utf-8")
        code = self.cli("--target", "cursor")
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "{ this is not json")

    def test_dry_run_writes_nothing(self) -> None:
        self.assertEqual(self.cli("--target", "cursor", "--dry-run"), 0)
        self.assertFalse((self.home / ".cursor" / "mcp.json").exists())

    def test_print_only_writes_nothing(self) -> None:
        code = install_mcp.main(["--print", "--target", "cursor", "--mode", "docker"])
        self.assertEqual(code, 0)
        self.assertFalse((self.home / ".cursor").exists())

    def test_auto_mode_touches_nothing_when_no_client_exists(self) -> None:
        self.assertEqual(self.cli("--target", "auto"), 0)
        self.assertEqual(list(self.home.rglob("*.json")), [])

    # -- project-level database wiring ------------------------------------ #
    def test_cursor_uses_workspace_folder_variable(self) -> None:
        code = install_mcp.main(
            ["--no-verify", "--yes", "--target", "cursor", "--project-dir", str(self.project)]
        )
        self.assertEqual(code, 0)
        entry = self.read_json(self.home / ".cursor" / "mcp.json")["mcpServers"]["enos"]
        self.assertEqual(
            entry["args"],
            ["-m", "enos.mcp_server", "--db", "${workspaceFolder}/.enos/memory.db"],
        )
        self.assertNotIn(str(self.home), json.dumps(entry))

    def test_vscode_uses_workspace_folder_variable(self) -> None:
        code = install_mcp.main(
            ["--no-verify", "--yes", "--target", "vscode", "--project-dir", str(self.project)]
        )
        self.assertEqual(code, 0)
        entry = self.read_json(self.project / ".vscode" / "mcp.json")["servers"]["enos"]
        self.assertIn("${workspaceFolder}/.enos/memory.db", entry["args"])
        self.assertNotIn(str(self.home), json.dumps(entry))

    def test_claude_desktop_omits_the_db_flag(self) -> None:
        code = install_mcp.main(
            ["--no-verify", "--yes", "--target", "claude-desktop", "--project-dir", str(self.project)]
        )
        self.assertEqual(code, 0)
        path = install_mcp.claude_desktop_path()
        entry = self.read_json(path)["mcpServers"]["enos"]
        self.assertNotIn("--db", entry["args"])
        self.assertNotIn(str(self.home), json.dumps(entry))

    def test_codex_omits_the_db_flag(self) -> None:
        code = install_mcp.main(
            ["--no-verify", "--yes", "--target", "codex", "--project-dir", str(self.project)]
        )
        self.assertEqual(code, 0)
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("--db", text)
        self.assertNotIn(str(self.home), text)

    def test_explicit_db_wins_over_the_workspace_variable(self) -> None:
        self.cli("--target", "cursor")
        entry = self.read_json(self.home / ".cursor" / "mcp.json")["mcpServers"]["enos"]
        self.assertEqual(entry["args"][-2:], ["--db", str(self.db)])

    def test_print_uses_workspace_variable_for_cursor(self) -> None:
        self.assertEqual(install_mcp.main(["--print", "--target", "cursor"]), 0)
        # print_only writes nothing at all
        self.assertFalse((self.home / ".cursor").exists())

    # -- Cline family ----------------------------------------------------- #
    def test_cline_settings_written_with_defaults(self) -> None:
        settings = (
            self.home / ".config" / "Code" / "User" / "globalStorage"
            / "saoudrizwan.claude-dev" / "settings"
        )
        settings.mkdir(parents=True)
        self.assertEqual(self.cli("--target", "cline"), 0)
        config = self.read_json(settings / "cline_mcp_settings.json")
        entry = config["mcpServers"]["enos"]
        self.assertFalse(entry["disabled"])
        self.assertEqual(entry["autoApprove"], [])

    def test_zoo_fork_with_custom_file_is_detected(self) -> None:
        settings = (
            self.home / ".config" / "Code" / "User" / "globalStorage"
            / "zoocode.zoocode" / "settings"
        )
        settings.mkdir(parents=True)
        (settings / "mcp_settings.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "keep-me"}}}), encoding="utf-8"
        )
        self.assertEqual(self.cli("--target", "zoocode"), 0)
        config = self.read_json(settings / "mcp_settings.json")
        self.assertIn("other", config["mcpServers"])
        self.assertIn("enos", config["mcpServers"])

    # -- TOML client ------------------------------------------------------ #
    def test_codex_toml_add_and_remove(self) -> None:
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir(parents=True)
        path.write_text('model = "gpt-5"\n', encoding="utf-8")

        self.assertEqual(self.cli("--target", "codex"), 0)
        text = path.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-5"', text)
        self.assertIn("[mcp_servers.enos]", text)
        self.assertIn("enos.mcp_server", text)

        self.assertEqual(self.cli("--target", "codex", "--uninstall"), 0)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("[mcp_servers.enos]", text)
        self.assertIn('model = "gpt-5"', text)

    def test_codex_toml_update_does_not_duplicate(self) -> None:
        self.cli("--target", "codex")
        self.cli("--target", "codex")
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(text.count("[mcp_servers.enos]"), 1)

    # -- explicit config escape hatch ------------------------------------- #
    def test_explicit_config_file(self) -> None:
        custom = Path(self.tmp.name) / "custom" / "mcp.json"
        self.cli("--config", str(custom))
        config = self.read_json(custom)
        self.assertIn("enos", config["mcpServers"])

    def test_verify_detects_working_command(self) -> None:
        spec = install_mcp.build_launch("python", self.db)
        ok, detail = install_mcp.verify_launch(spec)
        self.assertTrue(ok, detail)
        self.assertIn("ok", detail)


if __name__ == "__main__":
    unittest.main()
