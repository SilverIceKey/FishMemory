# 16. DSH 原生归档插件与本地记忆服务（2026-10-30）

> 状态：**已实现并在本机 DSH 0.2.1-rc.5 + desktop profile 完成端到端验证**（2026-10-30）。本页同时是**集成契约**：`fishmemory` Python 侧与 `integrations/dsh-fishmemory` 插件侧必须按本页字段协作。实现结果、证据与未完成门禁见第 9/10 节。

## 1. 本轮范围（依据用户 2026-10-30 指令与 ADR-015）

用户要求把现有本地检索实验“完善到 DSH 插件中”。本轮交付：

1. **读路径**：DSH 官方 `@deepseek-ai/dsh-mcp-client` 以 stdio 连接 `python -m fishmemory.mcp_server`，向主模型暴露单一 `memory_recall` 工具。依据：`docs/07`、`docs/12` ADR-013/ADR-015。
2. **写路径**：新增 DSH 原生 Host 插件，订阅已提交的 `session/event`，把**用户消息与主模型消息**（可选工具结果）归档到本地 FishMemory 服务；不依赖主模型主动写入。
3. **本地服务**：新增独立 stdio 之外的本地 HTTP 服务（127.0.0.1），提供归档写入与受控读取；MCP 与 HTTP 共享同一 SQLite 与作用域配置。
4. **不做**：自动预取/GATE、记忆注入、Thinking、微调、向量检索、对外网暴露。ADR-015 仍然有效。

## 2. 锁定版本与已核实 API（证据）

DSH 版本：本轮实测安装包内 `@deepseek-ai/dsh-*` 为 **`0.2.1-rc.5`**（`@deepseek-ai/dsh-mcp-client`、`dsh-desktop-host`、`dsh-app-boot`、`dsh-session`、`dsh-hmr` 的 `package.json` 均为此版本）。**更正**：`docs/14` 记录的“0.2.1-rc.3”为上一工作包时的安装版本，现已过期；`reports/dsh-source/package.json`（rc.3）也来自上一轮 npm 包快照，不是当前安装包的权威来源。证据：本机 `resources/app.asar`（安装于 2026-10-30，`app.asar` 创建时间 19:45:22），解出副本在 `reports/dsh-source/extracted/`。

| 事实 | 证据（0.2.1-rc.5） |
|---|---|
| Host 插件形式：`apply(ctx, config)`，可导出 `inject` / `Config`；在 `apply` 内用 `ctx.effect` / `ctx.on` 注册并返回清理 | `@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/references/host-plugin.md` |
| Bundle 包：`package.json` 声明 `dsh.bundle.patch` 指向 YAML patch；安装由 `plugin_manager` 完成 | 同上；`references/mcp-bundle.md` |
| 事件：`ctx.on('session/event', (session, event) => …)`，事件在**提交后**通知，含 `session`、`event.seq`、`event.type`、`event.data`、`event.time` | `@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js:421`；`@deepseek-ai/dsh-session/lib/index.js:1579-1598` |
| 事件类型：`user/message` 的 `data` 即消息；`assistant/message` / `tool/result` 的消息在 `data.message` | `@deepseek-ai/dsh-session/lib/index.js:217-230` |
| 消息形状：`{id, role, content:[{type:'text',text}…], source:{kind}}`；user 的 `source.kind='user'`，assistant 为 `'model'`，tool 为 `'tool'` | `@deepseek-ai/dsh-session/lib/index.js:1194-1219` |
| `session/event` 是 fire-and-forget 通知，不是可靠投递；DSH 自身持久化另由 persistence 插件负责 | `docs/07` 已有结论；`@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js:409-442` |
| HMR 监听 profile 清单与 patch；`dsh.profile.bundles` 顺序变化触发重组；linked 包变更会在下次重组替换 | `@deepseek-ai/dsh-hmr/README.md:59,61,71` |
| MCP 工具名 `mcp__<serverName>__<rawName>`；stdio transport 可用 | `@deepseek-ai/dsh-mcp-client/README.md` |

未核实/不可假定：DSH 没有稳定的“发布版→commit”映射；本轮以安装包 0.2.1-rc.5 实际文件为准，不承诺跨版本兼容。**另需注意**：`reports/dsh-source/` 下还留有上一轮的 rc.3 副本（`package.json`、`lib-index.js`、`README.md`），阅读时不要与 `extracted/` 下的 rc.5 文件混用。

