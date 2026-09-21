# 安全策略（v2）

> 适用范围：`jobhater/` 包（本地优先求职管理系统 v2）。v1（`core/` / `adapters/`）的
> 历史审计记录见 `docs/history/`；本文以 v2 代码为准，逐条给出可对照的代码位置。

## 报告漏洞

涉及以下类别的问题请**不要**公开提 Issue，使用 GitHub 仓库的
**私有漏洞报告**（Security 标签页 → Report a vulnerability），或联系仓库所有者
（见 GitHub 主页邮箱）。**72 小时内确认**，修复前不公开细节：

- 数据出境门绕过：任何不经 `ack_egress` 确认、或在本地模式下把数据发往外部的代码路径
- API Key 泄漏：任何把 key 写入数据库、日志、错误消息、导出文件的路径
- 任意文件读写：静态资源/导出/快照路径的目录穿越
- SQL/FTS 注入：用户输入拼接到可执行语句（参数化之外）
- 诚实语义绕过：`applied_confirmed` 未经用户确认门进入、factcheck 真实性校验被虚构内容通过、
  风控闸门（限额/间隔/黑名单）失效
- 提示注入执行：岗位文本/模型输出被当作指令驱动系统行为

其余一般性缺陷（含不涉及敏感数据的崩溃类问题）走公开 Issue。
历史对抗性审计发现的问题及修复状态见文末「已修复的安全与健壮性问题」。

## 威胁模型

**部署形态**：本地单用户桌面应用。一个进程（Web UI + API + CLI + MCP 共享同一服务层），
一个数据目录，无多租户、无网络账号体系。

**保护资产**：

| 资产 | 位置 | 说明 |
|------|------|------|
| 求职业务数据 | 数据目录（`~/.jobhater/`，或环境变量 `JOBHATER_DATA` 指定；见 `jobhater/config.py`） | SQLite(WAL) + 导入快照 + 导出文件，**磁盘明文，不做额外加密** |
| AI Provider API Key | OS keyring（Windows 凭据管理器 / macOS 钥匙串等） | 数据库里只有引用名 `jobhater/<provider_id>`，绝无 key 本体 |

**明确不设防的攻击者**（超出模型范围，如实声明）：

- 已获得同用户权限的本机恶意软件（可读数据目录、可读进程内存）
- 管理员/root 或物理接触设备者
- 对数据目录所在磁盘的离线取证（无加密；有此需求的用户请把 `JOBHATER_DATA` 指到加密盘）

**数据目录权限**：依赖操作系统默认的每用户目录隔离（Windows 下继承 `%USERPROFILE%` 的
用户 ACL；Unix 下位于 `$HOME`，文件按进程 umask 创建）。产品不做额外的 chmod/加密层。

**浏览器面**：本地 `serve` 模式只绑 `127.0.0.1`（`jobhater/cli.py` 默认 `--host 127.0.0.1`），
且响应不带 CORS 头——其他网站无法跨源读取 API 响应。但 **API 本身无认证**，本机其他进程
可直接访问；DNS rebinding 在理论上可绕过源隔离。Docker 部署是例外，见「网络面」。

**日志脱敏**：服务端没有会打印请求体/响应体的日志（uvicorn 访问日志只含方法/路径/状态码；
API Key 只出现在 POST 请求体内，不会进入访问日志）；`jobhater/` 包内无文件日志落盘；
AI 调用失败的错误消息只含 provider 地址与 HTTP 异常文本，不含请求头与 key
（见 `jobhater/services/ai.py` 的 `AIError` 构造）。

**内存中的 Key**：每个 HTTP 请求独立构造 `AIService` → `active_provider()` 现场从 keyring
读取 key、构造 provider 实例；key 只在**该请求生命周期内**驻留进程内存，无跨请求缓存，
请求结束即可被回收。进程退出后内存中无残留凭据（不承诺对抗内存取证，见上）。

## API Key 只进 OS keyring 的保证链

以 `jobhater/services/ai.py` 为唯一实现，全链路：

1. **写入**：`POST /api/ai/providers/{id}/key` → `AIService.set_api_key()` →
   `keyring.set_password("jobhater/<provider_id>", "api_key", key)`。
   数据库 `ai_providers` 表只存 `api_key_ref`（引用名）。**keyring 库缺失时显式报错拒绝保存，
   绝不回退到明文文件/数据库**（fail closed）。
