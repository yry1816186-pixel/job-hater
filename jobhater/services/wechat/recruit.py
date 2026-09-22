"""微信消息招聘信息识别引擎（纯规则、可解释、零网络）。

设计目标：从群聊/私聊/公众号文本中识别「正在发布招聘」的消息，并抽取结构化字段。
不调用任何外部服务——全部判定基于透明规则，每条命中携带 evidence（触发规则清单），
置信度是各信号的加权之和（可解释、可调参、可测试）。

两类误报是主要敌人，规则围绕它们设计：
1. 求职者在群里问「有内推吗/帮我看看简历」——触发词命中但方向相反 → 负信号词压分；
2. 闲聊提及「招聘」字样——没有可抽取的字段结构 → 低置信度自然过滤。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 词典

# 招聘意图强信号（消息在「发布岗位」的标志）
INTENT_STRONG = (
    "校园招聘", "校園招聘", "校招", "秋招", "春招", "暑期实习", "寒假实习", "日常实习",
    "内推", "員工內推", "急招", "急聘", "诚聘", "招聘", "招贤纳士", "招人啦", "招人",
    "岗位名称", "职位描述", "工作职责", "任职要求", "岗位要求", "薪资范围", "福利待遇",
    "网申", "宣讲会", "空中宣讲", "campus hiring", "join us", "we're hiring", "we are hiring",
    "hc", "headcount", "jd如下", "base地", "发车", "接力", "offer收割",
)
# 届别/人群信号
COHORT_RE = re.compile(r"20(\d{2})\s*届")
COHORT_WORDS = ("应届", "届毕业生", "届本科", "届硕士", "届博士", "应届生", "毕业年级", "校園徵才")
KIND_WORDS = {
    "campus": ("校招", "校园招聘", "秋招", "春招", "campus", "宣讲会", "网申", "应届"),
    "intern": ("实习", "intern", "internship", "日常实习", "暑期实习", "寒假实习", "trainee"),
    "social": ("社招", "社会招聘", "工作经验", "三年以上", "五年以上", "3年以上", "5年以上"),
}
# 升学招生信号（推免/保研/夏令营等研究生招生通知——是「升学」不是「就业招聘」；
# 命中标记 kind="edu" 并大幅压分：雷达页可见但不进岗位库）
EDU_ADMISSION_WORDS = (
    "推免", "推荐免试", "免试研究生", "保研", "夏令营", "优秀大学生",
    "招生简章", "招收攻读", "研究生招生", "推免生", "研究生复试", "预推免",
)
EDU_ADMISSION_STRONG = ("推免", "推荐免试", "免试研究生", "预推免", "研究生招生")
# 求职者方向信号（负分：是「找工作的人」不是「发岗位的人」）
SEEKER_WORDS = (
    "求内推", "求推荐", "有没有内推", "有无内推", "帮我内推", "可以内推吗", "还能内推吗",
    "请问还招", "还招人吗", "招不招", "在招吗", "怎么投", "怎么申请", "投递入口", "发我一份",
    "我的简历", "求一份", "带带我", "收留", "捞一捞", "帮朋友问", "接私活", "找兼职的",
)
# 岗位词典（常见职位；用于 title 识别与打分）
TITLE_WORDS = (
    "工程师", "开发工程师", "算法工程师", "测试工程师", "软件开发", "后端", "前端", "全栈",
    "产品经理", "产品实习生", "产品运营", "运营", "运营专员", "运营实习生", "数据分析师",
    "数据分析", "算法", "机器学习", "深度学习", "大模型", "llm", "nlp", "cv工程师",
    "java", "golang", "python", "c++", "c#", "rust", "安卓", "android", "ios", "客户端",
    "嵌入式", "固件", "硬件工程师", "电气工程师", "机械工程师", "结构工程师", "工艺工程师",
    "设计师", "视觉设计", "交互设计", "ui设计", "ux", "平面设计", "动画", "三维", "建模师",
    "管培生", "培训生", "管理培训生", "储备干部", "管培", "菁英计划", "领军计划", "星计划",
    "人力", "hr", "人事", "行政", "财务", "会计", "审计", "法务", "合规", "风控",
    "市场", "品牌", "公关", "销售", "商务", "bd", "渠道", "客户经理", "售前", "售后",
    " solution", "咨询顾问", "战略", "投资", "研究员", "科研", "助理", "专员", "文员",
    "客服", "供应链", "物流", "采购", "质检", "翻译", "编辑", "记者", "编导", "剪辑",
    "游戏策划", "数值策划", "文案", "主播", "运营经理", "项目经理", "项目经理", "架构师",
)
# 公司后缀（公司名抽取的第二优先级模式）
COMPANY_SUFFIX_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9·•（）()]{2,28}?"
    r"(?:科技|信息技术|网络|智能|数据|云计算|人工智能|互联网|软件|信息|数字|传媒|文化|教育|医药|生物|医疗|新能源|汽车|半导体|芯片|电子|通信|金融|证券|基金|银行|咨询|置业|地产|建筑|能源|环境|制造|装备|重工|物流|商贸|百货|食品|服饰|时尚|传媒))"
    r"(?:有限|股份)?公司|"
    r"([\u4e00-\u9fa5A-Za-z0-9·•]{2,20}(?:集团|股份|银行|证券|基金|研究所|研究院|学院|大学|事务所|实验室|工作室|工作室|官方号))",
    re.IGNORECASE,
)
CITY_WORDS = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "苏州",
    "天津", "重庆", "长沙", "郑州", "青岛", "合肥", "福州", "厦门", "东莞", "佛山",
    "宁波", "无锡", "济南", "沈阳", "大连", "哈尔滨", "长春", "石家庄", "太原", "南昌",
    "昆明", "贵阳", "南宁", "兰州", "乌鲁木齐", "海口", "呼和浩特", "银川", "西宁", "拉萨",
    "珠海", "中山", "惠州", "常州", "绍兴", "嘉兴", "南通", "镇江", "扬州", "徐州",
    "芜湖", "泉州", "烟台", "威海", "株洲", "洛阳", "咸阳", "绵阳", "remote", "远程", "全国", "多地",
)
EDU_RE = re.compile(r"(博士|硕士|研究生|本科|大专|专科|学历不限|本科及以上|硕士及以上|统招本科)")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:联系电话?|简历投递|联系方式|tel)[：:\s]*((?:\+?86[-\s]?)?1[3-9]\d{9}|0\d{2,3}-?\d{7,8})")
URL_RE = re.compile(r"https?://[^\s，。；、）)】\]]+|www\.[^\s，。；、）)】\]]+")
REFERRAL_CODE_RE = re.compile(r"(?:内推码|推荐码|口令)[：:\s]*([A-Za-z0-9]{4,16})")
DEADLINE_RE = re.compile(
    r"(?:截止|ddl|DDL|Ddl|deadline|Deadline|期前|之前投递|前投递|尽快投递)[^\d]{0,12}"
    r"((?:20\d{2})[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}日?|\d{1,2}\s*[月./-]\s*\d{1,2}日?(?:号)?)"
)
LABEL_COMPANY_RE = re.compile(
    r"(?:公司|单位|企业|employer|company|招聘方?)[名]?\s*[:：]\s*([^\n，,；;。]{2,40})"
)
# 无「有限公司」后缀也常直接出现在招聘帖里的知名企业（裸名词典，透明可维护）
KNOWN_BRANDS = (
    "字节跳动", "腾讯", "阿里巴巴", "阿里", "蚂蚁集团", "华为", "美团", "百度", "小米", "京东",
    "网易", "拼多多", "哔哩哔哩", "B站", "米哈游", "莉莉丝", "叠纸", "鹰角", "完美世界",
    "蔚来", "理想汽车", "小鹏汽车", "比亚迪", "宁德时代", "大疆", "科大讯飞", "商汤", "旷视",
    "快手", "抖音", "小红书", "得物", "携程", "贝壳", "链家", "京东物流", "顺丰", "中兴",
    "OPPO", "vivo", "荣耀", "传音", "联想", "海尔", "美的", "格力", "海康威视", "大华",
    "中国电信", "中国移动", "中国联通", "国家电网", "南方电网", "中石油", "中石化", "中海油",
    "工商银行", "建设银行", "农业银行", "中国银行", "招商银行", "浦发银行", "兴业银行", "民生银行",
    "中信证券", "中信建投", "国泰君安", "华泰证券", "广发证券", "中金公司", "汇添富", "易方达",
    "普华永道", "德勤", "毕马威", "安永", "麦肯锡", "波士顿咨询", "贝恩", "联合利华", "宝洁",
    "强生", "辉瑞", "罗氏", "诺华", "恒瑞医药", "药明康德", "百济神州", "信达生物", "再鼎医药",
    "京东方", "搜狐畅游", "搜狐", "新浪", "58同城", "奇安信", "深信服", "TP-LINK",
)
# 品牌匹配用最长优先序（否则「京东方」会被子串「京东」抢先命中）
_BRANDS_BY_LEN = tuple(sorted(KNOWN_BRANDS, key=len, reverse=True))
LABEL_TITLE_RE = re.compile(
    r"(?:岗位|职位|职务|position|role|title)[名称]?\s*[:：]\s*([^\n，,；;。]{2,40})"
)
LABEL_CITY_RE = re.compile(r"(?:城市|地点|工作地|base|Base|BASE)[：:\s]*([^\n，,；;。]{2,30})")
LABEL_SALARY_RE = re.compile(r"(?:薪资|薪酬|待遇|工资|salary)[：:\s]*([^\n，,；;。]{2,30})")

_MAX_TEXT = 6000  # 超长文本截断（保护正则性能；招聘正文极少超过这个量级）


@dataclass
class RecruitHit:
    """一条招聘信息识别结果。"""

    company: str | None = None
    title: str | None = None
    cities: list[str] = field(default_factory=list)
    salary: str | None = None
    education: str | None = None
    cohort: int | None = None  # 如 2027；None=未提及届别
    kind: str = "unknown"  # campus / intern / social / edu(升学招生) / unknown
    deadline: str | None = None
    apply_method: str | None = None  # 邮箱/链接/内推码（原文摘录）
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)  # 触发规则（可解释性）
    is_recruiter_side: bool = True  # False=疑似求职者提问（默认发布方）

    def to_dict(self) -> dict:
        return {
            "company": self.company, "title": self.title, "cities": self.cities,
            "salary": self.salary, "education": self.education, "cohort": self.cohort,
            "kind": self.kind, "deadline": self.deadline, "apply_method": self.apply_method,
            "confidence": round(self.confidence, 3), "evidence": self.evidence,
            "is_recruiter_side": self.is_recruiter_side,
        }


def _findall_words(text: str, words: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [w for w in words if w in low]


def _sender_as_company(sender_name: str) -> str | None:
    """发送者/群名兜底抽公司。群名（含「群」字或明显群名模式）不是公司名，拒绝。"""
    if not sender_name or "群" in sender_name:
        return None
    if m := COMPANY_SUFFIX_RE.search(sender_name):
        name = (m.group(1) or m.group(2) or "").strip()
        if 2 <= len(name) <= 40:
            return name
    if brand := next((b for b in _BRANDS_BY_LEN if b in sender_name), None):
        return brand
    return None


def extract_company(text: str, sender_name: str | None = None) -> tuple[str | None, str | None]:
    """抽取公司名。优先标签行，其次裸名词典/后缀模式。返回 (公司名, 依据)。"""
    if m := LABEL_COMPANY_RE.search(text):
        name = m.group(1).strip().removesuffix("招聘").strip()
        if 2 <= len(name) <= 40:
            return name, "标签行"
    if brand := next((b for b in _BRANDS_BY_LEN if b in text), None):
        return brand, "企业名词典"
    if m := COMPANY_SUFFIX_RE.search(text):
        name = (m.group(1) or m.group(2) or "").strip()
        if 2 <= len(name) <= 40:
            return name, "公司后缀模式"
    # 公众号/发送者昵称兜底：如「XX科技招聘」（群名已过滤）
    if sender_name:
        if name := _sender_as_company(sender_name):
            return name, "发送者名称"
    return None, None


def extract_title(text: str) -> tuple[str | None, str | None]:
    if m := LABEL_TITLE_RE.search(text):
        t = m.group(1).strip().rstrip(".。…·")
        # 标签值是链接（如「投递：https://…」被职位标签捕获）不是岗位名，跳过
        if 2 <= len(t) <= 40 and "http" not in t and "www." not in t:
            return t, "标签行"
    hits = _findall_words(text, TITLE_WORDS)
    if hits:
        # 取最长命中（更具体的岗位词）
        best = max(hits, key=len)
        return best, "岗位词典"
    return None, None


def extract_cohort(text: str) -> tuple[int | None, bool]:
    """返回 (届别年份, 是否提到应届)。"""
    years = [2000 + int(d) for d in COHORT_RE.findall(text) if 20 <= int(d) <= 35]
    fresh = any(w in text for w in COHORT_WORDS)
    return (min(years) if years else None), fresh


def extract_kind(text: str) -> str:
    scores = {k: len(_findall_words(text, ws)) for k, ws in KIND_WORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "unknown"


def analyze(text: str, sender_name: str | None = None, talker_name: str | None = None) -> RecruitHit | None:
    """识别一条（已聚合的）消息文本。返回 None 表示不是招聘信息。

    ``sender_name``/``talker_name`` 参与公司名兜底抽取（群名片常含公司名）。
    """
    text = text.strip()
    if not text or len(text) < 8:
        return None
    text = text[:_MAX_TEXT]
    low = text.lower()

    # ---- 负信号：求职者方向 ----
    seeker_hits = _findall_words(text, SEEKER_WORDS)
    # ---- 升学招生信号（推免/保研通知常携带届别+宣讲会词，是噪声主源之一）----
    edu_adm_hits = _findall_words(text, EDU_ADMISSION_WORDS)
    is_edu_admission = len(edu_adm_hits) >= 2 or any(w in text for w in EDU_ADMISSION_STRONG)
    # ---- 意图信号 ----
    intent_hits = _findall_words(low, INTENT_STRONG)
    # ---- 字段抽取 ----
    company, company_src = extract_company(text, sender_name or talker_name)
    title, title_src = extract_title(text)
    # 入口门槛：意图词命中，或「岗位词 + 结构化字段（薪资/学历/联系方式）」的社招式 JD
    structured = bool(LABEL_SALARY_RE.search(text) or EDU_RE.search(text) or EMAIL_RE.search(text) or PHONE_RE.search(text))
    if not intent_hits and "招" not in text:
        if not (title and structured):
            return None
    cities = [c for c in CITY_WORDS if c in low]
    cohort, fresh = extract_cohort(text)
    kind = extract_kind(text)
    edu = EDU_RE.search(text)
    salary_m = LABEL_SALARY_RE.search(text)
    deadline_m = DEADLINE_RE.search(text)
    email = EMAIL_RE.search(text)
    url = URL_RE.search(text)
    phone = PHONE_RE.search(text)
    ref_code = REFERRAL_CODE_RE.search(text)

    hit = RecruitHit()
    hit.company = company
    hit.title = title
    hit.cities = cities[:5]
    hit.education = edu.group(1) if edu else None
    hit.cohort = cohort
    hit.kind = kind
    hit.deadline = deadline_m.group(1) if deadline_m else None
    hit.salary = salary_m.group(1).strip() if salary_m else None
    if email:
        hit.apply_method = f"邮箱 {email.group(0)}"
    elif url:
        hit.apply_method = f"链接 {url.group(0)[:80]}"
    elif phone:
        hit.apply_method = f"电话 {phone.group(1)}"
    elif ref_code:
        hit.apply_method = f"内推码 {ref_code.group(1)}"

    # ---- 置信度：透明加权 ----
    score = 0.0
    ev: list[str] = []
    if intent_hits:
        score += 0.22
        ev.append(f"意图词×{len(intent_hits)}（{'、'.join(intent_hits[:3])}）")
    if company:
        score += 0.20
        ev.append(f"公司={company}（{company_src}）")
    if title:
        score += 0.18
        ev.append(f"岗位={title}（{title_src}）")
    if cohort:
        score += 0.12
        ev.append(f"届别={cohort}届")
    elif fresh:
        score += 0.06
        ev.append("提及应届")
    if kind != "unknown":
        score += 0.10
        ev.append(f"类型={kind}")
    if hit.salary:
        score += 0.08
        ev.append(f"薪资={hit.salary}")
    if cities:
        score += 0.06
        ev.append(f"城市={'、'.join(cities[:3])}")
    if hit.education:
        score += 0.04
        ev.append(f"学历={hit.education}")
    if hit.apply_method:
        score += 0.10
        ev.append(f"投递方式={hit.apply_method[:30]}")
    if hit.deadline:
        score += 0.04
        ev.append(f"截止={hit.deadline}")
    # 结构性加分：多行多字段（真实 JD 的形态：职责/要求分条）
    if re.search(r"(?:职责|要求|任职|职位|福利|待遇|描述)[^\n]*\n", text) and text.count("\n") >= 4:
        score += 0.08
        ev.append("JD 分段结构")

    # ---- 负信号压分（求职者提问方向） ----
    if seeker_hits:
        hit.is_recruiter_side = False
        penalty = 0.30 if len(seeker_hits) >= 2 else 0.18
        score -= penalty
        ev.append(f"⚠求职者方向词×{len(seeker_hits)}（{'、'.join(seeker_hits[:2])}）-{penalty}")

    # ---- 升学招生压分（推免/保研通知不是就业岗位；保留雷达可见性，压到岗位库导入线以下）----
    if is_edu_admission:
        hit.kind = "edu"
        score *= 0.45
        ev.append(f"⚠升学招生信号×{len(edu_adm_hits)}（{'、'.join(edu_adm_hits[:3])}）-压分55%")

    hit.confidence = max(0.0, min(1.0, score))
    hit.evidence = ev
    if hit.confidence < 0.35:
        return None  # 低置信度直接过滤（阈值随测试校准）
    return hit
