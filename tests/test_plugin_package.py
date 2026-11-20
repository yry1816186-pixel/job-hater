"""插件包（v3.7）结构完整性：拉取仓库即可装进 Claude Code / Codex / ZCode / dsh。

防两类回归：文件被拆散/改名（安装器与 marketplace 直接失效）、
SKILL/manifest 格式漂移（frontmatter/JSON 不合规则宿主静默不加载技能）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugin"
SKILL = PLUGIN / "skills" / "job-hater"


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    assert m, "缺 YAML frontmatter（宿主按 name/description 决定加载）"
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


class TestPluginStructure:
    def test_required_files_exist(self):
        for p in (
            PLUGIN / ".claude-plugin" / "plugin.json",
            PLUGIN / ".mcp.json",
            PLUGIN / "install.ps1",
            PLUGIN / "install.sh",
            PLUGIN / "README.md",
            SKILL / "SKILL.md",
            SKILL / "references" / "channels.md",
            SKILL / "references" / "tools.md",
            PLUGIN / "commands" / "find-jobs.md",
            PLUGIN / "commands" / "track-applications.md",
            PLUGIN / "codex" / "config-snippet.toml",
            Path(__file__).resolve().parent.parent / ".claude-plugin" / "marketplace.json",
        ):
            assert p.is_file(), f"插件缺文件：{p}"

    def test_plugin_manifest_valid(self):
        data = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert data["name"] == "job-hater"
        assert re.match(r"^\d+\.\d+\.\d+$", data["version"])
        assert data["description"]

    def test_marketplace_valid(self):
        root = Path(__file__).resolve().parent.parent
        data = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = data["plugins"][0]
        assert entry["name"] == "job-hater"
        assert (root / entry["source"] / ".claude-plugin" / "plugin.json").is_file(), \
            "marketplace source 必须指向含 plugin.json 的插件目录"

    def test_mcp_json_shape(self):
        data = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
        srv = data["mcpServers"]["job-hater"]
        # 命令名必须与 pyproject 实际注册的 console script 一致（写错 = 四端 MCP 全挂）
        assert srv["command"] == self._mcp_script_name()

    @staticmethod
    def _mcp_script_name() -> str:
        root = Path(__file__).resolve().parent.parent
        for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if "jobhater.mcp_server:main" in line:
                return line.split("=")[0].strip()
        raise AssertionError("pyproject 未注册 mcp console script")

    def test_skill_frontmatter(self):
        fm = _frontmatter((SKILL / "SKILL.md").read_text(encoding="utf-8"))
        assert fm.get("name") == "job-hater"  # 必须与目录同名（Agent Skills 规范）
        assert "求职" in fm.get("description", "") and len(fm["description"]) > 20

    def test_command_frontmatter(self):
        for cmd in ("find-jobs.md", "track-applications.md"):
            fm = _frontmatter((PLUGIN / "commands" / cmd).read_text(encoding="utf-8"))
            assert fm.get("description")

    def test_installers_reference_real_paths(self):
        ps1 = (PLUGIN / "install.ps1").read_text(encoding="utf-8")
        sh = (PLUGIN / "install.sh").read_text(encoding="utf-8")
        for text in (ps1, sh):
            assert "skills" in text and "job-hater" in text
        # 安装器 MCP 指引、codex 片段与 .mcp.json 的命令名三处一致且与 pyproject 对账
        name = self._mcp_script_name()
        for text in (ps1, sh,
                     (PLUGIN / "codex" / "config-snippet.toml").read_text(encoding="utf-8")):
            assert name in text
        # 技能源目录名被安装器引用
        assert "skills\\job-hater" in ps1 or "skills/job-hater" in ps1
        assert "skills/job-hater" in sh

    def test_skill_mentions_core_workflow(self):
        """技能手册必须覆盖六步作战流与铁律（这是插件的核心价值，防退化成空壳）。"""
        body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for must in ("profile_create", "preset_set", "match_run", "jobs_import",
                     "绝不代投", "paste_parse", "internship", "social"):
            assert must in body, f"SKILL.md 缺关键内容：{must}"
