# links-enos MCP 记忆系统 · 接入指南

links-enos 是一个**通用、跨平台的 MCP 记忆服务器**：一个进程、一条 `stdio` 管道、一个 SQLite 文件。
没有网络端口、没有守护进程、没有第三方 Python 依赖 —— 任何 MCP 客户端（Cline / ZooCode / Roo Code /
Claude Code / Cursor / Claude Desktop / VS Code / Windsurf / Gemini CLI / Codex）都能拉起它。

```
客户端 ──stdio(JSON-RPC)──> enos.mcp_server ──> <项目目录>/.enos/memory.db
```

**每个项目一个数据库。** 不给 `--db` 时，服务端用 `os.getcwd()` 决定路径，
并在首次启动时自动创建 `.enos/` 目录。同一台机器上开两个项目，记忆互不可见。

---

## 1. 30 秒上手

| 你想要 | 命令 |
| --- | --- |
| 直接跑（有 Python） | `cd 你的项目 && python3 -m enos.mcp_server` |
| 自动配置本机所有客户端 | `python3 install_mcp.py` |
| 一键脚本（含镜像构建） | `./setup.sh` 或 `./setup.sh --mode docker --docker-build` |
| 零 Python 环境 | `docker run -i --rm -v "$PWD/.enos:/data" links-enos` |

装好之后重启客户端，在工具列表里应能看到 `memory_write` / `memory_search` 等 8 个工具。

---

## 2. 标准入口：`python3 -m enos.mcp_server`

这是**唯一**需要记住的命令，其它形态（wheel 的 `links-enos`、PyInstaller 二进制、容器）都只是它的包装。

```bash
python3 -m enos.mcp_server                     # 用 <cwd>/.enos/memory.db（自动创建）
python3 -m enos.mcp_server --db ./memory.db    # 显式指定数据库（优先级最高）
python3 -m enos.mcp_server --scope project:x   # 指定默认 scope
python3 -m enos.mcp_server --version
python3 -m enos.mcp_server --selftest          # 自检：写→搜→删，打印 JSON 后退出
```

Python 模块入口与 `python3 -m enos` 等价（`enos/__main__.py`）。

### 数据库路径解析顺序（项目级记忆）

1. `--db /path/to/memory.db` —— 显式传入，永远优先；
2. `$ENOS_MEMORY_DB` —— 容器与 CI 用；
3. `os.path.join(os.getcwd(), ".enos", "memory.db")` —— 默认，**按当前工作目录隔离**。

第 3 步是“项目级记忆”的关键：客户端在哪个仓库里拉起服务，记忆就落在哪个仓库的
`.enos/memory.db`；目录由 `os.makedirs(..., exist_ok=True)` 自动创建，不需要手工建。
`.enos/` 已在 `.gitignore` 中，不会误提交用户记忆。

### 环境变量

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `ENOS_MEMORY_DB` | SQLite 数据库路径 | `<cwd>/.enos/memory.db` |
| `ENOS_DEFAULT_SCOPE` | `memory_write` 省略 scope 时的默认值 | `global` |

### 为什么它一定“能被拉起”

* **只用标准库**（`sqlite3` / `json` / `argparse`），没有 `mcp` SDK、没有 `pydantic`，
  所以不会因为依赖解析失败而起不来。
* **stdout 只写 JSON-RPC**：所有日志走 stderr。客户端解析流不会被我方输出污染。
* **协议向后兼容**：支持 `2024-11-05`、`2025-03-26`、`2025-06-18`，按客户端请求回显；
  未请求过的版本回落到 `2024-11-05`。
* **两种帧格式都能收**：默认按行分隔 JSON；同时兼容老的 `Content-Length: N` 头。
* **单条畸形输入不会杀进程**：解析失败只记 stderr，继续服务。
* **FTS5 缺失自动降级**：建不出 FTS5 虚拟表时，搜索自动走 `LIKE` 扫描。

---

## 3. 一键配置：`install_mcp.py`

`install_mcp.py` 只做一件事：把 `enos` 这一条服务器条目**合并**进各客户端自己的配置文件，
不覆盖文件里的其它内容，改动前自动备份。

```bash
python3 install_mcp.py --list                     # 列出探测到的客户端
python3 install_mcp.py                            # 只配置“已存在配置文件”的客户端（默认 auto）
python3 install_mcp.py --target all --yes         # 写入全部标准位置
python3 install_mcp.py --target cursor,claude-code
python3 install_mcp.py --print --target cursor    # 只打印将写入的 JSON
python3 install_mcp.py --dry-run --target all     # 演练，不落盘
python3 install_mcp.py --uninstall --target all   # 移除 enos 条目
```

