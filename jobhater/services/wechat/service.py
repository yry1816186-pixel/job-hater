"""微信数据源编排：检测 → 密钥 → 解密 → 解析 → 招聘识别 → 导入。

运行模型：
- ``start_scan`` 在后台线程执行全流程（避免 HTTP 请求超时）；
- 前端轮询 ``status()`` 拿阶段进度（detect/decrypt/parse/analyze/done/failed）；
- 结果（含每条命中及其证据、来源会话与时间）缓存为 JSON；
- ``import_hits`` 把选中命中经 ``JobService.ingest`` 走项目统一入库链
  （去重/规范化/FTS 一致），source_id 固定 ``wechat``。

隐私边界：
- 密钥仅在内存中使用，绝不持久化；
- 解密产物放在 jobhater 数据目录 ``wechat/`` 下，``purge()`` 一键清除；
- 分析只保留命中消息的必要字段（会话名/发送者/时间/正文），不做全量聊天导出。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobhater import config
from jobhater.services.wechat import parser as wx_parser
from jobhater.services.wechat import recruit as wx_recruit
from jobhater.services.wechat.decrypt import decrypt_database, verify_key
from jobhater.services.wechat.detect import detect as detect_env
from jobhater.services.wechat.keyring_scan import find_key

# 同发送者在同一会话内的消息合并窗口（秒）：拆成多条发的 JD 能拼回整体
BURST_WINDOW_S = 180
MIN_CONFIDENCE = 0.35


@dataclass
class ScanState:
    """后台扫描状态（线程共享, 读写字段均为原子替换）。"""

    phase: str = "idle"  # idle/detecting/extracting_key/decrypting/parsing/analyzing/done/failed
    message: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    progress_detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    # 统计
    accounts: int = 0
    dbs_decrypted: int = 0
    messages_total: int = 0
    messages_analyzed: int = 0
    hits: int = 0

    def to_dict(self) -> dict:
        return {
            "phase": self.phase, "message": self.message,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "progress": self.progress_detail, "error": self.error,
            "stats": {
                "accounts": self.accounts, "dbs_decrypted": self.dbs_decrypted,
                "messages_total": self.messages_total,
                "messages_analyzed": self.messages_analyzed, "hits": self.hits,
            },
        }


class WeChatService:
    """微信扫描编排（进程内单例语义由 API 层持有）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state = ScanState()

    # ---------- 目录 ----------

    def work_dir(self) -> Path:
        d = config.data_dir() / "wechat"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def decrypted_dir(self) -> Path:
        return self.work_dir() / "decrypted"

    def result_path(self) -> Path:
        return self.work_dir() / "scan_result.json"

    def purge(self) -> None:
        """清除全部解密产物与结果缓存（不动微信原始数据）。"""
        import shutil

        self.stop_scan()
        wd = self.work_dir()
        if wd.exists():
            shutil.rmtree(wd)

    # ---------- 扫描生命周期 ----------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        out = self.state.to_dict()
        out["running"] = self.running
        if not self.running and self.result_path().exists():
            out["has_result"] = True
        return out

    def stop_scan(self) -> None:
        self._stop_flag = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    _stop_flag = False

    def start_scan(self, *, min_confidence: float = MIN_CONFIDENCE) -> dict:
        """启动后台全流程扫描。已在跑 → 返回当前状态（幂等）。"""
        with self._lock:
            if self.running:
                return self.status()
            self.state = ScanState(phase="detecting", started_at=time.time())
            self._stop_flag = False
            self._thread = threading.Thread(target=self._run_scan, args=(min_confidence,), daemon=True)
            self._thread.start()
        return self.status()

    # ---------- 主流程 ----------

    def _run_scan(self, min_confidence: float) -> None:
        try:
            self._scan_impl(min_confidence)
        except Exception as e:  # noqa: BLE001（后台线程兜底: 任何失败都要可见）
            self.state.phase = "failed"
            self.state.error = f"{type(e).__name__}: {e}"
            self.state.finished_at = time.time()

    def _scan_impl(self, min_confidence: float) -> None:
        state = self.state
        # 1) 环境检测
        env = detect_env()
        if env.blockers:
            state.phase = "failed"
            state.error = "；".join(env.blockers)
            state.finished_at = time.time()
            return
        account = max(env.accounts, key=lambda a: a.total_db_bytes)
        state.accounts = len(env.accounts)
        state.message = f"账号 {account.wxid}，消息库 {len(account.message_dbs)} 个"

        # 2) 密钥提取（全部消息+联系人库的第一页做锚点：每库密钥独立缓存，多锚提高命中面）
        state.phase = "extracting_key"
        want = [("message", p) for p in account.message_dbs] + [("contact", p) for p in account.contact_dbs]
        anchors = []
        for _kind, db in want:
            try:
                anchors.append(db.read_bytes()[:4096])
            except OSError:
                continue

        def _prog(p: dict) -> None:
            state.progress_detail = dict(p)

        key = find_key(anchors, progress=lambda pr: _prog(pr.to_dict()), stop_check=lambda: self._stop_flag)
        if self._stop_flag:
            state.phase = "idle"
            return
        if key is None:
            state.phase = "failed"
            state.error = (
                "未能在微信进程内存中找到数据库密钥——密钥只在微信刚启动/刚登录的"
                "瞬间驻留内存。请退出微信并重新打开（自动登录即可），登录完成后立即"
                "重新扫描；仍失败可在重启后 1 分钟内多试几次。"
            )
            state.finished_at = time.time()
            return

        # 3) 解密（消息/联系人；同库共享密钥失败时单独重扫该库）
        state.phase = "decrypting"
        out_root = self.decrypted_dir() / account.key
        out_root.mkdir(parents=True, exist_ok=True)
        keys: dict[bytes, None] = {key: None}  # 已知密钥集合(按 salt 试全部)
        decrypted: dict[str, Path] = {}
        for _kind, db in want:
            if self._stop_flag:
                state.phase = "idle"
                return
            p1 = db.read_bytes()[:4096]
            k = next((c for c in keys if verify_key(c, p1)), None)
            if k is None:
                k = find_key(p1, stop_check=lambda: self._stop_flag)
                if k is not None:
                    keys[k] = None
            if k is None:
                continue
            out = out_root / db.parent.name / db.name
            r = decrypt_database(db, out, k)
            if r.ok:
                decrypted[f"{db.parent.name}/{db.name}"] = out
                state.dbs_decrypted += 1
        contact_dbs = [p for name, p in decrypted.items() if name.startswith("contact")]
        message_dbs = [p for name, p in decrypted.items() if name.startswith("message")]

        # 4) 联系人 + 消息
        state.phase = "parsing"
        contacts: dict[str, dict] = {}
        for cdb in contact_dbs:
            try:
                contacts.update(wx_parser.load_contacts(cdb))
            except Exception:  # noqa: BLE001（单个库损坏不阻断整体）
                continue
        state.progress_detail = {"contacts": len(contacts)}

        # 5) 招聘识别（含 burst 合并）
        state.phase = "analyzing"
        hits = self._analyze(message_dbs, contacts, account.wxid, min_confidence, state)
        state.hits = len(hits)

        # 6) 结果落盘（不含密钥；tmp+rename 原子写，避免轮询读到半文件）
        result = {
            "generated_at": time.time(),
            "account": account.wxid,
            "stats": state.to_dict()["stats"] | {"contacts": len(contacts)},
            "hits": hits,
        }
        tmp = self.result_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.result_path())
        state.phase = "done"
        state.message = f"扫描完成：{state.messages_analyzed} 条消息中识别出 {len(hits)} 条招聘信息"
        state.finished_at = time.time()

    def _analyze(
        self,
        message_dbs: list[Path],
        contacts: dict[str, dict],
        self_wxid: str,
        min_confidence: float,
        state: ScanState,
    ) -> list[dict]:
        """逐条识别 + 同发送者连发合并识别，去重后按置信度排序。"""
        msgs = wx_parser.iter_messages(message_dbs, contacts, self_wxid=self_wxid)
        hits: list[dict] = []
        seen_keys: set[str] = set()
        burst: list[wx_parser.WeChatMessage] = []

        def _flush_burst() -> None:
            if not burst:
                return
            if len(burst) >= 2:
                merged_text = "\n".join(m.content for m in burst)
                hit = wx_recruit.analyze(merged_text, sender_name=burst[0].sender_name, talker_name=burst[0].talker_name)
                if hit and hit.confidence >= min_confidence:
                    _add(hit, burst[0], merged_text, merged=True)
            burst.clear()

        def _add(hit: wx_recruit.RecruitHit, msg: wx_parser.WeChatMessage, text: str, *, merged: bool = False) -> None:
            key = f"{hit.company or ''}|{hit.title or ''}|{msg.talker}|{text[:80]}"
            if key in seen_keys:
                return
            seen_keys.add(key)
            hits.append(
                {
                    **hit.to_dict(),
                    "talker": msg.talker,
                    "talker_name": msg.talker_name,
                    "sender_name": msg.sender_name,
                    "create_time": msg.create_time,
                    "time_str": msg.time_str,
                    "source_text": text[:4000],
                    "merged": merged,
                }
            )

        for m in msgs:
            state.messages_total += 1
            if not m.content or m.is_self or m.msg_type != 1:
                continue
            state.messages_analyzed += 1
            # burst: 同会话同发送者且时间相邻
            if burst and (
                burst[0].talker != m.talker or burst[0].sender != m.sender or m.create_time - burst[-1].create_time > BURST_WINDOW_S
            ):
                _flush_burst()
            hit = wx_recruit.analyze(m.content, sender_name=m.sender_name, talker_name=m.talker_name)
            if hit and hit.confidence >= min_confidence:
                _add(hit, m, m.content)
            burst.append(m)
            if state.messages_total % 2000 == 0:
                state.progress_detail = {"messages": state.messages_total, "hits": len(hits)}
        _flush_burst()
        hits.sort(key=lambda h: (-h["confidence"], h["create_time"]))
        return hits

    # ---------- 结果 / 导入 ----------

    def results(self) -> dict | None:
        p = self.result_path()
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
