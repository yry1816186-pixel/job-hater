"""示例岗位包：新用户在录入自己的信息前，就能看到系统的完整形态
（匹配分怎么算、ATS 报告长什么样、看板怎么流转）。

设计立场：
- 示例岗位 = 普通岗位数据，走同一条 ingest 去重链（重复导入幂等）；
- 雇主名为虚构（避免暗示任何真实公司的招聘行为），JD 文本为真实形态的合成内容；
- 不建演示画像、不产生演示投递——用户自己的求职数据不被污染；
- 清除只删「未被投递引用」的示例岗位（applications.job_id 是 RESTRICT，
  已投递过的岗位如实保留并计数告知）。
"""
from __future__ import annotations

import sqlite3

from jobhater.db.connection import transaction
from jobhater.services.jobs import JobService

DEMO_SOURCE_ID = "demo"

# 8 条覆盖不同方向/城市/批次/薪资形态的示例岗位（雇主均为虚构）
DEMO_JOBS: list[dict] = [
    {
        "title": "后端开发工程师（2027届校招）",
        "company": "星辰科技",
        "city": "杭州",
        "salary": "25K-40K·16薪",
        "education": "本科",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 负责核心交易系统的服务端设计与开发，保障高并发场景下的稳定性；"
            "2. 参与技术方案评审，推动代码质量与可观测性建设；3. 与产品、算法团队协作，"
            "完成业务需求的技术落地。【任职要求】1. 计算机相关专业本科及以上学历；"
            "2. 熟悉 Java 或 Golang，了解常用框架（Spring Boot / Gin）；3. 熟悉 MySQL、Redis，"
            "了解索引优化与缓存策略；4. 了解消息队列（Kafka/RocketMQ）与分布式系统基础；"
            "5. 有实习或开源项目经验者优先。校招批次，面向2027届毕业生。"
        ),
        "keywords": ["Java", "Golang", "MySQL", "Redis", "Kafka", "分布式"],
    },
    {
        "title": "前端开发工程师（校招）",
        "company": "云图智能",
        "city": "深圳",
        "salary": "22K-35K",
        "education": "本科",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 负责数据可视化平台的前端开发与交互实现；2. 参与组件库建设与 "
            "性能优化；3. 与设计师协作还原视觉稿，保障多端体验一致。【任职要求】"
            "1. 熟悉 HTML/CSS/JavaScript/TypeScript；2. 熟悉 React 或 Vue 生态，理解组件化与状态管理；"
            "3. 了解 Webpack/Vite 构建与前端性能优化手段；4. 对数据可视化（ECharts/D3）有兴趣者优先。"
            "面向2027届校招生，有完整项目经历加分。"
        ),
        "keywords": ["React", "TypeScript", "Vue", "Vite", "可视化"],
    },
    {
        "title": "算法工程师-自然语言处理",
        "company": "山海数据",
        "city": "北京",
        "salary": "30K-50K·15薪",
        "education": "硕士",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 参与大模型在垂直场景的微调与评测；2. 负责文本理解、信息抽取管道的"
            "算法研发；3. 跟进前沿论文并推动落地实验。【任职要求】1. 计算机、数学相关专业硕士及以上；"
            "2. 扎实的机器学习/深度学习基础，熟悉 PyTorch；3. 有 NLP 相关科研或实习经历，"
            "熟悉 Transformer 结构与微调技术（LoRA/RLHF 方向均可）；4. 熟悉 Python，能独立完成实验闭环。"
        ),
        "keywords": ["PyTorch", "NLP", "深度学习", "Python", "Transformer"],
    },
    {
        "title": "数据分析师（秋招）",
        "company": "灯塔金融科技",
        "city": "上海",
        "salary": "18K-28K",
        "education": "本科",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 负责业务核心指标的监控体系与归因分析；2. 沉淀分析报告，支撑运营与"
            "风控决策；3. 参与数据仓库模型评审。【任职要求】1. 统计、数学、计算机相关专业本科及以上；"
            "2. 熟悉 SQL，能写出高效的多表关联查询；3. 掌握 Python 数据分析栈（pandas/numpy）；"
            "4. 了解 A/B 测试原理；5. 表达清晰，能把分析结论讲给非技术同学。"
        ),
        "keywords": ["SQL", "Python", "pandas", "A/B测试", "数据分析"],
    },
    {
        "title": "测试开发工程师（2027届）",
        "company": "疾风网络",
        "city": "广州",
        "salary": "20K-30K",
        "education": "本科",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 负责游戏平台服务的自动化测试体系建设；2. 开发测试工具与平台，"
            "提升回归效率；3. 参与线上问题排查与质量复盘。【任职要求】1. 本科及以上，计算机相关专业；"
            "2. 至少掌握一门语言（Python/Java/Go）；3. 熟悉接口测试与常用自动化框架（pytest/RestAssured）；"
            "4. 了解 CI/CD 流程（Jenkins/GitLab CI）；5. 对质量保障有热情，逻辑严密。"
        ),
        "keywords": ["Python", "自动化测试", "pytest", "CI/CD", "接口测试"],
    },
    {
        "title": "产品经理（校园招聘）",
        "company": "拾光教育",
        "city": "北京",
        "salary": "18K-26K",
        "education": "本科",
        "experience": "应届",
        "description": (
            "【岗位职责】1. 负责 K12 学习工具的功能规划与需求撰写；2. 跟进用户反馈，驱动产品迭代；"
            "3. 协调设计、研发、运营推进项目落地。【任职要求】1. 本科及以上，专业不限，教育/心理背景加分；"
            "2. 逻辑清晰，文档能力强（PRD/竞品分析）；3. 有校园社团或实习中的项目主导经验；"
            "4. 对教育产品有真实热情，能共情学生与家长。"
        ),
        "keywords": ["产品经理", "PRD", "竞品分析", "需求分析"],
    },
    {
        "title": "Go 后端开发实习生",
        "company": "远航汽车智能",
        "city": "上海",
        "salary": "300-400元/天",
        "education": "本科",
        "experience": "在校生",
        "description": (
            "【岗位职责】1. 参与车联网云端服务的开发与维护；2. 编写单元测试与接口文档；"
            "3. 参与代码评审，学习工程规范。【任职要求】1. 在校本科生/研究生，每周可实习4天及以上，"
            "持续3个月以上；2. 熟悉 Go 语言，了解 goroutine 与 channel；3. 熟悉 Linux 常用命令与 Git；"
            "4. 了解 gRPC/protobuf 者优先。实习表现优秀可转正。"
        ),
        "keywords": ["Go", "gRPC", "Linux", "Git"],
    },
    {
        "title": "安全工程师（渗透测试方向）",
        "company": "青梧云安全",
        "city": "成都",
        "salary": "20K-35K",
        "education": "本科",
        "experience": "1-3年",
        "description": (
            "【岗位职责】1. 对客户系统开展渗透测试与红队评估；2. 输出专业测试报告与修复建议；"
            "3. 跟踪新漏洞并复现分析。【任职要求】1. 本科及以上，计算机相关专业；2. 熟悉 Web 安全"
            "常见漏洞原理（OWASP Top10）与利用方式；3. 熟练使用 Burp Suite、Nmap 等工具，能独立完成"
            "渗透项目；4. 有 CTF 竞赛获奖或 SRC 排名者优先；5. 恪守职业道德与授权边界。社招岗位。"
        ),
        "keywords": ["渗透测试", "Web安全", "Burp Suite", "OWASP"],
    },
]


