#!/usr/bin/env python3
"""One-click MCP client configuration for the links-enos memory server.

Writes (or removes) the links-enos server entry in every local MCP client it can
find, without touching anything else in those files:

===================================  ==================================================
target                               config file
===================================  ==================================================
``cline`` / ``roo`` / ``zoocode``    ``<vscode globalStorage>/<ext>/settings/*mcp_settings.json``
``claude-code``                      ``claude mcp add`` else ``~/.claude.json``
``cursor``                           ``~/.cursor/mcp.json``
``claude-desktop``                   platform-specific ``claude_desktop_config.json``
``vscode``                           ``<project>/.vscode/mcp.json`` (uses ``servers``)
``windsurf``                         ``~/.codeium/windsurf/mcp_config.json``
``gemini``                           ``~/.gemini/settings.json``
``codex``                            ``~/.codex/config.toml``
===================================  ==================================================

Usage
-----
    python3 install_mcp.py                       # detect and configure what exists
    python3 install_mcp.py --target all --yes    # configure every supported client
    python3 install_mcp.py --mode docker         # no Python venv needed at all
    python3 install_mcp.py --mode binary --binary ./links-enos
    python3 install_mcp.py --print --target cursor
    python3 install_mcp.py --uninstall --target all
    python3 install_mcp.py --config /path/to/any_mcp_settings.json

Design notes
------------
* Standard library only, and no import of the ``enos`` package — this file stays
  usable on a machine that has not installed anything yet.
* Existing config is merged, never replaced: unknown keys and other MCP servers
  are preserved, and the previous file is backed up first.
* Output is ASCII-only so it renders correctly on a default Windows console.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.1.0"
DEFAULT_SERVER_NAME = "enos"
DEFAULT_IMAGE = "links-enos"
DEFAULT_DB_DIRNAME = ".enos"
DEFAULT_DB_FILENAME = "memory.db"

# Clients that substitute ${workspaceFolder} inside "args" / "env". Writing the
# database path through the variable keeps a single global config correct for
# every project, instead of pinning one absolute home-directory path.
WORKSPACE_VAR_TARGETS = frozenset({"vscode", "cursor"})
WORKSPACE_DB_PATH = "${workspaceFolder}/%s/%s" % (DEFAULT_DB_DIRNAME, DEFAULT_DB_FILENAME)

PROJECT_ROOT = Path(__file__).resolve().parent

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def say(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def warn(message: str) -> None:
    sys.stderr.write("warn: " + message + "\n")
    sys.stderr.flush()


def expand(path: str) -> Path:
    """Expand ``~``, ``$VARS`` and ``%VARS%`` then return an absolute Path."""
    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    return Path(expanded)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def project_db_path(start: Optional[Path] = None) -> Path:
    """Return ``<start>/.enos/memory.db`` — ``start`` defaults to the cwd."""
    base = Path(start).expanduser() if start is not None else Path.cwd()
    return base / DEFAULT_DB_DIRNAME / DEFAULT_DB_FILENAME


def default_db_path() -> Path:
    """Resolve the installer's default database path.

    ``$ENOS_MEMORY_DB`` wins; otherwise the path is project-scoped (``cwd``),
    mirroring what the server does when it is launched without ``--db``.
    """
    env = os.environ.get("ENOS_MEMORY_DB")
    if env:
        return expand(env)
    return project_db_path()


def read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON config, tolerating BOM and blank files."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("%s does not contain a JSON object" % path)
    return data


def write_text_atomic(path: Path, text: str, backup: bool = True) -> Optional[Path]:
    """Write ``text`` via a temp file + rename; optionally back up the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Optional[Path] = None
    if backup and path.exists():
        backup_path = path.with_name("%s.bak-%s" % (path.name, timestamp()))
        shutil.copy2(path, backup_path)

    fd, tmp_name = tempfile.mkstemp(prefix=".enos-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    if not IS_WINDOWS:
        try:
            # Configs can contain tokens for other servers: keep them private.
            os.chmod(path, 0o600)
        except OSError:
            pass
    return backup_path


def dump_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- #
# launch specification
# --------------------------------------------------------------------------- #
@dataclass
class LaunchSpec:
    mode: str
    command: str
    args: List[str]
    env: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    image: Optional[str] = None

    def as_client_config(self, require_type: bool = False) -> Dict[str, Any]:
        config: Dict[str, Any] = {"command": self.command, "args": list(self.args)}
        if self.env:
            config["env"] = dict(self.env)
        if require_type:
            config = {"type": "stdio", **config}
        return config

    def with_args(self, args: Sequence[str], description: Optional[str] = None) -> "LaunchSpec":
        """Copy of this spec with different arguments."""
        return replace(self, args=list(args), description=description or self.description)

    def with_db(self, db_path: str) -> "LaunchSpec":
        """Copy of this spec that opens ``db_path`` explicitly.

        For docker the path is translated into the host bind mount, because the
        in-container location stays ``/data/memory.db``.
        """
        if self.mode == "docker":
            return self.with_docker_dir(str(Path(db_path).expanduser().parent))
        return self.with_args(list(self.args) + ["--db", str(db_path)])

    def with_docker_dir(self, host_dir: str) -> "LaunchSpec":
        """Copy of a docker spec whose bind mount uses ``host_dir`` on the host."""
        args: List[str] = []
        skip_next = False
        for index, arg in enumerate(self.args):
            if skip_next:
                skip_next = False
                continue
            following = self.args[index + 1] if index + 1 < len(self.args) else ""
            if arg == "-v" and following.endswith(":/data"):
                args += ["-v", "%s:/data" % host_dir]
                skip_next = True
                continue
            args.append(arg)
        description = "container image '%s' (data in %s)" % (
            self.image or DEFAULT_IMAGE, host_dir
        )
        return replace(self, args=args, description=description)

    def verify_command(self) -> Optional[List[str]]:
        """Command that runs ``--selftest`` (or ``None`` when not runnable locally)."""
        if self.mode == "docker":
            return [
                "docker", "run", "--rm",
                "--entrypoint", "python", self.image or DEFAULT_IMAGE,
                "-m", "enos.mcp_server", "--selftest",
            ]
        return [self.command] + list(self.args) + ["--selftest"]


def build_launch(
    mode: str,
    db_path: Optional[Any] = None,
    binary: Optional[Path] = None,
    image: str = DEFAULT_IMAGE,
    project_root: Path = PROJECT_ROOT,
) -> LaunchSpec:
    """Translate ``--mode`` into a concrete client command.

    ``db_path=None`` leaves the ``--db`` flag out entirely, so the server falls
    back to its own project default: ``<cwd>/.enos/memory.db``. Passing a path
    pins the launch to that exact file instead.
    """
    explicit_db = Path(db_path).expanduser() if db_path else None
    if explicit_db is not None:
        explicit_db.parent.mkdir(parents=True, exist_ok=True)

    if mode == "python":
        env = {"PYTHONPATH": str(project_root)}
        # Use the absolute interpreter path: GUI clients often have a PATH that
        # lacks the user's shell additions (pyenv, conda, homebrew, ...).
        interpreter = sys.executable or shutil.which("python3") or "python3"
        args = ["-m", "enos.mcp_server"]
        if explicit_db is not None:
            args += ["--db", str(explicit_db)]
        return LaunchSpec(
            mode=mode,
            command=interpreter,
            args=args,
            env=env,
            description="runs from this Python interpreter (%s)" % interpreter,
        )

    if mode == "binary":
        if binary is None:
            raise SystemExit("--mode binary requires --binary /path/to/links-enos")
        binary_path = Path(binary).expanduser().resolve()
        if not binary_path.exists():
            raise SystemExit("binary not found: %s" % binary_path)
        args: List[str] = []
        if explicit_db is not None:
            args += ["--db", str(explicit_db)]
        return LaunchSpec(
            mode=mode,
            command=str(binary_path),
            args=args,
            description="single-file binary (%s)" % binary_path,
        )

    if mode == "docker":
        # A container never inherits the host's working directory, so it always
        # needs a concrete host directory to bind-mount.
        host_db = explicit_db or default_db_path()
        data_dir = host_db.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        volume = "%s:/data" % data_dir
        args = [
            "run", "-i", "--rm",
            "-v", volume,
            "-e", "ENOS_MEMORY_DB=/data/%s" % host_db.name,
        ]
        if not IS_WINDOWS and hasattr(os, "getuid"):
            # Match the invoking user so memory.db is not created root-owned.
            args += ["--user", "%d:%d" % (os.getuid(), os.getgid())]
        args.append(image)
        return LaunchSpec(
            mode=mode,
            command="docker",
            args=args,
            description="container image '%s' (data in %s)" % (image, data_dir),
            image=image,
        )

    if mode == "uvx":
        args = ["--from", "links-enos", "links-enos"]
        if explicit_db is not None:
            args += ["--db", str(explicit_db)]
        return LaunchSpec(
            mode=mode,
            command="uvx",
            args=args,
            description="uvx (no manual install)",
        )

    raise SystemExit("unknown mode: %s" % mode)


def workspace_launch(spec: LaunchSpec) -> LaunchSpec:
    """Variant that stores memory under the client's ``${workspaceFolder}``.

    Only used for clients that substitute the variable (VS Code, Cursor), so a
    single global config keeps resolving to whichever project is open.
    """
    return spec.with_db(WORKSPACE_DB_PATH)


def launch_for_target(
    spec: LaunchSpec,
    target_key: str,
    explicit_db: Optional[Path],
    project_db: Path,
) -> LaunchSpec:
    """Choose the database wiring a given client understands.

    * an explicit ``--db`` from the user wins everywhere;
    * clients with ``${workspaceFolder}`` support get the project-relative path;
    * all others get **no** ``--db`` at all, and the server resolves
      ``<cwd>/.enos/memory.db`` itself;
    * docker always needs a concrete host directory for the bind mount.
    """
    if explicit_db is not None:
        return spec.with_db(str(explicit_db))
    if target_key in WORKSPACE_VAR_TARGETS:
        return workspace_launch(spec)
    if spec.mode == "docker":
        return spec.with_docker_dir(str(project_db.parent))
    return spec


def verification_spec(spec: LaunchSpec) -> LaunchSpec:
    """Spec used for the post-install smoke test.

    The selftest writes to a throwaway file so that merely installing never
    creates ``.enos/`` in whatever directory the installer happened to run from.
    """
    if spec.mode == "docker":
        return spec
    scratch = Path(tempfile.gettempdir()) / "links-enos-selftest.db"
    return spec.with_db(str(scratch))


def verify_launch(spec: LaunchSpec, timeout: int = 30) -> Tuple[bool, str]:
    """Run the configured command's ``--selftest`` and report the outcome."""
    cmd = spec.verify_command()
    if cmd is None or shutil.which(cmd[0]) is None:
        return False, "not runnable here (missing '%s')" % cmd[0] if cmd else "n/a"
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **spec.env},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "verification failed: %s" % exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return False, "exit %d: %s" % (completed.returncode, detail[-1] if detail else "")
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, "unexpected selftest output"
    if not payload.get("ok"):
        return False, "selftest reported ok=false"
    return True, "ok (db=%s, fts5=%s)" % (payload.get("db_path"), payload.get("fts5"))


