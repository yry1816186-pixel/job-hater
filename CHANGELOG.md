# 更新日志

格式遵循 Keep a Changelog；版本遵循语义化版本（SEMVER）。

## [Unreleased]

## [3.8.0] - 2026-11-21

### Added — 采集引擎化（webharvest：发现管线从 agent 手写脚本升级为引擎能力）
- **新服务 `services/webharvest.py`**：种子 URL → 放大抓取（沿「相关职位」链接
  BFS，depth≤2/max_pages 硬顶）→ 三级提取（猎聘站点适配器 / JSON-LD
  `schema.org/JobPosting` 通用标准 / 通用启发式标 needs_review）→
  按来源域名溯源入库。v3.7 实战验证的提取管线（41 抓 40 成）正式产品化。
- **MCP 23→25 工具**：`jobs_fetch_preview`（单页提取预览，只读）+
  `jobs_harvest`（批量采集入库，dry_run 支持；failed/degraded 明细如实返回）。
- **CLI**：`job-hater harvest --url/--urls --depth --max-pages [--dry-run]`。
- **安全面**：SSRF 闸延续（私网/非 http 零请求）；robots.txt 遵守（每主机缓存，
  403 按全站不抓）；per-host 限速 1.2s 复用判活层 `_ThrottledClient`；
  反爬拦截如实降级绝不规避；三级全败如实返回失败原因不臆造。
- **无偏向设计**：适配器按站点结构不按方向/地域；JSON-LD 适配器对任何内嵌
  schema.org/JobPosting 的站点生效（全行业通用）；幂等（已入库 URL 自动跳过，
  可反复重跑）。
- 新增 18 项测试（三提取路径/@graph 变体/放大器/SSRF 零请求/robots 两态/限速/
  幂等重跑/入库溯源/MCP 往返/CLI 参数）；技能手册改打法（种子→harvest 为标准
  路径，手写提取器指引删除）。

## [3.7.0] - 2026-11-21

### Added — 插件形态（本版主线）
- **`plugin/` 完整插件包**：可装进 Claude Code / Codex CLI / ZCode / DeepSeek Harness
  或任何 Agent Skills 宿主——`SKILL.md` 作战手册（六步工作流+铁律）、
  `references/channels.md` 全领域四通道打法（ATS 公开 API 端点/浏览器+搜索/内置源/
  微信QQ 雷达）、`references/tools.md` 23 工具速查、`/find-jobs` 与
  `/track-applications` 命令、`.claude-plugin/plugin.json` 清单、插件级 `.mcp.json`
  （stdio `job-hater-mcp`）、仓库根 `marketplace.json`（`claude plugin marketplace add`
  可直接装）、`install.ps1`/`install.sh` 一键安装器（装引擎+技能入检测到的宿主）。
- **MCP 四工具补口**（代理从零闭环）：`profile_list` / `profile_create`（简历文本
  自动建档，warnings 如实返回）/ `preset_set`（类型校验，非法值退回不静默吞）/
  `sources_fetch`（内置源抓取+dry_run）。
- **CLI 闭环**：`job-hater profile list|create`（--resume-file 自动入档）与
  `job-hater preset`（--role/--city/--type/--salary-min…）——无 Web 无 MCP 也能
  建档→偏好→导入→匹配全流程。

### Changed — 减防御性阻碍（不降安全底线）
- Host 白名单**默认放行私网/环回 IP 字面量**：局域网设备 IP 直连开箱即用，不再需要
  配 `JOBHATER_ALLOWED_HOSTS`；DNS rebinding 防线不变（攻击面是域名 Host，仍走白名单）。
- 新增 `JOBHATER_GUARD=0` 逃生口：有外层防护（反代/防火墙）的部署可整体关闭本机
  Host/Origin 闸（启动打警告）。
- netguard 域名决策缓存（TTL 5 分钟）：判活大池不再每岗双倍 DNS 解析。

### Verified
- 新增 12 项测试（中间件放开三例/GUARD=0/DNS 缓存/MCP 闭环+边界/CLI 动词/插件包
  结构八项断言），子集 41 passed；全量门见 CI。

## [3.6.0] - 2026-11-21

可部署性硬化批次：目标是「陌生人克隆仓库 → 按README部署 → 打开就能用」。
三路审计（部署面/跨平台残留/安全）修复 + 陌生人视角端到端验证。

### Fixed（部署断点，全部实测复现后修复）
- **Docker 部署 UI 永远 404**：前端 dist 拷贝位置与后端查找逻辑错位
  （`app.py` 按 pip 安装布局解析，dist 在 `/app/frontend/dist`）。
  重构为 `JOBHATER_DIST` env → 仓库布局双候选解析；Dockerfile 用
  `ENV JOBHATER_DIST=/opt/jobhater/dist`；未构建前端时 `/` 返回 200
  引导页 + WARNING（不再静默 404）；
