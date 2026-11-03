# AGENTS.md

Shortest path for coding agents. Details: `README.md` · wire contract: `docs/PROTOCOL.md`.

1. Install (Python >= 3.8, standard library only): `python3 -m pip install -e .`
   (or a wheel, `docker build -t links-enos .`, or `./scripts/build_binary.sh`).
2. Configure, never interactively: `python3 install_mcp.py --yes --target all`.
   Inspect first with `--list`, `--print` or `--dry-run`; `--target` accepts
   `cline`, `roo`, `zoocode`, `claude-code`, `cursor`, `claude-desktop`, `vscode`, `windsurf`,
   `gemini`, `codex`, `all`, `auto`.
3. Self-check before blaming the client: `python3 -m enos --selftest`
   → exit `0` OK, `1` round-trip failed, `2` database unavailable.
4. Smoke-test the protocol: `printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
   | python3 -m enos.mcp_server 2>/dev/null`
5. Call a tool: `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_write",
   "arguments":{"content":"...","scope":"project:demo"}}}`
6. Trust `tools/list` over prose; `examples/tools.snapshot.json` is generated from the same `TOOL_DEFINITIONS`.
7. Memories live in `<cwd>/.enos/memory.db`; override with `--db` or `$ENOS_MEMORY_DB`.
8. Server code must never print to stdout — logs belong on stderr.

Do not:
- `git push`, add remotes, publish, or tag releases from this workspace.
- commit `*.db`, `.enos/`, `__pycache__/`, build output, or any credential.
- hand-edit `examples/tools.snapshot.json`; regenerate it from `enos.mcp_server.TOOL_DEFINITIONS`.
