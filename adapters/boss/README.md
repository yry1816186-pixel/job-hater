# Boss直聘适配器 — boss-agent-cli 对接方案

> 指定数据源：`can4hou6joeng4/boss-agent-cli`（MIT，Python ≥3.10，schema 驱动，
> stdout 只输出 `{ok, data, pagination, error, hints}` JSON 信封，`boss schema` 为能力真源）。
> 源码已归档：`repos/boss-agent-cli/`。

## 验证状态（2026-09-20，本部署环境实测）

| 环节 | 状态 | 说明 |
|------|------|------|
| 源码归档 | ✅ | repos/boss-agent-cli（394 files） |
| `uv` 工具链 | ✅ | uv 0.12.17 已装 |
| **boss-agent-cli v2.0.0 安装** | ✅ | `uv tool install "boss-agent-cli[mcp]"` 完成，`boss` / `boss-mcp` 已在 PATH |
| **`boss-mcp` MCP server（77 工具）** | ✅ 握手验证 | 已写入项目 `.mcp.json`；`boss doctor` 实测：到 zhipin.com/zhaopin.com 网络 OK，29 项结构化自检正常 |
| 登录态（`boss login`） | ⚠️ 需本地有头环境 | 扫码登录属人工动作；`boss doctor` 自动给出恢复动作清单 |
| 浏览器内核（patchright chromium） | ⚠️ | 本环境限速未下载（~170MB）；本地执行 `uv run patchright install chromium` |
| 岗位数据 → 本系统评分 | ✅ 契约已通 | 见下方数据流 |

## 本地完整启用步骤（用户图形环境）

```bash
uv tool install boss-agent-cli
patchright install chromium
boss login                      # 浏览器扫码，会话存 ~/.boss-agent
boss search --keyword "AI产品" --city 南京 --welfare "双休,五险一金" --sort score
```

## 数据流：采集 → 评分（已验证的契约段）

```bash
# 1) boss-agent-cli 导出岗位（JSON 信封）
boss search --keyword "AI产品经理" --format json > raw.json
# 2) 转成 ingest 契约（字段映射 core/ingest.py::normalize）
python3 - <<'EOF'
import json
raw = json.load(open('raw.json'))
jobs = raw.get('data') or raw
rows = [{
    "title": j.get("jobName") or j.get("title"), "company": j.get("brandName") or j.get("company"),
    "city": j.get("cityName") or j.get("city"), "salary": j.get("salaryDesc") or j.get("salary"),
    "description": j.get("jobDesc") or j.get("description", ""), "url": j.get("jobUrl", ""),
    "experience_required": j.get("jobExperience"), "source_platform": "boss",
} for j in (jobs if isinstance(jobs, list) else [])]
json.dump(rows, open('jobs_boss.json', 'w'), ensure_ascii=False)
EOF
# 3) 入库（自动清洗/去重/校招过滤）
python3 core/cli.py ingest --file jobs_boss.json --source boss
```

MCP 方式（免写映射脚本）：本地完成 `uv tool install "boss-agent-cli[mcp]"` 后，
把下面配置加入项目 `.mcp.json` 的 `mcpServers`，Claude 即可直接调用其 77 个工具搜索岗位，
再经 `campus-job-agent.add_job` 入库——两条路都在：

```json
"boss-agent": { "command": "/home/ubuntu/.local/bin/boss-mcp", "args": [], "env": {} }
```

## 风控红线（继承自 skills/job-finder + core/risk.py）

- 单平台每日投递 ≤25 份、消息间隔 ≥30s、敏感时段禁投——boss-agent-cli 的投递动作
  必须在 `apply --send` 通过风控闸门之后再人工驱动，绝不让自动化绕过本地台账。