- **裸 `pip install .` 启动即崩**：`python-multipart` 缺失（FastAPI 注册
  UploadFile 路由的硬前置；此前靠 `[mcp]` extra 传递带入，Docker LIVE
  实测容器崩溃重启循环）——补进核心依赖；
- **pip 安装用户 OCR 静默归零**：`ocr_worker.ps1` 未进 package-data，
  补 `services/wechat/*.ps1`；
- **README 三处失实**：Node ≥ 18 → ≥ 20（vite 8 实际要求）；
  `jobs.json` 示例不存在 → 附 `examples/jobs.example.json`；
  注明 CLI `match` 前需先经 Web 向导/API 建画像；`job-hater-mcp` 标注
  需 `[mcp]` extra；
- 新增 `.dockerignore`（防宿主 `frontend/node_modules` 毒死容器构建）；
  Dockerfile/compose 加 HEALTHCHECK（`/api/health`）；
- 版本漂移：`app.py` 硬编码 `3.1.0` → `jobhater.__version__`。

### Added（安全与健壮性）
- **本机 API 双白名单中间件**（`_LocalOnlyMiddleware`）：
  - Host 白名单封 **DNS rebinding**（恶意域名指向 127.0.0.1 后同源拉
    `/api/backup/download` 全库 PII）→ 400；
  - Origin 白名单封**跨源简单请求写**（网页偷触发 demo/seed、inbox/scan
    等无预检端点）→ 403；放行浏览器扩展（chrome/moz-extension）、本机
    任意端口（vite 开发代理）与同源部署；
  - 局域网部署经 `JOBHATER_ALLOWED_HOSTS` 显式放开；`serve --host 0.0.0.0`
    未配置时启动即警告；
- **SSRF 闸**（`services/netguard.py`）：判活 Tier2/公众号深挖的出站 URL
  （三方输入来源）解析 IP 分类，私网/环回/链路本地/云元数据（169.254.169.254）
  与非 http(s) 协议拒绝且不发请求；公众号白名单从子串匹配改 netloc
  点号边界精确匹配（防 `http://evil.com/#mp.weixin.qq.com/s` 绕过）；
- **请求体/上传限额**：粘贴 2MB、岗位导入与简历上传 20MB、备份恢复 200MB
  （Content-Length 预检 + 读入上限，读入内存前 413）；收件箱启用 10MB/
  200 文件闸（超限跳过记账、文件留原处）；
- **错误回显收敛**：`_err` 500 兜底改固定文案 + 服务端 `logger.exception`
  （不再回显内部路径）；AIError→502、ValueError→422 语义映射；
- 扩展 popup：全部 `innerHTML` 动态拼接改 DOM 构建（页面文本无注入面）；
  apiBase 强制回环校验；
- 纯标点搜索词经分词后 FTS 空 MATCH 语法错误 500 → 零结果；
  keyring 无后端（无桌面 Linux）→ 中文 AIError（fail-closed）；
  `keyring_scan` numpy 导入守卫；`job-hater-mcp` 未装 SDK 给可行动指引；
- **CI 升级**：docker job 从只 build 改 build+compose up --wait+UI HTML
  断言+恶意 Host 断言；新增 wheel-smoke job（build→干净 venv 安装→doctor/
  serve 冒烟+package-data 断言）；backend 矩阵补 Python 3.13；
- demo 示例岗 10→13 条（补 UI 设计师/行政专员/制造工艺工程师——全国各
  方向用户打开就有对口示例）；README 新增**平台能力矩阵**；
- 测试 22 例（tests/test_deployability.py）：双白名单/前缀伪装绕过/
  限额/dist 解析/引导页/SSRF 判活不发请求/公众号白名单/纯标点搜索/
  打包资源/收件箱超限。

### Changed（卫生清零）
- `git rm $null .control/`（PowerShell 重定向笔误产物与开发期状态文件）；
  `.gitignore` 防回；删 `config.repo_root()` 死代码；微信 detect 注释
  真实 wxid 改中性；测试夹具与黄金集校名中性化（江南大学→临江大学等）。

### LIVE 验收（2026-11-21，陌生人视角）
- **wheel 陌生人模拟**：git archive 干净副本 → 离线 build wheel → 新 venv
  安装 → `doctor`/`serve` 全通：health 3.6.0、引导页 200、恶意 Host 400、
  跨源写 403、迁移 7 个与 ps1 均在包内；
- **Docker LIVE**：`docker compose up --build -d` → healthy → `/` 返回 SPA
  HTML（修复前此处为 `{"detail":"Not Found"}`）；
- **浏览器向导冒烟（容器内）**：文科运营方向新用户全流程走通——粘贴简历
  文本→解析（教育/技能如实提示补充）→人工补全→建档→定方向（示例岗 13 条
  自动入库）→匹配页出分且方向一致（新媒体运营岗 59.4 居首）。

## [3.5.0] - 2026-11-20

