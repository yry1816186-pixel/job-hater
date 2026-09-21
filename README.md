# Job Hater · 本地求职全流程管理系统

帮你管理整个求职生命周期：**发现岗位 → 判断匹配 → 管理岗位 → 准备材料 → 投递确认 → 跟踪进度 → 面试 → Offer 比较**。

- **本地优先**：全部业务数据（画像、岗位库、投递记录、简历）只存在你自己的机器上，没有账号、没有云端、没有遥测。
- **3 分钟冷启动**：打开页面就是新手向导——上传你现成的简历（PDF/DOCX/TXT/MD/JSON Resume）自动解析建档，核对一遍、定个方向就能跑匹配；没有简历文件也能粘贴文本或手动填写。
- **事实不可捏造**：简历里每条经历都能溯源到你确认过的证据原文；机器事实校验不过闸，不能定稿。
- **匹配可解释**：每个岗位给出「结论 + 依据 + 不确定性」，不是一个神秘的 87 分；硬性淘汰原因逐条透明。
- **投递由你完成**：系统不代替你向任何平台发送投递。「已投递」状态只在你亲手确认后生效。
- **诚实的降级**：没配 AI、没网、信源挂掉——功能如实降级并告诉你原因，绝不假装成功。

适用于应届校招、实习、社招、转行；技术/产品/设计/运营/职能/制造/科研各类方向——所有"应届生该怎样""国企更好"式的预设都在你的偏好设置里，不在代码里。

## 快速开始（3 分钟）

**方式一：Docker（零工具链，推荐初次体验）**

```bash
docker compose up --build
```

浏览器打开 <http://127.0.0.1:8787>。

**方式二：Python 直跑**（Python ≥ 3.10；Web 界面需再装 Node ≥ 18 构建一次前端）

```bash
git clone https://github.com/yry1816186-pixel/job-hater.git
cd job-hater
pip install -e .
cd frontend && npm install && npm run build && cd ..
job-hater serve
```

**方式三：只用命令行**

```bash
job-hater doctor                          # 环境自检
job-hater import jobs.json                # 导入岗位 JSON
job-hater paste --save --url <链接> < jd.txt   # 从 stdin 粘贴 JD 解析入库
job-hater match --profile <画像ID>        # 匹配排序榜
job-hater fetch --companies mihoyo        # 官网信源抓取（米哈游/百度/网易）
```

## 第一次使用（3 步）

克隆下来的库是空的、也不带任何数据——打开 Web 界面就是**新手向导**：

1. **上传简历**：拖入你的简历文件（PDF / DOCX / TXT / MD / JSON Resume；图片型扫描件会如实报错并给出替代路径），系统解析出基本信息、学历、经历、项目、技能；没有文件就粘贴文本或手动填写。
2. **核对保存**：自动解析可能不准——草稿逐项可编辑（识别不到的会明确提示），你核对后才落库。配置了 AI Provider 的用户可选「AI 精解析」（走数据出境确认门，本地模式自动跳过）。
3. **定方向**：目标角色 / 城市 / 薪资底线 / 批次，或跳过（建通用偏好）。可顺手导入 8 条示例岗位，先看看匹配分和 ATS 报告长什么样。

之后回到总览页：**进岗位**（「导入岗位」粘贴任何网站的 JD 原文，或装浏览器助手一键抓取）→ **看匹配**（收件箱按你的偏好排序，详情页六维依据 + ATS 报告）→ **备材料 & 管投递**（材料工坊一键生成定制简历/求职信/面试题库，投递看板拖拽推进）。总览页的开始清单会标注剩余步骤。

进阶能力（对齐顶尖商业产品，全部本地免费）：

- **投递看板**：Kanban 拖拽 + 列表双视图、标签过滤、批量推进、联系人 CRM（HR/内推人/面试官挂到投递）、每投递跟进提醒（停滞 7 天自动建议跟进，采纳才落库）。
- **求职分析**：漏斗（发现→已投→面试→Offer 转化率）、周活跃、渠道效果、薪资分位洞察（样本数随行标注，样本不足明说）。
- **模拟面试练习器**：对具体岗位开练（逐字稿本地记录、确定性统计、自评打分；可选 AI 复盘走数据出境确认门）。
- **浏览器助手**（Chrome/Edge）：任何网页一键抓 JD 入库 + 网申表单基础字段填充（橙框高亮、**永不自动提交**）。
- **命令面板**：`Ctrl/⌘+K` 全局直达页面与岗位。
- **暗色模式**、保存搜索、新增岗位角标、全量备份/一键恢复、CSV 导出。

## 命令行参考

| 命令 | 作用 |
|------|------|
| `job-hater serve` | 启动本地 Web 服务（127.0.0.1:8787） |
| `job-hater doctor` | 数据库/迁移/信源自检 |
| `job-hater import <file.json> [--source manual]` | 导入岗位（数组或 `{"jobs":[...]}`） |
| `job-hater paste [--save] [--url <链接>]` | stdin 读 JD 原文：默认出草稿，`--save` 入库 |
| `job-hater jobs [--q 关键词] [--limit N]` | 检索列出岗位 |
| `job-hater match --profile <id> [--preset <id>] [--limit N]` | 匹配排序（默认评估全部在库岗位；输出含匹配分与检索相关性） |
| `job-hater applications --profile <id>` | 列出投递跟踪（含岗位名） |
| `job-hater transition <投递ID> <状态> [--note]` | 推进状态（漏斗内可跳步） |
| `job-hater confirm-applied <投递ID> [--channel 官网]` | 用户确认已投递（投后阶段唯一入口） |
| `job-hater fetch [--companies mihoyo,baidu,netease] [--dry-run]` | 官网信源抓取 |
| `job-hater migrate-v1 <旧data目录>` | 从 v1 (Campus-Job-Agent) JSON 一次性迁移 |
| `job-hater backup` | WAL checkpoint 后复制数据库到 exports/backups |

