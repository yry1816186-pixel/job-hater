#!/usr/bin/env bash
# Job Hater 插件安装器（macOS / Linux）
# 用法：
#   ./install.sh                  # 装引擎 + 技能装进所有检测到的 AI 编程助手
#   ./install.sh -H claude        # 只装 Claude Code（可选：codex/zcode/dsh/all）
#   ./install.sh -d <目录>        # 装进任意 Agent Skills 宿主的技能目录
#   ./install.sh -s               # 只装技能（引擎已装好时）
set -euo pipefail

HARNESS="all"; DEST=""; SKIP_ENGINE=0; ENGINE=""
while getopts "H:d:s" opt; do
  case "$opt" in
    H) HARNESS="$OPTARG" ;;
    d) DEST="$OPTARG" ;;
    s) SKIP_ENGINE=1 ;;
    *) echo "用法：$0 [-H all|claude|codex|zcode|dsh] [-d 技能目录] [-s]"; exit 1 ;;
  esac
done

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$PLUGIN_DIR")"
SKILL_SRC="$PLUGIN_DIR/skills/job-hater"

echo "== Job Hater 插件安装 =="

# ---------- 1. 引擎 ----------
if [ "$SKIP_ENGINE" -eq 0 ]; then
  command -v python3 >/dev/null || command -v python >/dev/null || { echo "❌ 未找到 python（需要 3.10+）"; exit 1; }
  PY="$(command -v python3 || command -v python)"
  if [ -z "$ENGINE" ] && [ -f "$REPO_ROOT/pyproject.toml" ]; then ENGINE="local"; fi
  [ -z "$ENGINE" ] && ENGINE="git"
  if [ "$ENGINE" = "local" ]; then
    echo "→ 从本仓库安装引擎（含 MCP）"
    (cd "$REPO_ROOT" && "$PY" -m pip install "./[mcp]")
  else
    echo "→ 从 GitHub 安装引擎（含 MCP）"
    "$PY" -m pip install "jobhater[mcp] @ git+https://github.com/yry1816186-pixel/job-hater.git"
  fi
  command -v job-hater >/dev/null && job-hater doctor || "$PY" -m jobhater.cli doctor
fi

# ---------- 2. 技能安装 ----------
install_skill() {  # $1=名称 $2=技能根目录
  mkdir -p "$2"
  rm -rf "$2/job-hater"
  cp -R "$SKILL_SRC" "$2/job-hater"
  echo "✅ 技能已装入 $1：$2/job-hater"
}
if [ -n "$DEST" ]; then
  install_skill "自定义目录" "$DEST"
else
  declare -A KNOWN=(
    [claude]="Claude Code:$HOME/.claude"
    [codex]="Codex CLI:$HOME/.codex"
    [zcode]="ZCode:$HOME/.zcode"
    [dsh]="DeepSeek Harness:$HOME/.dsh"
  )
  found=0
  for k in "${!KNOWN[@]}"; do
    [ "$HARNESS" != "all" ] && [ "$HARNESS" != "$k" ] && continue
    IFS=: read -r name root <<< "${KNOWN[$k]}"
    if [ -d "$root" ]; then
      install_skill "$name" "$root/skills"; found=1
    else
      echo "· 未检测到 $name（$root 不存在）——用 -d <目录> 手动指定"
    fi
  done
  [ "$found" -eq 0 ] && { echo "❌ 没有可安装的目标（用 -d <目录> 指定技能目录）"; exit 1; }
fi

# ---------- 3. MCP 注册指引 ----------
cat <<'EOF'

== MCP 服务器注册（可选，装了可用结构化工具；不装也能走 CLI）==
  Claude Code : claude mcp add job-hater -- jobhater-mcp
  Codex CLI   : 把 plugin/codex/config-snippet.toml 内容并入 ~/.codex/config.toml
  ZCode / dsh : 在 MCP 服务器设置添加 stdio 命令：jobhater-mcp

== 下一步 ==
  对你的 AI 助手说：「帮我找工作」或 /find-jobs —— 它会按 job-hater 技能开战。
  本地 Web 界面（可选）：job-hater serve → http://127.0.0.1:8787
EOF