发现层全网化批次：文件收件箱（表格/文档/截图→过目入库）+ QQ 数据面能力层 +
公开 API 信源探针结论。与 3.4.0 同日的连续第三批。

### Added
- **文件收件箱**（`jobhater/services/inbox.py` + `/api/inbox/*` + CLI `inbox`
  + 前端「文件收件箱」页）：拖文件进 `~/.jobhater/inbox/` → 解析 → 暂存 →
  勾选过目 → 入库（source_id=inbox），原件移 processed/ 留证。支持：
  - **xlsx/csv 表格**：列头智能映射（岗位/公司/城市/薪资/链接/截止/学历/届别
    等 12 类别名）+ 表头行自动定位（前 10 行内）；csv 编码自适应
    （utf-8-sig/gb18030）；openpyxl 为可选依赖组 `[inbox]`；
  - **pdf/docx/txt/md 文档**：复用简历导入 extract_text → `parse_jd_text`
    （草稿契约）+ 微信雷达 `recruit.analyze`（置信度规则引擎）双解析择优；
  - **png/jpg 截图**：复用 Windows 内置 OCR（临时目录法防 processed/ 重扫）；
  - 空收件箱扫描不覆写上一批待过目结果。
- **QQ 数据面能力层**（`jobhater/services/qq/`）：p25c 实战配方产品化——
  protobuf 头页+SQLCipher 体解密（`decrypt.py`，含测试造库器与页尾暂存
  往返机制）、环境探测（`detect.py`，云盘重定向目录+blockers 分层）；
  `/api/qq/env`。密钥仅内存流转（mission 脚本曾缓存 qq_key.bin 的行为
  已移除）。
- 测试 9 例（tests/test_inbox_qq.py）：QQ 造库解密往返/错误 passphrase
  拒绝/探测目录隔离、表格映射（utf-8-sig+gb18030+表头偏移+xlsx）、文本
  双解析、OCR 通道接线、勾选导入（缺公司拒收）、API 全链。

### LIVE 验收（2026-11-20，真实库）
- **收件箱一击即中**：用户真实《秋招岗位总表_20260924_学历核验版.xlsx》
  （5840 行）→ 解析 4340 条（7 列映射/1012 家公司/90% 带链接/0 缺公司）→
  用户确认全量入库：**净增 1804 + 精确去重 2536 + 拒收 0**，库
  9890→11784 条；
- 实习僧跨职能大部队 150/150 全带 JD 正文采齐（公司回填与入库收尾中）；
- **QQ 实况边界（负结果如实存档）**：QQ NT 本机已卸载、密钥缓存已清——
  存量 nt_msg.db（3.4MB，2026-10-14 冻结）不可解；nt_data 仅表情/头像
  （803 表情+93 头像）无聊天图，OCR 通道无数据可产。重装 QQ 登录后解密
  链路即可复活；
- **公开 API 探针结论**：国聘（POST 405/GET 返 SPA 壳）与牛客日程（非
  JSON）端点均已漂移，需浏览器 XHR 探针（probe_jd_apis 模式）再定位——
  按预案不硬写适配器，记蓝图待办。

## [3.4.0] - 2026-11-20

computer use 采集批次（发现层复活）：实习僧浏览器采集 LIVE 验证 + BOSS 边界探明 +
产品定位转向全国大众（用户 2026-11-20 明确：不再做设计方向，示例包与偏好通用化）。

### Added
- **浏览器采集工具**（`scripts/harvest_browser.py`，会话级两步走第一步）：playwright
  CDP 接真实 Edge（独立自动化 profile `~/.jobhater/harvest/edge-profile`，cookie 常驻）；
  实习僧全自动（列表锚点提取+详情 JD）；BOSS 协作模式（`--assist`，见下）；护栏：
  只读绝不点击投递/沟通、人类节奏 3-6s、单批硬顶、验证码即停、断点暂存
  （`~/.jobhater/harvest/{site}/staging.json` 原子写）、`--probe` 选择器自检。
- **回填工具**（`scripts/harvest_backfill.py`）：实习僧列表卡容器层级不稳致公司空 →
  详情页 `<title>`（「{岗位}实习招聘-{公司}实习生招聘-实习僧」）回填公司/标题，
  首批 30/30 实测成功；空公司行 ingest 会拒收，本脚本是入库前必经工步。
- **示例岗位包 8→10**：补「新媒体运营（文旅）」「人力资源专员」两条通用岗
  （原 8 条中 7 条技术偏科），新手向导/首页/README 文案同步。

### LIVE 验收（2026-11-20，真实库）
- 实习僧首批 30 条全带 JD 正文（491-1285 字/条）→ 过目后入库 29 条（1 近似去重），
  24 家公司（美团/快手/科大讯飞/金山办公/地平线/字节…）9 城；跨职能大部队
  （运营/数分/前端/算法/HR/市场/测试/游戏策划/安全/财务 十词）批采与回填入库进行中；
