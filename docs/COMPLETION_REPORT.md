# Job Hater v2 重建 — Completion Report

日期：2026-09-20 · 分支：`rebuild`（13 提交，自 a279caf）· 主验证环境：Windows 11 / Python 3.12 / Node 24

## Architecture（最终系统）

单一领域模型 + 单一服务层 + 单一 SQLite，三接口（Web UI / CLI / MCP）零平行逻辑：

- **数据层**：SQLite WAL，迁移运行器（0001 全量 schema + 0002 增量），FTS5 中文检索（search_text 预分词影子列 + 触发器同步；jieba 可选、字符 bigram 兜底）
- **领域层**：30+ Pydantic 实体（画像簇/证据/偏好 preset/雇主/信源/岗位/匹配/简历版本/投递/面试/Offer/反馈/AI Provider），13 态投递状态机
- **服务层**：Profile（证据先行）/ Jobs（四层去重+快照）/ Matching（Gate→BM25→六维加权，全可解释）/ Resume（版本链+provenance+factcheck 六闸）/ Lifecycle（确认门）/ Feedback（可重置）/ AI（local-only 默认+keyring+egress 披露）
- **接口层**：FastAPI（40+ 路由）+ React/TS 九页面 + CLI（doctor/serve/import/match/migrate-v1/backup）+ MCP 官方 SDK（10 工具）

## Replaced（替换/废除的旧能力）

| v1 | v2 | 原因 |
|----|----|------|
| 纯标准库 CLI + Claude Code Skills 为唯一入口 | Web UI 为第一入口，CLI/MCP 为接口层 | 普通用户不该需要理解 Claude Code（§2） |
| JSON 文件存储 | SQLite+迁移+FTS5 | 原型只适合玩具规模（§4） |
| 代码级 SKILL_GROUPS/权重 35/25/20/15/5/A-D 阈值/城市薪资线/国企=100/≥1年淘汰 | 画像数据 + 偏好 preset + 可配置 Gate | 单用户硬编码不得是产品核心（§1） |
| `apply --send` 假发送 | 13 态状态机 + 用户确认门 | 假装成功是错误产品语义（§14） |
| 投递后自动拉黑公司 | 同岗位唯一 + 雇主多岗位并行 | 阻止同公司多岗位是错的（§15） |
| 手写 MCP 旧协议 | 官方 SDK FastMCP | 停止扩展手写协议（§24） |
| bootstrap_upstream.sh 拉上游 main 直接执行 | 无上游运行时依赖 | 可复现性（§20） |
| "全部数据永不上传"错误声明 | 本地存储 vs 远程 AI 出境两分法 + 逐任务披露 | 声明必须真实（§13） |

## OSS Fusion

| 项目 | 许可证 | 决策 | 说明 |
|------|--------|------|------|
| JSON Resume（schema 标准） | CC-BY-SA/W3C 社区标准 | ADOPT | 内部简历 sections 结构 |
| wenke-radar | MIT | 参考其 fetcher 架构 | SourceAdapter 契约设计输入；适配器接入为可选增强 |
| Reactive Resume | MIT | REFERENCE_ONLY | 多服务架构（PostgreSQL+Gotenberg+对象存储）与单 SQLite 本地优先冲突 |
| offerPilot / get_jobs / job-radar | AGPL / PolyForm / 无 | 未复制任何代码 | 许可证不兼容（§23） |

## Verification（实际执行）

| 项 | 命令/方式 | 结果 |
|----|-----------|------|
| 后端测试 | `python -m pytest tests/` | **44 passed**（含 §35 十六步 E2E、3-persona 匹配、factcheck 对抗、迁移幂等/原子性、API 全链、MCP 往返） |
| Lint | `python -m ruff check jobhater tests` | 0 error |
| 前端类型+构建 | `npx tsc -b && npx vite build` | 通过（gzip 94KB） |
| 浏览器 UI 走查 | IAB 真实浏览器：建档→技能→证据→偏好→粘贴 JD→解析→入库→列表带分→六维详情→收藏→看板 | 通过（1440 视口截图视觉验收） |
| 真实数据迁移 | `migrate("data")`（9745 真实岗位） | added=9745；二次迁移全去重（幂等）；5011 条 v1 拒绝透明保留 |
| 性能基准 | 同上 + 检索 | 导入 123.5s（优化前 599s）；列表 2ms / FTS 中文 9ms（目标 p95<500ms） |
| 干净安装 | 全新 venv `pip install -e .` → doctor/import/backup/serve | 全通过；SPA 200；数据跨进程可见 |

## Product Walkthrough

新用户：`pip install -e .` → `job-hater serve` → 浏览器 127.0.0.1:8787 →
建档（技能+证据+确认）→ 设偏好 preset → 任意网站复制 JD 粘贴导入（解析草稿带来源标注，人工确认入库）→
岗位收件箱看匹配分 → 详情页六维依据（分数+原因+不确定性）与 gate 检查 → 收藏进看板 →
按状态机推进 → 亲手投递后确认 → 简历页从画像生成主简历（factcheck 过闸才能定稿）→
导出 md/html（pdf/docx 可选依赖，缺失时诚实降级）→ 面试排期 → Offer 录入与并排比较 →
backup / 重启后数据完整。

## Known Limitations（仅真实外部限制）

1. **CI 矩阵尚未在 GitHub 运行**：workflow 已就绪（3 OS × Python 3.10/3.12 + 前端 + 迁移烟测 + Docker 构建），推送后生效；本地已验证 Windows。
2. **面试深度内容生成依赖远程 AI**：无 Provider 时面试模块为记录与复盘工具（诚实降级，非假装生成）。
3. **官方 API 信源适配器未接入**（wenke-radar 28 源等）：SourceAdapter 契约与健康隔离已就绪；粘贴导入是永不失效的主链。
4. **PDF/DOCX 导出需可选依赖**（playwright/python-docx）：缺失时提供可打印 HTML 并如实说明。
5. **English i18n 未实现**：当前简体中文单语言（§18 的"English 基础"未做，列为待办而非声称完成）。

## 不自称完成的部分

- rebuild → main 的合并与推送（外部可见操作，待用户授权）
- macOS/Linux 的本地真机验证（交由 CI）
