# 岗位发现四通道（全领域打法）

目标读者：agent 你。核心原则——**任何网站、任何行业、任何类型（实习/校招/社招）的
岗位都能进库**；进库走 `paste_parse`（文本→结构化草稿）+ `jobs_import`（去重入库），
引擎保证字段一致性。你不被限制在任何一个站点里。

组合策略：一次作战至少走两条通道，目标入库 ≥30 条方向相关岗位。
先用通道 B/C 铺量，再用通道 A 补大厂细节，最后通道 D 收私域信息。

---

## 通道 A：引擎内置官方直读源（无登录、公开 JSON）

- `sources_fetch`（MCP）/ `job-hater fetch`（CLI）：wenke 源——米哈游/百度/网易
  **校招**官网公开接口，直接入库。偏校招，量少但官方可靠。
- `job-hater enrich --limit 50`：JD 补全引擎——字节/阿里/腾讯/美团等大厂官方端点
  按已有岗位 URL 补全 JD 正文（分层匹配防串岗，可中断续跑）。
  这是**补全**不是搜索：先有岗位 URL 才有用。
- `job-hater liveness --limit 50`：判活——官方接口+静态页双阶梯确认岗位是否还在招，
  死岗自动标 expired。定期跑，保证库的新鲜度。

## 通道 B：ATS 招聘板公开 API（外企/科技公司全领域，无登录）

大量公司（外企、出海公司、多数科技公司）的招聘站背后是标准 ATS，岗位列表是
公开 JSON API，不需要任何登录。引擎已 LIVE 验证以下端点家族（详见
`jobhater/services/enrich/providers_ats.py`）：

| ATS | 岗位列表 API（公开） | slug 从哪来 |
|-----|---------------------|-------------|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` | 招聘页 URL `job-boards.greenhouse.io/{board}/...` 的 {board} |
| Lever | `https://api.lever.co/v0/postings/{company}?mode=json`（欧洲板用 api.eu.lever.co） | 招聘页 URL `jobs.lever.co/{company}` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{org}` | 招聘页 URL `jobs.ashbyhq.com/{org}` |
| Workday | CXS 端点复杂（tenant+shard），别手拼——把岗位 URL 入库后交给 `job-hater enrich` | `{tenant}.{shard}.myworkdayjobs.com` |

**2026-11 实战注记**：端点本身稳定（duolingo 板 54 岗 LIVE 抓通），但 slug
发现是真瓶颈——盲猜板名（sheingroup/snap/canva）全部空。正确顺序：先搜
「公司名 careers」拿到招聘页 URL，从 URL 提取 slug，再调 API；拿不到招聘页
URL 的公司直接放弃走通道 C。地理/方向错配也是常态（对国内城市求职者，
ATS 板命中低），按宁缺毋滥原则只入库真相关条目。

打法：用户给目标公司清单 → 每家搜「公司名 + careers / 招聘」→ 从招聘页 URL 识别
ATS 类型和 slug → curl 列表 API → 把每条转成 raw job
（title/company/city/salary/description/url，字段名见 tools.md 的 jobs_import）
→ `jobs_import` 一次入库。注意限速：每请求间隔 ≥1s，礼貌抓取。

中国公司常用北森/Moka/自建站，没有统一公开 API——走通道 C 读页面。

## 通道 C：浏览器与搜索（主力，覆盖国内全领域）

你的浏览器能力和搜索引擎在这里是最大优势：

**站点可读性实况（2026-11 LIVE 实测，决定打法）**：

| 站点 | 详情页 | 搜索列表页 | 自动化浏览器 |
|------|--------|-----------|--------------|
| 猎聘 liepin.com | ✅ curl 可读（正文在 `data-selector="job-intro-content"`） | ❌ JS 壳 | ❌ 被弹空白页（勿规避，如实降级） |
| 51job / zhaopin | ❌ JS 壳+拦截 | ❌ | ❌ |
| BOSS 直聘 | ❌ 需登录 | ❌ | ❌ CDP 全拦 |
| 高校人才网 gaoxiaojob.com | ✅ curl 可读（通用启发式命中；标题格式 `岗位名招聘_公司名招聘-平台名`；详情页混有专题/页脚链接会如实失败） | ❌ | — |
| 实习僧 shixiseng.com | ✅ curl 可读（无 JSON-LD，走通用启发式） | ❌ | — |
| 公司官网 careers / 政府人才网 | 多为静态 ✅ | — | — |

**JSON-LD 情报（2026-11-21 实测）**：国内招聘站普遍**不**内嵌
schema.org/JobPosting（高校人才网、实习僧均无）——猎聘详情页例外（内嵌且
提取器已实弹验证）。国内打法的提取主力是**猎聘适配器 + 通用启发式**，
JSON-LD 通道主要面向国际板（greenhouse/lever 等外企 careers 页）。

**主力打法（v3.8 起引擎内置，零手写脚本）**：

1. **搜索引擎拿种子**：`site:liepin.com 武汉 运营`、`岗位名 城市 招聘` 等查询
   找到 1 条招聘详情页直链即可。
2. **（可选）验质量**：`jobs_fetch_preview(url)`——单页提取预览（方法/字段/
   相关职位链接数），失败原因如实返回。
3. **放大采集**：`jobs_harvest(urls_json=["该URL"], depth=1)`——引擎自动抓取 →
   三级提取（猎聘适配器 / JSON-LD JobPosting 通用标准 / 通用启发式）→ 沿
   「相关职位」链接放大（**一次 30-40 条**，v3.7 实战验证 41 抓 40 成）→
   限速 1.2s/页 + robots 遵守 + 去重幂等 → 按来源域名溯源入库。
   返回 added/failed/degraded 明细——degraded 是反爬/robots 拦截的诚实记录，
   换下一个源即可，**绝不规避**。
4. **需要登录的站点**（BOSS 多数操作）：**如实告诉用户请他/她自己登录**，
   绝不替用户输密码；未登录能浏览的部分正常读，按人工节奏。
5. **公司官网 careers 页 / 地方人才网**（全领域社招公告，多为静态页）：
   拿到详情 URL 直接喂 `jobs_harvest`；JSON-LD 适配器对任何内嵌
   schema.org/JobPosting 的站点生效（全行业通用）。非标准页面退回
   `paste_parse` → `jobs_import` 手工链路。

## 通道 D：私域情报（微信/QQ 招聘雷达，仅 Windows）

用户本机微信/QQ 聊天记录里的招聘卡片、公众号文章、群内 JD 截图，引擎有解密+OCR
+深挖链路（密钥只在内存，绝不出境）：

- 用户把文件拖进收件箱目录 → `job-hater inbox --scan`（表格/截图/文档自动解析，
  过目后入库）。
- 公众号文章 URL 深挖由引擎在判活/导入时自动处理（域名严格白名单）。
- 该通道需要用户知情配合（本机数据），绝不在用户未明确要求时触碰聊天记录。

---

## 入库字段速查（jobs_import 的 raw job 形状）

```json
{
  "title": "岗位名（必填）",
  "company": "公司名（必填）",
  "city": "城市",
  "salary": "薪资原文，如 15-25K·14薪",
  "description": "JD 正文（越长越好，匹配打分靠它）",
  "url": "岗位链接（判活与补全都靠它）",
  "recruitment_type": "internship | campus | social（能判断就给，判断不了不给）"
}
```

去重引擎自动做（精确+近似），重复导入幂等——大胆导，不会翻倍。
