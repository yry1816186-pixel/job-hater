"""微信 4.x 解密库 → 结构化消息流。

已知库表结构（逆向社区公开结论）：
- ``contact.db``：``contact(username, alias, remark, nick_name, local_type, ...)``；
  local_type：1=好友 2=群聊 3=群成员(非好友) 4=公众号/服务号 5/6=企业微信；
- ``message_N.db``：每会话一张 ``Msg_<md5(username)>`` 表，列
  ``local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status,
  ..., message_content, packed_info_data``；
  另有 ``Name2Id(id, username)`` 映射表；
- ``message_content``：明文 TEXT 或 zstd 压缩 BLOB（魔数 ``28 b5 2f fd``）；
  群聊内容格式 ``"<发送者wxid>:\\n<正文>"``；
- local_type：低 32 位是真实类型，高位是 v4 的 flag 组合（如 244813135921
  的低位=1 文本）——**必须先掩码再判型**，否则约 14% 带标志消息被漏：
  1=文本 3=图片 34=语音 42=名片 43=视频 47=表情 49=链接/文件卡片
  10000=系统提示。

本模块只做「读已解密库」这一件事，输出 :class:`WeChatMessage` 流；
招聘识别在 ``recruit.py``，编排与入库在 ``service.py``。
"""
from __future__ import annotations

import hashlib
import html
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# v4 local_type 高位是 flag 组合，低位才是真实消息类型
TYPE_MASK = 0xFFFFFFFF

# 值得进入分析视野的消息类型（文本/链接卡片/公众号图文/名片）
ANALYZABLE_TYPES = {1, 49, 42}

# appmsg（type 49 链接/文件卡片）XML → 文本化：标题+摘要+链接是招聘信息金矿
_APPMSG_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_APPMSG_DES_RE = re.compile(r"<des>(.*?)</des>", re.DOTALL)
_APPMSG_URL_RE = re.compile(r"<url>(.*?)</url>", re.DOTALL)


def _clean_xml_text(s: str) -> str:
    """剥 CDATA 包裹 + XML 实体反转义（公众号图文的 title/des 常带 <![CDATA[…]]>）。"""
    s = s.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        s = s[9:-3]
    return html.unescape(s).strip()


def appmsg_to_text(content: str) -> str:
    """49 型卡片 XML → 「标题\\n摘要\\n链接」纯文本（非 XML 原样返回）。

    公众号图文的 title 常是「XX公司2027校招启动」，des 是摘要——正是
    识别引擎要的输入；文件卡片的 title 是文件名，无意图词时自然不过阈值。
    """
    stripped = content.lstrip()
    if not (stripped.startswith("<?xml") or stripped.startswith("<msg")):
        return content
    parts: list[str] = []
    if m := _APPMSG_TITLE_RE.search(content):
        t = _clean_xml_text(m.group(1))
        if t and not t.startswith("##"):  # "##token##" 是视频号噪声前缀
            parts.append(t)
    if m := _APPMSG_DES_RE.search(content):
        d = _clean_xml_text(m.group(1))
        if d:
            parts.append(d)
    if m := _APPMSG_URL_RE.search(content):
        u = html.unescape(m.group(1)).strip()
        if u.startswith("http"):
            parts.append(u)
    return "\n".join(parts) if parts else content


def _try_zstd(data: bytes) -> bytes:
    """解压 zstd；未安装 zstandard 或解压失败时原样返回（调用方按明文处理）。"""
    if not data.startswith(ZSTD_MAGIC):
        return data
    try:
        import zstandard  # noqa: PLC0415（可选依赖，延迟导入）

        return zstandard.ZstdDecompressor().decompress(data)
    except Exception:  # noqa: BLE001（缺库/坏数据都降级为原文, 由上层跳过不可读内容）
        return data


@dataclass
class WeChatMessage:
    """一条规范化消息。"""

    talker: str  # 会话标识（wxid / xxx@chatroom / gh_xxx）
    talker_name: str  # 会话显示名（备注>昵称>原id）
    is_chatroom: bool
    sender: str  # 发送者 wxid（私聊=对方或自己）
    sender_name: str  # 发送者显示名
    is_self: bool
    msg_type: int  # 微信 local_type
    create_time: int  # Unix 秒
    content: str  # 已解压、已剥离群前缀的正文

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.create_time).strftime("%Y-%m-%d %H:%M")

    def to_dict(self) -> dict:
        return {
            "talker": self.talker, "talker_name": self.talker_name,
            "is_chatroom": self.is_chatroom, "sender": self.sender,
            "sender_name": self.sender_name, "is_self": self.is_self,
            "msg_type": self.msg_type, "create_time": self.create_time,
            "time_str": self.time_str, "content": self.content,
        }


