# 第三方声明（Third Party Notices）

## 随产品分发的依赖（pip 安装，各自许可证约束）

| 依赖 | 用途 | 许可证 |
|------|------|--------|
| FastAPI / Starlette / Pydantic | Web API 框架与数据校验 | BSD-3 / BSD-3 / MIT |
| Uvicorn | ASGI 服务器 | BSD-3 |
| httpx | AI Provider HTTP 客户端 | BSD-3 |
| keyring | 操作系统钥匙串访问 | MIT |
| jieba（可选 `[segment]`） | 中文分词（FTS 检索增强） | MIT |
| python-docx（可选 `[docx]`） | 简历 DOCX 导出 | MIT |
| Playwright（可选 `[pdf]`） | 简历 PDF 导出（无头浏览器打印） | Apache-2.0 |
| mcp（可选 `[mcp]`） | MCP Agent 接口（官方 SDK） | MIT |

前端构建依赖（npm）：React / React Router / Vite / TypeScript（均为 MIT）。

## 未随产品分发、仅作设计参考的项目

以下项目在本产品 v2 重建调研中被评估过；**当前代码库不包含它们的任何代码**。

| 项目 | 许可证 | 参考结论 |
|------|--------|----------|
| wenke-radar | MIT | 28 个官方招聘接口抓取器架构作为 SourceAdapter 设计参考；适配器尚未接入 |
| xiaozhao-radar | Apache-2.0 | 校招种子数据思路参考 |
| Reactive Resume | MIT | 完整多服务应用（PostgreSQL+Gotenberg），与单 SQLite 本地优先架构冲突，仅参考编辑器交互；本产品采用 JSON Resume schema 标准 |
| career-ops / ai-job-search / boss-* / openhire | MIT 等 | 产品范围与工作流参考 |
| offerPilot | AGPL-3.0 | 仅产品参考；未复制任何代码（AGPL 与 MIT 主库不兼容） |
| get_jobs | PolyForm Noncommercial | 仅阅读参考；未复制任何代码 |
| job-radar | 无 LICENSE | 仅使用其公开的信源名单事实信息 |

历史版本（v1 Campus-Job-Agent）的上下游记录见 `docs/history/` 的迭代与收尾报告。
