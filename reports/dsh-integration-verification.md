# DSH 集成独立验证报告（task-3，2026-10-30）

> 验证者：`verifier`（独立于 task-1 `python-core`、task-2 `dsh-plugin`）
> 被验证交付：`fishmemory/`（config/service/observe/mcp_server/__main__）、`tests/`、`integrations/dsh-fishmemory/`
> 集成契约：`docs/16-DSH原生插件集成.md` §4-§7；验收规约：`AGENTS.md` §5-§6
> 约束：未修改 `fishmemory/`、`tests/`、`integrations/`、`docs/`、`README`、`config.example.json`、`demo.sqlite`、`$DSH_HOME` profile；未触碰运行中的 DSH 与 8765 服务；仅新增本报告。所有测试服务使用临时配置/临时 DB/临时日志与随机端口，结束后已终止。

## 0. 结论

- **阻塞性缺陷：无。** Python 服务、MCP stdio 桥、DSH 归档插件三者在独立黑盒与契约审查下均符合 `docs/16` §4-§7 的功能契约。
- **通过项**：Python 56 项测试全绿（1 项环境相关 skip，退出码 0）；Node 插件 22/22 通过；`node --check` 通过；HTTP 服务独立黑盒 **42/42** 检查通过；CLI scope 隔离独立验证通过；MCP stdio 端到端 smoke 用**真实本地模型**通过（`protocol=2025-11-25 tool=memory_recall source=smoke-1 PASS`）。
- **未发现契约功能性不符**（§6 第 1-9 条逐条核对见 §2.5）。
- **发现 1 项中等级文档/证据缺陷**：文档声明锁定 DSH `0.2.1-rc.3`，实际安装版本为 **`0.2.1-rc.5`**（本报告从 `app.asar` 独立读取确认）。逐条核对后 rc.5 中契约依赖的 API/行号与 README 证据**仍然成立**，故不影响运行时正确性，但违反 `AGENTS.md` §3“锁定实际发布版本并记录”的可追溯性要求，需回写。
- **未独立复验**：真实 DSH desktop profile 内插件激活与 home patch 安装（Lead 已报告完成；本任务禁止触碰 profile/运行中 DSH），见 §6。

## 1. 验证环境

| 项 | 值 |
|---|---|
| 工作目录 | `D:\Project\Ai\FishMemory\FishMemory-Project-Spec` |
| Python | `.venv\Scripts\python.exe`（3.11.9）+ 仓库内 `mcp` 包 |
| Node | `v24.19.0` |
| 本机本地模型 | llama.cpp `127.0.0.1:11435` **LISTENING**；key file `E:/AI/llama.cpp-cuda12/service/api-keys.txt` 存在（45 bytes）；因此 MCP smoke **未 skip，真实执行** |
| 运行中 DSH | Web URL 变化（61334 不再监听，当前 env 为 63885），说明 DSH 曾重启；本会话只读探测端口，未接触其进程/配置。8765 上 Lead 的服务保持 LISTENING，未被停止 |
| 临时服务端口 | 随机 ephemeral（本轮实际 52111、57759 等），从未使用 8765；临时目录 `%TEMP%\fishmem-bb-*`、`fishmem-cli-*` |
| 真实数据 | 未读写真实 `.fishmemory/`、`demo.sqlite`（MCP smoke 重定向到临时 DB；本地模型 key 仅被读取） |

## 2. 已核实事实（AGENTS §6「已实现 / 依据」）

### 2.1 自动化测试（原始结果）

```text
$ .venv\Scripts\python.exe -m unittest discover -s tests -v
Ran 56 tests in 17.891s
OK (skipped=1)            # skip: test_init_default_target_relative_to_repo_root
                          #       'fishmemory.local.json already exists'（环境相关，非失败）
EXIT=0
```

> 说明：首次前台运行时 PowerShell 把 Python 的 stderr（pydantic warning + unittest 输出）标记为
> `NativeCommandError` 并显示 `[exit code: 1]`；改用 stdout/stderr 重定向捕获后确认进程真实 `EXIT=0`。
> 这是 PowerShell 包装行为，不是测试失败。

```text
$ node --version
v24.19.0
$ node --check integrations/dsh-fishmemory/index.js
（无输出，退出码 0）
$ node --test "integrations/dsh-fishmemory/test/*.test.mjs"
ℹ tests 22
ℹ pass 22
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ duration_ms 77.4666
$ node --test integrations/dsh-fishmemory/test/      # docs/16 §9 的目录形式（经 test/package.json shim）
ℹ tests 22  pass 22  fail 0  duration_ms 93.0873
```