- **BOSS 边界探明（重要负结果）**：BOSS 对 CDP 导航**全站拦截**（任何页面→
  about:blank，含首页）；但对浏览器自开标签 CDP **只读正常**（966KB 全量）；
  computer use（a11y 读+OS 级输入）技术验证可行，需专用固定窗口产品化
  （v3.5 `job-hater harvest` 范畴）。反检测对抗明确不做（蓝图立场项修订存档）。
- 偏好纠正：用户在用 preset 的设计向 target_role（交互设计/UX设计）已删
  （删前展示/删后回读验证），保留 5 项 AI 产品/技术向。

### Changed
- 蓝图立场项：BOSS「不抓」修订为「限定解禁（用户授权+护栏不变）」；
- pyproject 3.4.0。

## [3.3.0] - 2026-11-20

v4.0「证据与供给」批次推进（蓝图见 docs/V4_BLUEPRINT.md）：**B1 JD 补全引擎** +
**B2 大厂/国际 ATS provider** + **B3 判活阶梯**。端点知识自 career-ops（MIT，
72k★）移植——该仓库是 v4 蓝图 §2.2 的 E3 借鉴源。B1 治第一病灶「89% 存量岗
无 JD 正文」；B2 把补全面从六路扩到十三路（存量 ~2000 条 other-URL 岗的主干道）；
B3 让死岗在投递前被零成本识别下线（保守判死：误杀真岗严格劣于多一次不确定）。

### Added
- **JD 补全 provider 注册表**（`jobhater/services/enrich/providers.py`）：六路
  官方信源「URL → 岗位标识 → JD 原文」，端点全部活体验证（复现脚本
  `scripts/probe_jd_apis.py`，Playwright 抓真实 XHR）：
  - `byteportal`（字节系门户框架，一套 API 覆盖字节/小米/飞书招聘板）：
    `GET /api/v1/job/posts/{id}` → `description/requirement`；
  - `zhiye`（北森）：`GetJobAdInfo` → `Duty/Require`；
  - `mihoyo`：`ats-portal/v1/job/info` POST；
  - `baidu`：SSR HTML `post-content-*__` 块直读（相对跳转 urljoin 解析 +
    同域限界 + 官方 404 识别为「岗位已下线」）；
  - `netease163`：URL 的 id 是批次非岗位号——列表 API 分页拉取 + 标题归一化
    匹配（防串岗：不匹配记 `no_match`，绝不把别岗正文挂错岗）；
  - `wechat`：复用公众号深挖通道（requests 缺失时如实降级）。
  安全立场：只请求注册表内官方域（点号边界后缀匹配防仿冒域），ID 注入前
  字符集校验，不抓商业招聘板（51job/猎聘等合规红线）。
- **补全引擎**（`engine.py`）：断点续跑（pending 真相源在库内：active+有
  URL+无正文+attempts<3，进程随时可杀重启自动续）；per-host 节流 1.2s；
  网络名额与记账分离（列表页/短链只记账不占名额，防新行饿死可直读旧行）；
  补全后同步重建 FTS search_text；账本表 `jd_enrich_log`（迁移 0005）。
- **B2 大厂官方源 provider×3**（`providers_cn.py`，career-ops 移植）：
  - `alibaba`：详情 `POST /position/detail`（自铸无状态 XSRF 对，career-ops
    实测语义）+ 列表 URL 搜索反查（`/position/search`）；
  - `tencent`：`GET /tencentcareer/api/post/ByPostId` + `/post/Query` 反查；
  - `meituan`：`POST /api/official/job/getJobDetail` + `getJobList` 反查
    （嵌套分页 `{page:{pageNo}}`，平铺页码被服务端静默忽略——career-ops 实测）。
  搜索反查与 163 同构：exact 优先 / fuzzy 须 ≥6 字符且比例 ≥0.6 / 脏 ID 兜底
  走标题搜索；api_title+match_mode 落账可审计。
- **B2 国际 ATS provider×4**（`providers_ats.py`，career-ops 移植，外企在华岗
  常用 Greenhouse/Workday）：`greenhouse`（单岗端点，content 双重编码 HTML 两遍
  解码）、`lever`（descriptionPlain+lists 拼接）、`ashby`（board 级单文档+ID
  大小写不敏感比对）、`workday`（CXS 单岗端点+WAF 关键头+tenant 正则锚定）。
  SSRF 防线同 B1：固定 API host + 路径段严格字符集（禁 `..`/`%2F`）+ 拒绝重定向。
- **B3 判活阶梯**（`jobhater/services/liveness.py`，career-ops liveness 设计移植）：
  - Tier1 provider API 判活：官方业务下线码（阿里 content:null / 腾讯 E1005 /
    美团 jobStatus 001 / Workday 403 S22 / 通用 404/410）→ expired；
    **Lever 404 显式不判死**（机密岗假阴性，career-ops 实测）；
  - Tier2 静态页硬关键词（中英双语保守词表，含 has-been-filled 双 guard、
    closed 复合词 guard）；JS 渲染站正文不足 → 如实 uncertain；
  - 保守立场贯穿：429/5xx/超时/结构不明一律 uncertain；判死只动 active 岗，
    不自动复活；凭据账本表 `job_liveness_log`（迁移 0007：verdict/code/evidence/
    checked_at，复查调度以 checked_at 为准）。
