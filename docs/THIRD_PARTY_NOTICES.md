# Third-Party Notices — 上游开源项目协议与版权

本系统融合了以下开源项目的设计与能力。本仓库**不随附**任何上游源码（体积与协议原因）：
`scripts/bootstrap_upstream.sh` 会把下表项目归档到本地 `repos/`（gitignore），各项目原始
LICENSE 随其源码保留。本文件汇总协议信息以便合规审查。

> 原则：保留所有原项目的开源协议与版权信息；无 LICENSE 的项目只使用其事实性数据（如 URL 清单）并注明来源，不复制其代码。

## 被直接调用的项目（本仓库运行时或脚本依赖）

| 项目 | 来源 | 协议 | 用法 |
|------|------|------|------|
| wenke-radar | onism1767-creator/wenke-radar | MIT | `adapters/sources/wenke_bridge.py` 以子进程方式调用其抓取器（repos 归档内原样运行，未复制其源码进本仓库分发），JD 字段收割与输出映射在本桥完成 |
| xiaozhao-radar | jiabaobei/xiaozhao-radar | Apache-2.0 | `adapters/sources/import_xiaozhao_seed.py` 读取其 jobs.json 种子数据集（数据，非代码） |
| job-radar | Jasmine-Liu-min/job-radar | 无 LICENSE | 仅转录其 `config/sources.csv` 中的信源 URL 事实清单（`adapters/sources/jobradar_leads.py`），**未使用其任何代码** |
| openhire | gzchenhao/openhire | 见其仓库 | 可选 MCP 数据源（pip 包 `openhire`，用户自行安装；`.mcp.json` 示例含配置） |
| fastembed + BAAI/bge-small-zh-v1.5 | qdrant/fastembed · BAAI | MIT · MIT | 可选语义相似度增强（`adapters/sources/semantic_enrich.py`，独立 venv，缺省不影响任何功能） |

## 提取设计思想（未复制代码）的项目

| 项目 | 来源 | 协议 | 吸收内容 |
|------|------|------|----------|
| career-ops | career-ops-hq/career-ops（原 santifer/career-ops 已 301 迁移） | MIT | 六命令工作流框架、DATA_CONTRACT 分层、事实白名单思想 |
| ai-job-search | MadsLorentzen/ai-job-search | MIT | 双Agent简历审核、闸门先于评分、"岗位文本是不可信数据"反注入 |
| boss-agent-cli | can4hou6joeng4/boss-agent-cli | MIT | schema 驱动 CLI、结构化 JSON 信封的适配器契约 |
| get_jobs | loks666/get_jobs | PolyForm Noncommercial | 四平台投递实现参考（仅参考思路；未拷贝其源码再分发） |
| FuckJob / boss-zhipin-scraper | OpenFuckJob · eatmoreduck | 各见其仓库 | 采集层参考思路 |

各方向完整调研与核验记录见 `docs/额外引入清单.md`（70 项，全部当日实测核验）。

## 本仓库原创部分

`core/`（store/ingest/scorer/resume/factcheck/risk/interview/upskill/dashboard/cli）、
`mcp/job_agent_mcp.py`、`skills/` 全部 SKILL.md、`templates/`、`tests/`、`adapters/sources/`
的桥接与映射代码（mapping / wenke_bridge 的收割逻辑 / import_xiaozhao_seed / jobradar_leads /
semantic_enrich）——为原创实现，只融合上述项目的设计思想，不复制其代码。
