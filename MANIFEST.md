# 文件清单与完整性校验

> 版本：2026-10-30（第二版规格包）。仅 Markdown 规格文档，无实际业务实现。

下列 SHA-256 对应 ZIP 中各 Markdown 文件的 UTF-8 字节，`MANIFEST.md` 本身不做自引用校验。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `AGENTS.md` | 5903 | `dcdf5fe91db22f90b2ae6b832863ad3c9c3c93105cab335ee153e70ba3f4a674` |
| `CODEX_START_HERE.md` | 2528 | `db6059662fd1735a0bf29712cdfd9b2aa1ffde15a704d2d2fa7617f1b5877efe` |
| `README.md` | 5213 | `cc6cf43f9f2c0f3e30d265bd81c7ecc95e1c5748462d80fdec0231d91eb94164` |
| `docs/01-愿景与边界.md` | 3521 | `e1dda2ccb93814755ec15960450917fc760c1b7348d48ffb3f4d0c4f25729e68` |
| `docs/02-需求与场景.md` | 5528 | `def4fd35e34abf72a6378a80bfcbed99427379fb1184e38b02204f8dd0c270ac` |
| `docs/03-系统架构.md` | 5326 | `ccd858122288479503a9dee1f131dd35e87cf15ba92d6e2d33da404f805c046b` |
| `docs/04-数据模型与索引.md` | 5986 | `f866c8162a326c79160068ef970686e211a67983a2fcd984e519c14204cb9538` |
| `docs/05-检索与摘要算法.md` | 6637 | `f61a9e89d6a45b82e34d158bded968c7762e7badeda3b5ec918502e36c9db47a` |
| `docs/06-外部接口契约.md` | 5574 | `d0e8430358bd4d767829c5117af296411d1c530154fdf21d4c01c01b88e66a33` |
| `docs/07-DSH与MCP集成.md` | 7073 | `47c992b4f04d33dde1df30f6dc735c0b14778821a595f88345049fccd040ddba` |
| `docs/08-写入与长期演化.md` | 3323 | `8bd0b34bf6af76bdd6ff599acc610ca2b3ae2afaec58777595cbebaf52d3d0ba` |
| `docs/09-安全可靠性与观测.md` | 4932 | `127e1fe7e18967cbd7b4c663946602d9f7ebc69c9b6ee1fcd21c2e840648d8bb` |
| `docs/10-评测与验收.md` | 6749 | `7dc1e2f82b859699d377f7867c013cbf804be95e75d4afaad2ab8f3e14aa2bc6` |
| `docs/11-开发实施计划.md` | 4891 | `82547c921d1d97dbc7f3d68f25c8ca6d230abff9a241bd8084d90cbc1ae7e639` |
| `docs/12-决策记录与待验证项.md` | 4561 | `96f0b8515ed53ec4398f73cf1aa2f7e370d082b8902751ec7810c5465f114fa2` |
| `docs/13-官方来源.md` | 5235 | `dde5827bdb0f723c7697ada4ed6fc6b163db73c95a257003c91599a3d6a56eb5` |

**共 17 个 Markdown 文件（含 MANIFEST.md）。**

本包不包含模型权重、运行脚本、依赖锁文件、SQL 或可执行程序。

## 开发后说明
上表仅用于原始规格ZIP的历史校验；文档现已修改并新增实验源码，上表不代表当前工作目录。当前新增内容与范围见docs/14-本地检索实验.md。