# --------------------------------------------------------------------------- #
# config writers
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    target: str
    path: Optional[Path]
    status: str  # written | removed | skipped | failed | unchanged
    detail: str = ""

    def render(self) -> str:
        label = {"written": "[ok]     ", "removed": "[removed]", "skipped": "[skip]   ",
                 "failed": "[fail]   ", "unchanged": "[same]   "}[self.status]
        location = str(self.path) if self.path else "-"
        return "%s %-16s %s%s" % (label, self.target, location,
                                  ("  <- " + self.detail) if self.detail else "")


def apply_json_config(
    path: Path,
    root_key: str,
    server_name: str,
    config: Dict[str, Any],
    uninstall: bool,
    dry_run: bool,
    backup: bool,
    require_type: bool = False,
    extra_defaults: Optional[Dict[str, Any]] = None,
) -> Result:
    target = path.name
    payload = dict(config)
    if extra_defaults:
        for key, value in extra_defaults.items():
            payload.setdefault(key, value)
    if require_type:
        payload = {"type": "stdio", **payload}

    try:
        data = read_json(path)
    except (ValueError, OSError) as exc:
        return Result(target, path, "failed", "cannot parse existing config: %s" % exc)

    servers = data.get(root_key)
    if servers is not None and not isinstance(servers, dict):
        return Result(target, path, "failed", "'%s' is not an object" % root_key)
    servers = dict(servers or {})

    if uninstall:
        if server_name not in servers:
            return Result(target, path, "skipped", "no '%s' entry" % server_name)
        servers.pop(server_name)
    else:
        servers[server_name] = payload

    if servers:
        data[root_key] = servers
    else:
        data.pop(root_key, None)

    if uninstall and not data:
        if dry_run:
            return Result(target, path, "removed", "would delete empty file")
        try:
            path.unlink()
        except OSError as exc:
            return Result(target, path, "failed", str(exc))
        return Result(target, path, "removed", "empty config file deleted")

    if dry_run:
        return Result(target, path, "removed" if uninstall else "written",
                      "dry-run; " + json.dumps(payload, ensure_ascii=False))

    try:
        backup_path = write_text_atomic(path, dump_json(data), backup=backup)
    except OSError as exc:
        return Result(target, path, "failed", str(exc))

    detail = "backup: %s" % backup_path.name if backup_path else ""
    return Result(target, path, "removed" if uninstall else "written", detail)


