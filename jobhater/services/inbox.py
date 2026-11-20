"""文件收件箱（v3.5 Track B）：拖文件进 inbox/ → 解析 → 暂存 → 过目 → 入库。

支持的文件形态（对应用户手上真实的岗位物料）：
- **表格**：xlsx（openpyxl，可选依赖组 [inbox]）/ csv（标准库，编码自适应）——
  在线表格导出（金山文档/腾讯文档）、HR 内推表。列头智能映射 + 表头行自动定位。
- **文本**：pdf/docx/txt/md——JD 原文、公众号文章存档。复用简历导入的
  `extract_text` 抽文本，再走双解析器：`parse_jd_text`（草稿契约）+
  微信雷达 `recruit.analyze`（置信度规则引擎）字段择优合并。
- **图片**：png/jpg——招聘海报/群聊截图。复用 Windows 内置 OCR（wechat/ocr），
  识别文本喂 `recruit.analyze`。

铁律与微信雷达同构：解析产物只进暂存（inbox_result.json），**过目后才入库**
（indices 勾选式 import，source_id="inbox"）；原文件移 processed/ 保留证据。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from jobhater import config
from jobhater.services.wechat import recruit as wx_recruit

MAX_FILE_MB = 10
TABLE_EXTS = {".xlsx", ".csv"}
TEXT_EXTS = {".pdf", ".docx", ".txt", ".md", ".markdown"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

# 列头别名表：命中即映射到 ingest 的 raw 字段（顺序=优先级，首列命中优先）
COLUMN_ALIASES: dict[str, list[str]] = {
    "title": ["岗位", "职位", "招聘职位", "岗位名称", "职位名称", "招聘岗位", "名称", "工作名称"],
    "company": ["公司", "公司名称", "企业", "单位", "招聘单位", "雇主", "企业名称"],
    "city": ["城市", "地点", "工作地点", "工作城市", "base", "Base", "工作地"],
    "salary": ["薪资", "薪资范围", "待遇", "月薪", "年薪", "薪水", "薪资待遇"],
    "url": ["链接", "投递", "投递链接", "网址", "网申", "网申链接", "投递地址", "链接地址", "地址"],
    "deadline": ["截止", "截止时间", "截止日期", "deadline", "DDL", "ddl"],
    "education": ["学历", "学历要求", "教育背景"],
    "experience": ["经验", "工作年限", "经验要求", "年限"],
    "description": ["岗位描述", "职责", "JD", "jd", "描述", "岗位职责", "要求", "详情", "任职要求"],
    "graduation_year": ["届", "届别", "面向届别", "招聘届别", "毕业届"],
    "recruiter": ["内推人", "内推", "推荐人"],
    "keywords": ["关键词", "标签", "岗位类型", "职能"],
}


@dataclass
class InboxState:
    phase: str = "idle"  # idle | scanning | done | failed
    running: bool = False
    error: str = ""
    files_seen: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase, "running": self.running, "error": self.error,
            "files_seen": self.files_seen, "updated_at": self.updated_at,
        }


@dataclass
class InboxService:
    """收件箱服务：scan（CLI 直调同步 / API 走后台线程）/ results / import。"""

    con: object = None  # sqlite3.Connection（仅 import 时需要）
    state: InboxState = field(default_factory=InboxState)
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_scan(self) -> bool:
        """后台线程扫描（幂等：已在跑则忽略）。返回是否本次启动。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._scan_guarded, daemon=True)
            self._thread.start()
            return True

    def _scan_guarded(self) -> None:
        try:
            self.scan()
        except Exception as e:  # 状态机已记 failed；线程必须吞异常退出
            if self.state.phase != "failed":
                self.state = InboxState(phase="failed", error=str(e)[:300],
                                        updated_at=time.time())

    def status(self) -> dict:
        return {**self.state.to_dict(),
                "running": bool(self._thread and self._thread.is_alive())}

    # ---------- 路径 ----------

    @staticmethod
    def inbox_dir() -> Path:
        d = config.data_dir() / "inbox"
        d.mkdir(parents=True, exist_ok=True)
        (d / "processed").mkdir(exist_ok=True)
        return d

    @staticmethod
    def result_path() -> Path:
        return config.data_dir() / "inbox" / "inbox_result.json"

    # ---------- 扫描 ----------

    def scan(self) -> dict:
        """同步扫描：全部文件解析 → 暂存 → 原件移 processed/。"""
        self.state = InboxState(phase="scanning", running=True, updated_at=time.time())
        try:
            inbox = self.inbox_dir()
            files = [f for f in sorted(inbox.iterdir())
                     if f.is_file() and f.suffix.lower() in (TABLE_EXTS | TEXT_EXTS | IMAGE_EXTS)]
            items: list[dict] = []
            notes: list[dict] = []
            # 图片整体一次 OCR（worker 按目录批处理，避免逐文件起 PowerShell）
            ocr_texts = self._ocr_images(inbox)
            for f in files:
                try:
                    got, note = self._parse_file(f, ocr_texts)
                    items.extend(got)
                    notes.append({"file": f.name, "kind": note,
                                  "items": len(got)})
                except Exception as e:  # 单文件失败不阻断整批，如实记账
                    notes.append({"file": f.name, "kind": f"error: {type(e).__name__}",
                                  "items": 0, "error": str(e)[:200]})
                shutil.move(str(f), inbox / "processed" / f.name)
            if files:
                out = {"generated_at": time.time(), "files": notes, "items": items}
                self._atomic_write(out)
            # 空收件箱不覆写上次结果（扫了≠清空：上一批待过目的数据不得被空扫冲掉）
            self.state = InboxState(phase="done", files_seen=len(files),
                                    updated_at=time.time())
            return {"files": len(files), "items": len(items),
                    "needs_review": sum(1 for i in items if not i.get("company"))}
        except Exception as e:
            self.state = InboxState(phase="failed", error=str(e)[:300],
                                    updated_at=time.time())
            raise

    # ---------- 查询与导入 ----------

    def results(self, q: str = "") -> dict:
        res = self._load()
        items = res.get("items", [])
        if q:
            ql = q.lower()
            items = [i for i in items if ql in (i.get("title", "") + i.get("company", "")).lower()]
        return {"files": res.get("files", []), "items": items,
                "generated_at": res.get("generated_at")}

    def import_items(self, indices: list[int]) -> dict:
        from jobhater.services.jobs import JobService

        res = self._load()
        items = res.get("items", [])
        raw = []
        for idx in indices:
            if 0 <= idx < len(items) and items[idx].get("company"):
                raw.append({k: v for k, v in items[idx].items()
                            if k in ("title", "company", "city", "salary", "url",
                                     "description", "deadline", "education",
                                     "graduation_year", "keywords")
                            and v})
        stats = JobService(self.con).ingest(raw, source_id="inbox")
        return {"requested": len(indices), "imported": stats.added,
                "deduped": stats.deduped_exact + stats.deduped_near,
                "rejected": stats.rejected}

    # ---------- 文件解析 ----------

    def _parse_file(self, f: Path, ocr_texts: dict[str, str]) -> tuple[list[dict], str]:
        ext = f.suffix.lower()
        if ext == ".csv":
            return self._parse_table(*self._read_csv(f), f.name)
        if ext in TABLE_EXTS:  # .xlsx（.csv 已在上面分流，防 openpyxl 吃 csv）
            return self._parse_table(*self._read_xlsx(f), f.name)
        if ext in TEXT_EXTS:
            return self._parse_text_file(f)  # 已是 (list, note) 二元组，勿再包一层
        if ext in IMAGE_EXTS:
            text = ocr_texts.get(str(f), "")
            if not text:
                return [], "image(无文本)"
            got = self._analyze_text(text)
            for it in got:
                it["source_file"] = f.name
            return got, "image_ocr"
        return [], "unsupported"

    def _read_xlsx(self, f: Path) -> tuple[list[list], int]:
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("xlsx 解析需要 openpyxl：pip install 'jobhater[inbox]'") from e
        wb = load_workbook(f, read_only=True, data_only=True)
        ws = wb.active
        rows = [[("" if c is None else str(c).strip()) for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows, 0

    def _read_csv(self, f: Path) -> tuple[list[list], int]:
        import csv
        import io

        from jobhater.services.resume_import import _decode_text

        text = _decode_text(f.read_bytes())
        rows = list(csv.reader(io.StringIO(text)))
        return [[c.strip() for c in r] for r in rows], 0

    def _parse_table(self, rows: list[list], _hint: int, fname: str) -> tuple[list[dict], str]:
        """表头行自动定位（前 10 行里含 ≥2 个已知列名的行）→ 列映射 → 行→岗位。"""
        header_idx, colmap = -1, {}
        for i, row in enumerate(rows[:10]):
            cmap: dict[str, int] = {}
            for col, cell in enumerate(row):
                cell = (cell or "").strip()
                for field_name, aliases in COLUMN_ALIASES.items():
                    if cell and cell in aliases and field_name not in cmap:
                        cmap[field_name] = col
            if len(cmap) >= 2 and ("title" in cmap or "company" in cmap):
                header_idx, colmap = i, cmap
                break
        if header_idx < 0:
            return [], "table(未识别表头)"
        out: list[dict] = []
        for row in rows[header_idx + 1:]:
            def cell(row: list, name: str) -> str:
                col = colmap.get(name)
                if col is None or col >= len(row):
                    return ""
                return (row[col] or "").strip()

            title = cell(row, "title")
            if not title:
                continue
            item = {"title": title[:60], "company": cell(row, "company")[:40],
                    "source_file": fname}
            for k in ("city", "salary", "url", "deadline", "education",
                      "experience", "description", "graduation_year", "keywords"):
                v = cell(row, k)
                if v:
                    item[k] = v[:4000] if k == "description" else v[:120]
            out.append(item)
        return out, f"table({len(colmap)} 列映射)"

    def _parse_text_file(self, f: Path) -> tuple[list[dict], str]:
        from jobhater.services.resume_import import extract_text

        text, _kind = extract_text(f.name, f.read_bytes())
        got = self._analyze_text(text)
        for it in got:
            it["source_file"] = f.name
        return got, "text"

    def _analyze_text(self, text: str) -> list[dict]:
        """双解析器择优：recruit.analyze（结构+置信度）× parse_jd_text（草稿契约）。"""
        from jobhater.services.sources.paste import parse_jd_text

        text = (text or "").strip()
        if len(text) < 8:
            return []
        draft = parse_jd_text(text)
        hit = wx_recruit.analyze(text)
        item: dict = {
            "title": (draft.get("title") or (hit.title if hit else "") or "")[:60],
            "company": (draft.get("company") or (hit.company if hit else "") or "")[:40],
            "city": draft.get("city") or (hit.cities[0] if hit and hit.cities else "") or "",
            "salary": draft.get("salary") or (hit.salary if hit else "") or "",
            "description": text[:4000],
        }
        if hit:
            if hit.deadline:
                item["deadline"] = hit.deadline
            if hit.cohort:
                item["graduation_year"] = hit.cohort
            url = (hit.apply_method or "").replace("链接 ", "").strip()
            if url.startswith("http"):
                item["url"] = url
            item["confidence"] = round(hit.confidence, 2)
        if not item["title"]:
            item["title"] = "未识别标题（见正文）"
        return [item]

    def _ocr_images(self, inbox: Path) -> dict[str, str]:
        """只 OCR 本次待处理图片：拷到临时目录再跑（processed/ 里的历史图不重扫）。"""
        import shutil as _sh
        import tempfile

        imgs = [f for f in inbox.iterdir() if f.suffix.lower() in IMAGE_EXTS]
        if not imgs:
            return {}
        from jobhater.services.wechat.ocr import run_ocr

        with tempfile.TemporaryDirectory(prefix="jobhater_inbox_") as td:
            tmp = Path(td)
            for f in imgs:
                _sh.copy2(f, tmp / f.name)
            hits = run_ocr(tmp, timeout_s=900)
        # run_ocr 的 file 字段是 worker 侧绝对路径——按文件名映射回 inbox 件
        by_name = {Path(h.file).name: h.text for h in hits}
        return {str(f): by_name.get(f.name, "") for f in imgs}

    # ---------- 暂存 ----------

    def _atomic_write(self, data: dict) -> None:
        path = self.result_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> dict:
        path = self.result_path()
        if not path.exists():
            return {"generated_at": 0, "files": [], "items": []}
        return json.loads(path.read_text(encoding="utf-8"))