- **API**：`POST /api/jobs/{id}/enrich`（单条，详情页「拉取 JD 原文」）、
  `POST /api/enrichment/run`（批量，limit≤500，支持 provider 定向）、
  `GET /api/enrichment/stats`（覆盖率与逐 provider 账本）；
  新增 `POST /api/jobs/{id}/liveness`、`POST /api/liveness/run`、
  `GET /api/liveness/stats`。
- **CLI**：`job-hater enrich [--limit N] [--provider ID] [--stats]`；
  新增 `job-hater liveness [--limit N] [--job-id ID] [--stats]`。
- **测试**（70 例，新增 46）：六 provider 真实响应 fixture 回放 +
  七个新 provider fixture（按 career-ops 文档化响应形态构造）回放、URL 解析
  安全（仿冒域/列表页/非法 ID/路径穿越）、163 防串岗、断点续跑、重试上限、
  定向运行饿死回归、判活保守性（每 provider 死/活/不确定三态 + 传输故障
  不判死 + guard 词表）、API 端点全链。

### 验收证据（LIVE，真实库 2026-09-23 / 2026-11-20）
- B1 存量批量补全 296+ 条全成功（zhiye 60+199、字节 20、mihoyo 6、163 11），
  后续六路定向清尾累计 602+ 条、~40 万字符 JD 正文入库，覆盖率 24.4%→40%+；
- 节流实测 ~1.2s/条符合设计；百度死链 3 条被正确识别（官方 404）；
- B2 LIVE（2026-11-20，真实库 9890 岗）：**落地修正三处**——存量阿里岗全在
  campus-talent 子域且须用 campus_official_site channel（社招 channel 对 campus
  ID 返回 content=null 假死）；腾讯 634 条存量在 join.qq.com 投递站（无 postId，
  走搜索反查）；空结果容器 null+统计键在场（datas=null/Posts=null）是合法空结果。
  抽查命中：alibaba 6 条真实 JD 入库、meituan 3 条；
- B3 LIVE：美团 jobStatus=001 官方下线码判死全链验证（status=expired +
  reject_reason + job_liveness_log 三处一致）；SPA 站静态页层如实 uncertain；
  迁移 0007 在真实库 v6→v7 干净应用；
- 全量门禁：287 pytest / ruff 全绿。

## [3.2.0] - 2026-09-22

微信本地数据源批次：新增「微信招聘雷达」，自动检测本机微信 4.x（Windows 桌面版）
聊天记录并识别其中全部招聘信息（校招/实习/社招），一键导入岗位库。

### Added
- **微信环境自动检测**（`jobhater/services/wechat/detect.py`）：注册表/ini 配置/
  常见位置三链路自动发现数据目录与账号，无需手工配置；进程/版本/账号/阻塞项
  （blockers）以可行动文案返回 UI。
- **SQLCipher4 变体解密器**（`decrypt.py`）：AES-256-CBC + HMAC-SHA512、
  reserve=80、页 1 salt；密钥 HMAC 终审（无假阳性）+ 首块 AES 快筛；解密输出
  标准 SQLite；测试用加密器支持合成库往返。
- **进程内存密钥检索**（`keyring_scan.py`）：枚举 Weixin.exe 全部可读区域，
  hex 字符串（ASCII/UTF-16）+ 逐字节滑窗熵预筛（numpy）候选，多锚点验证
  （全部消息/联系人库），密钥仅内存流转绝不落盘。
- **微信 4.x 消息解析**（`parser.py`）：`Msg_<md5(talker)>` 每会话表 + Name2Id
  映射 + zstd 压缩内容解压 + 群聊发送者前缀剥离 + 联系人备注>昵称显示名。
- **招聘识别引擎**（`recruit.py`）：纯规则、可解释（每条命中携带证据链）；
  意图词/届别/类型/薪资/城市/学历/投递方式/截止时间抽取；求职者方向负信号
  压分；知名企业裸名词典 + 公司后缀/标签行双模式；置信度透明加权。
- **扫描编排服务**（`service.py`）：后台线程全流程（检测→密钥→解密→解析→
  识别）+ 阶段进度 + 同发送者 3 分钟连发消息合并识别（拆条 JD 拼回整体）+
  结果缓存 JSON（不含密钥）+ purge 一键清除。
- **API**：`GET /api/wechat/env`、`POST /api/wechat/scan|stop|import`、
  `GET /api/wechat/status|results`、`DELETE /api/wechat/data`；导入走统一
  ingest 链（去重/规范化/FTS），source_id=wechat。
