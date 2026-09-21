"""匹配引擎黄金评测集（质量基准，pytest 常驻）。

数据在 tests/golden/matching_golden.json：persona 画像 + preset 偏好 + 岗位原文 + 期望。
用途：匹配引擎任何语义变更（升 ENGINE_VERSION 前后）必须让本套件全绿；
期望过期时应显式重标注数据文件，而不是改断言迁就实现。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.jobs import JobService
from jobhater.services.matching import ENGINE_VERSION, MatchService
from jobhater.services.profile import ProfileService

GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "matching_golden.json").read_text(encoding="utf-8")
)


def _build_profile(ps: ProfileService, spec: dict) -> str:
    p = ps.create_profile(spec["display_name"], spec.get("headline"))
    for sk in spec.get("skills", []):
        ps.add_skill(p.id, name=sk["name"], level=sk.get("level"), years=sk.get("years"),
                     aliases=sk.get("aliases", []))
    for e in spec.get("experiences", []):
        ps.add_experience(p.id, employer=e["employer"], title=e["title"],
                          tags=e.get("tags", []), description=e.get("description"))
    for ed in spec.get("educations", []):
        ps.add_education(p.id, school=ed["school"], degree=ed.get("degree"), major=ed.get("major"))
    return p.id


@pytest.fixture(scope="module")
def engine():
    """黄金集只针对当前引擎版本；文件与引擎版本不匹配时直接失败（强制同步重标注）。"""
    assert GOLDEN["engine_version"] == ENGINE_VERSION, (
        f"黄金集标注 engine_version={GOLDEN['engine_version']} 与引擎 {ENGINE_VERSION} 不一致："
        "引擎语义变更后请重跑标注并更新数据文件"
    )
    return


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["id"] for c in GOLDEN["cases"]])
def test_golden_matching(case, tmp_path, engine):
    db = tmp_path / f"gold_{case['id']}.db"
    apply_all(db)
    con = connect(db)
    try:
        ps = ProfileService(con)
        persona = GOLDEN["personas"][case["persona"]]
        pid = _build_profile(ps, persona)
        preset_spec = {**GOLDEN["preset_defaults"], **(case.get("preset") or {})}
        preset = ps.create_preset(pid, **preset_spec)
        js = JobService(con)
        js.ingest([case["job"]], source_id="golden")
        job = js.search("")[0]
        outcome = MatchService(con).evaluate(job, ps.match_view(pid), preset)

        exp = case["expect"]

        # eligible / 结论档
        if "eligible" in exp:
            assert outcome.eligible is exp["eligible"], (
                f"[{case['id']}] eligible={outcome.eligible} 期望 {exp['eligible']}；"
                f"gates={[(g.code, g.passed) for g in outcome.gate_reasons]}"
            )
        if "verdict" in exp:
            assert outcome.verdict == exp["verdict"], f"[{case['id']}] verdict={outcome.verdict}"
        if "rank_min" in exp:
            assert (outcome.rank_score or 0) >= exp["rank_min"], (
                f"[{case['id']}] rank={outcome.rank_score} < {exp['rank_min']}"
            )

        # gate 结论（子集断言：code ∈ 通过/未通过集合）
        failed = {g.code for g in outcome.gate_reasons if not g.passed}
        passed = {g.code for g in outcome.gate_reasons if g.passed}
        for code in exp.get("failed_gates_contains", []):
            assert code in failed, f"[{case['id']}] 期望未通过 gate {code}，实际未通过={sorted(failed)}"
        for code in exp.get("passed_gates_contains", []):
            assert code in passed, f"[{case['id']}] 期望通过 gate {code}，实际通过={sorted(passed)}"

        # 证据（matched/unmatched/旗标）
        ev = outcome.evidence
        for name in exp.get("matched_contains", []):
            assert name in ev.get("matched_skills", []), f"[{case['id']}] matched 缺 {name}"
        for name in exp.get("unmatched_contains", []):
            assert name in ev.get("unmatched_core_skills", []), f"[{case['id']}] unmatched 缺 {name}"
        for key in exp.get("evidence_has", []):
            assert key in ev, f"[{case['id']}] evidence 缺 {key}：{sorted(ev)}"
        for key in exp.get("evidence_absent", []):
            assert key not in ev, f"[{case['id']}] evidence 不应含 {key}：{sorted(ev)}"

        # 维度分（精确/上限）
        for dim, val in exp.get("dim_exact", {}).items():
            got = outcome.dims[dim].score
            assert got == val, f"[{case['id']}] {dim}={got} 期望精确 {val}"
        for dim, cap in exp.get("dim_max", {}).items():
            got = outcome.dims[dim].score
            assert got <= cap, f"[{case['id']}] {dim}={got} 应封顶 ≤{cap}"

        # 维度不确定性 / 依据文案（子串）
        for dim, frag in exp.get("dim_uncertainty", {}).items():
            unc = outcome.dims[dim].uncertainty or ""
            assert frag in unc, f"[{case['id']}] {dim}.uncertainty={unc!r} 缺 {frag!r}"
        for dim, frag in exp.get("reasons_contains", {}).items():
            joined = "；".join(outcome.dims[dim].reasons)
            assert frag in joined, f"[{case['id']}] {dim}.reasons 缺 {frag!r}：{joined}"

        # any_of：任一条件成立即过（用于多路径防御机制）
        if "any_of" in exp:
            alt = exp["any_of"]
            results = []
            for key in alt.get("evidence_has", []):
                results.append(key in ev)
            for dim, frag in alt.get("dim_uncertainty", {}).items():
                results.append(frag in (outcome.dims[dim].uncertainty or ""))
            assert any(results), f"[{case['id']}] any_of 全不成立：evidence={sorted(ev)}"
    finally:
        con.close()
