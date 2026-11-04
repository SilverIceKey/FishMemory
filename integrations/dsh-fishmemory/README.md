# @fishmemory/dsh-fishmemory

**0.2 安装默认值**：bundle 只启用 `guidance.js`。先接入 FishMemory MCP 的 `memory_store` 和 `memory_recall`；新安装不会全量归档。旧安装若显式启用了归档，需单独关闭。安装入口见仓库 `docs/17-安装与宿主接入.md`。

以下为可选旧归档模块 `index.js` 的说明：订阅**已提交**的 `session/event`，把用户消息与主模型消息（可选工具结果）
通过 HTTP `POST /v1/events` 归档到本机 FishMemory 服务。零 npm 运行时依赖，只用 `node:` 内置模块
与全局 `fetch`（Node 24）。

集成契约：`docs/16-DSH原生插件集成.md` §6（本轮锁定 DSH **`0.2.1-rc.5`**，证据在
`reports/dsh-source/extracted/`）。本包**不修改** DSH Session、不注册模型可见工具、不注入上下文；
读路径（`memory_recall`）由 `@deepseek-ai/dsh-mcp-client` + `python -m fishmemory.mcp_server` 提供。

```text
DSH 会话事件（post-commit 通知）
  └─ fishmemory-archive（本插件，in-process）
        └─ POST http://127.0.0.1:8765/v1/events（Bearer token）
              └─ FishMemory HTTP 服务 → SQLite（scope 由服务配置强制）
```

## 包结构

| 文件 | 作用 |
|---|---|
| `index.js` | `apply(ctx, config)`、`createArchiver(config, deps)`、`resolveConfig`、`buildArchiveEvent`、`extractText` |
| `cordis.patch.yml` | bundle patch：`insert` 一行 `id: fishmemory-archive` 的默认 config |
| `locale/en.json`、`locale/zh.json` | Plugin Manager 显示用的 `meta.title` / `meta.description` |
| `icon.svg` | 原创图标（Plugin Manager / Settings 显示） |
| `test/archive.test.mjs` | `node --test` 测试，fake ctx / fake fetch，无真实网络 |
| `test/package.json` | 兼容 shim：Node 24.19 的 `--test` 不展开目录参数，`main` 让 `node --test integrations/dsh-fishmemory/test/` 解析到测试文件（新增测试请用 glob 形式） |

## 安装

### 路径 A（有 plugin_manager 的 profile）：install_bundle

先运行本地服务并生成 token：

```powershell
python -m fishmemory init --config fishmemory.local.json
python -m fishmemory serve --config fishmemory.local.json
```

把 `cordis.patch.yml` 的 `tokenFile` 改成目标机器上 `.fishmemory/service.token` 的绝对路径，
然后在 DSH 内用 `plugin_manager` 的 `install_bundle` 安装**本目录的绝对路径**：

```text
plugin_manager action=install_bundle target=D:\Project\Ai\FishMemory\FishMemory-Project-Spec\integrations\dsh-fishmemory
```

`install_bundle` 会写 profile 的 `package.json` / bundle 选择并应用 patch；不要手工改 profile。
安装结果以返回的 `application` / `warnings` 为准（`applied` 才说明已生效）。

### 路径 B（本机 Desktop 已验证）：Harness home patch + 绝对路径