- **前端「微信招聘雷达」页**（`WeChatPage.tsx`）：环境状态卡（缺什么怎么补）、
  一键扫描 + 阶段进度动画、结果卡（届别/类型/置信度/关键词过滤，原文与
  识别依据折叠可查）、批量导入岗位库、本地数据一键清除。
- **依赖**：新增 extras `wechat`（pycryptodome/numpy/zstandard）；缺失时
  功能降级为明确安装指引，不阻断应用。
- **测试**：22 条新增用例——手造 WCDB 风格（reserved=80）SQLite 夹具、
  加密往返+篡改拒绝、解析金样（zstd/群聊/过滤）、识别正负样本、离线 E2E
  全链路（密钥不落盘断言）、API 契约。

## [3.0.0] - 2026-09-21

商业级对齐批次：以 Teal / Huntr / Jobscan / Simplify / CareerFlow 为基准做全量差距
分析（两份竞品调研），把商业及格线 20 项全部补齐或超越，无剩余项。

### Added
- **投递看板（Kanban）**：拖拽流转（拖动=合法状态转移，非法转移展示后端允许的
  下一步）、看板/列表双视图、标签系统（0004 迁移）与过滤、批量推进/批量打标、
  每投递联系人 CRM（HR/内推人/面试官，挂投递或雇主）。
- **提醒与跟进**：手动提醒 CRUD + 确定性建议（投递停滞 7 天 / 面试 3 天窗口 /
  Offer 决策期限临近），建议不落库、采纳才生效，同 owner+kind 防轰炸；总览页
  「今日待办」面板（过期红色、完成/撤销）。
- **求职分析页**：漏斗（发现→已投→面试→Offer，已投按 applied_at 事实判据）、
  周活跃双柱图、渠道效果表、Top 雇主、薪资分位洞察（p25/p50/p75，样本数随行
  标注、<5 明示不具参考性）、健康度卡（停滞/临近面试/待决策/截止）。
- **ATS 简历-JD 匹配报告**（对标 Jobscan，确定性规则引擎）：关键词覆盖 70 分
  （硬技能45/软技能10/其他15，JD 内权重=标题×3/要求×2/频次）+ 可解析性 30 分
  （联系方式/标准分节/日期一致性）；**证据链**（每命中词：简历分节+原文摘录+JD
  计数，竞品空白）；**反堆砌黄牌**（技能词只在技能栏无经历支撑时提醒，与 factcheck
  立场一致——不教用户作弊）；计分透明度声明（计入/仅建议分列）。
- **模拟面试练习器**：会话挂真实面试（岗位上下文），逐字稿本地记录永不外发、
  确定性统计（问题/回答/未接问题/字数/时长）、自评打分；AI 复盘走 ack_egress
  数据出境确认门（428+披露→确认重试），本地模式诚实降级。
- **浏览器助手**（Chrome/Edge MV3，`extension/`）：任意网页一键抓 JD（可编辑
  草稿→入库）；网申表单基础字段填充（姓名/电话/邮箱/学校/学历/专业，下拉匹配，
  橙框高亮，**永不自动提交**）；只通信 127.0.0.1，权限最小化。
- **全量备份/恢复**：SQLite 在线备份 API 快照下载；恢复三重校验（文件头/
  integrity_check/schema 版本一致才执行），原位替换 + WAL 截断 + 恢复后外键复核。
- **CSV 导出**：投递/岗位（RFC 4180 + UTF-8 BOM，Excel 直开）；标签分号拼接。
- **命令面板**（Ctrl/⌘+K）：页面直达 + 岗位搜索（防抖）。
- **保存搜索**（岗位页 chips 一键应用）+ **🆕 新增 N 条角标**（对照上次查看，
  后端 `since` 参数只算新入库，可只看新增）。
- **简历双模板**：classic（衬线标准版）+ compact（一页致密版），导出路由
  `template` 参数。
- **设置页数据中枢**：备份下载/恢复上传（确认对话框）/CSV 导出中心。

### Changed
- 投递列表 API 嵌入 tags；标签增删写审计事件（tag_added/tag_removed）。
- 统计口径全量事实化：已投=applied_at 非空（用户确认门唯一写入）、面试/offer
  按事实表存在性，不依赖状态列。

### 竞品对齐说明（诚实记录）
- 商业及格线 20 项：18 项已具备（其中 11 项本批次新建）、1 项天然成立（开源
  免费=无付费墙）、自定义看板阶段 1 项**有意不做**——13 态诚实状态机是产品
  深度而非限制（对标决策见执行记录）。
- 超越点：隐私/本地优先（竞品全为云端 SaaS）、免费导出（超级简历 PDF 收费被
  用户怨恨）、中文生态（竞品全部英语-only）、评分透明+反堆砌（Jobscan 黑盒/
  教堆砌被批）、提醒可达（站内+ICS）、证据链（全类目空白）。

## [2.3.0] - 2026-09-21

