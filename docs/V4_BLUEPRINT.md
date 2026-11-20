# job-hater v4 蓝图 · 商业级重构（证据驱动）

> 2026-09-22。触发：用户实战反馈「在实际使用中感受不到便利性、性能、功能、可拓展性和智能性，不如直接调用开源工具，甚至不如豆包」。
> 本文档 = 三路外部调研（商业产品对标 / GitHub 开源情报 / 智能感产品范式，共 200+ 次检索与页面抓取）× 内部诊断的收敛结论。
> 事实边界：标注 `FACT`（有证据）/ `INFERENCE`（推断）/ `TARGET`（目标态）。

---

## 0. 方案选择（为什么是「深化三支柱」而不是别的）

| 方案 | 结论 | 理由 |
|------|------|------|
| A. 继续横向加功能 | 否决 | 功能广度已够（对标矩阵里六维打分/看板/雷达/练习器齐全），用户仍觉得不如豆包——问题不在广度在深度、可信度与主动性 |
| B. 全面对话化（做成聊天产品） | 否决 | 在聊天赛道与豆包/ChatGPT 正面对抗是劣势区；外部证据：聊天框赢在输入自由，结构化工具的反超点在证据、主动、成品（Perplexity/Cursor/Ramp 模式） |
| C. 三支柱深化：数据自己长 / 结论带证据 / 主动干活 | **选定** | 每一支柱都直击一个「豆包结构性做不到」的点：本地隐私数据（微信雷达）、跨月持久状态（库+画像）、定时任务与可审计产物 |

## 1. 诊断：为什么跑不赢豆包（根因清单，按权重排序）

1. **匹配没有输入**（R1，第一病灶）`FACT`：秋招快照 9745 岗中 89% 无 JD 正文（`秋招岗位清单_待过目.md` 自述），六维+BM25 实际打在标题上。豆包拿到的是用户亲手贴的完整 JD——我们在无输入状态下输出的精确分数，输给豆包基于全文的自然语言分析。
2. **分数没有证据感**（R2）`FACT`：输出「62.1 / 84.6」这类裸分数，未展开到「简历第 X 条经历 ↔ JD 第 Y 句要求」的原文对。外部结论：无出处的分数=不透明=不可信，与 LLM 自然语言解释对比必输（JobRight 被批「无解析器透明层」同理）。
3. **感知延迟与交互形态**（R3）`INFERENCE`：操作是「点按钮→等→跳结果」，无流式、无状态词、无 Stop。实测数据：流式界面让感知延迟下降约 80%，300ms 首字比 4s 首字更主导体感（ai-tldr.dev / webdeveloper.com）。
4. **表单对抗自由输入**（R4）`INFERENCE`：用户想问「我该不该投这个」，工具只给筛选器和排序下拉。对照实验（SINTEF, IJHCS 2022）：自由文本输入给用户显著更强的灵活感。正确姿势是「自由输入、结构化输出」（Perplexity 模式）。
5. **不主动**（R5）`FACT`：所有动作都等用户驱动；快照是死的（无增量源、无判活）。Cognition 2026-05：过半 Devin 会话由事件/定时任务异步触发——「它替我上班」是智能感的核心来源。
6. **画像不成长**（R6）`FACT`：一次性建档+手动改。ChatGPT Memory 的产品答案：自动更新 + 用户可编辑纯文本 + 每条推断带来源。
7. **产出不是成品**（R7）`FACT`：生成物停在网页/MD，未闭环到「材料直接落盘并挂到投递卡片」。
8. **匹配科学层缺位**（R8）`FACT`：BM25+规则，无技能本体归一（React↔Vue 邻接关系不可知）、无语义召回/重排；标题岗无「职业推断+低置信标注」补救（业界做法：JobBERT 标题嵌入→ESCO 职业→标准技能集）。
9. **匹配质量无评测门控**（R9）`FACT`：改权重/改 prompt 无回归保障。先例：career-ops eval-golden（离散档位一致率 ≥80% 门控 + fixture 离线回放）。
10. **生态位错位**（R10）`INFERENCE`：与聊天产品对立而非互补。MCP 已有 19 工具（好底座），但「本地数据+定时任务+结构化产物」这层聊天产品够不着的能力没有产品化。

## 2. 外部调研核心情报（全部 2026-09-22 实查）

### 2.1 胜负手：工具必须在五点上碾压聊天框（landthisjob.com，作者 100+ 申请实测）

