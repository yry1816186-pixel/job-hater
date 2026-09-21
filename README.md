# Job Hater · 本地求职全流程管理系统

帮你管理整个求职生命周期：**发现岗位 → 判断匹配 → 管理岗位 → 准备材料 → 投递确认 → 跟踪进度 → 面试 → Offer 比较**。

- **本地优先**：全部业务数据（画像、岗位库、投递记录、简历）只存在你自己的机器上，没有账号、没有云端、没有遥测。
- **事实不可捏造**：简历里每条经历都能溯源到你确认过的证据原文；机器事实校验不过闸，不能定稿。
- **匹配可解释**：每个岗位给出「结论 + 依据 + 不确定性」，不是一个神秘的 87 分；硬性淘汰原因逐条透明。
- **投递由你完成**：系统不代替你向任何平台发送投递。「已投递」状态只在你亲手确认后生效。
- **诚实的降级**：没配 AI、没网、信源挂掉——功能如实降级并告诉你原因，绝不假装成功。

适用于应届校招、实习、社招、转行；技术/产品/设计/运营/职能/制造/科研各类方向——所有"应届生该怎样""国企更好"式的预设都在你的偏好设置里，不在代码里。

## 快速开始（3 分钟）

**方式一：Python 直跑**（Python ≥ 3.10）

```bash
git clone https://github.com/yry1816186-pixel/job-hater.git
cd job-hater
pip install -e .
# 构建前端（需要 Node ≥ 18）
cd frontend && npm install && npm run build && cd ..
# 启动（本地 Web 界面 + API 同进程）
job-hater serve
```

浏览器打开 <http://127.0.0.1:8787>。

**方式二：Docker**

```bash
docker compose up --build
```

**方式三：只用命令行**

```bash
job-hater doctor                          # 环境自检
job-hater import jobs.json                # 导入岗位 JSON
job-hater paste --save --url <链接> < jd.txt   # 从 stdin 粘贴 JD 解析入库
job-hater match --profile <画像ID>        # 匹配排序榜
job-hater fetch --companies mihoyo        # 官网信源抓取（米哈游/百度/网易）
```

## 第一次使用（5 步）

打开 Web 界面后，总览页有一个**开始清单**，跟着点就行：

1. **建档**：「我的画像」按步骤 1-5 组织——学历经历 → 技能（含同义词） → 事实证据 → 求职偏好；**联系方式（手机/邮箱）在画像头部，会写进简历与求职信落款**。每节开头都写明「这步影响什么」。
2. **设方向**：「求职偏好」默认**不限批次**（避免悄悄过滤），按需收窄：目标角色、城市、薪资底线、经验上限、毕业届数。
3. **进岗位**：「导入岗位」→ 粘贴任何网站的 JD 原文 → 核对草稿 → 入库（**你核对修改的字段不会被覆盖**）。粘贴是主链入口：即使所有自动化信源都不可用，粘贴永远可用。
4. **看匹配**：「岗位收件箱」每个岗位带分与四档结论（强烈推荐/推荐/可考虑/暂缓），排序可在「最近入库 / 匹配优先」间切换（匹配优先按最近一次匹配结果：合格 → 匹配分 → 相关性）→ 详情页看六维依据与硬性 gate 检查（含疑似薪资倒挂提示）。
5. **备材料 & 管投递**：详情页「材料工坊」一键生成**岗位定制简历 / 求职信 / 打招呼话术 / 面试题库 / 技能提升计划**（全部本地确定性生成，不用 AI）；「收藏并开始跟踪」后去投递看板推进——准备漏斗可一键跳步，**「我已投递」只在你亲口确认后生效**。

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
| 文件导入 | JSON（数组或 `{"jobs":[...]}`） | ✅ |
| 校招官网适配器 | `job-hater fetch --companies mihoyo,baidu,netease`（米哈游/百度/网易校招官网公开 JSON API，来自 MIT 项目 wenke-radar 的移植，限速抓取） | ✅ 已接入 |
| 官方 API 适配器 | `SourceAdapter` 插件契约（capabilities/health_check/rate_policy/provenance） | 契约就绪，适配器按源渐进接入 |
| MCP | `job-hater-mcp`（15 个工具，官方 SDK） | ✅ Agent 增强层 |

导入统一走分层去重：同源同 ID → 跨源同岗键 → 近似标题（标记人工复核）→ 内容指纹。每条岗位保留原始快照与来源。

## 数据与备份

- 数据目录：`~/.jobhater/`（SQLite WAL + 导出文件）。备份 = 复制该目录；卸载 = 删除该目录。
- **简历互操作**：导出 JSON Resume 开放标准（简历页 `json-resume` 格式，或
  `GET /api/resume-versions/{id}/json-resume`）；也可从其他工具导出的 JSON Resume
  导入画像（`POST /api/profiles/{id}/import/json-resume`）——导入与手工建档同级，
  同样要经证据确认与事实校验。
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