真实数据实战批次：迁移内容保全 + 匹配校准 + 简历可用性补全（全部来自
9745 岗/真实画像的实跑发现，修复均带回归测试）。

### Fixed
- **v1 迁移丢内容**（P1）：v1 实际 schema 的经历 `name/highlights`、顶层
  `experience_ev_map` 证据链接、GPA 数值、获奖 `level/work/year`、
  `identity` 联系方式此前全部未迁移；`_source_files` 元数据键混入证据表。
  现按真实 schema 全量映射（经历亮点并入 description，结构如实降级并在
  迁移器文档说明），新增真实 v1 schema 回归测试锁定。
- **批量相关性 BM25 恒空**（P1）：`fts_query` 词组间 AND，没有任何文档能
  同时命中全部词组 → `relevance_score` 自 v2.1.0 起恒为 None（检索层从未
  工作）。相关性打分场景改用 OR 连接（搜索框的 AND 语义不变）。
- 简历渲染：多行 summary（迁移合并产物）压成单 bullet、空 label 输出孤立
  `**`、项目经历 description 渲染与工作经历对齐（MD/HTML 双端）。
- 技能缺口把「2027届校园招聘、正式」类批次词当技能（keywords 假阳性）：
  批次/流程词过滤后不再进入缺口分析与提升计划。

### Added
- **画像联系方式字段**（phone/email，迁移 0003）：API `PATCH /api/profiles/{id}`、
  画像页编辑；写入简历 MD/HTML 头部、JSON Resume `basics.email/phone`、
  求职信落款——修复「导出简历无联系方式不可投」的可用性断点。
- **收件箱「匹配优先」排序**：`GET /api/jobs?sort=match&profile_id=…`
  按已存最近一次匹配结果排序（合格→匹配分→相关性），翻页不重复计算
  （此前每次翻页重跑 ≤500 岗匹配并重写历史）。
- `accept_unknown_city` 偏好 gate 开关（数据层，默认关）：城市缺失岗位
  可选择放行+标注人工核实，与「数据不足不是候选人的错」原则对齐。
- 求职信论据按 JD 相关度重排（最贴岗位的经历先说）+ 句界摘录替代硬截断；
  API 返回 `content_display`（剥离内部 `[ev:]` 标记的可投递人面版本，
  原文仍留锚点供审计）。

### Changed
- **匹配引擎 2.3.0**：BM25 检索相关性按 0.6/0.4 融合进 `skill_match`
  （词法为主、检索印证，防任一单一信号独大），排序 tiebreak 引入相关性；
  金样评测集 22 例全过（evaluate 路径语义不变，仅批量路径校准）。
- `job-hater match` 与 MCP `match_run` 默认评估全部在库岗位（此前默认
  只评前 100/50 条且按入库序）；CLI 输出补 `rel=` 相关性列。

## [2.2.0] - 2026-09-21

前沿机会扫描（F1-F9）落地批次：质量可评估化 + 标准互操作 + 生态文档。

### Added
- **匹配质量黄金评测集**（`tests/golden/matching_golden.json`，22 标注用例）：
  gate 全覆盖 + 同义组/硬域封顶/薪资倒挂/四档结论/交叉校验；文件锁
  engine_version，引擎语义变更强制同步重标注；CI 新增独立质量门禁 job。
  **首跑即抓出存量真 bug**：recency 维度用 `isdigit()` 做日期守卫，ISO 日期
  含连字符恒 False → 一切带日期岗位都被判「发布时间未知」（v2.0 起存在）。
- **JSON Resume 开放标准互操作**：简历导出标准 schema（证据引用以 `_` 扩展
  属性保留）；JSON Resume 文档导入画像簇（技能/经历/教育/项目，同名幂等跳过，
  导入与手工建档同级无免检特权）；往返测试锁定。
- **iCalendar 日历导出**（RFC 5545）：投递截止（提前1天提醒）+面试排期
  （提前30分钟），只导出未来事件，看板一键下载导入系统/Google 日历。
- CI 性能金丝雀 job（benchmark 2000 岗宽松阈值，防量级级回退）。
- `docs/SOURCES.md`：SourceAdapter 插件契约文档（社区贡献信源的入口）。

### Changed
- MCP 兼容 mcp 1.x 与 2.x 双运行时（FastMCP/MCPServer 双路 import，
  pyproject 解除 `<2` 钉版）。

### Fixed
- 匹配 recency 维度对 ISO 日期恒判「未知」（黄金集发现，见上）。

## [2.1.0] - 2026-09-21

产品团队全流程打磨：能力补全 + 可用性重建（真实用户反馈「搞不懂逻辑和交互」的系统性修复）。

