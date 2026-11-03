# 07. DSH 插件集成与 MCP 适配规范

## 目标

业务核心独立；DSH 作为主要 Agent 宿主。实现时可以选择 DSH 原生插件或使用其 MCP Client 桥接，**不要把二者强行混成同一份代码**。

## 官方来源已核实的事实（2026-10-29 查阅官方仓库）

1. DeepSeek Harness 是插件式 Agent Harness，官方 README 将其标为 developer preview，明确提示可能有破坏兼容性的变更。
2. 官方 `@deepseek-ai/dsh-tools` 支持通过 `ctx.tools.register(...)` / `defineTool(...)` 注册工具。
3. 官方 extension cookbook 列出 `agent/pre-step`、`tools/result`、`session/event` 等扩展机制，但实际 payload、时序和可用性必须在锁定的版本源码中核实。
4. 官方 `@deepseek-ai/dsh-mcp-client` 可以连接外部 MCP Server，并注册为有服务器名前缀的工具；支持 stdio/Streamable HTTP，具体 transport 和配置以锁定版本为准。
5. MCP 官方规范支持服务器向客户端暴露 Tools；MCP 不规定我们项目自己的 `memory_recall` 参数，也不自动提供持久记忆能力。

> 本文仅记录已验证的**能力级事实**；未锁定 commit 前，不承诺某个插件配置或 hook payload 一定能直接复制运行。参考链接见 `13-官方来源.md`。

## 集成方案 A：DSH 原生插件

职责：
- 注册尽量少的 `memory_recall` 主模型可见工具；附带严格 Schema、参数验证与输出大小限制。
- 在选定的安全时机触发预取；向本轮请求注入记忆，不自动把大段上下文永久追加到历史。
- 从可信的 DSH 会话事件中收集已授权对话和工具结果，送往独立记忆 API；不得仅依赖模型主动写入工具。
- 明确 host session 与长期 `agent_id` 的映射；会话重置后映射不变。

**特别需要源码确认**：`agent/pre-step` 是否发生在在线推理请求前、能否安全注入临时上下文、能否避免本轮多次执行、是否会进入持久 Session；`session/event` 的事件范围、过滤策略、提交时序、消息原文获取权限、重放和事件 ID。不得直接根据示例猜测钩子数据类型。

## 集成方案 B：使用 DSH 自带 MCP Client

职责：
- 独立记忆服务实现标准 MCP Tools。
- DSH 通过已有 MCP Client 连接，将 `memory_recall` 注册到主模型工具表。
- 这一路径适合**按需 Tool Call**；并不天然解决“自动预取”和“可信会话事件入库”，两者仍可能需要小型 DSH 插件。

### 推荐组合

**Memory Core API + MCP Tool Server + 轻量 DSH Host Plugin**。主模型按需调用优先通过 MCP Client；原生插件不应重复注册同名读工具。

优点：其他宿主可以直接连 MCP；DSH 插件只做会话自动化和低 Token 注入；升级 DSH 时不必改存储和核心检索算法。

## 模型调用模式

1. **自动预取（可选）**：用户消息 → DSH hook → 本地 Recall → 必要时注入短证据 → 在线主模型。
2. **主动 Tool Call（必需）**：线上主模型需要详细回忆时 `memory_recall` → 本地模型多轮查询 → 返回证据。
3. **事件归档（必需）**：对话/工具事实在安全规则允许下写入 SQLite；不得由主模型决定是否“想起来才保存”。

## Tool 成本与重复注入

- Tool Schema 本身可能占线上输入 Token；先只暴露一个主要读工具。
- 如果预取已经注入同一来源内容，后续工具调用需要允许获取更多详情，但应尽量避免重复发送同样摘要。
- 应比较预取触发的额外本地延迟与 Tool Call 对主模型增加的往返调用成本。
- DSH 不同 `native` / PTC 模式下的工具展示与调用路径由选定的版本决定；开发前必须核验，不得照搬网上过时字段。

## 版本验证工作单（开发启动必做）

- [ ] 锁定 DSH tag/commit、Node 运行时版本、包管理方式。
- [ ] 核查 `ctx.tools.register` 和工具返回格式。
- [ ] 核查自动预取可用 hook、时序、注入语义和是否形成永久消息。
- [ ] 核查从 Session 获取消息的事件类型、隐私边界和幂等源 ID。
- [ ] 核查 MCP Client 实际 transport 配置与命名规则。
- [ ] 核查工具执行超时、取消、权限、并发和工具结果记录行为。
- [ ] 锁定并记录上述源码位置/提交 SHA 与验证结论。


