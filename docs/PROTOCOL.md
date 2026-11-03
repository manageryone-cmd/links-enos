# links-enos MCP wire contract

Short contract card for agents. The server is a single stdio process; the full catalogue with
descriptions is [`../examples/tools.snapshot.json`](../examples/tools.snapshot.json).

## Transport

- JSON-RPC 2.0 over **stdio**, **one JSON object per line** (newline-delimited).
- `stdout` carries protocol messages only; every diagnostic goes to `stderr`.
- `Content-Length: N` framing from older clients is also accepted.
- A JSON array is treated as a batch and answered message by message.
- Malformed JSON is logged to stderr and skipped; the server keeps running.
- Requests without `id` are notifications and never receive a response.

## Methods

| method | purpose |
| --- | --- |
| `initialize` | protocol negotiation; returns capabilities, `serverInfo`, instructions |
| `notifications/initialized` | client notification; no response |
| `ping` | liveness; returns `{}` |
| `tools/list` | returns `{"tools": [...]}` — 8 `memory_*` tools |
| `tools/call` | invoke a tool: `params.name` + `params.arguments` |
| `resources/list`, `resources/templates/list` | empty lists (declared, not used) |
| `prompts/list` | empty list (declared, not used) |
| `logging/setLevel` | accepted no-op |
| `shutdown` | stops the server loop |

## `initialize`

Request: `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"...","version":"..."}}}`

- Supported versions: `2025-06-18`, `2025-03-26`, `2024-11-05`. A supported requested version is
  echoed; anything else falls back to `2024-11-05`.
- Response result: `protocolVersion`, `capabilities` (`tools`, `resources`, `prompts`, `logging`),
  `serverInfo` (`name: "enos-memory"`, `version: "1.0.0"`), `instructions` (one paragraph).

## `tools/list`

`{"jsonrpc":"2.0","id":2,"method":"tools/list"}` → `{"jsonrpc":"2.0","id":2,"result":{"tools":[...]}}`

Each entry is `{name, description, inputSchema}`. `inputSchema` always has `type: "object"`,
`properties`, `required` and `additionalProperties: false`.

## `tools/call`

Request: `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_search","arguments":{"query":"deploy"}}}`

Success result (protocol level): `result.content` is a list of `{type:"text", text}` blocks,
`result.structuredContent` carries the same payload as an object, and `result.isError` is `false`.
Tool-level failures return `result.isError: true` and a readable text message — no JSON-RPC error.

## Tool schemas

### `memory_write`

```json
{"type":"object","properties":{"content":{"type":"string","description":"The memory body (Markdown ok)."},"title":{"type":"string","description":"Short label; derived from content if omitted."},"tags":{"type":"array","items":{"type":"string"},"description":"Retrieval tags, e.g. ['deploy','nginx']."},"scope":{"type":"string","description":"Namespace, e.g. 'project:demo'. Defaults to the server scope."},"kind":{"type":"string","enum":["note","fact","decision","preference","task","snippet","reference"],"description":"Memory category (default 'note')."},"importance":{"type":"number","minimum":0,"maximum":1,"description":"0..1 ranking hint (default 0.5)."},"id":{"type":"string","description":"Update this existing memory id instead of creating one."}},"required":["content"],"additionalProperties":false}
```

### `memory_search`

```json
{"type":"object","properties":{"query":{"type":"string","description":"Keywords to look for."},"limit":{"type":"integer","minimum":1,"maximum":100,"description":"Max results (default 10)."},"scope":{"type":"string","description":"Restrict to one scope."},"tags":{"type":"array","items":{"type":"string"},"description":"Require all of these tags."}},"required":["query"],"additionalProperties":false}
```

### `memory_get`

```json
{"type":"object","properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":false}
```

### `memory_list`

```json
{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":200,"description":"Default 20."},"offset":{"type":"integer","minimum":0,"description":"Pagination offset."},"scope":{"type":"string"}},"required":[],"additionalProperties":false}
```

### `memory_delete`

```json
{"type":"object","properties":{"id":{"type":"string"},"hard":{"type":"boolean","description":"Permanent delete (default false)."}},"required":["id"],"additionalProperties":false}
```

### `memory_context`

```json
{"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50,"description":"Default 8."},"scope":{"type":"string"},"max_chars":{"type":"integer","minimum":200,"maximum":20000,"description":"Default 4000."}},"required":["query"],"additionalProperties":false}
```

### `memory_stats`

```json
{"type":"object","properties":{},"required":[],"additionalProperties":false}
```

### `memory_export`

```json
{"type":"object","properties":{"scope":{"type":"string","description":"Optional single-scope export."}},"required":[],"additionalProperties":false}
```

## Errors

| JSON-RPC error | code | when |
| --- | --- | --- |
| Invalid Request | `-32600` | `method` missing or not a string |
| Method not found | `-32601` | unknown method name |
| Invalid params | `-32602` | `params` not an object; bad/unknown tool name; invalid arguments |
| Internal error | `-32603` | unexpected server-side exception |

- `-32700` (parse error) is never emitted: unparseable input is dropped with a stderr log line.
- Notifications never get error responses, including invalid ones.
- `tools/call` business failures (`memory not found`, empty `query`, ...) use `isError: true` inside a
  normal result, so a model can read the message and retry.

## `--selftest` exit codes

`python3 -m enos --selftest` (also `links-enos --selftest`, `./dist/links-enos --selftest`) writes,
searches and deletes one probe memory, prints one JSON line, then exits:

| exit | meaning |
| --- | --- |
| `0` | round-trip OK; JSON payload has `"ok": true` |
| `1` | round-trip failed; JSON payload has `"ok": false` |
| `2` | database could not be opened (path or permissions) |

## Data location

`--db` > `$ENOS_MEMORY_DB` > `<cwd>/.enos/memory.db`. The default scope is `global` and can be set
with `--scope` or `$ENOS_DEFAULT_SCOPE`.