测试分组（`tests/`）：config 17（1 skip）、core 10、mcp_server 6、retrieval_v2 5、service 18，合计 56。
插件测试覆盖：配置校验、事件映射/文本抽取、注入排除、tool 开关、批量/防抖、逐条结果、
401/5xx 指数退避、重试耗尽、队列上限、卸载、健康探测、apply 装配（`integrations/dsh-fishmemory/test/archive.test.mjs`）。

### 2.2 HTTP 服务独立黑盒（42/42 通过）

独立脚本（`%TEMP%\fishmem-blackbox.py`）启动临时配置服务（临时 DB/token/call_log/ingest_log、
随机端口），`db` 指向 `%TEMP%`，`model.base_url` 故意指向不可达的 `127.0.0.1:1` 以触发模型故障路径，
`max_body_bytes=2048`、`max_events=3`、`recall_deadline_s=2`。原始关键行：

```text
PASS token generated as 64 hex chars | len=64
PASS GET /healthz unauthenticated 200 ok/version=1/db path | 200 {'status': 'ok', 'version': 1, 'db': '...\\memory.sqlite'}
PASS healthz does not echo token
PASS POST /v1/events without token -> 401 unauthorized | 401 {'status': 'error', 'error': 'unauthorized'}
PASS POST /v1/events wrong token -> 401 | 401 {'status': 'error', 'error': 'unauthorized'}
PASS ingest 2 events -> 200 stored,stored
PASS duplicate same id/text -> duplicate
PASS same id different text -> error conflicting_event_id
PASS per-item validation: stored + invalid_event + invalid_state, batch 200
PASS max_events=3 exceeded -> 413 too_many_events
PASS body over max_body_bytes=2048 -> 413 payload_too_large
PASS invalid JSON -> 400 invalid_json
PASS unknown POST path -> 404 not_found
PASS GET on POST path -> 405 method_not_allowed
PASS empty recall query -> 400 invalid_query
PASS forget with non-string id -> 400
PASS recall model-unavailable -> HTTP 200, status partial/error | 200 status=partial
PASS recall does not fabricate items on model failure | items=[]
PASS recall model failure has uncertainty text
PASS recall fallback_candidates are separate and carry stored id | fallback ids=['e1', 'e3']
PASS recall error code present | URLError
PASS recall no match + model down -> 200, items empty, status error | 200 status=error
PASS recall max_rounds out of range -> 400
PASS request-body user_id/agent_id ignored (event stored) | 200 {'results': [{'id': 'e5', 'status': 'stored'}]}
PASS recall body scope ignored: no scope error, response still scoped | 200 status=partial
PASS SQLite rows only under configured scope (scope-u, scope-a) | rows=[('scope-u', 'scope-a')]
PASS attacker scope has no rows
PASS forget existing -> forgotten | 200 {'status': 'forgotten'}
PASS replay forgotten id -> rejected | 200 {'results': [{'id': 'e1', 'status': 'rejected'}]}
PASS forget again -> absent | 200 {'status': 'absent'}
PASS locked SQLite -> 503 database_busy | 503 {'status': 'error', 'error': 'database_busy'}
PASS service recovers after lock release
PASS ingest.jsonl has no token
PASS ingest.jsonl has no event text
PASS call.jsonl has no token
PASS call.jsonl has no query text
PASS call.jsonl records query_chars + source=http (no text)
PASS service stdout/stderr has no token
PASS service stdout shows listening URL/db/token_file only
PASS recall without database -> 503 database_unavailable | 503 {'status': 'error', 'error': 'database_unavailable'}
PASS recall failure does not create the DB file
PASS non-loopback service.host rejected (exit 2, no bind) | rc=2 stderr=fishmemory: service.host must be 127.0.0.1 (local-only service)

RESULT 42/42 checks passed        EXIT=0
```

补充持久化/审计证据（对黑盒运行遗留的临时 DB 只读检查）：

```text
JOURNAL_MODE wal
EVENT_COUNT 4                      # e2/e3/e5/afterbusy；e1 已 forget，e4 invalid
TOMBSTONES [('e1',)]
SCOPES [('scope-u', 'scope-a')]    # 无 attacker scope 行
REOPEN_EVENT_COUNT 4               # 关闭连接后重开仍可读
```

call_log 审计行（无 token、无查询/事件原文，含身份与状态）：

```json
{"source": "http", "action": "forget", "user_id": "scope-u", "agent_id": "scope-a", "id": "e1", "status": "forgotten"}
{"source": "http", "action": "forget", "user_id": "scope-u", "agent_id": "scope-a", "id": "e1", "status": "absent"}
{"source": "http", "user_id": "scope-u", "agent_id": "scope-a", "query_chars": 19, "status": "partial", "ids": [], "error": "URLError", ...}
```

