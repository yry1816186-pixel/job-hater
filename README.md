# Campus-Job-Agent · 2027届秋招自动化求职系统

一套运行在 Claude Code 中的本地求职系统：岗位采集 → 校招过滤 → 五维评分 →
定制简历（双Agent校验）→ 投递风控 → 进度追踪 → 面试准备 → 技能冲刺。
**全部数据本地存储，无任何云端上传逻辑。**

## 环境配置

| 依赖 | 要求 | 用途 |
|------|------|------|
| Python | ≥ 3.10（开发环境 3.14） | 核心引擎，**仅标准库，零第三方依赖** |
| git | 任意近期版本 | 上游开源项目源码管理 |
| Claude Code | 任意近期版本 | 技能加载、对话交互 |
| （可选）采集底座 venv | 磁盘 ~100MB | `fetch` 信源采集（wenke-radar 依赖：requests/bs4/pycryptodome/openpyxl） |

```bash
# 第一次用（三步）：
python3 core/cli.py profile --init        # 1. 生成画像模板 → 填入你的真实信息（或把简历发给 Claude 对话式建档）
python3 core/cli.py profile --validate    # 2. 自检通过（✅ 证据锚点完整）后再继续
python3 core/cli.py fetch --env all       # 3. 采集岗位：官方接口(wenke) + 校招种子 + 渠道线索
python3 core/cli.py search                # 出你的专属评分榜

# （可选）启用 MCP：本项目已带 .mcp.json，在该目录启动 Claude Code 会自动加载；
# 全局使用可复制 skills/ 到 ~/.claude/skills/
```

## 六个命令

| 命令 | 干什么 | 示例 |
|------|--------|------|
| `/profile` | 导入/更新个人信息、简历、作品集 | 把新实习证明丢给Claude："帮我更新画像" |
| `/search` | 搜岗位、评分排序 | "帮我找南京的AI产品校招岗" / `search --job <id>` |
| `/apply` | 定制简历+求职信+打招呼话术 | "给XX公司这个岗出投递材料" / `apply --job <id> --send` |
| `/interview` | 面试题库+模拟面试 | "下周XX公司二面，帮我模拟" |
| `/pipeline` | 投递进度看板 | "现在投递情况怎么样了" |
| `/upskill` | 技能缺口+冲刺学习计划 | "我还差什么？30天怎么补" |

底层命令（Claude 自动调用，也可手动）：

```bash
python3 core/cli.py profile --init            # 从模板建档（不覆盖已有画像）
python3 core/cli.py fetch --env all           # 信源采集（wenke,xiaozhao,leads 可单选；--only 调试单源）
python3 core/cli.py sources                   # 信源健康面板（渠道线索+采集健康）
python3 core/cli.py ingest --file templates/manual_job_template.json --source manual  # 导入岗位
python3 core/cli.py search                    # 评分榜
python3 core/cli.py search --job <id>         # 单岗五维详情
python3 core/cli.py apply --job <id> --send   # 生成材料+记录投递（过风控才生效）
python3 core/cli.py interview --job <id>      # 题库
python3 core/cli.py pipeline                  # 看板（终端版）
python3 core/cli.py dashboard                 # 离线HTML看板（漏斗/分布/源健康，浏览器打开）
python3 core/cli.py upskill --job <id>        # 学习计划
python3 core/cli.py blacklist [--add 公司名]   # 黑名单
```

## 岗位数据从哪来（按可靠性排序）

1. **官方接口直连（`fetch --env wenke`）**：复用 wenke-radar（MIT）28 个官方招聘接口抓取器
   （腾讯/字节/阿里/百度/小米/美团/小红书/B站/米哈游等 + 北森/飞书ATS/Moka/百库平台），
   零浏览器、零登录、限速每日一次。首次运行 `bash adapters/sources/setup_env.sh` 装采集底座。
2. **校招种子（`fetch --env xiaozhao`）**：xiaozhao-radar（Apache-2.0）的 27届校招项目线索集
   （公司+批次+报名入口，约 900 条当前批次有效线索）。
