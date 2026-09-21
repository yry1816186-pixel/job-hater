# 更新日志

格式遵循 Keep a Changelog；版本遵循语义化版本（SEMVER）。

## [2.1.0] - 2026-09-21

产品团队全流程打磨：能力补全 + 可用性重建（真实用户反馈「搞不懂逻辑和交互」的系统性修复）。

### Added（能力补全——v1 打磨经验回归 + spec 零遗漏审计）
- **材料工坊**（`services/materials.py`，本地确定性生成，零 AI 依赖）：岗位定制简历（按 JD 相关度重排，只重排不编造）/ 求职信（证据锚定，cover_letters 表首次有写入方）/ 打招呼话术（~134 字）/ 面试题库（四板块+证据锚点）/ 技能提升计划（14/30/60 天+诚信规则）
- **AI 执行面**：`POST /api/ai/complete`（ack_egress 披露确认门→428；本地模式诚实返回 executed=false）+ `GET /api/ai/tasks` 任务目录；此前 6 类 AI 任务无任何执行入口
- **匹配引擎 v2.1**：内置技能同义组种子（SKILL_SYNONYM_GROUPS）；硬领域词封顶（防「IC后端」词法巧合虚高）；城市薪资参考线倒挂提示（数据旗标非 gate）；词法-语义余弦交叉校验（防关键词堆砌）；四档结论（强烈推荐80/推荐65/可考虑50/暂缓）；无 deadline 岗位发布超 60 天过期兜底
- **接口完备性**（审计 40+ 项缺口）：奖项/证书路由、单条投递读取、关联简历版本、面试完结、岗位状态 PATCH、证据/技能/偏好 DELETE、过滤同口径计数；offers/applications 列表嵌岗位真名（杀 UUID 不可辨识与 N+1）
- **CLI**：`paste`（stdin 粘贴主链）/ `jobs` / `applications` / `transition` / `confirm-applied`
- **MCP**：新增 `applications_create` / `paste_parse` / `preset_get` / `ai_egress_disclosure` / `materials_interview_questions`（10→16 工具）
- 校招官网信源接入（wenke-radar MIT 移植：米哈游/百度/网易）；jieba 升主依赖

### Changed（可用性重建——认知走查 + Nielsen 审计驱动）
- **导航信息架构**：按业务主线重排分组（准备→求职推进→系统）；画像切换器；404 路由；页面标题修复
- **新手引导**：总览页 5 步开始清单（完成态+下一步 CTA）；新用户三步卡；画像页分步编号+每节「这步影响什么」
- **简历工坊**：JSON textarea → 结构化表单 + 实时预览（JSON 降为高级模式）——最大可用性灾难修复
- **投递看板**：⚡一键待投递（4 击→1 击，漏斗跳步语义）；确认投递模态化；Offer 录入全字段模态；空列渲染；事件时间线中文
- **诚实语义收紧**：applied_confirmed 从状态转移表移除——通用 transition 一律拒绝，只能经用户确认门
- 全站中文标签映射（gate/事件/Offer 状态/AI 任务）；Toast 反馈系统；危险操作二次确认

### Fixed
- SQLite check_same_thread 跨线程 500（FastAPI threadpool 间歇性崩溃根因）
- CLI fetch 分支 `con` 未定义 NameError
- 设置页出境披露 URL 漏 `?` 致 404 静默
- 【数据丢失】导入确认入库改为用核对后草稿直接入库（原实现重新解析导致用户修正被覆盖）

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