## 2026-10-30 补充：接口分工、已核实事实与风险

### 已核实的官方能力（仍须在实际 commit 复核）

- `@deepseek-ai/dsh-mcp-client` 支持 `stdio` 和 `streamable-http`，文档中配置项包括 `serverName`、`transport` 以及 HTTP 的 `url`；工具公开名称为 `mcp__<serverName>__<tool>`，发生规范化冲突时命名实现还有处理逻辑。来源：官方 MCP Client README 与 `src/index.ts`。
- `agent/pre-step` 官方参考将其定义为可拦截步骤或替换进入该步骤的消息。它是 **step** 而非天然的“每轮新用户消息”事件，必须做频率限制、幂等和注入去重。来源：`docs/subsystems/core.md`。
- `session/event` 官方参考说明是 **post-commit、fire-and-forget** 通知；它不是等待远端 FishMemory ACK 的可靠交付通道。不能只监听事件就声称已全部保存。来源：`docs/subsystems/session.md`。
- 官方架构列出 `agent.inject()` 为模型可见上下文注入能力，但它是否落入会话历史、何时进入下一次请求以及撤销语义应以锁定的实现测试确认。来源：`docs/architecture.md`。

### 建议的两个插件接入层（分责）

**MCP Client（主模型按需回忆）**：把 FishMemory 服务提供的 MCP `recall` 注册给主模型。无需主模型掌握 SQLite schema。

**DSH 原生桥接（非必需重写 MCP）**：负责从可信 session 数据源抓取事件、可配置自动预取、适时向当前请求注入少量记忆，以及可靠递送/补偿游标。关键行为不能依赖主 LLM 自主决定是否触发写入。

### 可靠归档协议（必须在实施中明确）

1. 事件来源标识为 `(host_instance, session_id, event_seq)` 或等效稳定组合，不得使用内容哈希作为唯一幂等键。
2. 对外发送以前应持久记下尚未确认的事件，或从 DSH 已持久日志按游标可重放；异步通知丢失后必须能补扫。
3. FishMemory 在 SQLite 事务提交后返回 ACK，桥接方再推进提交游标；失败可重试且不产生重复记录。
4. `session_id` 仅为本次对话，`agent_id`/`user_id` 是授权主体与长期身份；改变 Session 不应把历史记忆分区换掉。
5. 不要因 DSH 自身对 Session 做压缩、删除、fork 就隐式删除长期记忆；用户显式遗忘操作是另一个授权流程。

### 部署网络边界

本地 DSH → 本地 FishMemory 适合 `127.0.0.1`；远程 DSH → 本地 FishMemory 必须通过明确的认证、授权与受保护网络连接。MCP HTTP 端点须按 MCP 安全规范处理 Origin 验证、绑定范围与认证，不能直接用端口映射公开私人记忆服务。

## 2026-10-30 用户新决策与开发状态（优先于本文件旧描述）

依据：用户明确要求由主LLM发起记忆查询，本地小模型只负责查询规划与证据筛选，并已授权开发测试。执行ADR-015：当前默认入口为显式memory_recall，不要求本地模型判断是否需要记忆；自动预取与GATE暂不作为当前路径的功能或验收项。后台授权归档职责不变。

仓库现含可运行的本地检索实验，尚非生产服务；原有“仅规格、无代码”描述为初始包历史状态。实现范围、实际环境、复现命令、测试结果和未完成门禁见 docs/14-本地检索实验.md（docs目录内读者使用同目录14文档）。

## 2026-10-30 实现落地（本页方案已部分实现）

- 方案 B（MCP Client）已落地：官方 `@deepseek-ai/dsh-mcp-client` stdio 连接 `python -m fishmemory.mcp_server`，只暴露 `memory_recall`；主模型实调返回带来源 ID 的证据。
- 方案 A 的“原生插件”已落地为**归档桥**：`integrations/dsh-fishmemory` 订阅已提交的 `session/event`，把 user/assistant 文本消息写入本地 HTTP 服务（`fishmemory/service.py`，Bearer token，127.0.0.1）；不注册工具、不注入上下文，避免与 MCP 重复。
- 本机 DSH 版本实测为 **0.2.1-rc.5**；desktop profile 由 Electron 独占，实际安装走 `$DSH_HOME/cordis.patch.yml` 绝对路径插入（已验证），bundle `install_bundle` 路径未走通。
- 仍成立的限制本页已写明：`session/event` 是 post-commit 通知，当前实现只有内存队列+重试，**没有持久化游标/补扫**；服务离线期间事件会丢。安装方式、证据与未完成门禁见 `docs/16-DSH原生插件集成.md`。