① 速度：工具 60 秒/申请 vs 聊天 10-15 分钟（面试率 4%→8%）；② 零幻觉约束：只从用户既有素材「检索+排序+给理由」，禁止改写原文（消灭 voice mismatch）；③ 持久资产：要点库/岗位库跨申请复利，聊天框每次都是新手；④ 分数反馈回路：改一个词分数会动（Teal 第一哇时刻）；⑤ 系统化动作：追踪/提醒/雷达——聊天框不主动、不记得、不跟进。

### 2.2 GitHub 可直接借鉴的先例（star/维护状态经 API 实查）

| 借什么 | 来源 | 用途 |
|--------|------|------|
| **ATS 公开 API 直读 JD 全文**：Greenhouse `boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}`、Lever `api.lever.co/v0/postings/{slug}/{id}`、Ashby `api.ashbyhq.com/posting-api/job-board/{org}`、Workday `{tenant}.{shard}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}` | career-ops（72k★，日更活跃，MIT） | JD 补全第一优先级，零 token 零浏览器 |
| **中国大厂官方接口**：阿里 talent.alibaba.com、腾讯 careers.tencent.com、美团 zhaopin.meituan.com、字节/飞书 `*.jobs.feishu.cn/api/v1/search/job/posts`（列表 payload 自带全文）；北森 zhiye.com / Moka / 百库 ATS 基类（新公司=子类+3行配置） | wenke-radar（45 源，230 测试 fixture 回放）+ career-ops | 治「中国岗位+JD 正文」的主干道 |
| **provider 防御性规范**：稳定原生 job_id、reported_total 对账（防只抓首页）、页数硬顶、429 指数退避+jitter、Retry-After 夹紧、空体返回 [] 结构错才 throw | career-ops ADDING_A_PROVIDER.md | 采集层健壮性，逐条抄 |
| **liveness 三级阶梯判活**（ATS API→静态 HTML→Playwright），404/410→expired，Lever 404 假阴性显式建模 | career-ops liveness-*.mjs | 死链标过期，省 LLM 开销 |
| **diff 式简历定制 + 三态验证**（existing/jd_added/rejected，master resume 作真相锚点，禁止整篇重生成） | Resume-Matcher（28.5k★，Apache-2.0） | 材料工坊升级：可审计、零幻觉 |
| **eval-golden 黄金评测集**：{jd, label:{档位,分数}} 强模型冻结标注，档位一致率≥80% 门控，fixture 离线回放 $0 跑 CI，NaN 案例单列 | career-ops eval-golden.mjs | 匹配质量回归门控 |
| **结构化输出加固**：括号匹配提取 + 2 次降温重试；API key 加密存储明文只在内存 | Resume-Matcher | LLM 调用层 |
| **纯 Python checkpoint 状态机**（管线每阶段序列化断点续跑） | LangGraph checkpoint 思想（不引入依赖） | 批量补全/抓取的中断恢复 |
| **append-only 事件日志** | OpenHands（88.7k★） | MCP/agent 动作可审计可重放 |
| **免费数据源**：HN Algolia（实测可用）、Remotive/Jobicy API（限额见调研记录）、Campus2025 等校招汇总仓库、Telegram 公开频道 t.me/s/<频道> | 多源 | 增量供给 |

### 2.3 智能感公式（乘法关系，任一项≈0 则整体≈0）

**即时反馈 × 证据可核 × 主动痕迹 × 记得我 × 产出即成品 × 可纠错**

落地手法：全链路 SSE 流式+通俗状态词（「正在读第 3 段经历…」）+真 Stop 保留已生成部分；分数→维度→「简历 span ↔ JD span」可点击证据卡；本机定时任务每日简报；画像=自动更新+可编辑纯文本+每条推断标来源；AI 产出一律先进人审草稿队列（Ramp 模式：AI 提案、人批准、状态可追踪）。

### 2.4 匹配科学最小本地方案（CPU、无云）

ESCO/O*NET 技能本体 + 自建中文标题→职业映射（LLM 离线生成+人工抽查）→ bge-small-zh int8 ONNX 嵌入 + sqlite-vec 召回 top-50 → 精排二选一（本地 cross-encoder 或已配 LLM 结构化重排，输出 span 对直接喂证据卡）→ 用户采纳/拒绝/投递行为回流为隐式标注，月度本地轻量 LTR 校准。标题岗：标题嵌入→最近 ESCO 职业→标准技能集替代，UI 显式标注「基于职业推断，置信度低」。

## 3. 北极星

> **从「求职管理系统」变成「每天替你上班的求职分析师团队」。**