实现依据：`fishmemory/service.py`（鉴权 83-90、请求体/上限 170-187、逐条事件 189-225、
recall 227-254、forget 256-274、统一错误 111-163、锁竞争 152-156、token 不回显 165-168）、
`fishmemory/core.py`（幂等/冲突 48-61、墓碑 52-53/63-70、scope 过滤 87-102、故障回退 179-184）、
`fishmemory/observe.py`（响应整形 24-34、日志不含 token/查询原文 37-86）。

### 2.3 CLI scope 隔离（独立验证）

临时配置 scope=`local/dsh`，CLI 读取配置写入 2 条，再用 `--user attacker --agent attacker` 写 1 条：

```text
CLI_INGEST_EXIT 0
CLI_INGEST_STDOUT [ { "id": "cli-1", "status": "stored" }, { "id": "cli-2", "status": "stored" } ]
CLI_INGEST_ATTACKER_SCOPE_EXIT 0
CLI_INGEST_ATTACKER_SCOPE_STDOUT [ { "id": "atk-1", "status": "stored" } ]
DB_ROWS [('attacker', 'attacker', 'atk-1'), ('local', 'dsh', 'cli-1'), ('local', 'dsh', 'cli-2')]
SCOPE_SEPARATED True
```

结论：scope 只来自配置/CLI（`fishmemory/config.py:189-199`、`fishmemory/__main__.py:108-127`），
CLI 覆盖是受信本机操作；HTTP 请求体携带的身份字段被忽略（黑盒已证）。

### 2.4 MCP stdio 端到端 smoke（真实本地模型，非 skip）

```text
$ .venv\Scripts\python.exe tests\mcp_smoke.py --config config.example.json
protocol=2025-11-25 tool=memory_recall source=smoke-1 PASS
EXIT=0
```

- 本地模型可用（11435 LISTENING、key file 存在），因此**未 skip**；脚本使用 `TemporaryDirectory`
  生成临时 DB/配置，并先写入合成事件 `smoke-1`，再通过独立 stdio 子进程调用 `memory_recall`，
  未复用真实 `.fishmemory/`，未调用 DSH 已连接的 MCP 进程。
- 工具面：只有 `memory_recall`，输入 schema 仅 `query` 且 required，无 scope 参数
  （`tests/test_mcp_server.py:80-85`；`fishmemory/mcp_server.py:48-84`）。
- 模型不可用时 `partial/error + items:[] + fallback_candidates` 独立字段，不伪造
  （`fishmemory/observe.py:24-34`；`tests/test_mcp_server.py:102-113`）；本轮黑盒已在 HTTP 侧
  实测同一行为，MCP 侧由单测覆盖并本轮真实调用验证工具链可用。

### 2.5 DSH 0.2.1-rc.5 API 独立核对（关键：实际版本不是 rc.3）

直接解析安装包 `C:\Users\zhouwj\AppData\Local\Programs\DeepSeek Harness\resources\app.asar`
头部与文件内容（只读），得到实际安装版本：

```text
dsh => 0.2.1-rc.5
dsh-session => 0.2.1-rc.5
dsh-session-persistence-jsonl => 0.2.1-rc.5
dsh-tools => 0.2.1-rc.5
dsh-mcp-client => 0.2.1-rc.5
cordis => 4.0.5
```

在 rc.5 中逐条核对契约依赖的 API（行号为本轮从 asar 读出的实际行号）：

| 契约事实 | rc.5 证据（本轮核对） |
|---|---|
| 已提交事件后派发 `session/event`，参数 `(session, event)` | `@deepseek-ai/dsh-session/lib/index.js:1579-1598`（commit 内 `log.push` 后 `invokeContainedSessionObservers`） |
| 事件信封 `{type, seq, time, data}`，`seq` 从 0 递增、`time=Date.now()` 毫秒 | 同上 `lib/index.js:1528-1537`（本轮命中 `seq: SessionSeq(...)` 1530、`time: Date.now()` 1531） |
| `user/message` 的 `data` 即消息；`assistant/message`/`tool/result` 在 `data.message` | `lib/index.js:217-229`（`deriveEventMessage`） |
| 消息形状与 source.kind 校验（assistant=`model`、tool=`tool`） | `lib/index.js:1194-1219`（`assertMessageEventShape`） |
| 用户可见消息 `source: { kind: 'user' }` | `@deepseek-ai/dsh-session/README.md:45`；`@deepseek-ai/dsh-agent/README.md:52` |
| 插件注入的 user-message 使用其它 source.kind（如 `ptc-mode`） | `@deepseek-ai/dsh-tools/README.md:134` |
| 订阅实际写法 `ctx.on('session/event', (session, event) => …)` | `@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js:421` |
| Host 插件 `apply(ctx, config)`；资源用 `ctx.effect`/`ctx.on` 注册并清理；`Config` 可选 | `@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/references/host-plugin.md:57-64` |
| `ctx.on` 返回 disposer；`ctx.effect` 支持同步/异步/生成器清理；有 `Config` 才调用 `~standard.validate` | `@deepseek-ai/cordis/lib/index.js:335-345`、`371-379`、`956-962`、`1142-1166`、`1169` |
| `mcp__<serverName>__<rawName>`；stdio transport | `@deepseek-ai/dsh-mcp-client/README.md:28,71,75`（安装包内同文） |