## 3. 架构与进程

```text
DSH 主模型
  ├─ Tool Call: mcp__fishmemory__memory_recall
  │     └─ dsh-mcp-client(stdio) → python -m fishmemory.mcp_server（只读、同一 SQLite/作用域）
  └─ 会话事件（user/assistant/tool）
        └─ Host 插件 fishmemory-archive（Native, in-process）
              └─ HTTP POST /v1/events → python -m fishmemory serve（127.0.0.1, bearer token）
                                            └─ SQLite（WAL）
```

- 会话重置/切换主模型不删除 SQLite；`session_id` 只作为事件来源之一，长期身份由配置的 `user_id` / `agent_id` 决定。
- 插件不注册任何模型可见工具，避免与 MCP 重复；不注入上下文。
- 服务与 DSH 解耦：服务可单独运行、供其他宿主使用。

## 4. 配置文件契约

新增 `fishmemory.local.json`（本机真实路径，加入 `.gitignore`）；`config.example.json` 为同结构模板，不含密钥。

```json
{
  "db": ".fishmemory/memory.sqlite",
  "user_id": "local",
  "agent_id": "dsh",
  "model": {
    "base_url": "http://127.0.0.1:11435/v1",
    "model": "qwen3.5-0.8b",
    "api_key_file": "E:/AI/llama.cpp-cuda12/service/api-keys.txt"
  },
  "service": {
    "host": "127.0.0.1",
    "port": 8765,
    "token_file": ".fishmemory/service.token",
    "call_log": ".fishmemory/mcp-calls.jsonl",
    "ingest_log": ".fishmemory/ingest.jsonl",
    "max_body_bytes": 262144,
    "max_events": 64,
    "recall_deadline_s": 20
  }
}
```

规则：

1. 相对路径相对**配置文件所在目录**解析。缺省值如上；`db`、`user_id`、`agent_id`、`model.*` 必填或有缺省。
2. 兼容旧扁平模型配置：顶层存在 `base_url`/`model`/`api_key_file` 时视为 `model`，供既有测试与 `--config config.example.json` 使用。
3. scope（`user_id`,`agent_id`）**只能来自配置或 CLI**；HTTP 请求体、MCP 工具参数都不得携带 scope。
4. `service.token_file` 不存在时由 `serve` 生成随机 32 字节 token 并以当前用户可读权限写入；插件只读取该文件。
5. 密钥不进入日志、错误体或 MCP 输出。

## 5. 本地 HTTP 服务契约

`python -m fishmemory serve --config <path>`（默认 `fishmemory.local.json`，其次 `config.example.json`）。绑定 `127.0.0.1`，仅本机；除 `/healthz` 外全部要求 `Authorization: Bearer <token>`。

- `GET /healthz` → `200 {"status":"ok","version":1,"db":"<path>"}`；不返回 token。
- `POST /v1/events` body `{"events":[{"id":str,"session":str,"topic":str,"text":str,"time":ISO8601,"state":"current|historical|disputed"}]}`
  - 上限：`max_events` 条/请求、`max_body_bytes`；超限 `413`/`400`。
  - 返回 `200 {"results":[{"id":…,"status":"stored|duplicate|rejected|error"}…]}`；单条错误不影响其他条。
  - 幂等键：`(user_id, agent_id, id)`；同 id 不同 text → `error: conflicting_event_id`；墓碑 → `rejected`。
  - 服务端强制写入配置 scope，不接受请求 scope。
- `POST /v1/recall` body `{"query":str,"max_rounds"?:1..3}` → `200` 与 MCP 工具同构的 JSON：`{"status":"found|not_found|partial|error","items":[…],"uncertainty":str}`；失败回退不得伪造记忆。
- `POST /v1/forget` body `{"id":str}` → `200 {"status":"forgotten|absent"}`；仅本机管理用途，不暴露给模型。
- 未知路径 `404`，错误统一 JSON `{"status":"error","error":"<code>"}`；服务异常不得返回 200。
- 并发：每请求独立 SQLite 连接，WAL；锁竞争返回 `503 database_busy`。

