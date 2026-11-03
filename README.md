# links-enos

A dependency-free MCP **memory** server over stdio — one process, one JSON-RPC pipe, one SQLite file.

> 中文摘要：links-enos 是一个零第三方依赖的 MCP 记忆服务器，走 stdio 传输，用单个 SQLite
> 文件按项目保存记忆。装好即可被 Claude Desktop、VS Code、Cursor、ZooCode 等 MCP 客户端直接拉起。

## What it does

- Gives an MCP client durable memory: write a fact once, then search or recall it in later sessions.
- Keeps one SQLite database per project (`<cwd>/.enos/memory.db`), so unrelated projects never share memories.
- Exposes eight `memory_*` tools, uses the standard library only, and degrades full-text search to `LIKE` when SQLite lacks FTS5.

## Install

Only `Python >= 3.8` is required, and only for the Python forms. Pick one path:

```text
Can the machine that runs the server use Python >= 3.8?
├─ yes ─┬─ working from a source checkout ─────► A. editable install
│       └─ want a pinned artifact ────────────► B. wheel
└─ no ──┬─ a container runtime is available ──► C. Docker image
        └─ no runtime available ──────────────► D. single-file binary
```

### A. Editable install (source checkout)

```bash
git clone https://github.com/<org>/links-enos.git
cd links-enos
python3 -m pip install -e .
links-enos --selftest
```

### B. Wheel

```bash
python3 -m pip install build
python3 -m build --wheel
python3 -m pip install dist/links_enos-*.whl
links-enos --selftest
```

### C. Docker

```bash
docker build -t links-enos:latest .
docker run -i --rm -v "$PWD/.enos:/data" links-enos:latest
```

`-i` is required (`-t` must not be used): stdin/stdout **is** the transport.

### D. Single-file binary

Build on a machine that has Python and PyInstaller; the result needs neither.

```bash
python3 -m pip install "pyinstaller>=6.0"
./scripts/build_binary.sh          # -> dist/links-enos
./dist/links-enos --selftest
```

## Install non-interactively

`install_mcp.py` merges one server entry into each client's own config file, backs the file up first,
and never touches other entries. It assumes **no interaction**: it never prompts, so scripts and
agents can run it unattended.

```bash
python3 install_mcp.py --yes --target all
python3 install_mcp.py --yes --target cursor,claude-desktop
python3 install_mcp.py --yes --target zoocode --mode docker --image links-enos:latest
```

| flag | effect |
| --- | --- |
| `--target <client>` | `cline`, `roo`, `zoocode`, `claude-code`, `cursor`, `claude-desktop`, `vscode`, `windsurf`, `gemini`, `codex`, `all`, or `auto` (default: existing configs only) |
| `--mode` | `python` (default), `binary`, `docker`, `uvx` |
| `--print` | print the JSON entry that would be written; writes nothing |
| `--dry-run` | show every file that would change; writes nothing |
| `--list` | list detected clients and their config paths; writes nothing |
| `--yes` / `-y` | non-interactive flag, accepted so automation can state it explicitly |
| `--server-name` | key used inside the client config (default `enos`) |

Exit codes: `0` success, `1` one or more targets failed (their files were left untouched),
`2` invalid launch options. The examples below use the key `links-enos`; pass
`--server-name links-enos` if you want the installer to write the same key.

## Use with an MCP client

### Claude Desktop