def toml_string(value: str) -> str:
    return json.dumps(value)  # JSON string escaping is valid TOML basic-string escaping


def render_toml_block(server_name: str, config: Dict[str, Any]) -> str:
    lines = ["[mcp_servers.%s]" % server_name]
    lines.append("command = %s" % toml_string(str(config.get("command", ""))))
    args = config.get("args") or []
    lines.append("args = [%s]" % ", ".join(toml_string(str(a)) for a in args))
    env = config.get("env") or {}
    if env:
        pairs = ", ".join("%s = %s" % (k, toml_string(str(v))) for k, v in env.items())
        lines.append("env = { %s }" % pairs)
    return "\n".join(lines) + "\n"


def apply_toml_config(
    path: Path,
    server_name: str,
    config: Dict[str, Any],
    uninstall: bool,
    dry_run: bool,
    backup: bool,
) -> Result:
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        return Result("codex", path, "failed", str(exc))

    header = "[mcp_servers.%s]" % server_name
    lines = text.splitlines()
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == header:
            start = index
            continue
        if start is not None and stripped.startswith("[") and not stripped.startswith("[["):
            end = index
            break

    block = render_toml_block(server_name, config).rstrip("\n").splitlines()

    if uninstall:
        if start is None:
            return Result("codex", path, "skipped", "no '%s' table" % server_name)
        new_lines = lines[:start] + lines[end:]
    else:
        if start is None:
            new_lines = lines + ([""] if lines and lines[-1].strip() else []) + block
        else:
            tail = lines[end:]
            new_lines = lines[:start] + block + ([""] if tail else []) + tail

    new_text = "\n".join(new_lines).rstrip("\n") + "\n"
    if new_text == text:
        return Result("codex", path, "unchanged")
    if dry_run:
        return Result("codex", path, "written", "dry-run")

    try:
        backup_path = write_text_atomic(path, new_text, backup=backup)
    except OSError as exc:
        return Result("codex", path, "failed", str(exc))
    detail = "backup: %s" % backup_path.name if backup_path else ""
    return Result("codex", path, "removed" if uninstall else "written", detail)


