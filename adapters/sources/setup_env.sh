#!/usr/bin/env bash
# setup_env.sh — 采集底座虚拟环境（wenke-radar 依赖：requests / beautifulsoup4 / openpyxl / pycryptodome）
# 用法：bash adapters/sources/setup_env.sh
set -euo pipefail
cd "$(dirname "$0")/../.."   # 项目根

if [ ! -f repos/wenke-radar/requirements.txt ]; then
  echo "❌ 缺少 repos/wenke-radar/（上游归档）。请先按 README 恢复 repos/ 归档。" >&2
  exit 1
fi

python3 -m venv .venv-sources
.venv-sources/bin/pip install --upgrade pip -q
.venv-sources/bin/pip install -r repos/wenke-radar/requirements.txt -q
echo "✅ 采集底座就绪：.venv-sources（仅 adapters 层使用，core/ 保持零依赖）"