结论：插件/文档所依赖的 rc.3 时代 API 在 rc.5 中**未变**（关键行号也基本一致；本文核对用 rc.5 实际行号）；
但 `docs/16` §2 与 `integrations/dsh-fishmemory/README.md:7` 的“锁定 0.2.1-rc.3”属于**过期证据标注**，
与同文件 :145/:166 的 rc.5 描述自相矛盾（见缺陷 D1）。

### 2.6 插件契约逐条核对（docs/16 §6 第 1-9 条）

| # | 契约要求 | 实现/测试证据 | 结论 |
|---|---|---|---|
| 1 | 激活时 `GET /healthz` 探测；失败仅 warning，不阻断启动，事件进入有界队列等待重试 | `integrations/dsh-fishmemory/index.js:468-498`（start 捕获全部失败）、`624-661`（apply 异步探测 + warning）、`test/archive.test.mjs:569-581,583-616` | 符合（探测异步不 await，符合“不阻断”） |
| 2 | 只归档已提交 `user/message`(kind=user)、`assistant/message`(kind=model)、可选 `tool/result`(kind=tool)；注入/系统/developer 不归档 | `index.js:160-206`（ARCHIVE_RULES 白名单 + source.kind 精确匹配 + 类型白名单）、`test:299-348` | 符合 |
| 3 | 按序拼接 `content` 中 `type==='text'` 的文本块，截断 `maxTextChars`，空文本不发 | `index.js:148-157`、`188-189`；`test:246-258,292-297` | 符合（截断粒度问题见 D5） |
| 4 | `id="dsh:"+session.id+":"+event.seq`、`session=session.id`、`topic=配置`、`time=new Date(event.time).toISOString()`、`state="current"` | `index.js:183-205`；`test:260-290` | 符合（rc.5 信封为毫秒 number，检查 `Number.isFinite(event.time)` 与 `Number.isSafeInteger(event.seq)`） |
| 5 | `flushDelayMs` 防抖/`batchSize` 批量；逐条结果决定重试；`stored/duplicate/rejected` 完成；网络错误/5xx 指数退避最大 `maxRetries`，耗尽丢弃并 warning | `index.js:284-441`；`test:354-480` | 符合；实现额外对 4xx/非 JSON 也退避重试（比契约更保守，不构成不符） |
| 6 | `maxQueue` 上限，超限丢最旧 + warning + 计数 | `index.js:444-465`；`test:482-499` | 符合（重试中的批次不计入 queue 上限，见 D 备注） |
| 7 | 卸载停表、尽力发送最后一批、不阻塞超过一个请求超时 | `index.js:514-587`（`withRequestBound` + 单次 `sendFinal` + 超量丢弃 warning）；`test:501-529` | 符合（只发送一个 batchSize 的“最后一批”，其余丢弃并 warning，符合“尽力”） |
| 8 | 不写 Session、不改派生消息、不注册工具、不注入上下文 | `index.js:624-651` 只使用 `ctx.effect`/`ctx.on`/`ctx.logger`；全文 grep 无 `ctx.tools`/`session.append`/`ctx.sessions`/`inject`/`writeFile`/`spawn`；`test:583-616` | 符合 |
| 9 | 导出 `createArchiver(config, deps)`，可替换 `fetch`/`logger`/`setTimeout`/`now` | `index.js:220-228`（另支持 `clearTimeout`/`readFile`）；`test` 全部注入 fake fetch/timers/logger | 符合（`deps` 为契约超集） |

包结构/元数据核对：`package.json` name/version/private/type/exports（`.`、`./locale/*.json`、`./icon`）、
`files`、`dsh.bundle.patch` 均符合 host-plugin.md 的 bundle 形式；**零 npm 依赖**；Node `>=24`；
`cordis.patch.yml` 字段与 `docs/16` §6 默认值逐项一致（`tokenFile` 为本机绝对路径，已在注释/README 要求按机器修改）；
`locale/en.json`、`locale/zh.json` 均含 `meta.title/description` 且 UTF-8 合法；`icon.svg` 634 bytes、合法 SVG
（host-plugin.md 上限 256 KiB）；`node --check` 通过。

