# Minimal stdio handshake

The transport is newline-delimited JSON-RPC 2.0: one JSON object per line on stdin, one per line on
stdout. Nothing else is written to stdout; diagnostics go to stderr (add `2>/dev/null` to hide them).

Copy-paste smoke test from a checkout (no client required):

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"handshake-demo","version":"0.1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 -m enos.mcp_server --db /tmp/handshake.db 2>/dev/null
```

## 1. `initialize` (request)

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"handshake-demo","version":"0.1"}}}
```

## 2. `initialize` (response, actual output)

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{"listChanged":false},"resources":{"listChanged":false,"subscribe":false},"prompts":{"listChanged":false},"logging":{}},"serverInfo":{"name":"enos-memory","version":"1.0.0"},"instructions":"links-enos durable memory. Use memory_write to store facts/decisions worth keeping across sessions, memory_search/memory_context to recall them, and memory_stats to inspect storage. Scopes are namespaces (e.g. 'project:demo'); the default scope is 'global'."}}
```

`protocolVersion` echoes one of `2024-11-05`, `2025-03-26`, `2025-06-18` when requested, and falls
back to `2024-11-05` otherwise.

## 3. `notifications/initialized` (notification)

```json
{"jsonrpc":"2.0","method":"notifications/initialized"}
```

No `id`, so no response is sent.

## 4. `tools/list` (request)

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

## 5. `tools/list` (response, trimmed)

```json
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"memory_write","description":"Persist a durable memory (fact, decision, preference, snippet). Idempotent: writing the same content in the same scope refreshes the existing entry instead of duplicating it. Returns the stored record id.","inputSchema":{"type":"object","properties":{"content":{"type":"string","description":"The memory body (Markdown ok)."},"title":{"type":"string","description":"Short label; derived from content if omitted."},"tags":{"type":"array","items":{"type":"string"},"description":"Retrieval tags, e.g. ['deploy','nginx']."},"scope":{"type":"string","description":"Namespace, e.g. 'project:demo'. Defaults to the server scope."},"kind":{"type":"string","enum":["note","fact","decision","preference","task","snippet","reference"],"description":"Memory category (default 'note')."},"importance":{"type":"number","minimum":0,"maximum":1,"description":"0..1 ranking hint (default 0.5)."},"id":{"type":"string","description":"Update this existing memory id instead of creating one."}},"required":["content"],"additionalProperties":false}},... 7 more tools ...]}}
```

The catalogue is exactly 8 entries: `memory_write`, `memory_search`, `memory_get`, `memory_list`,
`memory_delete`, `memory_context`, `memory_stats`, `memory_export`. The untrimmed catalogue (same
schemas, pretty-printed) is [`tools.snapshot.json`](tools.snapshot.json).

## 6. First tool call (request)

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_write","arguments":{"content":"Use the staging cluster for load tests.","tags":["deploy"],"scope":"project:demo"}}}
```

Successful tool results carry `"isError": false`; failed ones carry `"isError": true` plus a text
message, so the model can read and correct the call. See [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)
for the method set, schemas and error codes.