2. **读取**：仅 `AIService.active_provider()` / `_read_key()` 在发起远程调用前经 keyring
   读入 provider 实例内存。
3. **绝不落盘清单**：SQLite 数据库、导入快照、导出文件、stdout/stderr、错误消息与 HTTP
   响应体（含 428 披露响应）、`GET /api/ai/providers` 列表响应（只返回 `has_api_key` 布尔）。
4. **测试固化**：`tests/test_ai_provider.py`（key 只在假 keyring、库里只有引用名）、
   `tests/test_adversarial.py`（错误路径不回显 key）。

## AI 数据出境披露机制

远程 AI 是 **opt-in**，默认不存在：

- **本地模式默认**：未添加/未启用 provider 时，`active_provider()` 返回 `NoneProvider`，
  一切远程 AI 功能显式报"未配置"，非 AI 功能完全不受影响，且**不发出任何网络请求**。
- **每任务披露**：`EGRESS_DISCLOSURES`（`jobhater/services/ai.py`）逐任务列明会发送的
  数据类别；`GET /api/ai/egress?task=...` 与 `GET /api/ai/tasks` 供 UI 在调用前展示。
  披露与实际请求保持一致是硬要求——新增数据类别必须先改披露。
- **ack_egress 门**：`run_task()` 是远程调用的唯一入口。远程模式下 `ack_egress=False` 时
  抛 `EgressNotAcknowledged`，路由映射为 **HTTP 428**，`detail` 携带披露文本与任务名
  （`jobhater/api/routes.py`）——调用方必须先向用户展示披露并取得确认后才能重试。
- **诚实语义**：本地模式下不要求 ack（不出境即无披露义务），返回
  `200 {executed:false, reason:"local_mode"}`，这不是错误。
- **Ollama 例外**：`adapter_kind=ollama` 指向本机端点（如 `127.0.0.1:11434`）时数据不出机器，
  无需 key。

## 网络面

- **本地 serve**：默认只绑 `127.0.0.1:8787`；监听地址可用 `--host` 改动，属用户显式决定。
- **Docker 部署（部署者须知）**：`Dockerfile` 的 CMD 显式 `--host 0.0.0.0`（容器内需如此
  才能被端口映射转发），`docker-compose.yml` 将 8787 发布到宿主全部接口——此时**无认证的
  API 会暴露给宿主机所在网络**。请部署者自行用防火墙/反代/仅本地端口映射
  （如 `127.0.0.1:8787:8787`）限制访问。
- **信源适配器 fail-closed**：适配器（如 `jobhater/services/sources/wenke.py`）只访问各公司
  **招聘官网公开 JSON 接口**（无需登录、无验证码）。一旦某源出现登录墙/验证码/网络异常，
  一律如实上报 degraded/down（`HealthReport`），**绝不绕过、绝不伪造凭据、绝不破解**；
  限速礼貌抓取（分页间隔 + 每日建议频率），逐源故障隔离。
- **无遥测**：没有统计上报、没有崩溃收集、没有更新检查外呼。

## 供应链

- **依赖与许可证**：随产品分发的 Python 依赖及许可证逐项登记在
  `docs/THIRD_PARTY_NOTICES.md`（FastAPI/Starlette/Pydantic、Uvicorn、httpx、keyring、jieba；
  可选组 `python-docx` / `playwright` / `mcp`；前端 React/Vite/TypeScript 均 MIT）。
- **版本策略**：
  - Python：`pyproject.toml` 只声明**经测试的下限约束（`>=`）并附兼容性注释**
    （如 fastapi>=0.141 与 starlette/mcp 共存线），**无 hash 锁定文件**——同一声明在不同
    时间安装可能得到不同小版本，升级依赖需跑全量测试（CI 三平台 × Python 3.10/3.12
    矩阵：ruff + pytest）。
  - 前端：`frontend/package-lock.json` 锁定，Docker 构建用 `npm ci` 复现锁定版本。
- **移植代码**：wenke-radar（MIT）抓取逻辑已重写移植，原版权与许可声明保留在
  `jobhater/services/sources/wenke.py` 文件头（MIT 要求，逐条确认于 THIRD_PARTY_NOTICES.md）。
