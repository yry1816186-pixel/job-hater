# Job Hater 插件——把你的 AI 编程助手变成求职作战代理

装进 Claude Code / Codex CLI / ZCode / DeepSeek Harness（dsh）或任何支持
Agent Skills（SKILL.md）的 AI 助手后，对它说**「帮我找工作」**，它会：

1. 读你的简历建档案（技能/经历/教育自动提取，识别不了的如实问你）
2. 问清方向/城市/类型（实习、校招、社招都行——全行业全方向）
3. 多渠道找岗位入库：目标公司 ATS 公开 API、招聘网站浏览、搜索引擎定向、
   微信/QQ 招聘情报（Windows）、大厂官方接口——单次目标 30+ 条
4. 本地匹配排序，每条带分数和依据（为什么合格/为什么被拦）
5. 对选中的岗位生成简历、ATS 关键词扫描、面试题库
6. 跟踪每一笔投递直到 Offer——**绝不替你投递**，投递永远你亲自确认

数据全部只存你本机 SQLite。不代投、不外发、不收集。

## 安装（三步）

```bash
git clone https://github.com/yry1816186-pixel/job-hater.git
cd job-hater/plugin
./install.ps1          # Windows PowerShell（macOS/Linux 用 ./install.sh）
```

安装器会：装引擎（pip，含 MCP）→ 把技能装进检测到的 AI 助手 → 打印 MCP 注册指引。

常用参数：`-Harness claude|codex|zcode|dsh` 只装某一个；`-Dest <目录>` 装进任意
Agent Skills 宿主；`-SkipEngine` 只装技能。装完验证：

```bash
job-hater doctor       # 引擎自检
```

## 各助手单独说明

| 助手 | 技能位置 | 交互入口 | MCP 注册（可选） |
|------|----------|----------|------------------|
| Claude Code | `~/.claude/skills/job-hater/` | 说「找工作」或 `/find-jobs` | `claude mcp add job-hater -- jobhater-mcp`；或 `/plugin marketplace add yry1816186-pixel/job-hater` 后装 job-hater 插件（自带 MCP） |
| Codex CLI | `~/.codex/skills/job-hater/` | 说「帮我找工作」 | 把 `plugin/codex/config-snippet.toml` 并入 `~/.codex/config.toml` |
| ZCode | `~/.zcode/skills/job-hater/` | 说「找工作」 | MCP 设置添加 stdio 命令 `jobhater-mcp` |
| DeepSeek Harness (dsh) | 其技能目录（`./install.sh -d <目录>`） | 说「找工作」 | MCP 设置添加 stdio 命令 `jobhater-mcp` |
| 其他 Agent Skills 宿主 | 任意技能目录 | 说「找工作」 | 任何 MCP 宿主：stdio `jobhater-mcp` |

不装 MCP 也能用：技能手册里每个 MCP 工具都有等价的 `job-hater` CLI 命令，
助手会用 shell 走完全流程。

## 为什么比让 AI 自己写脚本找强

- **不臆造**：JD 解析/去重/匹配/判活全是本地确定性实现，模型只负责发现和解读
- **端点已验证**：ATS 公开 API（greenhouse/lever/ashby）、大厂官方接口都是引擎
  LIVE 验证过的，现写脚本第一脚就踩在失效端点上
- **越用越准**：岗位库、匹配历史、投递漏斗都在本机累积，脚本每次从零开始
- **有依据**：每个匹配分都给 gate 解释，每条 JD 都留原文可回查
- **守规矩**：绝不代投、AI 出境先披露、不规避反检测——脚本不管这些

## 常见问题

- **pip 装 git 包慢/失败**：配置代理 `pip config set global.proxy http://127.0.0.1:7890`
  （换成你的代理），或先 clone 仓库后用 `./install.ps1 -Engine local` 从本地装。
- **`job-hater` 命令找不到**：Python Scripts 目录不在 PATH；可临时用
  `python -m jobhater.cli doctor` 代替。
- **微信/QQ 雷达**：仅 Windows；需要用户本机微信/QQ 数据与知情配合，密钥只在内存。
- **AI 增强功能**：默认关闭（本地模式全流程可用）；开启需配 API Key（只存系统钥匙串）。

## 目录结构

```
plugin/
├── .claude-plugin/plugin.json   # Claude Code 插件清单
├── .mcp.json                    # 插件自带 MCP 服务器（stdio: jobhater-mcp）
├── skills/job-hater/
│   ├── SKILL.md                 # 技能主文件（作战手册）
│   └── references/              # channels.md 四通道打法 / tools.md 工具速查
├── commands/                    # /find-jobs、/track-applications 两个命令
├── codex/config-snippet.toml    # Codex MCP 注册片段
├── install.ps1 / install.sh     # 一键安装器
└── README.md                    # 本文件
```