三支柱（= 豆包结构性做不到的三件事）：
1. **数据自己长**——增量官方信源 + 存量 JD 补全 + 判活，库永远是新鲜的全文库；
2. **结论带证据**——每个分数可展开到简历与 JD 的原文对，可审计、可纠错；
3. **主动干活**——定时简报、草稿队列、人审采纳，工具追着用户走而不是等用户来点。

生态位：不替代聊天产品，做聊天产品够不着的那层（本地隐私数据、持久状态、定时任务、可审计结构化产物），并经 MCP 双向开放——任何 MCP 客户端（含豆包/Claude/IDE）可指挥本地求职库。

## 4. 批次规划

### v4.0 「证据与供给」批次（最高杠杆，直击 R1/R2/R5/R7/R9）

- **B1 JD 补全引擎** ✅（2026-09-23 LIVE_VERIFIED）：URL→ATS 类型识别 → API 直读；
  路径段严格字符集校验+拒绝跨 host 重定向（防 SSRF）；请求层节流（含翻页/跳转链内部）；
  断点续跑 checkpoint（真相源在库内 jd_enrich_log）；fixture 回放测试 29 例。
  落地修正（按真实 URL 分布，非理论清单）：六路 provider=byteportal（字节/小米/飞书同一
  门户框架，一套 API）/zhiye 北森/mihoyo/百度 SSR/netease163 批次列表+标题分层匹配
  （exact 优先，fuzzy 须 ≥6 字符且比例 ≥0.6，api_title+match_mode 落账可审计）/
  wechat 复用深挖通道。存量批量补全 LIVE：602+ 条成功、~40 万字符 JD 正文入库，
  覆盖率 24.4%→~40%+（清尾批次后）；对抗评审 P0/P1 全修（串岗防线/attempts 闸/
  节流下沉/装饰性断言）。
