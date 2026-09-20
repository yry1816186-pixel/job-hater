#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mapping.py — 外部信源数据 → Campus-Job-Agent 统一岗位契约的纯映射层

本模块只做字段映射与格式规范化，不做网络请求、不碰数据库、不过滤——
那些分别是 wenke_bridge（采集）、core.ingest（入库）的职责。

映射原则（与全系统一致）：
- 缺字段如实留空/None，绝不臆造补齐
- 来源信息保留在 source_platform / extras.origin，可溯源
- 批次/届别等线索进 keywords（校招信号检测在 ingest.detect_flags 消费它）

包含两组映射：
1. wenke JobItem → raw job（wenke-radar，MIT，见 repos/wenke-radar/LICENSE）
2. xiaozhao 紧凑 schema → raw job（xiaozhao-radar，Apache-2.0，见 repos/xiaozhao-radar/LICENSE）
"""
from __future__ import annotations

import datetime as dt
import re

# xiaozhao 的截止日期形如 "2026  10  31"（空格分隔），规范化为 ISO；无法解析则原样保留给 keywords
_XZ_DATE = re.compile(r"^(20\d{2})\s+(\d{1,2})\s+(\d{1,2})$")


def _get(obj, name, default=""):
    """兼容对象属性与字典键两种形态（wenke JobItem 是 dataclass，测试夹具常用 dict）。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def jobitem_to_raw(item, origin: str = "wenke-radar") -> dict:
    """wenke-radar 的 JobItem → 统一 raw job 契约。

    JobItem 字段：company/job_id/title/category/location/url/publish_time/tags/
    recruit_type/description（见 repos/wenke-radar/domain/models.py）。
    recruit_type 过滤不在本层做——采集层只采校招源，这里如实透传 tags 供 ingest 检测。
    """
    tags = _get(item, "tags", "") or ""
    keywords = [t.strip() for t in re.split(r"[,，;；/|]+", str(tags)) if t.strip()]
    return {
        "id": str(_get(item, "job_id", "") or ""),
        "title": str(_get(item, "title", "") or "").strip(),
        "company": str(_get(item, "company", "") or "").strip(),
        "department": str(_get(item, "category", "") or "") or None,
        "city": str(_get(item, "location", "") or "") or None,
        "salary": None,
        "experience_required": None,
        "education_required": None,
        "description": str(_get(item, "description", "") or "").strip(),
        "keywords": keywords,
        "url": str(_get(item, "url", "") or ""),
        "published_at": (str(_get(item, "publish_time", "") or "")[:10] or None),
        "extras": {
            "recruit_type": str(_get(item, "recruit_type", "") or ""),
            "origin": origin,
        },
    }


def xiaozhao_to_raw(entry: dict) -> dict:
    """xiaozhao-radar jobs.json 紧凑字段 → 统一 raw job 契约。

    字段语义（据 repos/xiaozhao-radar/README 与数据实测，2026-09-20）：
      c=公司  p=岗位方向（类别，非单一职位名）  l=地点（/分隔多城市）
      w=批次线索  d=截止（多为"招满即止"，少数 "2026  10  31"）
      s=信息来源  t=类型  ind=行业  u=报名/详情链接；e 字段全库为空，忽略。
    注意如实定位：这是「校招项目级线索」（公司+批次+入口），不是逐条职位 JD，
    因此 description 由已知字段拼装、不虚构任何 JD 内容。
    """
    deadline = None
    m = _XZ_DATE.match(str(entry.get("d", "")).strip())
    if m:
        try:
            deadline = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            deadline = None

    wave = str(entry.get("w", "")).strip()
    industry = str(entry.get("ind", "") or entry.get("t", "")).strip()
    src = str(entry.get("s", "")).strip()
    desc_parts = []
    if wave:
        desc_parts.append(f"批次：{wave.removeprefix('批次:').removeprefix('批次：')}")
    if industry:
        desc_parts.append(f"行业：{industry}")
    if src:
        desc_parts.append(f"信息来源：{src}")
    desc_parts.append("（校招项目级线索：报名入口与详情见链接，以官网为准）")

    keywords = [k for k in (wave, industry) if k]
    # 批次进标题：同一公司同一方向的「秋招正式批」与「暑期实习」是不同机会、不同报名入口，
    # 必须能共存于岗位库（去重身份由此区分），榜单上也能一眼看出批次
    title = str(entry.get("p", "")).strip() or "校招（方向未标注）"
    wave_clean = wave.removeprefix("批次:").removeprefix("批次：").strip()
    if wave_clean:
        title = f"{title}（{wave_clean}）"
    return {
        # id 留空：由 ingest.normalize 以 公司|岗位方向|url 哈希生成，保证稳定且无碰撞风险
        "id": None,
        "title": title,
        "company": str(entry.get("c", "")).strip(),
        "department": None,
        "city": str(entry.get("l", "")).strip() or None,
        "salary": None,
        "experience_required": None,
        "education_required": None,
        "description": "｜".join(desc_parts),
        "keywords": keywords,
        "url": str(entry.get("u", "") or ""),
        "published_at": None,
        "deadline": deadline,
        "extras": {"origin": "xiaozhao-radar (Apache-2.0)", "lead": True},
    }