## 3. 缺陷清单（按严重度）

### 阻塞性：无

### 中等

**D1. 版本锁定与证据标注过期（文档/可追溯性，非运行时缺陷）**
- 现象：`docs/16` §2 与 `integrations/dsh-fishmemory/README.md:7` 声称锁定/核对 `0.2.1-rc.3`；
  实际安装包为 `0.2.1-rc.5`（本报告 §2.5 独立读取 app.asar 确认）。`reports/dsh-source/package.json`
  仍是 `@deepseek-ai/dsh-mcp-client 0.2.1-rc.3`；`reports/dsh-source/extracted/` 为混合副本
  （cordis 4.0.5、dsh-tools 与 dsh-experimental-agent-team-profile 为 rc.5，其余多为无 package.json 的局部抽取）。
  同一 README 在 :145/:166 又写 rc.5，自相矛盾。
- 影响：不改变已验证的运行时行为（rc.5 API 与行号仍成立），但违反 `AGENTS.md` §3“锁定实际发布版本、
  记录证据来源”，后续升级/回归无法判断证据对应版本。
- 建议：回写 `docs/16` §2/§9 与插件 README 为 rc.5，重新导出/标注 `reports/dsh-source/` 证据版本；
  或在证据索引中明确“rc.3 证据，rc.5 已复核的条目清单”。

**D2. `docs/16` §8 安装步骤与实测路径不一致，§9 验证结果未回填**
- 现象：§8 第 3 步写用 `plugin_manager install_bundle`；插件 README 与本机实测使用
  `$DSH_HOME/cordis.patch.yml` home patch（README :50-70 自述 desktop profile 下 `install_bundle` 未走通）。
  §9 仍为“验证计划（实现后回填结果）”，§16 页首状态仍“实现与验证进行中”。
- 影响：运维按 §8 操作可能失败；验证结论没有落盘到契约页，可追溯性缺口。
- 建议：§8 增补已实测的 home patch 路径（含 UTF-8 无 BOM、删除行即停用、重启说明），§9 回填本报告结果；
  `install_bundle` 路径标注“未验证”。

### 低

| ID | 缺陷 | 位置 | 说明与建议 |
|---|---|---|---|
| D3 | `userId`/`agentId` 为校验保留字段但不随请求发送；若插件 patch 与服务的 `user_id/agent_id` 不一致，归档 scope 会静默由服务配置决定，无告警 | `integrations/dsh-fishmemory/index.js:50-52,84`；README:80；`docs/16` §7 | 与 §4.3“scope 只能来自配置”一致，但 §7“归档与查询使用配置的 user_id/agent_id”易误解。建议 §6/§7 明确“插件不携带身份，scope 由服务配置强制”，或在健康探测成功日志中提示无法校验 scope 一致性 |
| D4 | `serviceUrl` 只校验 http(s) 绝对 URL，不限制 loopback；误配可把消息正文与 Bearer token 发往远端 | `index.js:121-131` | 默认值为 loopback 且服务只绑定 loopback，风险限于误配/恶意 patch。建议限制 `127.0.0.1`/`localhost`（或至少 warning）并加测试 |
| D5 | `maxTextChars` 用 JS UTF-16 `.slice()` 截断，可能在 emoji 代理对中间切开产生 lone surrogate | `index.js:188` | 直接实验：`Store.ingest` 对 lone surrogate 抛 `UnicodeEncodeError`（`ValueError` 子类）→ 服务逐条返回 `error: invalid_event`（`service.py:210-215`，非 5xx）→ 插件视作未完成、重试 `maxRetries` 后丢弃并 warning。失败安全（不伪造、不 500），但会造成边界字符消息丢失。建议按码点截断（如 `Array.from(text).slice`）并加测试 |
| D6 | token 文件权限：`os.chmod(0o600)` 在 Windows 为 best-effort，实际 ACL 未验证 | `fishmemory/config.py:247-250`；`docs/16` §4.4 | 代码注释已声明 best-effort；契约“当前用户可读权限”在 Windows 上不能由 chmod 保证。建议文档记录该限制，或验证仓库目录 ACL/改为 `%LOCALAPPDATA%` 私有目录 |
| D7 | 服务端无 socket 读超时：`_read_json` 按 `Content-Length` 阻塞读，慢速/故障本机客户端可长期占用工作线程 | `fishmemory/service.py:170-187`；`ThreadingHTTPServer` 未设 timeout | 仅 loopback + token 保护，风险低。建议设置 `self.connection.settimeout(...)` 或记录为已知限制 |
| D8 | `/v1/forget` 对不存在 id 也写墓碑并返回 `absent`，`docs/16` §5 未规定此“防重放”语义 | `fishmemory/service.py:264-270`；`tests/test_service.py:231-236` | 属于安全的防御性设计（防止后续重放复活），建议回写 §5 明确语义 |
| D9 | `core.py` 注释“No public management endpoint in this experiment”已过期（现有本机 `/v1/forget`） | `fishmemory/core.py:63-64` | 仅注释漂移；建议更新为“仅本机管理端点、不暴露给模型” |
| D10 | `tests/mcp_smoke.py` 在 `not_found` 时打印 SKIP 并 exit 0（`:87-93`），不能作为质量门禁通过证据 | `tests/mcp_smoke.py` | 作为手动 smoke 定位合理；报告与 README 不应把它当作“评测通过”。建议文末注明 |
| D11 | `/healthz`（未鉴权）返回数据库绝对路径 | `fishmemory/service.py:165-168` | 契约明确要求该字段（docs/16 §5），本机单用户可接受；不建议对外暴露，保持 loopback 绑定即可 |