## 6. DSH 原生插件契约（`integrations/dsh-fishmemory`）

包名 `@fishmemory/dsh-fishmemory`，零 npm 运行时依赖（仅 Node 内置模块），`package.json` 声明 `dsh.bundle.patch`。

插件行（bundle patch 默认值）：

```yaml
- insert:
    - id: fishmemory-archive
      name: '@fishmemory/dsh-fishmemory'
      config:
        serviceUrl: http://127.0.0.1:8765
        tokenFile: 'D:/Project/Ai/FishMemory/FishMemory-Project-Spec/.fishmemory/service.token'
        userId: local
        agentId: dsh
        topic: 会话
        archiveUser: true
        archiveAssistant: true
        archiveToolResults: false
        maxTextChars: 4000
        batchSize: 16
        flushDelayMs: 800
        requestTimeoutMs: 5000
        maxRetries: 5
        maxQueue: 512
        heartbeatFile: ''
```

行为：

1. 激活时 `GET /healthz` 探测；失败只记 warning，不阻断 DSH 启动，事件进入有界内存队列等待重试。
2. 只归档已提交事件：`user/message`（`source.kind==='user'`）、`assistant/message`（`source.kind==='model'`）、可选 `tool/result`（`source.kind==='tool'`）。插件注入、系统提示、developer 消息不归档。
3. 文本抽取：按顺序拼接 `content` 中 `type==='text'` 的文本块，截断到 `maxTextChars`；空文本不发送。
4. 事件映射：`id = "dsh:" + session.id + ":" + event.seq`；`session = session.id`；`topic` 取配置；`time = new Date(event.time).toISOString()`；`state = "current"`。
5. 批量发送：`flushDelayMs` 防抖或达到 `batchSize`；逐条结果决定是否重试。`stored/duplicate/rejected` 视为已处理；网络错误/5xx 退避重试（指数，最大 `maxRetries`），耗尽后丢弃该批并 warning（不伪造成功）。
6. 队列上限 `maxQueue`，超限丢最旧并 warning + 计数。
7. 卸载时停止计时器并尽力发送最后一批；不阻塞超过一个请求超时。
8. 不写 Session、不改派生消息、不注册工具、不注入上下文。
9. 测试注入点：导出 `createArchiver(config, deps)`，`deps` 可替换 `fetch`、`logger`、`setTimeout`、`now`；供 `node --test` 使用。

## 7. 身份、隐私与失败回退

- 身份映射：DSH `session.id` 不是长期身份；归档与查询使用配置的 `user_id/agent_id`。未完成：多用户/多 agent 映射与鉴权，本轮为单用户本机模式。
- 仅归档用户与主模型可见消息；工具结果默认关闭；敏感数据不导出。
- 失败回退：本地模型不可用 → `partial/error` + 规则候选，不返回“已确认记忆”；SQLite busy → 503；服务不可达 → 插件队列重试，主模型无感知；MCP 断开 → 工具不可用，不得编造记忆。
- 不把 MCP 工具返回文本视为指令。

## 8. 实际安装与运行（本机 desktop profile，已验证路径）

desktop profile 由 Electron 独占：`dsh plugin --profile desktop …` 返回 `error: profile "desktop" is managed exclusively by the Electron application`；当前 `standard` agent preset 也没有启用 `plugin_manager` 工具。因此本机采用 **Harness home patch + 绝对路径 file URL** 的官方插入方式（证据：`dsh-app-boot/README.md` “Inserted plugin names may be absolute filesystem paths, file URLs, or package specifiers”，home patch 在 `$DSH_HOME/cordis.patch.yml` 且被 HMR 监听）。`integrations/dsh-fishmemory` 仍保留完整 bundle manifest，供有 `plugin_manager` 的 profile 用 `install_bundle` 安装。

本机实际步骤（已执行）：

