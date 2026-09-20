#!/usr/bin/env bash
# bootstrap_upstream.sh — 拉取上游开源项目归档到 repos/（本地运行，不入库）
# 用法：bash scripts/bootstrap_upstream.sh
# 说明：网络受限环境可对每个仓库改用 codeload 整包下载（见 docs/反思优化报告.md 的实测教训）。
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p repos && cd repos

# 采集层运行依赖的前三个仓库必须有；其余为参考归档，失败不阻塞
REPOS=(
  "https://github.com/onism1767-creator/wenke-radar.git"
  "https://github.com/jiabaobei/xiaozhao-radar.git"
  "https://github.com/Jasmine-Liu-min/job-radar.git"
  "https://github.com/career-ops-hq/career-ops.git"
  "https://github.com/MadsLorentzen/ai-job-search.git"
  "https://github.com/can4hou6joeng4/boss-agent-cli.git"
  "https://github.com/loks666/get_jobs.git"
  "https://github.com/OpenFuckJob/FuckJob.git"
  "https://github.com/eatmoreduck/boss-zhipin-scraper.git"
  "https://github.com/gzchenhao/openhire.git"
)

fail=0
for url in "${REPOS[@]}"; do
  name=$(basename "$url" .git)
  if [ -d "$name/.git" ]; then
    echo "✓ $name 已存在，跳过"
    continue
  fi
  echo "… clone $name"
  if ! git clone --depth 1 "$url" "$name"; then
    echo "⚠️ $name 克隆失败（网络/迁移）。可手动获取后放入 repos/$name；采集层必须的三个仓库见 docs/THIRD_PARTY_NOTICES.md"
    [ "$name" = "wenke-radar" ] || [ "$name" = "xiaozhao-radar" ] || [ "$name" = "job-radar" ] && fail=1
  fi
done

if [ "$fail" = "1" ]; then
  echo "❌ 采集层必需的仓库缺失，fetch --env wenke/xiaozhao 不可用；粘贴导入路径不受影响"
  exit 1
fi
echo "✅ 上游归档就绪"