## 4. 安全与失败场景证据（AGENTS §5.4/§5.5/§5.6）

| 场景 | 期望 | 独立观测 | 证据 |
|---|---|---|---|
| 无 token / 错误 token | 401，统一 JSON，不回显 token | 均 401 `{"status":"error","error":"unauthorized"}`；healthz 响应不含 token | 黑盒；`service.py:83-110` |
| token 只读、不落日志 | 插件只读 token 文件；服务日志/stdout 不含 token | 插件无写文件 API（grep 无 `writeFile*`）；ingest/call JSONL 与 stdout 均不含 token | `index.js:268-274`；黑盒 5 项 secret 检查 |
| 查询/事件原文不落日志 | JSONL 只记长度/ID/状态 | ingest.jsonl 不含事件文本；call.jsonl 不含查询原文（仅 `query_chars`） | 黑盒；`observe.py:37-61` |
| scope 注入 | 请求体身份无效，写入/读取均用配置 scope | HTTP body 带 `user_id/agent_id=attacker` 仍存到 `(scope-u, scope-a)`；SQLite 无 attacker 行；recall body 带 attacker 仍返回配置 scope 结果 | 黑盒 3 项；`config.py:189-199`、`service.py:211` |
| 本地模型不可用 | 不得编造记忆；`partial/error` + `items:[]`，候选另列 | `200 status=partial`、`items=[]`、`error=URLError`、`fallback_candidates=[e1,e3]`；另一查询无候选时 `status=error` | 黑盒；`core.py:179-184`、`observe.py:24-34` |
| 数据库不存在 | recall 服务错误而非“无记忆” | `503 database_unavailable`，且未创建 DB 文件 | 黑盒；`service.py:238-240` |
| SQLite 锁竞争 | 503 `database_busy`，释放后恢复 | 锁持有时 503；释放后下一请求 `stored` | 黑盒；`service.py:152-156`、`core.py:39-43` |
| 请求体/事件数上限 | 413/400，统一 JSON | `payload_too_large`/`too_many_events` 均 413 | 黑盒；`service.py:170-196` |
| 未知路径/方法 | 404/405，统一 JSON | 404 not_found、405 method_not_allowed | 黑盒；`service.py:129-146` |
| 遗忘与审计 | 鉴权后遗忘；写入审计 | `/v1/forget` 需 Bearer；call_log 记录 `action=forget`、scope、id、status；重放返回 `rejected` | 黑盒 + §2.2 call_log 原文；`service.py:256-274` |
| 插件健康探测失败 | 只 warning，不阻断 DSH 启动，事件排队 | 单测：health 503/异常 → `start()` 返回 false、仅 warning；apply 不抛错；事件仍入队发送 | `test/archive.test.mjs:569-616` |
| 插件服务不可达/5xx | 指数退避；耗尽丢弃 + warning，不伪造成功 | 单测覆盖 401/5xx 退避、耗尽丢弃、`stats().droppedFailed` | `test/archive.test.mjs:417-480` |
| 插件队列溢出 | 丢最旧 + warning + 计数 | 单测 `queued=2, droppedOverflow=1` | `test/archive.test.mjs:482-499` |
| 插件卸载 | 停表、退订、有界 flush、不抛异常 | 单测：最终批次尽力发送、timer 清空、dispose 幂等、失败只 warning | `test/archive.test.mjs:501-529` |
| 插件越权面 | 不注册工具/不写 Session/不注入上下文 | apply 只用 `ctx.effect/on/logger`；全文 grep 无相关 API；单测订阅仅 `session/event` | §2.6 第 8 条 |
| 服务绑定 | 只允许 loopback | `service.host=0.0.0.0` 启动被拒，exit 2，错误信息明确 | 黑盒；`config.py:173-175` |