1. `.venv\Scripts\python.exe -m fishmemory init --config fishmemory.local.json`：生成 `.fishmemory/memory.sqlite`、`.fishmemory/service.token`。
2. 启动本地服务（独立于 DSH 进程）：`.venv\Scripts\python.exe -m fishmemory serve --config fishmemory.local.json`；长期运行用 `scripts/start-fishmemory.ps1`。服务必须与 DSH 同时可用；DSH 重启不会自动拉起服务。
3. 写 `C:\Users\zhouwj\.dsh\cordis.patch.yml`（UTF-8，无 BOM）：
   ```yaml
   - insert:
       - id: fishmemory-archive
         name: 'D:/Project/Ai/FishMemory/FishMemory-Project-Spec/integrations/dsh-fishmemory/index.js'
         config:
           serviceUrl: http://127.0.0.1:8765
           tokenFile: 'D:/Project/Ai/FishMemory/FishMemory-Project-Spec/.fishmemory/service.token'
           userId: local
           agentId: dsh
           topic: 会话
           archiveUser: true
           archiveAssistant: true
   ```
   完整字段以 `integrations/dsh-fishmemory/cordis.patch.yml` 为准（15 个键：archiveToolResults、maxTextChars、batchSize、flushDelayMs、requestTimeoutMs、maxRetries、maxQueue 等）。移除 home patch 中该行（或删除文件）即停用归档，不改 profile。
4. profile patch 的 MCP 行改为 `args: [ '-m', 'fishmemory.mcp_server', '--config', 'D:/Project/Ai/FishMemory/FishMemory-Project-Spec/fishmemory.local.json' ]`（已执行；HMR 触发 MCP client 重连，新进程参数已核实）。
5. 验收：见第 9 节实测。

**安装期异常说明**：安装 home patch 前后，DSH 桌面渲染进程在 2026-10-30T12:08:10Z 崩溃并被 Electron 自动重启（`%APPDATA%\@deepseek-ai\dsh-desktop\logs\crash-2026-10-30T12-08-10-864Z-renderer.log`），GUI 端口由 61334 变为 63885。时间核对：崩溃早于 home patch 写入（文件 mtime 20:08:16 本地），且崩溃日志中的 renderer console 只有无关的 `im-qqbot` 报错；无法证明与本插件相关，也不能排除 HMR 重组影响。重启后插件按启动配置加载、归档与 MCP 均正常。**因此不建议在长会话中反复 HMR 增删该插件；优先在启动前配置好。**

## 9. 实测结果（2026-10-30）

### 9.1 自动测试

| 命令 | 结果 |
|---|---|
| `.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 56 tests in 17.903s` `OK`（连续 3 次全绿；config 17 + service 18 + mcp 6 + core 10 + retrieval_v2 5） |
| `node --check integrations/dsh-fishmemory/index.js` | exit 0 |
| `node --test "integrations/dsh-fishmemory/test/*.test.mjs"` | 22/22 pass，exit 0 |
| `python tests/mcp_smoke.py`（本机模型可用时） | `protocol=2025-11-25 tool=memory_recall source=smoke-1 PASS` |
| 独立验证（另一 agent，临时配置/DB/随机端口，未用 8765） | HTTP 黑盒 **42/42 PASS**；CLI scope 隔离 PASS；报告 `reports/dsh-integration-verification.md` |

### 9.2 本地服务黑盒（真实进程，127.0.0.1:8765）

- `GET /healthz` → 200 `{"status":"ok","version":1,...}`（不返回 token）。
- 无 token `POST /v1/events` → 401；带 Bearer → `{"results":[{"id":"…","status":"stored"}]}`。
- 重复 id → `duplicate`；同 id 异 text → `error/conflicting_event_id`；`/v1/forget` → `forgotten`，重放同 id → `rejected`。
- `/v1/recall` 真实调用本机 Qwen：`SQLite` → `found` 返回 `demo-1`；完整问句在 HTTP 服务进程里曾返回 `not_found`（同一问句 CLI 返回 `found`），属已记录的 0.8B 规划/筛选不稳定（docs/15），不是桥接故障。
- 请求体携带 `user_id/agent_id` 不改变 scope（响应仍为配置 scope 的数据）。

### 9.3 DSH 内端到端（desktop profile，0.2.1-rc.5）

1. **MCP 读路径**：`mcp__fishmemory__memory_recall("SQLite")` 返回 `demo-1`；调用日志 `source=mcp, user_id=local, agent_id=dsh` 证明 MCP 进程已使用新配置（不是旧 demo 后端）。
2. **原生归档写入**：安装后创建一个一次性 subagent（session `e898c32d…`）产生真实会话事件；`.fishmemory/ingest.jsonl` 出现：
   - `dsh:e898c32d-364d-4421-83e4-b72aa3ef8f3f:9`（user/message）→ `stored`
   - `dsh:e898c32d-364d-4421-83e4-b72aa3ef8f3f:18`（assistant/message）→ `stored`