# --------------------------------------------------------------------------- #
# target discovery
# --------------------------------------------------------------------------- #
VSCODE_GLOBALSTORAGE_ROOTS = [
    "~/.config/Code/User/globalStorage",
    "~/.config/Code - Insiders/User/globalStorage",
    "~/.config/VSCodium/User/globalStorage",
    "~/.vscode-server/data/User/globalStorage",
    "~/.vscode-remote/data/User/globalStorage",
    "~/Library/Application Support/Code/User/globalStorage",
    "~/Library/Application Support/Code - Insiders/User/globalStorage",
    "~/Library/Application Support/VSCodium/User/globalStorage",
    "%APPDATA%/Code/User/globalStorage",
    "%APPDATA%/Code - Insiders/User/globalStorage",
    "%APPDATA%/VSCodium/User/globalStorage",
]

# Known extension ids. Unknown forks (ZooCode, ...) are found by glob below.
CLINE_LIKE_EXTENSION_IDS = (
    "saoudrizwan.claude-dev",       # Cline
    "rooveterinaryinc.roo-cline",   # Roo Code
    "zoocode.zoocode",              # ZooCode (and forks that reuse the id)
    "zoo-code.zoo-code",
    "zoo.zoocode",
)

CLINE_SETTINGS_FILENAMES = (
    "cline_mcp_settings.json",
    "mcp_settings.json",
    "roo_mcp_settings.json",
    "zoo_mcp_settings.json",
)


@dataclass
class PathTarget:
    key: str          # target name used on the CLI
    label: str        # human label
    path: Path
    kind: str         # json | toml
    root_key: str = "mcpServers"