class DemoService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def seed(self) -> dict:
        """导入示例岗位（幂等：dedupe 链自动跳过已存在的）。"""
        js = JobService(self.con)
        js.ensure_source(DEMO_SOURCE_ID, "manual_paste", "示例数据（可一键清除）")
        stats = js.ingest(DEMO_JOBS, source_id=DEMO_SOURCE_ID)
        return {
            "added": stats.added,
            "deduped": stats.deduped_exact + stats.deduped_near,
            "note": "示例岗位已就绪：去「岗位收件箱」看匹配分，点开任意岗位可试 ATS 扫描与投递看板。",
        }

    def clear(self) -> dict:
        """删除未被投递引用的示例岗位；被引用的如实保留并告知数量。"""
        with transaction(self.con):
            referenced = {
                r["job_id"] for r in self.con.execute(
                    "SELECT DISTINCT job_id FROM applications"
                ).fetchall()
            }
            rows = self.con.execute(
                "SELECT id FROM job_postings WHERE source_id=?", (DEMO_SOURCE_ID,)
            ).fetchall()
            deletable = [r["id"] for r in rows if r["id"] not in referenced]
            for jid in deletable:
                self.con.execute("DELETE FROM match_results WHERE job_id=?", (jid,))
                self.con.execute("DELETE FROM feedback_events WHERE job_id=?", (jid,))
                self.con.execute("DELETE FROM job_postings WHERE id=?", (jid,))
        return {"deleted": len(deletable), "kept": len(rows) - len(deletable)}

    def status(self) -> dict:
        row = self.con.execute(
            "SELECT COUNT(*) AS n FROM job_postings WHERE source_id=?", (DEMO_SOURCE_ID,)
        ).fetchone()
        return {"demo_jobs": row["n"]}