### Added（能力补全——v1 打磨经验回归 + spec 零遗漏审计）
- **材料工坊**（`services/materials.py`，本地确定性生成，零 AI 依赖）：岗位定制简历（按 JD 相关度重排，只重排不编造）/ 求职信（证据锚定，cover_letters 表首次有写入方）/ 打招呼话术（~134 字）/ 面试题库（四板块+证据锚点）/ 技能提升计划（14/30/60 天+诚信规则）
- **AI 执行面**：`POST /api/ai/complete`（ack_egress 披露确认门→428；本地模式诚实返回 executed=false）+ `GET /api/ai/tasks` 任务目录；此前 6 类 AI 任务无任何执行入口
- **匹配引擎 v2.1**：内置技能同义组种子（SKILL_SYNONYM_GROUPS）；硬领域词封顶（防「IC后端」词法巧合虚高）；城市薪资参考线倒挂提示（数据旗标非 gate）；词法-语义余弦交叉校验（防关键词堆砌）；四档结论（强烈推荐80/推荐65/可考虑50/暂缓）；无 deadline 岗位发布超 60 天过期兜底
- **接口完备性**（审计 40+ 项缺口）：奖项/证书路由、单条投递读取、关联简历版本、面试完结、岗位状态 PATCH、证据/技能/偏好 DELETE、过滤同口径计数；offers/applications 列表嵌岗位真名（杀 UUID 不可辨识与 N+1）
- **CLI**：`paste`（stdin 粘贴主链）/ `jobs` / `applications` / `transition` / `confirm-applied`
- **MCP**：新增 `applications_create` / `paste_parse` / `preset_get` / `ai_egress_disclosure` / `materials_interview_questions`（10→16 工具）
- 校招官网信源接入（wenke-radar MIT 移植：米哈游/百度/网易）；jieba 升主依赖

### Changed（可用性重建——认知走查 + Nielsen 审计驱动）
- **导航信息架构**：按业务主线重排分组（准备→求职推进→系统）；画像切换器；404 路由；页面标题修复
- **新手引导**：总览页 5 步开始清单（完成态+下一步 CTA）；新用户三步卡；画像页分步编号+每节「这步影响什么」
- **简历工坊**：JSON textarea → 结构化表单 + 实时预览（JSON 降为高级模式）——最大可用性灾难修复
- **投递看板**：⚡一键待投递（4 击→1 击，漏斗跳步语义）；确认投递模态化；Offer 录入全字段模态；空列渲染；事件时间线中文
- **诚实语义收紧**：applied_confirmed 从状态转移表移除——通用 transition 一律拒绝，只能经用户确认门
- 全站中文标签映射（gate/事件/Offer 状态/AI 任务）；Toast 反馈系统；危险操作二次确认

### Fixed
- SQLite check_same_thread 跨线程 500（FastAPI threadpool 间歇性崩溃根因）
- CLI fetch 分支 `con` 未定义 NameError
- 设置页出境披露 URL 漏 `?` 致 404 静默
- 【数据丢失】导入确认入库改为用核对后草稿直接入库（原实现重新解析导致用户修正被覆盖）

## [2.0.0-alpha.1] - 2026-09-20

完整产品重建（v1 视为原型归档于 docs/history/）。

### 重建（Rebuilt）
- **数据层**：JSON 文件 → SQLite（WAL/迁移运行器/FTS5 中文检索：search_text 预分词，jieba 可选 bigram 兜底）；30+ 实体 schema 覆盖画像/证据/偏好/雇主/信源/岗位/匹配/简历/投递/面试/Offer/反馈/AI Provider
- **通用化**：移除全部单用户硬编码（技能词表/权重/阈值/城市薪资线/国企加分/应届假设）→ 下沉为画像数据与偏好 preset；多 persona 匹配回归测试防再犯
- **匹配引擎**：分层 Eligibility Gate（可配置硬约束，透明拒绝原因）→ BM25 相关性 → 六维个性化排序（权重为用户数据；每维分数+依据+不确定性）
- **投递语义**：13 态状态机 + 用户确认门（applied_confirmed 只能由确认进入）；废除 v1"投递即拉黑公司"
- **简历系统**：JSON Resume 兼容 sections + 不可变版本链 + bullet 级 provenance（六档改写类别）+ factcheck 六道闸 + md/html/json/pdf/docx 导出（可选依赖诚实降级）
- **接口层**：FastAPI + React/TS Web UI（九页面）+ CLI + MCP（官方 SDK，10 工具）——同一 services，无平行逻辑
- **AI Provider**：OpenAI 兼容（GLM/DeepSeek/Qwen/Kimi/Ollama）+ Anthropic；默认本地模式；key 只进 OS keyring；逐任务数据出境披露
- **信源**：SourceAdapter 契约 + 注册表 + 信源健康隔离；粘贴导入（任意 JD 文本→结构化草稿→人工确认入库）为主链
- **迁移**：v1 data/*.json 一次性迁移工具（幂等，语义降级如实报告）

### 性能（真实基准）
- 9745 真实岗位导入 599s → 123.5s（批量化+内存去重索引）；检索 2-9ms

## [1.0.0] - 2026-09-20

首个公开版本（v1 原型，已被 2.0 重建取代）。

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