def msg_table_name(talker: str) -> str:
    """会话用户名 → 消息表名（Msg_ + md5(username)）。"""
    return "Msg_" + hashlib.md5(talker.encode("utf-8")).hexdigest()


def load_contacts(contact_db: Path) -> dict[str, dict]:
    """读 contact.db → {username: {name, alias, remark, nick, type}}。"""
    out: dict[str, dict] = {}
    con = sqlite3.connect(f"file:{contact_db}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        for r in con.execute("SELECT username, alias, remark, nick_name, local_type FROM contact"):
            remark = (r["remark"] or "").strip()
            nick = (r["nick_name"] or "").strip()
            out[r["username"]] = {
                "name": remark or nick or r["username"],
                "alias": r["alias"] or "",
                "remark": remark,
                "nick": nick,
                "type": r["local_type"],
            }
    finally:
        con.close()
    return out


def _name2id_map(con: sqlite3.Connection) -> dict[int, str]:
    """message_N.db 的 Name2Id 表 → {id: username}（表缺失时返回空）。"""
    out: dict[int, str] = {}
    try:
        for i, u in con.execute("SELECT id, username FROM Name2Id"):
            out[i] = u
    except sqlite3.OperationalError:
        pass
    return out


def iter_messages(
    message_dbs: list[Path],
    contacts: dict[str, dict],
    *,
    self_wxid: str = "",
    types: set[int] | None = None,
) -> Iterator[WeChatMessage]:
    """遍历所有消息库的所有 Msg_* 表，产出规范化消息。

    - ``types``：关注的 local_type 集合（默认 :data:`ANALYZABLE_TYPES`）；
    - 表名反解 talker：以 contact 名单/自 wxid 匹配 md5，未匹配的表跳过
      （避免把 Name2Id 等非消息表混进来）。
    """
    wanted = types if types is not None else ANALYZABLE_TYPES
    # 预建 md5(talker) → talker 映射（contact + 常见系统会话）
    candidates = dict.fromkeys(contacts)
    if self_wxid:
        candidates.setdefault(self_wxid, None)
    md5_to_talker = {hashlib.md5(t.encode("utf-8")).hexdigest(): t for t in candidates}

    for db in message_dbs:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            name2id = _name2id_map(con)
            tables = [
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if r[0].startswith("Msg_") and len(r[0]) == 4 + 32
            ]
            for table in tables:
                talker = md5_to_talker.get(table[4:])
                if talker is None:
                    continue
                info = contacts.get(talker, {})
                talker_name = info.get("name", talker)
                is_room = talker.endswith("@chatroom")
                try:
                    rows = con.execute(
                        f"SELECT local_type, real_sender_id, create_time, status, message_content FROM {table}"  # noqa: S608（表名已白名单校验）
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                for local_type, sender_id, create_time, _status, raw in rows:
                    real_type = local_type & TYPE_MASK if local_type is not None else 0
                    if real_type not in wanted or create_time is None:
                        continue
                    sender = name2id.get(sender_id, "" if sender_id is None else str(sender_id))
                    content = _try_zstd(raw) if isinstance(raw, (bytes, bytearray)) else (raw or "")
                    if isinstance(content, (bytes, bytearray)):
                        content = bytes(content).decode("utf-8", errors="replace")
                    if is_room and ":\n" in content:
                        head, body = content.split(":\n", 1)
                        if head:
                            sender = head.strip()
                            content = body
                    if real_type == 49:
                        content = appmsg_to_text(content)
                    sender_info = contacts.get(sender, {})
                    is_self = (not is_room and talker == self_wxid) or sender == self_wxid
                    yield WeChatMessage(
                        talker=talker,
                        talker_name=talker_name,
                        is_chatroom=is_room,
                        sender=sender or talker,
                        sender_name=sender_info.get("name", sender or talker_name),
                        is_self=is_self,
                        msg_type=real_type,
                        create_time=int(create_time),
                        content=content.strip(),
                    )
        finally:
            con.close()
