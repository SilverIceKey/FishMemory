# FishMemory 0.2 验证记录（2026-11-04）

- Windows，Python 3.11.9。独立测试环境安装 AstrBot 4.14.6；按该版本插件命名空间导入仓库原生入口，使用真实 SDK 的工具注册器、参数解析器和插件数据目录。用合成的单聊事件调用工具，未连接实际聊天平台账号。
- `python tests/astrbot_smoke.py fishmemory.local.json` 通过：注册 `memory_store` / `memory_recall`，访问现有带鉴权 llama.cpp 的 `/v1/models` 自动选中 `qwen3.5-0.8b`。在临时数据库写入合成事实，重新创建插件实例，再通过真实推理检索命中相同的来源 ID。凭据与原始日志不随仓库分发。
- 安装 wheel 后 `python tests/portable_smoke.py fishmemory.local.json` 通过：临时工作目录中先后启动三个独立 MCP 进程，分别返回 `stored`、`duplicate`、`found`；各次都有本地审计凭证。一次本地检索样本 1484 ms，模型调用两次，输入共 392 tokens，输出 94 tokens；服务端报告第一步缓存 74 个输入 tokens。非端到端聊天时延或性能保证。
- `python -m unittest discover -s tests -q`：74 项，73 通过、1 跳过（存在本机配置时避开默认路径初始化测试）。覆盖鉴权模型发现、拒绝带凭据跳转、多模型选择、跨用户/机器人隔离、并发去重、写入失败、遗忘重放、HTTP 与 MCP 审计。DSH 的 25 项 Node 测试通过，Skill 格式校验通过。
- 上述测试直接调用工具，不使用线上主 LLM（线上请求/输入/输出均为 0；线上缓存、自然聊天触发率及成本变化未测）。原生 AstrBot 的 WebUI 安装、真实消息平台及各宿主主模型主动触发仍需单独验收。新手安装入口见 [接入指南](../docs/17-安装与宿主接入.md)。

仓库发布后追加验证：`python tests/astrbot_smoke.py fishmemory.local.json --repository` 使用 AstrBot 4.14.6 的真实 `PluginUpdator.install` 从公开 GitHub 仓库默认分支下载、解压，并通过同一套原生工具注册、带密钥发现、写入与读取测试；最后一个参数改为 `dist/astrbot_plugin_fishmemory.zip` 时，本地 ZIP 安装也通过。此测试没有操作 WebUI，也未使用真实消息平台账号。