- **B2 中国大厂官方源 provider** ✅（2026-11-20，v3.3.0，LIVE_VERIFIED）：阿里/腾讯/
  美团三路详情直读 provider + 列表 URL 搜索反查（163 模式：exact 优先/fuzzy 过闸/
  api_title+match_mode 落账）；同批移植国际 ATS 四路（Greenhouse/Lever/Ashby/
  Workday 单岗详情端点，外企在华岗）。端点规格来源 career-ops（MIT）providers/*.mjs +
  liveness-api.mjs（2026-07/11 活体验证），fixture 按其文档化形态构造，移植见
  `jobhater/services/enrich/providers_cn.py` / `providers_ats.py`。字节飞书已在 B1
  覆盖（byteportal）；BaseScraper 整板扫描（fetch 全板入池）留后续批次。
  LIVE 落地修正（2026-11-20，真实库 9890 岗）：①存量阿里岗全在 **campus-talent
  子域**且 channel 必须用 campus_official_site（社招 channel 对 campus ID 返回
  content=null 假死——不修正会把活岗判死）；②腾讯 634 条存量在 join.qq.com
  投递站（resume.html?k=令牌），只走搜索反查；③空结果容器为 null+统计键在场
  （阿里 campus datas=null / 腾讯 Posts=null）是合法空结果，不是结构漂移。
  LIVE 抽查：alibaba 6 条 ok（真实 JD 入库）、meituan 3 条 ok、官方下线码
  （美团 001）判死路径全链验证（status/reject_reason/判活账本三处一致）。
- **B3 判活阶梯** ✅（2026-11-20，v3.3.0，LIVE_VERIFIED）：`jobhater/services/liveness.py`——
  Tier1 官方业务码（阿里 content:null / 腾讯 E1005 / 美团 jobStatus 001 /
  Workday 403 S22 / 通用 404/410；**Lever 404 显式不判死**=机密岗假阴性）+
  Tier2 静态页中英硬关键词（has-been-filled 双 guard、closed 复合词 guard）；
  保守立场贯穿（429/5xx/超时/正文不足一律 uncertain，判死只动 active 岗不自动
  复活）；账本 `job_liveness_log`（迁移 0007）；CLI `liveness` + API 三端点。
  百度 /jobs/404 哨兵判死保留自 B1（LIVE 验证 3 条）。LIVE 抽查（2026-11-20）：
  美团 001 官方下线码判死全链验证（status=expired + reject_reason + 账本一致）；
  SPA 站（zhiye 等）静态页层如实 uncertain；4655 条 active 待批扫。
- **B4 匹配证据卡**：精排输出「简历证据 span ↔ JD 原文 span」对，收件箱/详情页可展开。
- **验收（量化）**：JD 正文覆盖率 11%→≥70%（B1 后 active 池 ~40%，≥70% 需 B2 列表页
  存量搜索反查落地——FACT：URL 形态统计 other=2303 条在 B2 范围）；每条匹配分可展开到
  原文对（B4）；匹配排序黄金评测集扩至 ≥50 例且档位一致率 ≥80% 入 CI；批量补全中断后
  可续跑（单测+LIVE 双验证 ✓）。

### v4.1 「智能感」批次（R3/R4/R5/R6/R7）

- **C1** 全链路流式（SSE）+ 状态词 + 真 Stop + 乐观 UI；
- **C2** 每日求职简报：本机定时任务扫库+雷达 → 「今日新增匹配 Top5 + 一句理由 + 证据」（能力边界如实：生成配置与验证说明，启用由用户决定——总指令 §3.2）；
- **C3** 画像 memory 化：交互自动并入 + 纯文本可编辑 + 每条推断带来源；
- **C4** 草稿队列：定制简历/打招呼话术/面试答案一律先进人审队列，采纳才挂看板（与「绝不代投」铁律同构）；
- **C5** 自由输入框：自然语言问任何求职问题 → 检索本地库作答 → 结构化卡片输出（自由输入、结构化输出）；
- **C6** 产出即成品：材料直接导出 docx/pdf 落 exports 并挂到投递卡片。
- **验收**：>1.5s 的操作零白屏（全走流式/骨架屏）；简报可一键跳转证据卡；画像每条自动推断可溯源可纠正。

### v4.2 「匹配科学」批次（R8）

- **D1** 技能本体层（ESCO/O*NET + 中文映射）；**D2** 嵌入召回+精排两段式；**D3** 标题岗职业推断+低置信标注；**D4** 行为回流月度校准。
- **验收**：召回 top-50 命中率对比 BM25 基线提升可测；标题岗全部带置信标注；黄金集门控不回退。

### 横切（随批次）

MCP 工具扩至 JD 补全/简报/证据查询；append-only 事件日志记录 agent 动作；评测集与 fixture 全部入 CI。

## 5. 立场项：明确不做（附证据）

| 不做 | 依据 |
|------|------|
| 自动投递 | AIHawk（31.6k★）被媒体围剿后整个放弃投递场景转型；JobRight 实测自动海投触发 ATS 量级异常检测被静默降权，精投 5-10 个回复率高 4-6 倍；Sonara 关停。与「绝不代投」铁律一致 |
| 抓 BOSS直聘/智联/前程无忧 | wenke-radar 列为「不可协商的合规红线」；career-ops 以 robots.txt 主动拒绝拉勾/Glassdoor——只走公司官网官方接口 |
| 反检测/规避爬虫对抗 | career-ops 判「技术和道德上都出局」；Playwright 仅限用户主动触发的单条高价值岗位只读公开页 |
| 实时面试答案 | 检测对抗+作弊争议，面试鸭以「不作弊」作品牌站位；本地做「事后复盘」而非实时答案 |
| 云端规模爬库/内推网络/行业大数据报告 | 云端规模生意，本地优先架构不该做（Simplify 50+板、JobRight 800万库的反例映射） |

## 6. 证据索引

- E1 `秋招岗位清单_待过目.md` §数据局限：「本批快照 89% 无 JD 正文」（FACT，本仓库）
- E2 landthisjob.com/blog/why-i-stopped-using-chatgpt-to-tailor-my-resume（聊天框三失败模式+五胜负手）
- E3 github.com/career-ops-hq/career-ops（ARCHITECTURE.md / ADDING_A_PROVIDER.md / liveness-api.mjs / eval-golden.mjs，经 zread 全文读取）
- E4 github.com/onism1767-creator/wenke-radar（README+CONTRIBUTING 全文：45 源、四类 ATS 基类、合规红线）
- E5 github.com/srbhr/Resume-Matcher（docs/agent/architecture：diff 定制+三态验证）
- E6 ai-tldr.dev / webdeveloper.com（流式与感知延迟实验数据）；SINTEF IJHCS 2022（自由输入实验）
- E7 cognition.com/blog（异步触发过半）；ramp/cpapracticeadvisor（AP agents HITL 模式）；help.openai.com Memory FAQ
- E8 arXiv 2503.02056 CareerBERT / 2109.09605 JobBERT / 1809.06473 LinkedIn LTR（匹配科学）
- E9 atsverification.com/blog/jobright-ai-review-2026（无透明层批评+海投降权实测）
- E10 三份调研原始报告全文（2026-09-22 会话产出，结论均已内联本文档；未核实项在各报告中逐一声明）
