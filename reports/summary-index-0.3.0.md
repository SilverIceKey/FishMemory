# v0.3.0 验证记录（2026-11-06）

## 已实现与依据

用户明确要求：0.8B 处理写入摘要和查询，摘要用于匹配，原文最后交给主 LLM，原文不得改写；
AstrBot 更新后可用，兼容以前记忆，向量环境不具备时允许替代方式。实现细节见 ADR-025 和 docs/18。

- AstrBot 默认 summary 检索：原问题 + 模型扩展的摘要/原文 FTS，取消主题硬过滤。
- 0.8B 非思考模式后台摘要；写入原文与摘要生成解耦，旧记忆自动分批补建、失败退避、重载恢复。
- 可选 OpenAI-compatible embeddings；标准库 float32 BLOB + 余弦搜索，不新增依赖，不自动下载权重。
- 完整原文返回、分段检索、来源身份区分；新宿主原文与旧写入事务一起提交。
- 旧表列数、记录 ID、作用域和墓碑保留；触发器清理遗忘后的摘要/全文索引/向量。
- AstrBot v0.3.0、Python 包 0.3.0、ZIP 新模块及安装说明一起更新。

开发基线：FishMemory `33a52dec022de2047b916b26ef5d0118af444795`。
AstrBot 官方源码核验：4.14.6 对应 `6b4498a554f2ce0e5a7f6c0f09e388c5c4aef016`；
`get_message_str()`、star_manager 的 initialize/terminate 调用与当前接入相符。
未改 DSH 插件逻辑；其既有 rc.5 验证边界不变。

## 测试命令与结果

环境：Python 3.12.14，SQLite 3.53.1（实际创建 FTS5 表），没有真实 AstrBot 安装及 Qwen/BGE 服务。

```bash
PYTHONPATH=tests python -m unittest \
  test_summary test_astrbot_lifecycle test_core test_config \
  test_identifier_recall test_integration test_retrieval_v2 test_writing \
  test_astrbot_adapter.AstrBotTests.test_private_identity_and_restarts
```

**68 项通过**，包括 23 项新增测试。新增测试使用真实 SQLite/跨进程读取与可控模型/HTTP 响应替身，
不是语义模型准确率测试。覆盖：

- 错误 topic 和错误规划不隐藏原始命中，摘要匹配后返回原文。
- 旧 source_quote 独有词、长文尾部、Unicode/换行/空格原样返回、分段去重。
- 新宿主消息绑定、旧来源不伪造认证、跨用户/机器人隔离、历史状态传递。
- 从真实旧 v1 表结构升级、旧八列 INSERT 兼容、高 user_version 不被覆盖。
- 旧连接删除时触发器传播、遗忘与后台生成并发时不能复活、修改来源后索引失效。
- 摘要失败及退避重试、模型失效不返回确认事实、越界/错误类型序号拒绝。
- embedding 请求、前缀、非有限向量拒绝、模型/维度隔离、向量持久化与更换、向量离线回退。
- AstrBot 默认路由、legacy 回退、生命周期接口替身的初始化/终止、插件重新创建后持久记录存在。

```bash
python -m unittest discover -s tests
```

在修改前基线运行即有 **29 项网络相关错误**，原因为沙箱禁止创建 socket（`PermissionError: Operation not permitted`）。
因此最终验证中显式排除 test_service、test_mcp_server 和 AstrBot 的本地 HTTP 发现测试，共 29 项。
不声称全量网络回归通过，也未用 mock 结果替代真实 HTTP/MCP 验收。

```bash
node --test 'integrations/dsh-fishmemory/test/*.test.mjs'
python -m compileall -q fishmemory main.py tests scripts
python scripts/build_astrbot.py
git diff --check
```

上述命令退出码均为 0；Node runner 报告两个测试文件通过。
生成的 ZIP 已检查包含 embedding/summary_index/summary_retrieval；
从临时工作目录在独立 Python 进程直接导入 ZIP 中模块并创建 SQLite schema 成功，包内无数据库、密钥和日志。

## 未实现、风险与失败场景

- 未连接用户实际 AstrBot、Qwen3.5-0.8B 或 BGE 服务，不声称模型召回质量、并发延迟或真正宿主在线验收通过。
- 默认摘要 FTS 仍是词法匹配；语义向量需要用户另配本地 embedding 服务。没有训练或替换用户模型。
- 0.8B 仍可能生成错误摘要或选错证据；摘要不是事实，最终返回的是实际来源。
- 旧记录没保存过的原消息无法恢复；旧版仅有 caller_supplied 引用的仍保持该标识。
- 初次补建分批完成；大库暂未补齐的引用词可能漏查。向量扫描尚无大库性能基准。
- 旧偏好状态不自动重写；矛盾事实可能同时存在，由带时间/状态的原文供筛选与主模型判断。
- 回退旧插件不会删除旁表，但旧检索仍有原来的 topic 硬过滤问题。

## 成本与延迟

本次验证没有调用线上主 LLM 或真实本地模型，不能据此估算生产节省比例、Token 或速度。
运行时将本地摘要调用的 usage/elapsed 记录在原有本地审计日志中；召回增加 candidate_ids 和 warnings，日志不保存原文。
一次查询最多 1 次规划 + 3 次小批量筛选，最多返回 4 条完整原文；后台每批最多 2 次摘要生成。
完整原文比旧 1200 字符截断可能增加线上输入量，是本次用户明确选择；需要在实际聊天中评估质量与 Token。

## 用户更新后的核验

更新到 v0.3.0，保留原数据目录与模型设置，私聊 `/fishmemory_index` 观察进度。
用一条已有习惯换主题/换表达查询，再测试一条新习惯、清除会话后查询；核对返回是否来自原始记忆。
如摘要失败持续增加，检查本地模型地址、模型 ID 和 JSON Schema/非思考模式支持。
向量可先留空；需要时再配置 embedding 地址，确认“已有向量”开始增加。