### 支持的目标

| `--target` | 客户端 | 写入位置 |
| --- | --- | --- |
| `cline` | Cline | `<VS Code>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` |
| `roo` | Roo Code | `<…>/rooveterinaryinc.roo-cline/settings/*mcp_settings.json` |
| `zoocode` | ZooCode 及其分支 | 自动扫描 `*cline*` / `*roo*` / `*zoo*` 扩展目录下的 `*mcp_settings.json` |
| `claude-code` | Claude Code | 先 `claude mcp add enos -s user -- …`；失败则 `~/.claude.json` |
| `cursor` | Cursor | `~/.cursor/mcp.json` |
| `claude-desktop` | Claude Desktop | macOS `~/Library/Application Support/Claude/…`、Windows `%APPDATA%\Claude\…`、Linux `~/.config/Claude/…` |
| `vscode` | VS Code（工作区） | `<project>/.vscode/mcp.json`（使用 `servers` 键 + `type: "stdio"`） |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| `gemini` | Gemini CLI | `~/.gemini/settings.json` |
| `codex` | Codex CLI | `~/.codex/config.toml`（`[mcp_servers.enos]` 表） |
| `custom` | 任意客户端 | `--config /path/to/whatever.json` |

> VS Code 远程 / WSL / SSH 会额外扫描 `~/.vscode-server/data/User/globalStorage` 与
> `~/.vscode-remote/data/User/globalStorage`。
> 未知分支（例如某些 ZooCode 打包）没有固定扩展 ID：脚本会按目录名匹配 `cline|roo|zoo`，
> 优先复用该扩展 settings 目录里已存在的 `*mcp_settings.json`；实在找不到时用 `--config` 直接指定。

### 四种启动形态

```bash
# 1) Python（默认）：用当前解释器，并显式写入 PYTHONPATH，源码目录也能直接用
python3 install_mcp.py

# 2) 单文件二进制：客户端机器不需要 Python
python3 install_mcp.py --mode binary --binary ./dist/links-enos

# 3) 容器：客户端机器不需要 Python，也不需要装包
python3 install_mcp.py --mode docker --image links-enos:latest

# 4) uvx：不手工安装，交给 uv 解析
python3 install_mcp.py --mode uvx
```

写入 Cursor / VS Code（支持变量替换的客户端）的条目形如：

```json
{
  "mcpServers": {
    "enos": {
      "command": "/usr/bin/python3",
      "args": ["-m", "enos.mcp_server", "--db", "${workspaceFolder}/.enos/memory.db"],
      "env": { "PYTHONPATH": "/opt/links-enos" }
    }
  }
}
```

不支持变量的客户端（Claude Desktop / Windsurf / Gemini / Codex / Cline 系等）**不带 `--db`**：

```json
{
  "command": "/usr/bin/python3",
  "args": ["-m", "enos.mcp_server"],
  "env": { "PYTHONPATH": "/opt/links-enos" }
}
```

服务端会自己取 `cwd`，在 `<cwd>/.enos/memory.db` 建库。诚实的边界：这类客户端通常把
`cwd` 设成它自己的工作目录（如家目录），因此“按项目隔离”对它们**不成立** —— 它们得到的是
“按客户端进程”的一个库。真正要项目级隔离，请用 VS Code / Cursor，或显式 `--db`。

容器形态（VS Code / Cursor）：

```json
{
  "command": "docker",
  "args": ["run", "-i", "--rm", "-v", "${workspaceFolder}/.enos:/data",
           "-e", "ENOS_MEMORY_DB=/data/memory.db", "--user", "1000:1000", "links-enos"]
}
```

容器无法继承宿主的 `cwd`，所以绑定的宿主目录要么写成 `${workspaceFolder}/.enos`
（支持变量的客户端），要么在安装时解析成具体项目路径；容器内路径恒为 `/data/memory.db`。

`-i` 是必须的（流就是传输层），脚本**绝不会**加 `-t`。`--user` 只在 POSIX 上追加，
目的是让写出的 `memory.db` 归当前用户所有，而不是 root。

### 数据库位置一览