## 5. 未核实 / 未通过项

- **无功能性“未通过”项**。唯一一次 `FAIL` 是验证脚本自身的断言写错（检查 stderr 是否含“loopback”，
  而实现用的是 “local-only service”）；修正断言后 42/42 全过。这不是产品缺陷。
- **真实 DSH desktop profile 内插件激活、home patch 安装、DSH 会话产生 `dsh:<session>:<seq>`、主模型
  MCP recall 命中**：Lead 报告已用 `$DSH_HOME/cordis.patch.yml` 完成；本任务明确禁止触碰 profile/运行中
  DSH，故**未独立复验**，只能登记为“依赖 Lead 证据”。
- **`install_bundle` 安装路径**：插件 README 自述本机 desktop 未走通，属未验证路径。
- **多用户/多 agent 鉴权与身份映射**：未实现（单 scope/单 token 本机模式；docs/16 §7 已声明）。
- **DSH 崩溃后的断线补扫/来源检查点/补偿重放**：未实现（`session/event` 为 fire-and-forget，
  插件仅内存队列 + 重试；与 AGENTS 2026-10-30 决策要求的“去重、检查点、落盘确认、补偿重放”仍有差距）。
- **质量评测与线上成本**：未运行评测集（模糊回忆/偏好应用/时间冲突/无记忆/被遗忘记忆/工具注入），
  未测线上 Token 与 A/B 成本；本轮 MCP smoke 只是 1 条合成查询的连通性/契约验证。
- **Windows 上 token 文件 ACL**、**DSH HMR 增删插件行为**、**插件把真实会话替换事件（replace）按新 seq
  重复归档**（README 已声明未过滤 `surfaceOp !== 'append'`）均未实测。
- **MCP 侧数据库 busy 回退**：`mcp_server.py:69-72` 有映射，但 `tests/test_mcp_server.py` 未直接覆盖
  busy → `database_busy`（core/service 侧有覆盖）；属测试覆盖缺口而非已知故障。
- **`docs/16` §9 结果回填**：未完成（D2）。

## 6. 未完成门禁（对照 AGENTS §5）

| # | 门禁 | 本轮状态 |
|---|---|---|
| 1 | 会话隔离（不同 user_id/agent_id 不串读） | **部分通过**：单进程内 scope 强制/无注入已验证；CLI 覆盖 scope 隔离已验证。但多用户鉴权与跨 agent 映射未实现，不能宣称门禁完成 |
| 2 | 持久化（换会话/换主模型、服务重启后仍可读） | **部分通过**：SQLite WAL、关闭重开仍可读已独立验证；真实 DSH 会话重置/主模型切换后的可读性依赖 Lead 的 DSH E2E，未独立复验 |
| 3 | 质量（模糊/偏好/时间冲突/无记忆/遗忘/注入 自动化评测） | **未完成**：无评测集运行 |
| 4 | 成本（线上输入/输出 token、请求次数、缓存、本地 token/延迟、端到端延迟） | **未完成**：本轮无线上模型调用与 A/B；本地调用日志字段存在（`observe.recall_log`），但未产出对比报告 |
| 5 | 失败回退（本地模型/锁/MCP/联网失败不编造） | **通过（本轮范围）**：模型不可用、DB 不存在、锁竞争、插件服务不可达均有结构化回退证据；MCP 断开场景仅由工具不可用语义覆盖，未做真实断连实验 |
| 6 | 安全（遗忘/修改/敏感导出需鉴权与审计；不无限制暴露 SQLite） | **部分通过**：Bearer 鉴权、scope 强制、forget 审计日志、绑定 loopback 已验证；用户级授权、敏感导出控制、Windows token ACL 未完成 |
| 7 | 文档更新（偏离回写 ADR/契约/验收依据） | **未完成**：D1/D2/D8 等需回写 `docs/16` 与插件 README |

## 7. 测试命令 / 结果汇总（可直接复现）

```powershell
# 工作目录：D:\Project\Ai\FishMemory\FishMemory-Project-Spec
.venv\Scripts\python.exe -m unittest discover -s tests -v      # Ran 56 tests, OK (skipped=1), EXIT=0
node --check integrations/dsh-fishmemory/index.js              # EXIT=0
node --test "integrations/dsh-fishmemory/test/*.test.mjs"      # 22 pass / 0 fail, EXIT=0
node --test integrations/dsh-fishmemory/test/                  # 目录形式（shim）22 pass, EXIT=0
.venv\Scripts\python.exe tests\mcp_smoke.py --config config.example.json
                                                               # protocol=2025-11-25 ... source=smoke-1 PASS, EXIT=0
```