def claude_desktop_path() -> Path:
    if sys.platform == "darwin":
        return expand("~/Library/Application Support/Claude/claude_desktop_config.json")
    if IS_WINDOWS:
        return expand("%APPDATA%/Claude/claude_desktop_config.json")
    return expand("~/.config/Claude/claude_desktop_config.json")


def _looks_like_cline_family(ext_dir: Path) -> bool:
    name = ext_dir.name.lower()
    return any(token in name for token in ("cline", "roo", "zoo"))


def discover_cline_family() -> List[PathTarget]:
    """Find Cline / Roo / ZooCode style ``*mcp_settings.json`` files."""
    found: List[PathTarget] = []
    seen: set = set()

    for raw_root in VSCODE_GLOBALSTORAGE_ROOTS:
        root = expand(raw_root)
        if not root.is_dir():
            continue
        try:
            candidates = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for ext_dir in candidates:
            known = ext_dir.name in CLINE_LIKE_EXTENSION_IDS
            if not (known or _looks_like_cline_family(ext_dir)):
                continue
            settings_dir = ext_dir / "settings"
            if not settings_dir.is_dir():
                # Not an MCP-capable extension install.
                continue
            chosen: Optional[Path] = None
            for filename in CLINE_SETTINGS_FILENAMES:
                candidate = settings_dir / filename
                if candidate.exists():
                    chosen = candidate
                    break
            if chosen is None:
                if not known:
                    continue  # unknown fork with no config yet: leave it alone
                chosen = settings_dir / "cline_mcp_settings.json"

            if chosen in seen:
                continue
            seen.add(chosen)
            key = "cline"
            if "roo" in ext_dir.name.lower():
                key = "roo"
            elif "zoo" in ext_dir.name.lower():
                key = "zoocode"
            found.append(PathTarget(key, "Cline-family (%s)" % ext_dir.name, chosen, "json"))
    return found


def standard_targets(project_dir: Path) -> List[PathTarget]:
    return [
        PathTarget("cursor", "Cursor", expand("~/.cursor/mcp.json"), "json"),
        PathTarget("claude-desktop", "Claude Desktop", claude_desktop_path(), "json"),
        PathTarget("windsurf", "Windsurf", expand("~/.codeium/windsurf/mcp_config.json"), "json"),
        PathTarget("gemini", "Gemini CLI", expand("~/.gemini/settings.json"), "json"),
        PathTarget("vscode", "VS Code (workspace)", project_dir / ".vscode" / "mcp.json", "json", root_key="servers"),
        PathTarget("codex", "Codex CLI", expand("~/.codex/config.toml"), "toml", root_key="mcp_servers"),
    ]


ALL_TARGET_KEYS = ("cline", "roo", "zoocode", "claude-code", "cursor",
                   "claude-desktop", "vscode", "windsurf", "gemini", "codex")


# --------------------------------------------------------------------------- #
# Claude Code (CLI first, JSON fallback)
# --------------------------------------------------------------------------- #
def configure_claude_code(
    spec: LaunchSpec,
    server_name: str,
    uninstall: bool,
    dry_run: bool,
    backup: bool,
) -> List[Result]:
    results: List[Result] = []
    claude_bin = shutil.which("claude")

    if claude_bin and not dry_run:
        if uninstall:
            cmd = [claude_bin, "mcp", "remove", server_name, "-s", "user"]
        else:
            cmd = [claude_bin, "mcp", "add", server_name]
            for key, value in spec.env.items():
                cmd += ["-e", "%s=%s" % (key, value)]
            cmd += ["-s", "user", "--", spec.command] + list(spec.args)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if completed.returncode == 0:
                return [Result("claude-code", None,
                               "removed" if uninstall else "written",
                               "via `claude mcp %s`" % ("remove" if uninstall else "add"))]
            warn("claude CLI failed (%d): %s -- falling back to ~/.claude.json"
                 % (completed.returncode, (completed.stderr or "").strip()[:200]))
        except (OSError, subprocess.SubprocessError) as exc:
            warn("claude CLI unavailable (%s) -- falling back to ~/.claude.json" % exc)

    if claude_bin and dry_run:
        results.append(Result("claude-code", None, "written",
                              "dry-run; would call `claude mcp add %s`" % server_name))
        return results

    # Fallback: user-scope config file (~/.claude.json).
    claude_json = expand("~/.claude.json")
    outcome = apply_json_config(
        claude_json, "mcpServers", server_name,
        spec.as_client_config(), uninstall, dry_run, backup,
    )
    return [Result("claude-code", claude_json, outcome.status, outcome.detail)]


