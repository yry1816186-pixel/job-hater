# Job Hater v2 重建 — Completion Report

日期：2026-09-21（2.1 产品团队批次）· 分支：`rebuild`（20 提交，自 a279caf）· 主验证环境：Windows 11 / Python 3.12 / Node 24

## Architecture（最终系统）

单一领域模型 + 单一服务层 + 单一 SQLite，三接口（Web UI / CLI / MCP）零平行逻辑：

- **数据层**：SQLite WAL（check_same_thread=False 适配 FastAPI 线程池），迁移运行器（0001 全量 schema + 0002 增量），FTS5 中文检索（search_text 预分词影子列 + 触发器同步；jieba 主依赖、字符 bigram 兜底；查询词内 token OR、词间 AND，引号成对转义）
- **领域层**：30+ Pydantic 实体（画像簇/证据/偏好 preset/雇主/信源/岗位/匹配/简历版本/投递/面试/Offer/反馈/AI Provider/CoverLetter），13 态投递状态机（applied_confirmed 不在转移表内——只经用户确认门）
- **服务层**：Profile（证据先行，删除可审计）/ Jobs（四层去重+快照+发布超龄过期兜底）/ Matching v2.1（Gate→BM25→六维加权，全可解释；内置同义组种子+硬域封顶+薪资倒挂旗标+词法-语义交叉校验+四档结论）/ Materials（定制简历/求职信/话术/题库/提升计划——本地确定性生成）/ Resume（版本链+provenance+factcheck 六闸）/ Lifecycle（确认门+漏斗跳步）/ Feedback（可重置）/ AI（local-only 默认+keyring+egress 披露+ack_egress 执行门）
- **接口层**：FastAPI（60+ 路由，材料工坊/AI 执行面/DELETE/过滤计数齐备）+ React/TS 十页面（引导清单/表单化简历编辑器+实时预览/看板跳步/材料工坊）+ CLI（12 命令）+ MCP 官方 SDK（16 工具）

## 2.1 产品团队批次（2026-09-21，多子 agent 协作）

用户真实反馈「完全搞不懂逻辑和交互、非常鸡肋」驱动的系统性修复。四个分析子 agent（浏览器认知走查 / spec 接口对照 / v1↔v2 能力差距 / 前端代码级 UX 审计）+ 三个实现子 agent（材料生成层 / benchmark+性能文档 / 安全文档+对抗测试）+ 主 agent 整合接线。

**能力补全**（v1 打磨经验回归 + spec 零遗漏审计驱动）：

| 缺口 | 修复 |
|------|------|
| 确定性材料生成层全丢（定制简历/求职信/话术/题库/提升计划，cover_letters 表无写入方） | `services/materials.py` 五生成器 + 5 路由 + 前端材料工坊（本地零 AI 依赖，只重排/只引用真实条目不编造） |
| AI 执行面三层全缺（AIService.complete 零调用方） | `POST /api/ai/complete`（ack_egress→428+披露；本地模式诚实 executed=false）+ `GET /api/ai/tasks` |
| 匹配防误报打磨全丢 | v2.1：同义组种子 / 硬域封顶 / 城市薪资参考线倒挂 / 余弦交叉校验 / 四档结论 / 发布超龄过期 |
| applied_confirmed 可经通用 transition 绕过确认门 | 移出转移表；transition 一律拒绝；漏斗改为允许跳步（语义蕴含）解决「记一笔投递线性点 4 次」 |
| 接口完备性 40+ 项缺口 | 奖项/证书/单条投递/关联简历/面试完结/岗位状态/DELETE/过滤计数/列表嵌真名 |
| CLI/MCP 覆盖度不均 | CLI 7→12 命令（paste/jobs/applications/transition/confirm-applied）；MCP 10→16 工具 |

**可用性重建**（认知走查发现的问题逐项修复）：

导航按业务主线分组+画像切换器；总览 5 步开始清单；画像页分步编号+每节「这步影响什么」；简历 JSON textarea→表单+实时预览（最大可用性灾难）；看板 ⚡一键待投递（4 击→1 击）；确认投递/Offer 录入模态化；全站中文标签映射（gate/事件/状态/任务）；Toast+确认框；无偏好是引导态非错误态。

**安全审计**（子 agent 对抗测试发现 5 真bug → 全部根因修复+测试转正）：

SPA 路径穿越任意文件读（高）/ 搜索引号 500（高）/ 空白粘贴 500（中）/ 非法状态内部异常回显（低）/ Offer 负薪资（低）——见 SECURITY.md 修复表，19 项对抗用例锁定。

**顺手修的 4 个真 bug**：SQLite 跨线程间歇 500（根因 check_same_thread）；CLI fetch NameError；设置页披露 URL 漏 `?` 静默 404；导入确认入库丢弃用户修正（数据丢失）。

## Replaced（替换/废除的旧能力）