| `--db` | 客户端 | 写入配置 |
| --- | --- | --- |
| 未传 | VS Code / Cursor | `--db ${workspaceFolder}/.enos/memory.db` |
| 未传 | 其它客户端 | 不写 `--db`，由服务端取 `cwd` |
| 未传 | docker | 绑定挂载项目下的 `.enos/` |
| 传入 | 全部 | 原样写 `--db <绝对路径>`（传了就以你为准） |

### 安全与可逆

* 合并而非覆盖：其它 MCP server、顶层键（主题等）原样保留。
* 写前备份：`<文件名>.bak-YYYYmmdd-HHMMSS`；`--no-backup` 可关闭。
* 原子写：临时文件 + `os.replace`，中途失败不会留下半个 JSON。
* JSON 解析失败时**拒绝写入**并返回非零退出码（退出码 `1`）。
* POSIX 上把配置权限设为 `600`（配置里可能有别的服务的 token）。
* `--uninstall` 只删 `enos` 这一条；文件因此变空时才删除文件本身。

---

## 4. 三种“环境无关”的运行形态

| 形态 | 客户端需要 | 优点 | 构建/使用 |
| --- | --- | --- | --- |
| Python 模块 | Python ≥ 3.8 | 最简单，零安装 | `python3 -m enos.mcp_server` |
| Wheel | Python ≥ 3.8 | 有 `links-enos` 命令 | `pip install .` → `links-enos` |
| 单文件二进制 | 无 | 拷一个文件即可 | `./scripts/build_binary.sh` → `dist/links-enos` |
| 容器 | Docker | 完全隔离，统一 Linux 行为 | `docker build -t links-enos .` |

### 4.1 Wheel

```bash
pip install .           # 或 pip install "links-enos @ git+https://…"
links-enos --selftest
```

元数据放在 `setup.cfg`（而非 PEP 621 `[project]`），因为 setuptools < 61（Debian/Ubuntu
系统 Python、离线 CI 常见）读不懂 `[project]`，会产出 `UNKNOWN-0.0.0` 空包。

### 4.2 单文件二进制（PyInstaller）

```bash
./scripts/build_binary.sh          # 先跑测试，再打包，最后自检
./dist/links-enos --selftest
python3 install_mcp.py --mode binary --binary ./dist/links-enos
```

* 每个平台各打一次（PyInstaller 不能交叉编译）。
* spec 里显式 `hiddenimports = ["enos", "enos.store"]`，因为包是按名字动态导入的。
* 二进制同样是**控制台**程序（`console=True`）——GUI 模式没有 stdin/stdout，MCP 无法通信。

### 4.3 容器

```bash
docker build -t links-enos .
docker run -i --rm -v "$PWD/.enos:/data" links-enos           # 直接跑（当前项目）
python3 install_mcp.py --mode docker                        # 写进客户端配置
./scripts/build_docker.sh                                   # 构建 + 容器内握手自检
```

镜像要点：

* 以 `python:3.12-slim` 为基座，**不执行 pip install**（无网络构建也能成功）。
* `ENTRYPOINT ["python", "-m", "enos.mcp_server"]`：`docker run -i links-enos` 无参数即启动服务器。
* `ENOS_MEMORY_DB=/data/memory.db` + `VOLUME ["/data"]`：数据落在宿主机。
* 不要用 `-t`（TTY 会改写/缓冲流），也不要在 `run` 后追加 `sh`。

---

## 5. 工具清单

| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `memory_write` | 写入/刷新记忆（按 scope + 内容哈希幂等） | `content`（必填）、`title`、`tags`、`scope`、`kind`、`importance`、`id` |
| `memory_search` | 全文检索（FTS5，缺失时 LIKE） | `query`（必填）、`limit`、`scope`、`tags` |
| `memory_get` | 取单条完整内容 | `id` |
| `memory_list` | 按重要度/时间列出 | `limit`、`offset`、`scope` |
| `memory_delete` | 软删除；`hard=true` 物理删除 | `id`、`hard` |
| `memory_context` | 生成可直接塞进 prompt 的 Markdown 上下文块 | `query`、`limit`、`scope`、`max_chars` |
| `memory_stats` | 条数、scope 分布、热门标签、是否启用 FTS5 | — |
| `memory_export` | 导出为可迁移 JSON | `scope` |