本机 desktop profile 由 Electron 独占（`dsh plugin --profile desktop …` 会拒绝），且 `standard` preset
没有启用 `plugin_manager`。经实测可用的是 DSH 官方 home patch 插入方式（证据：`dsh-app-boot`
README“Inserted plugin names may be absolute filesystem paths, file URLs, or package specifiers”，
`$DSH_HOME/cordis.patch.yml` 由 HMR 监听）：在 `C:\Users\<用户>\.dsh\cordis.patch.yml` 写入

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
```

其余字段用默认值。文件必须 UTF-8（无 BOM）；BOM 会让 DSH 的 manifest 兼容预检把该行禁用。
删除该行即停用。完整字段见 `cordis.patch.yml`；安装、端到端证据与限制见 `docs/16` §8-§9。
注意：本机安装期间桌面渲染进程曾崩溃并自动重启（时间早于 home patch 写入，原因未证实）；
建议在长会话之外的时机配置，重启后插件按启动配置加载。

## 配置字段（bundle patch 默认值，与 docs/16 §6 一致）

| 字段 | 默认 | 含义 / 校验 |
|---|---|---|
| `serviceUrl` | `http://127.0.0.1:8765` | 本地服务地址；必须是绝对 http(s) URL，尾部 `/` 会被去掉 |
| `tokenFile` | patch 中为绝对路径 | 读取 Bearer token 的文件；去 BOM 与首尾空白；只读不写；必填非空 |
| `userId` / `agentId` | `local` / `dsh` | **契约保留字段**：本轮只用于激活日志，不进入 HTTP 请求体（scope 由服务配置强制） |
| `topic` | `会话` | 归档事件的 `topic` 字段 |
| `archiveUser` | `true` | 是否归档 `user/message`（`source.kind === 'user'`） |
| `archiveAssistant` | `true` | 是否归档 `assistant/message`（`source.kind === 'model'`） |
| `archiveToolResults` | `false` | 是否归档 `tool/result`（`source.kind === 'tool'`）；默认关闭 |
| `maxTextChars` | `4000` | 单条事件文本截断长度（1..1,000,000） |
| `batchSize` | `16` | 每批最多事件数，达到即发送（1..256）。不要超过服务端 `service.max_events`（docs/16 §4 默认 64），否则 413 会走重试后丢弃 |
| `flushDelayMs` | `800` | 不足一批时的防抖延迟（0..600,000） |
| `requestTimeoutMs` | `5000` | 单次请求 `AbortSignal.timeout` 上限（100..120,000） |
| `maxRetries` | `5` | 指数退避重试次数上限（0..20）；耗尽后丢弃并 warning |
| `maxQueue` | `512` | 内存队列上限（1..100,000）；超限丢最旧并 warning + 计数 |
| `heartbeatFile` | `''` | **契约保留字段，本实现未使用**（见“未实现/边界”） |

非法配置在插件激活时抛错（`apply` → `createArchiver` → `resolveConfig`），错误信息与
`error.field` 带字段名，例如 `invalid config: field "batchSize" must be an integer between 1 and 256`。
未知字段同样报错，避免 patch 拼写错误被静默忽略（新增字段时需同时更新 `CONFIG_DEFAULTS`）。

**为什么不导出 `Config`**：cordis 激活插件时会执行 `runtime.Config["~standard"].validate(config)`
（`@deepseek-ai/cordis/lib/index.js:956-961`）。本包禁止依赖 `@deepseek-ai/schemastery`，手写普通对象
没有 `~standard`，导出它会让激活直接抛 `TypeError`。因此配置校验集中在 `resolveConfig`/`createArchiver`，
不在插件行声明 Config（host-plugin.md 中 `Config` 是可选导出）。

## 行为（docs/16 §6 第 1-9 条）

1. 激活时 `GET /healthz` 探测；失败只 warning，不阻断 DSH 启动，事件进入有界内存队列等待重试。
2. 只归档**已提交**事件：`user/message`（`data` 即消息，`source.kind==='user'`）、
   `assistant/message`（`data.message`，`source.kind==='model'`）、可选 `tool/result`
   （`data.message`，`source.kind==='tool'`）。`system/message`、`developer/message`、插件注入
   （如 `ptc-mode` 的 user-message）等一律排除。
3. 文本：按顺序拼接 `content` 中 `type==='text'` 的 `text`，截断到 `maxTextChars`；空白文本不发。
4. 映射：`id = "dsh:"+session.id+":"+event.seq`、`session = session.id`、`topic = 配置`、
   `time = new Date(event.time).toISOString()`、`state = "current"`。
5. 批量：`flushDelayMs` 防抖或达到 `batchSize`；`POST ${serviceUrl}/v1/events`，
   `Authorization: Bearer <token>`、`Content-Type: application/json`、每次请求 `AbortSignal.timeout`。
   逐条结果 `stored/duplicate/rejected` 视为完成；其余状态、网络错误、5xx/429 等按
   500ms·2^(n-1) 指数退避（封顶 8s）重试；`maxRetries` 耗尽后丢弃该批并 warning，不伪造成功。
6. 队列上限 `maxQueue`，超限丢最旧并 warning + 累计计数（`stats().droppedOverflow`）。
7. 卸载（插件 effect 清理）：停防抖/重试计时器、退订 `session/event`、忽略重试策略尽力发送
   最后一批（单次尝试，整体不超过一个 `requestTimeoutMs`），不抛未处理异常。
8. 不写 Session、不改派生消息、不注册工具、不注入上下文、不写 DSH 数据。
9. 测试注入点：`createArchiver(config, deps)`，`deps` 可替换 `fetch`、`logger`、`setTimeout`、
   `clearTimeout`、`now`、`readFile`。

## 故障模式

