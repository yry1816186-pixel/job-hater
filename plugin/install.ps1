# Job Hater 插件安装器（Windows PowerShell）
# 用法：
#   ./install.ps1                 # 装引擎 + 技能装进所有检测到的 AI 编程助手
#   ./install.ps1 -Harness claude # 只装 Claude Code（可选：codex/zcode/dsh/all）
#   ./install.ps1 -Dest <目录>    # 装进任意 Agent Skills 宿主的技能目录
#   ./install.ps1 -SkipEngine     # 只装技能（引擎已装好时）
#   ./install.ps1 -Engine git     # 引擎从 GitHub 安装（默认：仓库内运行用本地源码）
[CmdletBinding()]
param(
    [string]$Harness = "all",      # all | claude | codex | zcode | dsh
    [string]$Dest = "",            # 直接指定技能安装目录（优先于 Harness）
    [switch]$SkipEngine,
    [string]$Engine = ""           # 空=自动（仓库内=本地，否则 git）；git | local
)
$ErrorActionPreference = "Stop"
$PluginDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $PluginDir
$SkillSrc = Join-Path $PluginDir "skills\job-hater"

Write-Host "== Job Hater 插件安装 ==" -ForegroundColor Cyan

# ---------- 1. 引擎 ----------
if (-not $SkipEngine) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "❌ 未找到 python（需要 Python 3.10+）。https://www.python.org/downloads/" -ForegroundColor Red; exit 1 }
    if ($Engine -eq "" -and (Test-Path (Join-Path $RepoRoot "pyproject.toml"))) { $Engine = "local" }
    if ($Engine -eq "" -or $Engine -eq "git") { $Engine = "git" }
    if ($Engine -eq "local") {
        Write-Host "→ 从本仓库安装引擎（含 MCP）：pip install ./[mcp]"
        Push-Location $RepoRoot; & python -m pip install "./[mcp]"; Pop-Location
    } else {
        Write-Host "→ 从 GitHub 安装引擎（含 MCP）"
        & python -m pip install "jobhater[mcp] @ git+https://github.com/yry1816186-pixel/job-hater.git"
    }
    if ($LASTEXITCODE -ne 0) { Write-Host "❌ 引擎安装失败（网络/代理问题见 plugin/README.md 常见问题）" -ForegroundColor Red; exit 1 }
    & job-hater doctor
    if ($LASTEXITCODE -ne 0) { & python -m jobhater.cli doctor }
}

# ---------- 2. 技能安装 ----------
$targets = @()
if ($Dest -ne "") {
    $targets += @{ Name = "自定义目录"; Dir = $Dest; Root = $Dest }
} else {
    $known = @{
        claude = @{ Name = "Claude Code";  Dir = Join-Path $HOME ".claude\skills"; Root = Join-Path $HOME ".claude" }
        codex  = @{ Name = "Codex CLI";    Dir = Join-Path $HOME ".codex\skills";  Root = Join-Path $HOME ".codex" }
        zcode  = @{ Name = "ZCode";        Dir = Join-Path $HOME ".zcode\skills";  Root = Join-Path $HOME ".zcode" }
        dsh    = @{ Name = "DeepSeek Harness"; Dir = Join-Path $HOME ".dsh\skills"; Root = Join-Path $HOME ".dsh" }
    }
    foreach ($k in $known.Keys) {
        if ($Harness -ne "all" -and $Harness -ne $k) { continue }
        if (Test-Path $known[$k].Root) { $targets += $known[$k] }
        else { Write-Host "· 未检测到 $($known[$k].Name)（$($known[$k].Root) 不存在）——如需安装到它，先装好该工具或用 -Dest 指定目录" -ForegroundColor DarkGray }
    }
}
if (-not $targets) { Write-Host "❌ 没有可安装的目标（用 -Dest <目录> 指定技能目录）" -ForegroundColor Red; exit 1 }

foreach ($t in $targets) {
    New-Item -ItemType Directory -Force -Path $t.Dir | Out-Null
    $dst = Join-Path $t.Dir "job-hater"
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse $SkillSrc $dst
    Write-Host "✅ 技能已装入 $($t.Name)：$dst" -ForegroundColor Green
}

# ---------- 3. MCP 注册指引 ----------
Write-Host @"

== MCP 服务器注册（可选，装了可用结构化工具；不装也能走 CLI）==
  Claude Code : claude mcp add job-hater -- job-hater-mcp
  Codex CLI   : 把 plugin/codex/config-snippet.toml 内容并入 ~/.codex/config.toml
  ZCode / dsh : 在 MCP 服务器设置添加 stdio 命令：job-hater-mcp

== 下一步 ==
  对你的 AI 助手说：「帮我找工作」或 /find-jobs —— 它会按 job-hater 技能开战。
  本地 Web 界面（可选）：job-hater serve → http://127.0.0.1:8787
"@ -ForegroundColor Cyan
