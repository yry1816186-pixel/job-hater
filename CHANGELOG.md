# 更新日志

格式遵循 Keep a Changelog；版本遵循语义化版本（SEMVER）。

## [2.0.0-alpha.1] - 2026-09-20

完整产品重建（v1 视为原型归档于 docs/history/）。

### 重建（Rebuilt）
- **数据层**：JSON 文件 → SQLite（WAL/迁移运行器/FTS5 中文检索：search_text 预分词，jieba 可选 bigram 兜底）；30+ 实体 schema 覆盖画像/证据/偏好/雇主/信源/岗位/匹配/简历/投递/面试/Offer/反馈/AI Provider
- **通用化**：移除全部单用户硬编码（技能词表/权重/阈值/城市薪资线/国企加分/应届假设）→ 下沉为画像数据与偏好 preset；多 persona 匹配回归测试防再犯
- **匹配引擎**：分层 Eligibility Gate（可配置硬约束，透明拒绝原因）→ BM25 相关性 → 六维个性化排序（权重为用户数据；每维分数+依据+不确定性）
- **投递语义**：13 态状态机 + 用户确认门（applied_confirmed 只能由确认进入）；废除 v1"投递即拉黑公司"
- **简历系统**：JSON Resume 兼容 sections + 不可变版本链 + bullet 级 provenance（六档改写类别）+ factcheck 六道闸 + md/html/json/pdf/docx 导出（可选依赖诚实降级）
- **接口层**：FastAPI + React/TS Web UI（九页面）+ CLI + MCP（官方 SDK，10 工具）——同一 services，无平行逻辑
- **AI Provider**：OpenAI 兼容（GLM/DeepSeek/Qwen/Kimi/Ollama）+ Anthropic；默认本地模式；key 只进 OS keyring；逐任务数据出境披露
- **信源**：SourceAdapter 契约 + 注册表 + 信源健康隔离；粘贴导入（任意 JD 文本→结构化草稿→人工确认入库）为主链
- **迁移**：v1 data/*.json 一次性迁移工具（幂等，语义降级如实报告）

### 性能（真实基准）
- 9745 真实岗位导入 599s → 123.5s（批量化+内存去重索引）；检索 2-9ms

## [1.0.0] - 2026-09-20

首个公开版本（v1 原型，已被 2.0 重建取代）。

### Added
- 六命令体系：`/profile` `/search` `/apply` `/interview` `/pipeline` `/upskill`（Claude Code 技能 + CLI 双入口）
- 核心引擎 `core/`（纯标准库）：统一数据契约、采集清洗、校招强制过滤、精确+近似去重、五维评分（可解释锚点）、定制简历生成、factcheck 真实性硬校验、投递风控闸门、面试题库、技能缺口计划
- 信源采集层 `adapters/sources/`：wenke 官方接口桥（28 源）、xiaozhao 校招种子、job-radar 渠道线索索引、可选语义相似度增强（fastembed/BGE-small-zh）
- 简历可交付文件：A4 打印 CSS 的自包含 HTML（浏览器即出 PDF）+ Markdown + 审计版
- 投递进度离线看板（单文件 HTML，深浅双模式，配色经 CVD 校验）
- 本地 MCP server（9 工具，stdio，零第三方依赖）
- 反捏造机制：evidence_index 事实锚点 + 逐条 [ev:ID] 引用 + 引用覆盖/证据存在/数字溯源三重硬校验
- 测试 5 套（factcheck 对抗 / 过滤与评分 / 评分质量回归 / 信源映射与近似去重 / 模块导入烟测；数据类测试在真实画像与公开夹具间自动切换）

### Security
- 投递风控：单平台每日上限、消息间隔、敏感时段禁投、黑名单去重，全部确定性强制并留审计记录
- 用户数据全部本地存储，核心引擎零网络外发代码（静态检查验证）

## [Unreleased]

- Boss 直聘站内半自动链路的本地图形环境验证（见 docs/降级路径说明.md 的现状）
- 更多官方 ATS 详情接口的 JD 全文补全