调用示例（JSON-RPC 一行一条）：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"curl","version":"0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_write","arguments":{"content":"回滚：先恢复 pre-upgrade dump 再重启。","tags":["ops","rollback"],"scope":"project:demo"}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_search","arguments":{"query":"rollback","scope":"project:demo"}}}
```

### 语义约定

* **幂等写入**：同一 `scope` 下内容完全相同 → 更新 `updated_at`、合并 tags，不产生副本。
  适合 Agent 重试。
* **scope 是命名空间**：建议 `project:<name>` / `agent:<id>` / `global`，删除/导出都支持按 scope。
* **软删除优先**：默认保留墓碑行，`hard=true` 才物理删除。
* **错误分两类**：工具不存在、参数类型错 → JSON-RPC 错误（`-32602`）；
  业务失败（找不到 id 等）→ `result.isError = true`，让模型读到并可自行纠正。

---

## 6. 数据、备份与迁移

```bash
# 备份（WAL 模式下也安全：先 checkpoint 或直接拷 .db + -wal）
sqlite3 ./.enos/memory.db ".backup '/backup/memory.db'"

# 跨机器迁移
python3 -c "from enos.store import MemoryStore; print(MemoryStore().dumps())" > memories.json
```

* 数据库是**单文件**，路径可任意（`--db` / `ENOS_MEMORY_DB`）。
* 默认位置是 `<cwd>/.enos/memory.db`：备份/迁移前先 `cd` 到项目目录。
* 默认开启 WAL（不支持的文件系统上自动跳过，不会启动失败）。
* 容器形态下数据库就是宿主机上被挂载的那个目录（通常是项目的 `.enos/`），宿主与容器可互换使用。

### 旧版迁移（可选）

早期版本把默认数据库固定在 `~/.enos/memory.db`。现在默认落在 `<cwd>/.enos/memory.db`：
把旧文件复制到项目的 `.enos/memory.db` 即可继续使用，或启动时用 `--db` 直接指向旧文件。

---

## 7. 排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 客户端显示 “server disconnected” | 启动命令里有东西往 stdout 打印了非 JSON。用 `--selftest` 单独验证命令，再看客户端日志里的 stderr。 |
| `No module named enos` | 客户端没继承你的 `PYTHONPATH`。重跑 `install_mcp.py`（它会写入绝对 `PYTHONPATH`），或改用 `--mode binary` / `--mode docker`。 |
| 客户端找不到 `python3` | GUI 客户端的 PATH 往往很干净。`install_mcp.py` 默认写入**解释器绝对路径**；若仍失败，用二进制或容器形态。 |
| 容器内 `disk I/O error` | 宿主目录权限与容器用户不一致。用 `install_mcp.py` 生成的条目（带 `--user uid:gid`），或 `-v` 挂载到可写目录。 |
| `fts5: syntax error` | 已由服务端转义（每个词加引号），正常不会出现；若出现请附查询串提 issue。 |
| 记忆“串项目”了 | 早期版本默认落在用户主目录。重跑 `install_mcp.py` 让配置改用 `${workspaceFolder}/.enos/memory.db`（或去掉 `--db`），再按“旧版迁移”迁移旧库。 |
| 配置被改坏 | 找同目录下的 `<文件名>.bak-<时间戳>` 还原。 |
| 想彻底卸载 | `python3 install_mcp.py --uninstall --target all`，再删除各项目下的 `.enos/`。 |

---

## 8. 文件清单

| 路径 | 说明 |
| --- | --- |
| `enos/mcp_server.py` | stdio JSON-RPC 服务器（协议层 + 8 个工具） |
| `enos/store.py` | SQLite 存储引擎（幂等写、FTS5、软删除、导出） |
| `enos/__main__.py` | `python3 -m enos` 简写 |
| `install_mcp.py` | 多客户端一键配置（合并 / 备份 / 卸载 / 演练） |
| `setup.sh` | POSIX sh 引导脚本（可选 `--docker-build`） |
| `Dockerfile` / `.dockerignore` | 容器形态 |
| `enos.spec` / `scripts/build_binary.sh` | PyInstaller 单文件二进制 |
| `scripts/build_docker.sh` | 构建镜像并做容器内握手自检 |
| `tests/` | 73 个用例：存储、项目级路径隔离、stdio 协议、配置合并 |

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

---

## 9. 协议实现范围

已实现：`initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`、
`resources/list`（空）、`resources/templates/list`（空）、`prompts/list`（空）、
`logging/setLevel`、`shutdown`。

未实现（客户端会优雅降级）：`resources/read`、`prompts/get`、采样（sampling）、
根目录（roots）、通知式 `tools/listChanged`。
