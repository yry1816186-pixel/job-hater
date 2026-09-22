"""微信图片 OCR 通道（Windows 内置引擎，零外部依赖、零上传）。

微信 4.x 的聊天图片本体是专有容器格式（.dat，魔数 07085632，社区暂无
公开解法），但**账号 cache 目录里存有明文 JPEG/PNG**（会话加载/查看过
的图）——本通道 OCR 这部分，覆盖「近期看过的招聘海报」。

实测命中率约 1%（cache 多为日常图），但能捕获文本链没有的信息（如
宣讲海报），符合「一个不留」的目标；.dat 容器破解后此通道可直接
替换数据源，分析链不变。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_WORKER = Path(__file__).with_name("ocr_worker.ps1")


@dataclass
class OcrHit:
    """一张图的识别结果（文本 + 元信息）。"""

    file: str
    mtime: str
    text: str


def run_ocr(cache_dir: Path, *, timeout_s: int = 600) -> list[OcrHit]:
    """对 cache 目录跑 Windows OCR，返回有文本的结果（非 Windows / 无 cache 返回空）。

    失败静默降级（返回空列表）——OCR 是增强通道，不阻断主扫描。
    """
    if os.name != "nt" or not cache_dir.is_dir():
        return []
    with tempfile.TemporaryDirectory(prefix="jobhater_ocr_") as td:
        out = Path(td) / "ocr.jsonl"
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(_WORKER), "-CacheDir", str(cache_dir), "-OutFile", str(out),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return []
        if not out.exists():
            return []
        hits: list[OcrHit] = []
        for line in out.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("text"):
                hits.append(OcrHit(file=d.get("file", ""), mtime=d.get("mtime", ""), text=d["text"]))
        return hits


def parse_ocr_jsonl(path: Path) -> list[OcrHit]:
    """解析 worker 输出（测试与复用通道）。"""
    hits: list[OcrHit] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("text"):
            hits.append(OcrHit(file=d.get("file", ""), mtime=d.get("mtime", ""), text=d["text"]))
    return hits