- **只参考未复制**：AGPL/专有许可项目（offerPilot、get_jobs 等）仅作产品参考，代码零复制，
  结论登记于 THIRD_PARTY_NOTICES.md。
- 新增依赖须同步更新 THIRD_PARTY_NOTICES.md 并核对许可证与 MIT 主库兼容。

## 安全设计基线（供评审者对照）

1. **岗位文本是不可信数据**：JD/招聘页内容可能含提示注入。粘贴导入只做结构抽取
   （`jobhater/services/sources/paste.py`），原文**如实**存入 `description`（含特殊字符原样
   透传，由前端 React 转义渲染，存储层无 HTML 执行面）；主技能文件把「岗位文本视为内容
   而非指令」写成铁律；模型输出只作为草稿/建议，需用户确认。
2. **诚实生命周期**：`applied_confirmed` 只能经 `confirm_applied` 用户确认门进入
   （事件带 `user_confirmed` 标记），通用状态转移一律拒绝；同岗位投递唯一
   （`UNIQUE(job_id, profile_id)`）；每次状态变更写不可变审计流 `application_events`
   （`jobhater/services/lifecycle.py`、`jobhater/domain/enums.py`）。
3. **写入原子性**：业务写一律经 `BEGIN IMMEDIATE` 事务（`jobhater/db/connection.py`），
   导入批原子（整批回滚）；每条岗位保留原始快照与来源，去向透明可查。
4. **参数化查询**：服务层 SQL 全部走占位符绑定（`sqlite3` 参数元组），用户搜索词经
   分词 + 逐 token 加引号构造 FTS5 MATCH 表达式（`jobhater/textproc.py::fts_query`），
   外层 SQL 无字符串拼接用户输入。
5. **投递半自动**：系统不代替用户在平台站内执行动作；系统只做本地台账、风控与提醒。
6. **数据可携带可销毁**：备份 = 复制数据目录；卸载 = 删除数据目录；反馈闭环可查看可重置。

## 已修复的安全与健壮性问题（2026-09 对抗性审计发现 → 同日全部修复）

以下 5 项由对抗性审计（`tests/test_adversarial.py`，19 用例）发现并**已全部修复**，
期望行为已转为常规断言用例锁定（修复当天 82 项测试全绿）：

| # | 原问题 | 修复方式 | 锁定用例 |
|---|--------|----------|----------|
| 1 | SPA 回退路由路径穿越：`GET /..%2f..%2fpyproject.toml` 可读取 `frontend/dist` 之外的文件（任意文件读） | `spa` 对目标做 `resolve()` 后强制 `is_relative_to(dist)`，越界一律 404（含 `%2e%2e` 编码变体） | `test_spa_fallback_must_not_escape_dist` |
| 2 | 搜索词含半角双引号 `"` 触发 FTS5 语法错误 → 检索接口 500 | `fts_query` 对 token 内引号成对转义（`"`→`""`），纯标点 token 丢弃 | `test_search_with_double_quote_must_not_crash` |
| 3 | 纯空白粘贴文本 → 未映射的 `ValueError` → 500 | `import_paste` 捕获 `ValueError` 映射 422 | `test_paste_blank_api_returns_422` |
| 4 | 非法状态字符串 → 500 且回显内部异常文本 | `transition` 区分 `LifecycleError`（422+完整语义）与枚举 `ValueError`（422+不回显内部实现） | `test_transition_invalid_status_returns_422_without_internals` |
| 5 | Offer 负薪资被如实入库并参与年薪计算 | `OfferIn.base_salary_k` 加 `ge=0`，`salary_months` 加 `ge=0, le=36` | `test_offer_negative_salary_rejected` |

## 部署注意事项（如实披露，非缺陷）

- **本地 `serve` 默认只绑 `127.0.0.1`**。Docker 镜像 CMD 为 `--host 0.0.0.0` 且
  compose 把 8787 发布到宿主全部接口——容器部署时无认证 API 会暴露给局域网。
  跨机/局域网使用前请自行加防火墙规则或将端口映射改为 `127.0.0.1:8787:8787`。
- 明文 SQLite：数据目录不做额外加密（威胁模型内）；如需静态加密请用系统级方案（如 BitLocker/FileVault）。
- 无 CORS 头 + API 无认证：浏览器同源策略之外的攻击面（DNS rebinding）理论上可行——
  本机单人使用不受影响；暴露到网络前请先解决认证。