独立黑盒与 CLI 脚本位于 `%TEMP%`（非仓库），命令分别为
`.venv\Scripts\python.exe %TEMP%\fishmem-blackbox.py`（42/42 PASS）与
`.venv\Scripts\python.exe %TEMP%\cli-scope-check.py`（scope 隔离 PASS）。

## 8. 线上成本与延迟变化

本轮**没有调用线上主模型**，无线上 Token/成本数据可报告，因此不能给出“节省 XX%”结论（符合 AGENTS §5.4
“不只给笼统节省比例”的要求：宁缺勿造）。本地可观测数据仅为：
- 服务/MCP 的 `call_log` 逐次记录 `elapsed_ms`、本地模型 `calls[].usage/timings`、`query_chars`（`observe.py:37-49`）；
- 黑盒中的模型不可用路径因连接拒绝在 ~2.0s deadline 内返回 `partial`（call log `elapsed_ms=2016.0`），
  说明失败的本地调用受 `recall_deadline_s` 约束而非无限等待；
- MCP smoke 真实调用 1 次，仅验证协议/工具链，不作为延迟基准（本地模型热/冷加载、中文检索延迟需专门 benchmark）。

## 9. 风险与失败场景总结

1. **最需要先修的是文档证据链（D1/D2）**：版本标注 rc.3 vs 实际 rc.5、安装路径与 §9 未回填。运行时代码本身已通过验证。
2. **可靠性缺口**是下一阶段最大产品风险：插件内存队列 + 重试无法覆盖 DSH 崩溃/关闭时的丢失；
   AGENTS 2026-10-30 已明确要求检查点/补偿重放，当前未实现（插件 README 与 docs/16 §9 已如实声明）。
3. **身份/鉴权**仍为单用户本机模式；多用户/多 agent 场景下 scope 靠“每进程一份配置 + 单一 token”，
   没有用户级授权与审计隔离。
4. **误配外发风险（D4）**：`serviceUrl` 接受任意 http(s)，配合 token 读取，极端误配可泄露消息正文与 token；
   建议限制 loopback。
5. **边界字符截断（D5）**、**Windows token ACL（D6）**、**无读超时（D7）** 属低概率本机风险，
   但都应以文档或代码硬化消除。

## 10. 建议下一工作包

| 优先级 | 工作包 | 内容 |
|---|---|---|
| P0 | 文档与证据对齐 | 回写 `docs/16` §2/§8/§9 与插件 README:7 为 rc.5；重新生成/标注 `reports/dsh-source/`；补记 home patch 安装路径与“install_bundle 未验证”；把本报告结果回填 §9 |
| P1 | 可靠性归档协议 | 插件侧持久化/检查点与 DSH 会话游标补扫、补偿重放；服务端增加来源检查点与 `dsh:<session>:<seq>` 幂等回归；覆盖 DSH 崩溃/重启场景 |
| P1 | 契约硬化与小修 | 限制插件 `serviceUrl` 为 loopback（或 warning）；按码点截断文本；明确 `userId/agentId` 保留字段语义；回写 forget-absent 墓碑语义；更新 `core.py` 过期注释 |
| P2 | 多用户身份与鉴权设计 | 明确 DSH session 与长期 user/agent 的映射、按用户 token/授权、敏感导出控制与审计 |
| P2 | 质量与成本门禁 | 建立 docs/14 评测集（模糊回忆、偏好、时间冲突、无记忆、遗忘、工具注入）与线上 Token/成本 A/B 报告 |
| P3 | 插件安装/回归 | 验证 `install_bundle` 路径；增加“插件→真实服务”的 Node 集成测试（fake DSH 事件 + 真 HTTP 服务）与 DSH 真机 E2E 回归清单 |

## 11. 清理与边界声明

- 本报告是唯一被写入仓库的文件；未修改 `fishmemory/`、`tests/`、`integrations/`、`docs/`、`README`、
  `config.example.json`、`demo.sqlite`、`.fishmemory/`、`$DSH_HOME` 下任何内容。
- 所有测试服务均为临时配置 + `%TEMP%` 的 DB/token/日志 + 随机端口，脚本在 `finally` 中终止子进程；
  未 kill 任何非本任务启动的进程（8765 服务与 DSH 相关进程保持原状）。
- 未使用真实 `.fishmemory/memory.sqlite` 或 `demo.sqlite`；MCP smoke 的临时目录由 `TemporaryDirectory` 自动清理。
- 临时脚本与运行目录位于 `%TEMP%\fishmem-*`，可在后续清理，不影响仓库。

