# 第三方声明（Third Party Notices）

## 随产品分发的依赖（pip 安装，各自许可证约束）

| 依赖 | 用途 | 许可证 |
|------|------|--------|
| FastAPI / Starlette / Pydantic | Web API 框架与数据校验 | BSD-3 / BSD-3 / MIT |
| Uvicorn | ASGI 服务器 | BSD-3 |
| httpx | AI Provider HTTP 客户端 | BSD-3 |
| keyring | 操作系统钥匙串访问 | MIT |
| jieba | 中文分词（FTS 检索增强；已列为主依赖，缺失时退回字符 bigram） | MIT |
| python-docx（可选 `[docx]`） | 简历 DOCX 导出 | MIT |
| Playwright（可选 `[pdf]`） | 简历 PDF 导出（无头浏览器打印） | Apache-2.0 |
| mcp（可选 `[mcp]`） | MCP Agent 接口（官方 SDK） | MIT |

前端构建依赖（npm）：React / React Router / Vite / TypeScript（均为 MIT）。

## 已移植进本仓库的第三方代码（附原始许可证条款）

| 来源 | 许可证 | 移植内容 | 本仓库位置 |
|------|--------|----------|------------|
| [wenke-radar](https://github.com/andylove-me/wenke-radar) | MIT | 米哈游/百度/网易校招官网公开 JSON API 抓取逻辑（fetch_mihoyo / fetch_baidu / fetch_netease 的请求构造与响应解析），重写为本项目 `SourceAdapter` 契约并加入限速与失败隔离 | `jobhater/services/sources/wenke.py`（文件头保留原始版权声明） |

MIT 许可证要求保留原版权与许可声明：上述文件头部已保留 wenke-radar 的 Copyright 声明与 MIT 许可文本指针，特此确认。

## 未随产品分发、仅作设计参考的项目

以下项目在本产品 v2 重建调研中被评估过；**当前代码库不包含它们的任何代码**。

| 项目 | 许可证 | 参考结论 |
|------|--------|----------|
| xiaozhao-radar | Apache-2.0 | 校招种子数据思路参考 |
| Reactive Resume | MIT | 完整多服务应用（PostgreSQL+Gotenberg），与单 SQLite 本地优先架构冲突，仅参考编辑器交互；本产品采用 JSON Resume schema 标准 |
| career-ops / ai-job-search / boss-* / openhire | MIT 等 | 产品范围与工作流参考 |
| offerPilot | AGPL-3.0 | 仅产品参考；未复制任何代码（AGPL 与 MIT 主库不兼容） |
| get_jobs | PolyForm Noncommercial | 仅阅读参考；未复制任何代码 |
| job-radar | 无 LICENSE | 仅使用其公开的信源名单事实信息 |

历史版本（v1 Campus-Job-Agent）的上下游记录见 `docs/history/` 的迭代与收尾报告。
