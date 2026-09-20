# Platform Adapters — 平台适配层（诚实记录验证状态）

> 采集纪律「反爬优先于硬刚」：官方API > 结构化数据源 > 成熟工具封装 > 浏览器自动化 > 硬写爬虫。
> 本目录记录每个平台的**已验证方案**与**降级路径**。未验证的方案如实标注 ⚠️，
> 全部不可用的平台如实标注 🚫 并给出手动导入路径——系统绝不假装采集成功。

## 万能降级路径（永远可用）

粘贴导入：把岗位 JD 原文发给 Claude（自动结构化入库），
或复制 `templates/manual_job_template.json` 填写后：

```bash
python3 core/cli.py ingest --file <你的文件.json> --source <平台名>
```

## 各平台方案与验证状态

### Boss直聘
- `repos/get_jobs`（loks666，Java+Selenium，多平台支持）：⚠️ **需本地图形环境+手机扫码登录，
  未在本环境验证**。本地运行方式见其 README；运行产出的岗位数据可转 JSON 后 ingest。
- `repos/boss-agent-cli`：⚠️ 同上，验证结论见 `boss/README.md`。
- 降级：粘贴导入。

### 猎聘 / 智联招聘 / 前程无忧
- 🚫 当前未发现验证可用的开源一键方案（调研结论见 docs/额外引入清单.md）。
- 降级：粘贴导入（完整功能路径：清洗→过滤→评分→简历全链路可用）。

### 实习僧 / 牛客 / 应届生求职网
- 🚫 同上。这三站以浏览+手动粘贴为主；牛客校招日历可人工核对 deadline 后录入。

### 国聘 / 央企国企官网
- 官方岗位页多为公开静态列表：可用 WebFetch 协助逐条读取（无登录、低频、合规），
  结构化后 ingest。批量爬取不做（尊重站点）。

### 海外备用（LinkedIn / Indeed 等）
- 参考 `repos/ai-job-search`（/scrape 命令体系）与调研清单中的海外自动化项目。
- 仅作思路参考与备用路径；主战场是国内校招。

## 给平台适配开发者的约定

新增适配器 = 一个产出统一 JSON 的脚本 + 本文件更新验证状态。JSON 字段契约见
`templates/manual_job_template.json` 与 `core/ingest.py::normalize`。不接入本契约的
采集脚本不要放进本目录。
