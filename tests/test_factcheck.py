#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_factcheck.py — 真实性校验对抗测试集 + 全引擎回归

运行：python3 tests/test_factcheck.py   （期望输出 ALL TESTS PASSED）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import factcheck, resume, scorer, store  # noqa: E402


def main() -> int:
    real = (store.ROOT / "data" / "profile" / "profile.json").exists()
    if real:
        profile = store.load("profile")
    else:
        # 公开环境（CI/新克隆）无真实画像：用夹具画像跑通用对抗用例（有真实画像时保持全量7项）
        profile = json.loads((Path(__file__).parent / "fixtures" / "minimal_profile.json").read_text(encoding="utf-8"))
    try:
        db = store.load("jobs")
    except FileNotFoundError:
        db = {"jobs": []}
    failures = []

    if real:
        true_case = ("真实内容放行", "- 以 DINOv2 为共享视觉骨干，取得 72.3% 情绪识别准确率  [ev:ev_paper]", True)
        footnote_case = ("分隔线脚注不误杀", "---\n*脚注说明*\n- 正常条目：GPA 3.8/5.0  [ev:ev_education]", True)
    else:
        true_case = ("真实内容放行（夹具）", "- 完成了测试项目A功能的设计与实现  [ev:ev_fixture]", True)
        footnote_case = ("分隔线脚注不误杀（夹具）", "---\n*脚注说明*\n- 正常条目：完成了测试项目A功能  [ev:ev_fixture]", True)
    CASES = [
        ("编造量化数字", "- 主导搭建推荐系统，QPS提升300%，服务500万日活用户  [ev:ev_fixture]", False),
        ("无引用经历", "- 独立完成分布式缓存系统设计", False),
        ("伪造证据ID", "- 完成模型训练与部署  [ev:ev_made_up]", False),
        true_case,
        ("注入伪造商业经历", "- 与XX公司合作完成商业项目（年收入过亿）  [ev:ev_fixture]", False),
        footnote_case,
    ]
    for name, text, should_pass in CASES:
        passed = factcheck.check(text, profile)["passed"]
        if passed != should_pass:
            failures.append(f"{name}: 期望{'通过' if should_pass else '拦截'}，实际{'通过' if passed else '拦截'}")

    if db.get("jobs"):
        audited, _ = resume.generate_resume(db["jobs"][0], profile)
        if not factcheck.check(audited, profile)["passed"]:
            failures.append("引擎简历回归: factcheck 未通过")

    # 评分器决定论回归：同输入两次评分必须逐字节一致
    if db.get("jobs"):
        job = db["jobs"][0]
        s1 = json.dumps(scorer.score_job(job, profile), ensure_ascii=False, sort_keys=True)
        s2 = json.dumps(scorer.score_job(job, profile), ensure_ascii=False, sort_keys=True)
        if s1 != s2:
            failures.append("评分器决定论: 同输入产生不同输出")

    if failures:
        print("FAILED TESTS:")
        for f in failures:
            print(" -", f)
        return 1
    n = len(CASES)
    print(f"ALL TESTS PASSED ({n} adversarial + engine regression + determinism，画像模式：{'真实' if real else '夹具'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
