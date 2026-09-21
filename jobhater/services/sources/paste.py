"""粘贴导入：任意招聘网页 JD 自由文本 → 结构化草稿（§7 通用入口）。

解析原则：
- 只抽高置信结构（明确标签"职位：/公司：/薪资："、日期/薪资/经验/学历模式、URL）；
- 抽不出的字段留 None，标注 uncertainty——绝不猜；
- 输出是「草稿」：UI 展示给用户确认/修改后才入库（human in the loop）。
"""
from __future__ import annotations

import re
from typing import Any

from jobhater import textproc as tp
from jobhater.services.sources.base import HealthReport

_LABEL_PATTERNS = {
    "title": re.compile(r"^\s*(?:职位|岗位|招聘职位|职位名称|job title)\s*[:：]\s*(.+)$", re.M | re.I),
    "company": re.compile(r"^\s*(?:公司|企业|单位|雇主|招聘单位|公司名称)\s*[:：]\s*(.+)$", re.M | re.I),
    "city": re.compile(r"^\s*(?:城市|地点|工作地点|工作城市|base|工作地)\s*[:：]\s*(.+)$", re.M | re.I),
    "department": re.compile(r"^\s*(?:部门|事业部|团队)\s*[:：]\s*(.+)$", re.M | re.I),
    "experience": re.compile(r"^\s*(?:经验|工作经验|经验要求|工作年限)\s*[:：]\s*(.+)$", re.M | re.I),
    "education": re.compile(r"^\s*(?:学历|学历要求|教育背景)\s*[:：]\s*(.+)$", re.M | re.I),
    "salary": re.compile(r"^\s*(?:薪资|薪水|待遇|薪酬|月薪|薪资范围)\s*[:：]\s*(.+)$", re.M | re.I),
}
_URL_RE = re.compile(r"https?://[^\s）)】\]\"'<>,，。]+")
_EDU_RE = re.compile(r"(博士|硕士|研究生|本科|学士|大专|专科|学历不限|不限学历)")
_CITIES = [
    "北京", "上海", "深圳", "广州", "杭州", "成都", "南京", "武汉", "西安", "苏州",
    "天津", "长沙", "重庆", "郑州", "青岛", "合肥", "福州", "厦门", "东莞", "佛山",
    "宁波", "无锡", "济南", "沈阳", "大连", "昆明", "贵阳", "南昌", "太原", "石家庄",
    "哈尔滨", "长春", "兰州", "乌鲁木齐", "南宁", "海口", "呼和浩特", "银川", "西宁", "拉萨",
    "香港", "澳门", "台北", "珠海", "常州", "温州", "绍兴", "嘉兴", "南通", "徐州",
    "洛阳", "芜湖", "烟台", "威海", "泉州", "漳州", "中山", "惠州",
]


def parse_jd_text(text: str, *, url: str | None = None) -> dict[str, Any]:
    """自由文本 → 结构化草稿。附带解析置信说明（extras.parse_notes）。"""
    text = text.strip()
    if not text:
        raise ValueError("JD 文本为空")
    notes: list[str] = []
    draft: dict[str, Any] = {"description": tp.clean_whitespace(text)}

    def _label(field: str) -> str | None:
        m = _LABEL_PATTERNS[field].search(text)
        if m:
            v = m.group(1).strip().strip("；;，,。")
            return v or None
        return None

    for field in ("title", "company", "city", "department", "experience", "education", "salary"):
        v = _label(field)
        if v:
            draft[field] = v
            notes.append(f"{field}：来自文本标签")

    # 标题兜底：首行短文本（≤28字、无句号）视作标题候选，标注不确定
    if "title" not in draft:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if first and len(first) <= 28 and "。" not in first and not first.endswith("，"):
            draft["title"] = first
            notes.append("title：取首行（无标签，需人工确认）")
    # 公司兜底：行内「XX（公司/集团/科技/有限公司）」不强猜——留空
    # 城市兜底：已知城市名命中（含「工作城市：」以外的正文提及）
    if "city" not in draft:
        hit = [c for c in _CITIES if c in text[:400]]
        if hit:
            draft["city"] = hit[0]
            notes.append(f"city：正文检出 {hit[0]}（需人工确认）")
    # 薪资兜底：正文任意位置的薪资模式
    if "salary" not in draft:
        for m in re.finditer(r"(\d+(?:\.\d+)?[kK千万元]?\s*[-~–—至]\s*\d+(?:\.\d+)?[kK千万]([·\/]\d+薪)?)", text):
            draft["salary"] = m.group(1).strip()
            notes.append("salary：正文模式命中")
            break
    # 经验/学历兜底
    if "experience" not in draft:
        years = tp.parse_experience_years(text[:600])
        if years is not None:
            draft["experience"] = "经验不限" if years == 0 else f"{years:g}年以上"
            notes.append("experience：正文解析")
    if "education" not in draft:
        m = _EDU_RE.search(text[:600])
        if m:
            draft["education"] = m.group(1)
            notes.append("education：正文解析")
    # URL：显式参数优先，其次正文第一个链接
    if url:
        draft["url"] = url
    else:
        m = _URL_RE.search(text)
        if m:
            draft["url"] = m.group(0)
    draft["parse_notes"] = notes
    # 用户直接阅读的提示（中文标签）；键名与草稿字段一一对应
    draft["needs_review_fields"] = [
        label for field, label in (
            ("title", "岗位标题"), ("company", "公司"), ("city", "城市"), ("salary", "薪资"),
        )
        if not draft.get(field)
    ]
    if not draft.get("company"):
        notes.append("company：未能解析——请补填公司名（必填）")
    return draft


class PasteAdapter:
    """手动粘贴信源（§7）：不主动产出，由用户动作触发 parse_jd_text。"""

    id = "manual"
    display_name = "手动粘贴/导入"

    def capabilities(self) -> dict:
        return {"search": False, "fetch_detail": False, "needs_browser": False,
                "trigger": "user_paste"}

    def rate_policy(self) -> dict:
        return {}

    def produce(self, query=None):
        return []

    def health_check(self) -> HealthReport:
        return HealthReport(ok=True, message="本地导入，无外部依赖")