3. **渠道线索（`fetch --env leads`）**：job-radar 信源清单转写（39 个渠道：国聘/国家大学生就业平台/
   央企专栏/高校就业网等），仅作线索索引，不自动入库。
4. **粘贴导入（永远可用）**：把 JD 原文发给 Claude，或填 `templates/manual_job_template.json` 后 `ingest`。
5. **MCP**：`.mcp.json` 已配置本地 server（`add_job` / `score_job_text` / `search_jobs` / `get_job` / `fetch_sources` 等9个工具）。

平台站内采集（Boss直聘等需浏览器会话+扫码的方案）状态见 `docs/降级路径说明.md`——
**系统绝不假装某个采集方案可用**。榜单中 🧭 标记 = 项目级线索（公司+批次入口），投递前先点开链接确认具体岗位。

## 反捏造机制（本系统的灵魂）

- 画像里每条事实锚定 `evidence_index`（可溯源到 `data/profile/raw/` 原始材料）；
- 生成的简历/求职信/话术每条经历带 `[ev:ID]` 引用，`core/factcheck.py` 做
  **引用覆盖 / 证据存在 / 数字溯源** 三项硬校验，不过闸 = 不交付；
- 审核Agent（LLM）逐条对照证据核验 + 机器 factcheck 双保险；
- 审核会拦截一切虚构：编造经历、虚构量化数据、"使用过"写成"构建了"。

## 投递风控（确定性强制）

- 单平台每日 ≤ 25 份；消息间隔 ≥ 30 秒；敏感时段（默认22:00-08:00）禁投；
- 自动黑名单防重复投递同一家企业；
- 风控拦截时明确告知原因，绝不静默放行（`core/risk.py`）。

## 目录结构

```
campus-job-agent/
├── Campus-Job-Agent.SKILL.md   # 主技能入口（六命令路由+总规则）
├── README.md
├── core/                       # 核心引擎（纯标准库，零网络代码）
│   ├── store.py ingest.py scorer.py resume.py factcheck.py
│   ├── risk.py interview.py upskill.py dashboard.py cli.py
├── skills/                     # 六大子技能 + jobmatch-ai
├── mcp/job_agent_mcp.py        # 本地MCP server（stdio，9工具）
├── adapters/                   # 平台/信源适配（含诚实验证状态，详见 adapters/sources/README.md）
├── templates/                  # 随仓库分发的建档/录入模板
├── scripts/bootstrap_upstream.sh  # 上游项目归档拉取（repos/ 不入库）
├── data/                       # 全部本地数据（gitignore：画像/岗位库/投递台账只存在你机器上）
├── docs/                       # 治理/引入清单/反思报告/降级说明/迭代报告
├── tests/                      # 自运行测试脚本
└── .github/                    # CI（3×Python矩阵 + 数据本地性/个人事实静态检查）
```

## 合规与免责声明

- 本项目**不提供平台站内自动投递**：真实站内动作需你在自己的浏览器会话中完成，
  系统只做采集、评分、材料生成、风控检查与本地台账（半自动设计，见 docs/降级路径说明.md）。
- 招聘平台的服务条款普遍限制自动化访问；使用任何采集能力前请自行确认平台条款与当地法规，
  账号风控风险由使用者自担。本项目仅用于个人求职目的（部分上游依赖为非商业许可）。
- 采集优先走官方公开接口，限速每日一次；抓不到的渠道如实标注，不破解、不伪装。
- **个人数据零上传**：`data/` 整体被 gitignore，核心引擎无网络代码（CI 有静态检查强制）。
  模型下载（可选的语义增强）仅从模型源下载权重，不发送你的任何数据。

## 注意事项

- 首次使用先跟 Claude 说"看看我的画像"，确认材料齐了再开始投；
- 简历投出去之前自己再读一遍——机器守真实底线，品味判断永远归你；
- 风控参数可在 `data/config.json` 调整，但**不建议放宽**（账号安全第一）；
- 上游开源项目各自的协议与版权信息见 `docs/THIRD_PARTY_NOTICES.md`。