Config file: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS),
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), `~/.config/Claude/claude_desktop_config.json` (Linux).
Full file: [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

```json
{
  "mcpServers": {
    "links-enos": {
      "command": "python3",
      "args": ["-m", "enos.mcp_server"],
      "env": { "PYTHONPATH": "<path-to-links-enos>" }
    }
  }
}
```

Replace `<path-to-links-enos>` with your checkout, or, after a wheel install, drop `env` and use
`"command": "links-enos", "args": []`.

### VS Code

Workspace file `.vscode/mcp.json`. Full file: [`examples/vscode-mcp.json`](examples/vscode-mcp.json).

```json
{
  "servers": {
    "links-enos": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "enos.mcp_server", "--db", "${workspaceFolder}/.enos/memory.db"],
      "env": { "PYTHONPATH": "${workspaceFolder}" }
    }
  }
}
```

### Cursor

Config file `~/.cursor/mcp.json`, same `mcpServers` shape as Claude Desktop. Cursor expands
`${workspaceFolder}`, so one global config follows the open project:

```json
{
  "mcpServers": {
    "links-enos": {
      "command": "python3",
      "args": ["-m", "enos.mcp_server", "--db", "${workspaceFolder}/.enos/memory.db"],
      "env": { "PYTHONPATH": "${workspaceFolder}" }
    }
  }
}
```

### ZooCode

MCP settings JSON. Full file: [`examples/zoocode-mcp_settings.json`](examples/zoocode-mcp_settings.json).

```json
{
  "mcpServers": {
    "links-enos": {
      "command": "python3",
      "args": ["-m", "enos.mcp_server"],
      "env": { "PYTHONPATH": "<path-to-links-enos>" },
      "disabled": false,
      "autoApprove": [],
      "timeout": 60
    }
  }
}
```

For all of the above, `python3 install_mcp.py --yes --target all` writes the entry for you.

## Verify install

Run the same self-check before and after wiring a client:

```bash
python3 -m enos --selftest
```

It writes, searches and deletes one probe memory, prints a single JSON line, and exits:

```json
{"ok": true, "version": "1.0.0", "db_path": "/path/to/project/.enos/memory.db", "fts5": true, "python": "3.12.0"}
```

| exit code | meaning |
| --- | --- |
| `0` | round-trip OK (the probe was written, found, then removed) |
| `1` | round-trip failed (the probe was not found) |
| `2` | the database could not be opened (bad path or permissions) |

The flag is the same in every form: `links-enos --selftest`, `./dist/links-enos --selftest`,
`docker run --rm --entrypoint python links-enos:latest -m enos.mcp_server --selftest`.

## Tools

All eight tools are namespaced `memory_*`. The wire-level JSON Schemas live in
[`examples/tools.snapshot.json`](examples/tools.snapshot.json); the short contract card is
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

| tool | what it does | main arguments |
| --- | --- | --- |
| `memory_write` | Persist a durable memory (fact, decision, preference, snippet); idempotent for the same content in the same scope | `content` (required), `title`, `tags`, `scope`, `kind`, `importance`, `id` |
| `memory_search` | Full-text search (FTS5 when available, `LIKE` fallback) | `query` (required), `limit`, `scope`, `tags` |
| `memory_get` | Fetch one memory by id, including full content | `id` (required) |
| `memory_list` | List recent memories, newest / most important first | `limit`, `offset`, `scope` |
| `memory_delete` | Soft delete by default; `hard=true` removes the row permanently | `id` (required), `hard` |
| `memory_context` | Build a compact Markdown context block from relevant memories | `query` (required), `limit`, `scope`, `max_chars` |
| `memory_stats` | Database path, entry counts per scope, top tags, FTS availability | — |
| `memory_export` | Export memories as portable JSON for backup or migration | `scope` |

## Data

Resolution order, first match wins:

1. `--db /path/to/memory.db`
2. `$ENOS_MEMORY_DB`
3. `<cwd>/.enos/memory.db` — the default, created on first launch

The default is deliberately project-scoped: a client that starts the server inside a repository gets
that repository's database, so memories never bleed between projects. `.enos/` and `*.db` are
git-ignored. WAL mode is enabled when the filesystem supports it; the database is a single file, so
backup or move it with ordinary file copies.

### Legacy: `~/.enos`

Older builds used a fixed `~/.enos/memory.db`. Nothing reads that path automatically anymore. To keep
old memories, copy the file to your project's `.enos/memory.db` or pass `--db ~/.enos/memory.db`.

## Not in this repository

Scope is deliberately narrow: this repository ships the memory MCP server only. Anything beyond
storing and recalling memories is out of scope here.

## Development

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -t . -p 'test_*.py'
```

The suite covers storage and project isolation, the stdio JSON-RPC round-trip, and installer config
merging. Packaging metadata lives in `setup.cfg`; the shell and Docker entry points are `setup.sh`,
`scripts/build_binary.sh` and `scripts/build_docker.sh`.

## License

MIT — see [LICENSE](LICENSE).