| 场景 | 行为 |
|---|---|
| 服务未启动 / `/healthz` 失败 | 激活成功 + warning；事件排队，退避重试 |
| tokenFile 缺失或为空 | 每次发送记 warning（含路径），按网络错误重试，耗尽后丢弃计数 |
| 服务返回 401/404/5xx/429 | 整批指数退避重试（401 覆盖 token 尚未生成/轮换的时序） |
| `/v1/events` 逐条 `error` | 只重试未处理的事件，已 `stored/duplicate/rejected` 的不重发 |
| 重试耗尽 | 丢弃该批 + warning + `stats().droppedFailed`，不伪造成功 |
| 队列满 | 丢最旧 + warning + `stats().droppedOverflow`（5s 内不重复刷屏，计数照加） |
| 插件卸载 / HMR | 停表退订 + 有界最终 flush；失败只 warning |
| DSH 进程崩溃 | 内存队列丢失（`session/event` 是 fire-and-forget 通知）；断线补扫/游标未实现 |

## 未实现 / 边界（必须与 docs/16 §9 门禁一起阅读）

- `heartbeatFile` 只做类型校验，不写文件：docs/16 §6 未定义其写入语义，本包不发明文件写入。
- `userId` / `agentId` 不随请求发送：scope 只能来自本地服务配置（docs/16 §4 第 3 条），请求体带 scope 会被拒绝。
- 没有持久化发送队列、断线补扫或 checkpoint：插件只做内存队列 + 重试；可靠归档仍依赖后续
  “来源检查点/补偿重放”工作包（AGENTS.md、docs/07 “可靠归档协议”）。
- 单用户本机模式；没有多用户/多 agent 映射与鉴权。
- 未过滤 `surfaceOp !== 'append'` 的替换事件：同一条消息若被 DSH 以 replace 事件重投影，可能以新
  `seq` 再归档一次（docs/16 未规定此情形；服务端按 id 幂等，不按内容去重）。
- 已在真实 DSH desktop profile（0.2.1-rc.5）通过 home patch 安装并验证归档 + MCP 闭环（docs/16 §9）。
  `install_bundle` 路径（profile 内 bundle 安装）本机 desktop 未走通，属未验证路径；HMR 增删插件在
  本机曾伴随一次渲染进程崩溃重启（时间早于安装写入，原因未证实），建议启动前配置。

## 测试

```powershell
node --check integrations/dsh-fishmemory/index.js
node --test integrations/dsh-fishmemory/test/                     # 经 test/package.json shim 解析
node --test "integrations/dsh-fishmemory/test/*.test.mjs"         # 推荐：新增测试文件也能被发现
node --test                                                       # 仓库根目录默认发现
```

> Node 24.19.0 的 `--test` 位置参数只按 glob/文件解析，传目录会被当作入口模块并报
> `MODULE_NOT_FOUND`；`test/package.json` 的 `main` 仅为兼容 docs/16 §9 的目录形式命令，不作为
> 测试发现机制（[Node 24 文档](https://nodejs.org/docs/latest-v24.x/api/test.html#running-tests-from-the-command-line)）。

测试使用 fake ctx（`on`/`effect`/`logger`）、fake fetch、注入计时器，覆盖事件映射与文本抽取、
注入消息排除、tool 开关、批量/防抖、逐条结果、401/5xx 重试后成功、重试耗尽、队列上限、卸载、
非法配置；不发起真实网络请求。

## DSH 0.2.1-rc.5 依据（仓库副本 `reports/dsh-source/extracted/`）

- `@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/references/host-plugin.md:57-64` —
  `export function apply(ctx, config)`、`ctx.effect`/`ctx.on` 注册与清理、Config 校验时机。
- `@deepseek-ai/cordis/lib/index.js:336-352`、`:371-378`、`:956-961`、`:1142-1146`、`:1169-1260` —
  `ctx.on` 返回 disposer；不导出 `Config` 时 config 原样传入；effect 支持同步/异步 disposer。
- `@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js:409-423`（`:421`）—
  `ctx.on('session/event', (session, event) => …)` 的实际订阅写法。
- `@deepseek-ai/dsh-session/lib/index.js:1522-1534`、`:1579-1598` —
  事件在提交后派发，信封为 `{type, seq, time, data}`。
- `@deepseek-ai/dsh-session/lib/index.js:217-230`、`:1194-1219`;
  `@deepseek-ai/dsh-session/README.md:45`; `@deepseek-ai/dsh-agent/README.md:52` —
  事件类型/消息位置与 `source.kind`。
- `@deepseek-ai/dsh-tools/README.md:134` — 工具产生的附加 user-message 使用其它 `source.kind`（必须排除）。
