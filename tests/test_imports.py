#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_imports.py — 全核心模块导入与最小功能烟测

背景：resume.py 重构删除 EXP_TO_EV 后，interview.py 的导入炸了，
而既有测试没覆盖 interview 的导入路径。本测试锁死这一类回归。
运行：python3 tests/test_imports.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MINIMAL_PROFILE = {
    "identity": {"name": "测试用户", "cohort_label": "2027届应届生"},
    "education": [{"school": "某大学", "major": "某专业", "degree": "本科",
                   "start": "2023-09", "end": "2027-06"}],
    "skills": [{"name": "Python", "level": "熟练", "evidence": ["ev1"]}],
    "experiences": [{"id": "exp_1", "type": "项目", "name": "X项目", "role": "负责人",
                     "tags": ["多模态大模型"], "highlights": ["完成了A"]}],
    "publications": [], "awards": [], "honors": [],
    "preferences": {"target_roles": ["测试"], "target_cities": ["上海"], "salary_min_k": 10},
    "evidence_index": {"ev1": "夹具证据"},
}
MINIMAL_JOB = {"id": "t1", "title": "测试岗", "company": "某公司", "city": "上海",
               "description": "负责大模型产品测试，熟悉Python者优先。", "keywords": []}


def main() -> int:
    ok = True
    from core import cli, dashboard, factcheck, ingest, interview, resume, risk, scorer, store, upskill  # noqa: F401
    print("✅ 全部 core 模块可导入（含 cli 的全量 import 路径）")

    s = scorer.score_job(MINIMAL_JOB, MINIMAL_PROFILE)
    ok &= ("total" in s and "dims" in s)
    print("✅ scorer.score_job 可执行")

    audited, visible = resume.generate_resume(MINIMAL_JOB, MINIMAL_PROFILE)
    ok &= ("[ev:" in audited and "[ev:" not in visible)
    print("✅ resume.generate_resume 审计版带锚点/用户版干净")

    resume.render_html(visible, "测试")
    print("✅ resume.render_html 可执行")

    qs = interview.build_question_set(MINIMAL_JOB, MINIMAL_PROFILE)
    ok &= bool(qs)
    print("✅ interview.build_question_set 可执行（EV 映射数据驱动后不炸）")

    plan = upskill.analyze(MINIMAL_JOB, MINIMAL_PROFILE)
    ok &= bool(plan)
    print("✅ upskill.analyze 可执行")

    if not ok:
        print("\n存在未通过的烟测项")
        return 1
    print("\nALL TESTS PASSED (imports + smoke)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