# --------------------------------------------------------------------------- #
# main orchestration
# --------------------------------------------------------------------------- #
def select_targets(
    requested: Sequence[str],
    project_dir: Path,
    explicit_config: Optional[Path],
) -> Tuple[List[PathTarget], bool]:
    """Resolve ``--target`` into path targets. Returns ``(targets, wants_claude_code)``."""
    cline_targets = discover_cline_family()
    std_targets = standard_targets(project_dir)
    everything = cline_targets + std_targets

    if explicit_config:
        kind = "toml" if explicit_config.suffix.lower() == ".toml" else "json"
        root = "mcp_servers" if kind == "toml" else "mcpServers"
        return ([PathTarget("custom", "Custom config", explicit_config, kind, root_key=root)], False)

    wants = {t.strip().lower() for t in requested if t.strip()}
    if not wants or "auto" in wants:
        # Auto: only touch configs that already exist (safe default).
        existing = [t for t in everything if t.path.exists()]
        return existing, False

    if "all" in wants:
        # Force-create every known standard location plus detected Cline installs.
        return everything, True

    selected: List[PathTarget] = []
    want_claude_code = "claude-code" in wants
    aliases = {
        "claude": "claude-code",
        "claudecode": "claude-code",
        "vscode-insiders": "vscode",
        "roo-code": "roo",
        "rope": "roo",
        "zoo": "zoocode",
    }
    wants = {aliases.get(w, w) for w in wants}

    if "cline" in wants:
        selected.extend([t for t in cline_targets if t.key == "cline"]
                        or [t for t in std_targets if t.key == "cline"])
        # If Cline is installed but never initialised, create its settings file.
        if not any(t.key == "cline" for t in selected):
            for raw_root in VSCODE_GLOBALSTORAGE_ROOTS:
                root = expand(raw_root)
                ext = root / "saoudrizwan.claude-dev" / "settings"
                if ext.parent.is_dir():
                    selected.append(PathTarget("cline", "Cline", ext / "cline_mcp_settings.json", "json"))
                    break
    for key in ("roo", "zoocode"):
        if key in wants:
            selected.extend([t for t in cline_targets if t.key == key])

    for target in std_targets:
        if target.key in wants:
            selected.append(target)

    unknown = wants - set(ALL_TARGET_KEYS) - {"cline", "roo", "zoocode"}
    if unknown:
        warn("ignoring unknown target(s): %s" % ", ".join(sorted(unknown)))

    return selected, want_claude_code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_mcp.py",
        description="Configure MCP clients to launch the links-enos memory server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[-1] if __doc__ else None,
    )
    parser.add_argument("--target", "-t", action="append", default=[],
                        help="Target client(s): %s, or 'all' / 'auto' (comma separated)."
                             % ", ".join(ALL_TARGET_KEYS))
    parser.add_argument("--mode", "-m", default="python",
                        choices=["python", "binary", "docker", "uvx"],
                        help="How the client should launch the server (default: python).")
    parser.add_argument("--binary", default=None, help="Path to the PyInstaller binary (--mode binary).")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Container image name (--mode docker).")
    parser.add_argument(
        "--db", default=None,
        help="Memory database path. Default: per-project <cwd>/.enos/memory.db "
             "(clients that support ${workspaceFolder} get it written as a variable).",
    )
    parser.add_argument("--server-name", default=DEFAULT_SERVER_NAME, help="MCP server key (default: enos).")
    parser.add_argument("--project-dir", default=".", help="Project directory for .vscode/mcp.json.")
    parser.add_argument("--config", default=None, help="Write into this specific config file instead.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the server entry.")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print the JSON entry and exit without writing anything.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change.")
    parser.add_argument("--verify", dest="verify", action="store_true", default=None,
                        help="Run the selftest against the configured command.")
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files.")
    parser.add_argument("--list", dest="list_only", action="store_true", help="List detected targets.")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive (accepted for scripts).")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)

    if args.version:
        say("enos install_mcp %s" % VERSION)
        return 0

    targets_raw: List[str] = []
    for chunk in args.target:
        targets_raw.extend(part for part in chunk.split(",") if part.strip())
    if not targets_raw:
        targets_raw = ["auto"]

    explicit_db = Path(args.db).expanduser() if args.db else None
    project_dir = Path(args.project_dir).expanduser().resolve()
    # Project-level default. Only used for docker bind mounts and for reporting;
    # non-docker clients are left to resolve their own cwd unless --db is given.
    project_db = project_db_path(project_dir)
    explicit_config = Path(args.config).expanduser() if args.config else None

    targets, wants_claude_code = select_targets(targets_raw, project_dir, explicit_config)

    if args.list_only:
        say("Detected MCP clients:")
        if not targets and not wants_claude_code:
            say("  (none - use --target all to write the standard locations)")
        for target in targets:
            state = "exists" if target.path.exists() else "missing"
            say("  %-16s %s  [%s]" % (target.key, target.path, state))
        if shutil.which("claude"):
            say("  %-16s %s  [claude CLI]" % ("claude-code", "~/.claude.json"))
        return 0

    try:
        spec = build_launch(
            args.mode, explicit_db, binary=Path(args.binary) if args.binary else None,
            image=args.image,
        )
    except SystemExit as exc:
        warn(str(exc))
        return 2

    if args.print_only:
        requested = {chunk.strip().lower() for chunk in targets_raw}
        sample_spec = spec
        if explicit_db is None and requested & WORKSPACE_VAR_TARGETS:
            sample_spec = workspace_launch(spec)
        sample = {"mcpServers": {args.server_name: sample_spec.as_client_config()}}
        say(json.dumps(sample, indent=2, ensure_ascii=False))
        say("")
        say("# launch mode: %s - %s" % (sample_spec.mode, sample_spec.description))
        say("# database: %s" % (explicit_db or "<client cwd>/.enos/memory.db (per project)"))
        return 0

    say("links-enos MCP installer %s" % VERSION)
    say("  mode    : %s (%s)" % (spec.mode, spec.description))
    say("  command : %s %s" % (spec.command, " ".join(spec.args)))
    say("  database: %s" % (explicit_db or "<client cwd>/.enos/memory.db (per project)"))
    if args.dry_run:
        say("  DRY RUN - no files will be written")
    say("")

    results: List[Result] = []

    if wants_claude_code:
        results.extend(configure_claude_code(
            launch_for_target(spec, "claude-code", explicit_db, project_db),
            args.server_name, args.uninstall, args.dry_run, not args.no_backup,
        ))

    for target in targets:
        client_spec = launch_for_target(spec, target.key, explicit_db, project_db)
        if target.kind == "toml":
            results.append(apply_toml_config(
                target.path, args.server_name, client_spec.as_client_config(),
                args.uninstall, args.dry_run, not args.no_backup,
            ))
            continue

        extra_defaults = None
        require_type = False
        if target.key in ("cline", "roo", "zoocode"):
            # Cline-family UI reads these; harmless for forks that ignore them.
            extra_defaults = {"disabled": False, "autoApprove": [], "timeout": 60}
        elif target.key == "vscode":
            require_type = True

        result = apply_json_config(
            target.path, target.root_key, args.server_name,
            client_spec.as_client_config(require_type=require_type),
            args.uninstall, args.dry_run, not args.no_backup,
            extra_defaults=extra_defaults,
        )
        result.target = target.key
        results.append(result)

    if not results:
        say("No MCP client config detected.\n")
        say("Options:")
        say("  python3 install_mcp.py --target all          # write every standard location")
        say("  python3 install_mcp.py --list                # show what was detected")
        say("  python3 install_mcp.py --print --target cursor")
        return 0

    for result in results:
        say(result.render())

    written = [r for r in results if r.status == "written"]
    failed = [r for r in results if r.status == "failed"]

    if written and not args.uninstall and not args.dry_run and args.verify is not False:
        say("")
        say("Verifying launch command ...")
        ok, detail = verify_launch(verification_spec(spec))
        say("%s selftest: %s" % ("[ok]  " if ok else "[fail]", detail))
        if not ok and args.mode == "python":
            say("       hint: try --mode binary, or --mode docker if Python is not "
                "available to the client.")

    say("")
    if failed:
        say("%d target(s) failed. Existing files were left untouched." % len(failed))
        return 1

    if args.uninstall:
        say("links-enos memory entry removed. Restart the client to apply.")
    else:
        say("Done. Restart the client(s) to pick up the new MCP server.")
        say("Smoke test any time with:  %s" % " ".join(spec.verify_command() or []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
