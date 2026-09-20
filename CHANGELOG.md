# 更新日志

格式遵循 Keep a Changelog；版本遵循语义化版本（SEMVER）。

## [1.0.0] - 2026-09-20

首个公开版本。

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