| v1 | v2 | 原因 |
|----|----|------|
| 纯标准库 CLI + Claude Code Skills 为唯一入口 | Web UI 为第一入口，CLI/MCP 为接口层 | 普通用户不该需要理解 Claude Code（§2） |
| JSON 文件存储 | SQLite+迁移+FTS5 | 原型只适合玩具规模（§4） |
| 代码级 SKILL_GROUPS/权重/A-D 阈值/城市薪资线/国企=100/≥1年淘汰 | 画像数据 + 偏好 preset + 可配置 Gate + 数据层种子词表（同义组/参考线，可扩展） | 单用户硬编码不得是产品核心（§1）；打磨经验以数据形态回归 |
| `apply --send` 假发送 | 13 态状态机 + 用户确认门（转移表收口） | 假装成功是错误产品语义（§14） |
| 投递后自动拉黑公司 | 同岗位唯一 + 雇主多岗位并行 | 阻止同公司多岗位是错的（§15） |
| 手写 MCP 旧协议 | 官方 SDK FastMCP | 停止扩展手写协议（§24） |
| bootstrap_upstream.sh 拉上游 main 直接执行 | 无上游运行时依赖 | 可复现性（§20） |
| "全部数据永不上传"错误声明 | 本地存储 vs 远程 AI 出境两分法 + 逐任务披露 + ack_egress 执行门 | 声明必须真实（§13） |
| v1 应届加分/专业对口/企业发展维度 | 六维可配置权重（有意弃用，非静默丢失） | 不预设立场：国企≠更好、应届≠不能投社招（产品决策，记录在案） |

## OSS Fusion

| 项目 | 许可证 | 决策 | 说明 |
|------|--------|------|------|
| JSON Resume（schema 标准） | CC-BY-SA/W3C 社区标准 | ADOPT | 内部简历 sections 结构 |
| wenke-radar | MIT | **代码移植**（米哈游/百度/网易 fetcher） | 重写为 SourceAdapter 契约+限速+故障隔离；文件头保留版权声明；THIRD_PARTY_NOTICES 如实记录 |
| Reactive Resume | MIT | REFERENCE_ONLY | 多服务架构与单 SQLite 本地优先冲突 |
| offerPilot / get_jobs / job-radar | AGPL / PolyForm / 无 | 未复制任何代码 | 许可证不兼容（§23） |

## Verification（实际执行）

| 项 | 命令/方式 | 结果 |
|----|-----------|------|
| 后端测试 | `python -m pytest tests/` | **82 passed**（十六步 E2E、3-persona 匹配、v2.1 特性锁定 5、材料生成 9、对抗 19、状态机新语义、迁移幂等/原子性、MCP 往返） |
| Lint | `python -m ruff check jobhater tests scripts` | 0 error |
| 前端类型+构建 | `npx tsc -b && npm run build` | 通过（gzip ~104KB） |
| 浏览器 UI 走查（2.1 后全链） | Playwright 真实浏览器 | 引导清单→补经历→建偏好→91.2 分「强烈推荐」→⚡待投递→确认投递（模态+渠道）→投后推进→材料工坊 134 字话术+复制——全通过 |
| 安全修复验证 | curl 实测 | 引号搜索 200（原 500）；`..%2f..%2f` 穿越 404（原 200 泄露）；空白粘贴 422（原 500） |
| 真实数据迁移 | `migrate("data")`（9745 真实岗位） | added=9745；二次迁移全去重（幂等） |
| 性能基准 | `scripts/benchmark.py`（20k 合成岗位） | 合成导入 562.8 jobs/s（真实 JD 参考 79 jobs/s，文档诚实校准）；检索最差 P95 37ms；全量匹配 431 jobs/s |
| 真实信源 | 米哈游官网 API live smoke | 259 fetched / 254 ingested |
| CLI 全命令 | doctor/serve/import/paste/jobs/match/applications/transition/confirm-applied/fetch/migrate-v1/backup | 实测通过（jobs 曾有 UnboundLocalError 已修复） |
| 干净安装 | 全新 venv `pip install -e .` → doctor/import/backup/serve | 全通过；SPA 200；数据跨进程可见 |

## Product Walkthrough

新用户：`pip install -e .` → `job-hater serve` → 浏览器 127.0.0.1:8787 →
**总览页「开始清单」**逐步点完（建档→技能→经历→偏好）→ 任意网站复制 JD 粘贴导入
（解析草稿带依据标注，**用户修正不被覆盖**）→ 收件箱看四档结论（强烈推荐/推荐/可考虑/暂缓）
→ 详情页六维依据+gate 中文检查+薪资倒挂提示 → **材料工坊**一键生成定制简历/求职信/
打招呼话术/面试题库/提升计划（全本地确定性生成）→ 收藏进看板 → ⚡一键待投递 →
亲手投递后「我已投递」确认（模态选渠道）→ 面试排期 → Offer 全字段录入与并排比较 →
简历工坊表单化编辑+实时预览 → factcheck 过闸定稿 → 导出 md/html（pdf/docx 可选依赖诚实降级）→
backup / 重启后数据完整。

## Known Limitations（仅真实外部限制）

1. **CI 矩阵尚未在 GitHub 运行**：workflow 已就绪（3 OS × Python 3.10/3.12 + 前端 + 迁移烟测 + Docker 构建），推送后生效；本地已验证 Windows。
2. **AI 深度分析/改写依赖远程 Provider**：本地模式诚实返回不可用提示；确定性材料生成（材料工坊）不依赖 AI 已覆盖主链。
3. **官方信源仅接入 3 家**（米哈游/百度/网易）：SourceAdapter 契约与健康隔离就绪，其余按源渐进接入；粘贴导入是永不失效的主链。
4. **PDF/DOCX 导出需可选依赖**（playwright/python-docx）：缺失时提供可打印 HTML 并如实说明。
5. **English i18n 未实现**：简体中文单语言（面向中国求职者的刻意取舍，列为待办而非声称完成）。
6. **Docker 部署暴露面**：镜像 CMD 绑 0.0.0.0，跨机使用需自行加认证/防火墙（SECURITY.md 部署注意）。