3. **闭环**：`mcp__fishmemory__memory_recall("archive probe alive")` 返回上述 `dsh:…:18` 原文证据（`status=found`）。即：DSH 会话事件 → 原生插件 → 本地 HTTP 服务 → SQLite → MCP → 主模型。
4. **跨进程**：服务进程重启后（同一 SQLite）MCP 仍能读取；会话重置/主模型切换不会删除长期记忆（数据在 SQLite）。

### 9.4 已知缺口（本轮实测发现）

- **工具调用型 assistant 步骤不归档**：本会话多数 assistant/message 只有 `reasoning` + `tool-call` 块，没有 `text` 块；按契约（只拼接 text）不发送。这是设计选择，但意味着“工具调用过程”不会进入记忆。后续可评估只归档 tool-call 名称/摘要，须先过隐私与评测门禁。
- **服务不可用期间的事件会丢**：安装期 DSH 重启导致服务短暂下线，期间的 user/message（如 seq 1158）在 5 次重试后丢弃；插件没有持久化队列/补扫游标（`session/event` 是 post-commit 通知）。这是下一工作包的核心可靠性缺口。
- 归档 topic 固定为配置值（默认“会话”），因此主题硬过滤会降低召回；docs/15 的检索质量限制仍然生效。
- 多用户/多 agent 鉴权、事件级授权、敏感数据策略、线上成本 A/B 均未实现。

## 10. 未完成门禁与下一工作包

1. 可靠归档：持久化待发送队列 + 会话来源游标/补扫（`session/flush`、启动时按 session 日志补扫），保证服务离线后可补发。
2. 安全：多用户/多 agent 身份映射与鉴权、事件采集白名单、敏感字段过滤、`/v1/forget` 的授权与审计。
3. 检索：宽召回/弱主题过滤/逐条判定的新持出集复测；决定是否升级默认检索策略（当前 ADR-016 不变）。
4. 成本：真实主模型 A/B（线上输入/输出 Token、请求数、提示缓存命中、端到端延迟），不能只报本地指标。
5. 兼容：建立 DSH 安装版本→官方 commit 的锁定记录；每次升级重跑本页第 9 节验收。
6. 安装：在有 `plugin_manager` 的 profile 用 `install_bundle` 安装 bundle 版本并核验 HMR/卸载/显示；本机 desktop 目前只能走 home patch。
7. 契约硬化（2026-10-31 已部分完成，ADR-017）：已限制归档 URL 为 loopback origin 并禁止重定向、按码点截断文本、补 MCP database_busy/恢复测试、将 absent ID 遗忘墓碑语义写入 docs/06、设置 5 秒 socket 空闲读取超时。仍缺 scope 漂移告警、Windows token ACL、请求总时限及并发上限。
8. 独立验证报告：`reports/dsh-integration-verification.md`（另一 agent，未触碰 profile/运行中 DSH；其唯一未独立复验项是真实 desktop profile 安装与归档，以本页第 9.3 节为准）。

## 11. 2026-10-31 复核

本次 Python 58 项中 57 通过、1 跳过；插件 24/24；真实本地模型 MCP smoke 通过。改动、命令、未完成项见 [复核报告](../reports/review-20261031.md)。本次未重启 DSH 或常驻 HTTP 服务，新源码不等于已加载进运行中进程；也未重新执行 DSH 界面测试。

### 后续同日 DSH 界面实测

用户打开 DSH 后，已使用 DeepSeek-V4.1-Flash 完成自动归档、新会话读取及无记忆测试。跨会话正确读出虚构暗号和饮品；但是首次完整问句漏召回、项目名查询混入无关记录，无记忆项目两次误报 found，最终由主模型判别为未知。因此仅链路通过，本地检索质量仍未通过。详细证据、成本与限制见 [DSH 实测报告](../reports/dsh-live-test-20261031.md)。开始时独立 HTTP 服务未启动，已启动并保持运行。