## AI 能力与隐私（如实说明）

**本地存储**：业务数据默认只保存在你设备的数据目录（`~/.jobhater`，或环境变量 `JOBHATER_DATA` 指定的位置）。

**远程 AI 处理是可选项，默认关闭**：

- 不配置任何 AI Provider = 本地模式：建档、导入、检索、匹配、投递管理、简历编辑与 Markdown/HTML 导出全部可用；PDF/DOCX 导出安装可选依赖后可用。
- 你主动添加并启用 Provider（智谱 GLM / DeepSeek / Qwen / Kimi / OpenAI 兼容 / Anthropic / 本地 Ollama）后，AI 增强功能才会工作。每次调用前，「设置与隐私」页明确显示该类任务会发送什么类别的数据。
- API Key 只存操作系统钥匙串（Windows 凭据管理器 / macOS 钥匙串），数据库与日志永不保存。
- Ollama 本地端点 = 数据不出机器。

**没有遥测、没有统计上报、没有崩溃收集。**

## 岗位从哪来

| 信源 | 方式 | 状态 |
|------|------|------|
| 粘贴导入 | 任何网站复制 JD 原文 → 解析草稿 → 确认入库 | ✅ 主链，永远可用 |
| **微信招聘雷达** | 「微信招聘雷达」页一键扫描本机微信 4.x（Windows）聊天记录：自动检测环境 → 进程内存提取数据库密钥（仅内存流转，绝不落盘）→ 解密 → 识别校招/实习/社招信息（公司/岗位/届别/薪资/截止/投递方式，证据链可查）→ 勾选导入岗位库 | ✅ 本地处理，需微信已登录运行；安装 `pip install -e ".[wechat]"` |
| 浏览器助手 | Chrome/Edge 扩展：页面上一键抓 JD 入库（见 `extension/README.md`） | ✅ |
| 文件导入 | JSON（数组或 `{"jobs":[...]}`） | ✅ |
| 校招官网适配器 | `job-hater fetch --companies mihoyo,baidu,netease`（米哈游/百度/网易校招官网公开 JSON API，来自 MIT 项目 wenke-radar 的移植，限速抓取） | ✅ 已接入 |
| 官方 API 适配器 | `SourceAdapter` 插件契约（capabilities/health_check/rate_policy/provenance） | 契约就绪，适配器按源渐进接入 |
| MCP | `job-hater-mcp`（19 个工具，官方 SDK：新增 stats_overview/stats_salary/ats_scan/reminders_list） | ✅ Agent 增强层 |

导入统一走分层去重：同源同 ID → 跨源同岗键 → 近似标题（标记人工复核）→ 内容指纹。每条岗位保留原始快照与来源。

## 数据与备份

- 数据目录：`~/.jobhater/`（SQLite WAL + 导出文件）。备份 = 复制该目录；卸载 = 删除该目录。
- **一键备份/恢复**（设置与隐私页）：下载完整数据库快照；恢复前做三重校验
  （SQLite 文件头 / integrity_check / schema 版本一致），不通过不动原库。
  API Key 按设计只存系统钥匙串，不在备份内。
- **CSV 导出**：投递记录 / 岗位库（UTF-8 BOM，Excel 直接打开）；简历导出
  Markdown / JSON / JSON Resume / HTML（双模板）/ PDF / DOCX 全免费。
- **简历互操作**：导出 JSON Resume 开放标准（简历页 `json-resume` 格式，或
  `GET /api/resume-versions/{id}/json-resume`）；也可从其他工具导出的 JSON Resume
  导入画像（新手向导上传 .json 文件，或设置页「导入 JSON Resume 文件」）——
  导入与手工建档同级，同样要经证据确认与事实校验。
- **日历导出**：投递看板右上「📅 导出日历」→ iCalendar 文件（投递截止+面试排期，
  含提前提醒），可导入系统日历 / Google Calendar。
- 从 v1 迁移：`job-hater migrate-v1 <旧data目录>`（画像/证据/偏好/岗位全量迁移，v1 的"投递即拉黑公司"等旧语义不迁移并如实报告）。

## 技术架构

```
┌─ 接口层 ────────────────────────────────┐
│ Web UI (React/TS) · CLI · MCP (官方SDK) │
├─ 应用服务层（唯一业务逻辑）──────────────┤
│ Profile / Jobs / Matching / Resume /    │
│ Lifecycle / Feedback / AI Provider      │
├─ 领域层 ────────────────────────────────┤
│ 30+ Pydantic 实体 · 状态机 · 枚举       │
├─ 数据层 ────────────────────────────────┤
│ SQLite(WAL) · 迁移 · FTS5中文检索       │
│ (search_text 预分词: jieba可选/bigram)  │
└─────────────────────────────────────────┘
```

一套 domain、一套 services、一套 DB——UI/CLI/MCP 不存在平行逻辑。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。开发环境：`pip install -e ".[dev]"` + `cd frontend && npm install`；测试 `python -m pytest`； lint `python -m ruff check jobhater tests`；前端 `npx tsc -b && npx vite build`。

## 许可

MIT。第三方依赖与设计参考见 [docs/THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md)。
