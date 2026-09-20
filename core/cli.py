#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cli.py — Campus-Job-Agent 统一命令行入口

六个用户命令与 SKILL.md 一一对应（profile/search/apply/interview/pipeline/upskill），
另有底层支撑命令（ingest/fetch/sources/dashboard/blacklist）：
  profile  导入/校验用户画像
  ingest   岗位导入（JSON文件/手动模板），清洗去重+校招过滤
  search   岗位评分排序（五维+应届生加分，可解释输出）
  apply    定制简历+求职信+打招呼话术（factcheck 硬闸门 + 风控闸门）
  pipeline 投递进度看板
  upskill  技能缺口与学习计划
  interview 面试题库

用法：python3 core/cli.py <command> [options]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 允许 python3 core/cli.py 与 python3 -m core.cli 两种调用方式
sys.path.insert(0, str(ROOT))

from core import dashboard, factcheck, ingest, interview, resume, risk, scorer, store, upskill  # noqa: E402


def _load_profile_or_guide():
    """读画像；缺失时不抛栈，给出可操作的两条建档路径（对话式/手动模板）。"""
    try:
        return store.load("profile")
    except FileNotFoundError:
        print("📭 还没有个人画像（data/profile/profile.json）。先建档，系统才能为你工作：\n")
        print("  A.（推荐）对话式建档：在 Claude Code 里把简历/成绩单/作品集发给 Claude，说「帮我建档」")
        print("     —— /profile 会提取事实、逐条建立证据锚点并自动校验（反捏造机制的输入端）")
        print("  B. 手动建档：python3 core/cli.py profile --init 生成模板，按字段填写后自检")
        return None


def cmd_profile(args) -> int:
    store.ensure_defaults()
    if args.init:
        target = store.PATHS["profile"]
        if target.exists():
            print(f"✅ 画像已存在：{target}")
            print("   --init 不会覆盖已有画像。更新内容请走 /profile（对话式，自动维护证据锚点）或直接编辑该文件。")
            return 0
        tpl = ROOT / "templates" / "profile.template.json"
        if not tpl.exists():
            print(f"❌ 模板缺失：{tpl}（仓库应自带，请恢复后重试）")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
        print("✅ 已从模板创建画像 → data/profile/profile.json")
        print("   下一步：把「（填写：…）」占位替换为你的真实信息；原始材料放进 data/profile/raw/ 并登记 evidence_index。")
        print("   填完自检：python3 core/cli.py profile --validate --show")
        return 0
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    ev = profile.get("evidence_index", {})
    if args.validate:
        errs = []
        raw_text = json.dumps(profile, ensure_ascii=False)
        if "（填写：" in raw_text or "此为格式演示" in raw_text:
            print("⏸ 画像还是未填写的模板（检测到「（填写：…）」占位）。")
            print("   下一步：把简历/成绩单发给 Claude 说「帮我建档」（对话式，自动建锚点），或手动填写模板后重新自检。")
            return 1
        for s in profile.get("skills", []):
            if not s.get("evidence"):
                errs.append(f"技能「{s['name']}」无证据引用")
            else:
                for e in s["evidence"]:
                    if e not in ev:
                        errs.append(f"技能「{s['name']}」引用了不存在的证据 {e}")
        for x in profile.get("experiences", []):
            if not x.get("id"):
                errs.append(f"经历「{x.get('name')}」缺少 id（无法作为简历引用源）")
        if errs:
            print("❌ 画像校验未通过：")
            for e in errs:
                print(f"  - {e}")
            print("   下一步：补齐上面列出的锚点引用后重跑自检；格式参考 templates/profile.template.json。")
            return 1
        print("✅ 画像校验通过：技能/经历均锚定 evidence_index")
        print("   下一步：python3 core/cli.py fetch --env all 采集岗位（或粘贴 JD 导入），然后 search 出评分榜。")
    if args.show:
        ident = profile["identity"]
        edu = profile["education"][0]
        print(f"{ident['name']}｜{ident['cohort_label']}｜{edu['school']}·{edu['major']}")
        print(f"目标：{'、'.join(profile['preferences']['target_roles'])}")
        print(f"城市：{'、'.join(profile['preferences']['target_cities'])}｜期望薪资下限：{profile['preferences']['salary_min_k']}K")
        print(f"技能 {len(profile['skills'])} 项｜经历 {len(profile['experiences'])} 段｜奖项 {len(profile['awards'])} 个｜证据锚点 {len(ev)} 条")
        raws = sorted((store.DATA / 'profile' / 'raw').glob('*')) if (store.DATA / 'profile' / 'raw').exists() else []
        if raws:
            print(f"原始材料：{', '.join(str(p.name) for p in raws)}")
        else:
            print("原始材料：（data/profile/raw/ 为空——把简历/证明等原件放进去，锚点才有溯源对象）")
    return 0


def cmd_ingest(args) -> int:
    try:
        raw_jobs = ingest.load_json_file(args.file)
    except FileNotFoundError:
        print(f"❌ 文件不存在：{args.file}\n   降级路径：可复制 templates/manual_job_template.json 作为模板手动录入。")
        return 1
    except (ValueError, json.JSONDecodeError) as e:
        print(f"❌ JSON 解析失败：{e}")
        print("   下一步：用 templates/manual_job_template.json 作为格式参照（数组或含 jobs 键的对象）；")
        print("   或者直接把 JD 原文发给 Claude 说「导入这个岗位」，由 Claude 负责结构化。")
        return 1
    stats = ingest.ingest_jobs(raw_jobs, source_platform=args.source)
    print(f"📥 导入完成（来源：{args.source}）：收到 {stats['received']}｜入库 {stats['added']}"
          f"｜精确去重 {stats['deduped']}｜近似去重 {stats.get('near_duped', 0)}"
          f"｜补全JD {stats.get('enriched', 0)}｜过滤拒绝 {stats['rejected']}")
    for d in stats["details"]:
        if d["result"] in ("入库", "去重并补全JD"):
            mark = "✅"
        elif "去重跳过" in d["result"] or "近似去重" in d["result"]:
            mark = "♻️"
        else:
            mark = "🚫"
        print(f"  {mark} {d['title']} @ {d['company']} — {d['result']}")
    if stats["rejected"]:
        print("  （被拒岗位保留在 jobs.json 中 status=rejected 留档，原因透明可查）")
    return 0


def _score_all(jobs, profile, min_score, keyword, city):
    results = []
    for j in jobs:
        if keyword and keyword.lower() not in (j.get("title", "") + j.get("company", "") + j.get("description", "")).lower():
            continue
        if city and city not in (j.get("city") or ""):
            continue
        s = scorer.score_job(j, profile)
        if s["total"] >= min_score:
            results.append((j, s))
    results.sort(key=lambda x: -x[1]["total"])
    return results


def cmd_search(args) -> int:
    store.ensure_defaults()
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    jobs = ingest.list_active_jobs()
    if not jobs:
        print("📭 岗位库为空。")
        print("   路径一（推荐）：python3 core/cli.py fetch --env all 采集官方接口岗位（需先 bash scripts/bootstrap_upstream.sh）")
        print("   路径二（永远可用）：把 JD 原文发给 Claude 粘贴导入，或填 templates/manual_job_template.json 后执行 ingest。")
        return 0
    results = _score_all(jobs, profile, args.min_score, args.keyword, args.city)
    if args.job:
        for j, s in results:
            if j["id"] == args.job or j["id"].startswith(args.job):
                print(f"\n🎯 {j['title']} @ {j['company']}（{j.get('city') or '城市未知'}）[id={j['id']}]")
                print(f"   总分 {s['total']} → {s['verdict']}")
                for k, d in s["dims"].items():
                    print(f"   - {k:<16} {d['score']:>3}分 × {d['weight']} = {d['weighted']}")
                    for r in d["reasons"]:
                        print(f"       · {r}")
                if s["bonus"]:
                    print(f"   应届生加分：{'；'.join(b for b, _ in s['bonus'])}")
                for f in s["flags"]:
                    print(f"   {f}")
                return 0
        print(f"❌ 未找到岗位 id={args.job}（可能不存在或已被过滤）。")
        print("   下一步：python3 core/cli.py search 查看在榜岗位及其 id。")
        return 1
    if not results:
        print(f"没有 ≥{args.min_score} 分的岗位。降低 --min-score 或补充导入岗位。")
        return 0
    print(f"📋 校招岗位评分榜（{len(results)} 个，≥{args.min_score}分，五维 35/25/20/15/5 + 应届生加分）\n")
    print(f"{'分':>5}  {'评级':<8}{'岗位':<28}{'公司':<20}{'城市'}  id")
    import re as _re
    for j, s in results[: args.top]:
        lead = "🧭" if (j.get("extras") or {}).get("lead") else "  "
        title = _re.sub(r"\s+", " ", j["title"])
        city = _re.sub(r"\s+", " ", j.get("city") or "—")
        print(f"{s['total']:>5}  {s['verdict']:<8}{title[:26]:<28}{j['company'][:18]:<20}{city[:6]:<6}  {lead} {j['id']}")
    print("\n🧭=项目级线索（公司+批次入口，非单条JD）。查看单岗详情：search --job <id>｜生成投递材料：apply --job <id>")
    return 0


def cmd_apply(args) -> int:
    store.ensure_defaults()
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    db = store.load("jobs")
    job = next((j for j in db["jobs"] if j["id"] == args.job or j["id"].startswith(args.job)), None)
    if not job:
        print(f"❌ 未找到岗位 {args.job}。")
        print("   下一步：python3 core/cli.py search 查看在榜岗位及其 id。")
        return 1
    if job.get("status") == "rejected":
        print(f"🚫 该岗位已被校招过滤拒绝（原因：{job.get('reject_reason')}），不生成投递材料。")
        print("   如认为误判：核对岗位信息后可在 data/config.json 调整 filter 规则（不建议放宽经验线）。")
        return 1
    if (job.get("extras") or {}).get("lead"):
        print("🧭 这是项目级线索（公司+批次入口），不是逐条职位 JD。")
        print("   建议：先打开链接确认在招的具体岗位与批次，把确认后的 JD 粘贴导入（ingest 或直接发给 Claude），再生成定制材料。")
        print("   仍可继续生成通用版材料（按画像而非该JD定制），仅供参考。\n")
    if not (job.get("description") or "").strip() and not args.allow_no_jd:
        print("📄 该岗位还没有 JD 全文（不少校招 ATS 详情页为前端渲染，采集层拿不到正文）。没有 JD 就谈不上「定制」。")
        print("   两条路：")
        print("   A.（推荐）打开岗位链接复制 JD 正文 → 发给 Claude 粘贴导入，再 apply（材料会针对 JD 定制）")
        print("   B. 明确接受按「标题+画像」生成通用版：apply --job <id> --allow-no-jd")
        return 1

    # 闸门一：真实性硬校验
    audited, visible = resume.generate_resume(job, profile)
    report = factcheck.check(audited, profile)
    if not report["passed"]:
        print(factcheck.render_report(report))
        print("→ 已阻止投递材料生成（铁律：事实不可捏造）")
        return 1
    print(factcheck.render_report(report))

    # 闸门二：投递风控
    platform = job.get("source_platform", "manual")
    gate = risk.check(platform, job.get("company", ""))
    for w in gate["warnings"]:
        print(f"⚠️ {w}")
    if not gate["allow"]:
        print("🚫 风控闸门拦截，本次不执行投递动作：")
        for r in gate["reasons"]:
            print(f"   - {r}")
        print("→ 材料仍会生成，你可调整时间/渠道后再投（保护账号优先）。")
        allowed_to_send = False
    else:
        allowed_to_send = True

    # 生成材料
    cover_audited, cover_visible = resume.generate_cover_letter(job, profile)
    greet_audited, greet_visible = resume.generate_greeting(job, profile)
    for text in (cover_audited, greet_audited):
        rep = factcheck.check(text, profile)
        if not rep["passed"]:
            print(factcheck.render_report(rep))
            print("→ 求职信/话术未通过真实性校验，已阻止。")
            return 1

    outdir = store.DATA / "out" / job["id"]
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "resume.md").write_text(visible, encoding="utf-8")
    (outdir / "resume.audited.md").write_text(audited, encoding="utf-8")
    (outdir / "cover_letter.md").write_text(cover_visible, encoding="utf-8")
    (outdir / "greeting.md").write_text(greet_visible, encoding="utf-8")
    # 可交付文件：浏览器打开即可打印/另存 PDF（A4 排版，中文字体走系统栈，无外部依赖）
    (outdir / "resume.html").write_text(resume.render_html(visible, f"简历-{profile['identity']['name']}"), encoding="utf-8")
    (outdir / "cover_letter.html").write_text(resume.render_html(cover_visible, f"求职信-{job.get('company', '')}"), encoding="utf-8")
    print(f"\n📝 投递材料已生成 → {outdir}/")
    print("   resume.html（打开即可打印/存PDF）+ resume.md + resume.audited.md（审计版含证据引用）")
    print("   cover_letter.html + cover_letter.md + greeting.md")

    if args.send:
        if allowed_to_send:
            risk.record(platform, job.get("company", ""), job["id"])
            print(f"✅ 已记录投递（{platform} · {job['company']}）。今日该平台计数与黑名单已更新。")
            print("   注意：真实站内投递需浏览器会话（见 adapters/ 对应平台说明），此处为本地台账记录+半自动引导。")
        else:
            print("🛑 --send 被风控闸门否决：未记录投递。原因见上。")
            return 2
    else:
        print("（--send 未指定：仅生成材料，不记录投递。半自动投递流程见 README）")
    return 0


def cmd_pipeline(args) -> int:
    store.ensure_defaults()
    app = store.load("applications")
    apps = app.get("applications", [])
    print("📊 投递进度看板")
    print("=" * 56)
    order = ["applied", "interviewing", "offer", "rejected", "closed"]
    buckets: dict[str, list] = {k: [] for k in order}
    for a in apps:
        buckets.setdefault(a.get("status", "applied"), []).append(a)
    today = dt.date.today().isoformat()
    daily = app.get("daily_log", {}).get(today, {})
    print(f"今日投递：", end="")
    if not daily:
        print(" 0 份")
    else:
        for p, v in daily.items():
            print(f" {p} {v['count']}份", end="")
        print()
    if not apps:
        print("（暂无投递记录。流程：search 挑岗位 → apply --job <id> --send；岗位库为空则先 fetch 或粘贴导入）")
    for k in order:
        if buckets.get(k):
            print(f"\n【{k}】{len(buckets[k])} 个")
            for a in buckets[k]:
                print(f"  · {a['company']:<20} {a.get('applied_at', '')}  {a['job_id']}")
    db = store.load("jobs")
    new_jobs = [j for j in db["jobs"] if j.get("status") == "new"]
    applied_companies = {risk._norm_company(a["company"]) for a in apps}
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    pending = [(j, scorer.score_job(j, profile)) for j in new_jobs if risk._norm_company(j.get("company", "")) not in applied_companies]
    pending.sort(key=lambda x: -x[1]["total"])
    if pending:
        print(f"\n📌 待投递高优岗位（评分排序，未投过该公司）：")
        for j, s in pending[:5]:
            print(f"  {s['total']:>5} {s['verdict']:<9} {j['title'][:24]:<26} {j['company'][:16]:<18} {j['id']}")
        print("→ 执行 apply --job <id> --send 推进")
    return 0


def cmd_interview(args) -> int:
    store.ensure_defaults()
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    db = store.load("jobs")
    job = next((j for j in db["jobs"] if j["id"] == args.job or j["id"].startswith(args.job)), None) if args.job else None
    qs = interview.build_question_set(job or {"company": "通用", "title": "求职模拟", "description": " ".join(str(w) for w in (args.skills or "").split(","))}, profile)
    print(interview.render_question_set(qs))
    return 0


def cmd_upskill(args) -> int:
    store.ensure_defaults()
    profile = _load_profile_or_guide()
    if profile is None:
        return 1
    db = store.load("jobs")
    job = None
    if args.job:
        job = next((j for j in db["jobs"] if j["id"] == args.job or j["id"].startswith(args.job)), None)
        if not job:
            print(f"❌ 未找到岗位 {args.job}。")
            print("   下一步：python3 core/cli.py search 查看在榜岗位及其 id。")
            return 1
    else:
        job = {"company": "目标方向", "title": args.keyword or "、".join(profile["preferences"]["target_roles"][:2]),
               "description": args.keyword or "", "keywords": []}
    plan = upskill.analyze(job, profile)
    print(upskill.render_plan(plan))
    return 0


def cmd_blacklist(args) -> int:
    store.ensure_defaults()
    app = store.load("applications")
    if args.add:
        app.setdefault("blacklist", []).append(args.add)
        store.save("applications", app)
        print(f"✅ 已加入黑名单：{args.add}")
    else:
        bl = app.get("blacklist", [])
        print(f"🚫 投递黑名单（{len(bl)}）：")
        for b in bl:
            print(f"  - {b}")
    return 0


def cmd_fetch(args) -> int:
    """信源采集入口：跑外部源 → 映射为本系统契约 → 走统一 ingest 入库。

    分层：core 不 import adapters（依赖只向上），这里用子进程组合脚本；
    wenke 桥需要 venv 解释器（adapters 层唯一有第三方依赖的地方）。
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent
    targets = [t.strip() for t in args.env.split(",") if t.strip()] if args.env != "all" \
        else ["leads", "xiaozhao", "wenke"]
    rc = 0
    for t in targets:
        if t == "leads":
            script = root / "adapters" / "sources" / "jobradar_leads.py"
            rc |= subprocess.run([sys.executable, str(script)]).returncode
        elif t == "xiaozhao":
            script = root / "adapters" / "sources" / "import_xiaozhao_seed.py"
            rc |= subprocess.run([sys.executable, str(script)]).returncode
        elif t == "wenke":
            venv_py = root / ".venv-sources" / "bin" / "python"
            if not venv_py.exists():
                print("⚠️ 采集底座未安装（.venv-sources 不存在）。")
                print("   安装：bash adapters/sources/setup_env.sh")
                print("   降级路径：不安装也可用 —— 粘贴 JD 导入 / xiaozhao 种子 / 手动模板 依然完整可用。")
                rc = 1
                continue
            cmd = [str(venv_py), str(root / "adapters" / "sources" / "wenke_bridge.py")]
            if args.only:
                cmd += ["--only", args.only]
            rc |= subprocess.run(cmd).returncode
            feeds = store.DATA / "feeds" / "wenke.json"
            if feeds.exists():
                try:
                    raw = ingest.load_json_file(str(feeds))
                    stats = ingest.ingest_jobs(raw, source_platform="official")
                    print(f"📥 wenke 采集入库：收到 {stats['received']}｜入库 {stats['added']}"
                          f"｜精确去重 {stats['deduped']}｜近似去重 {stats.get('near_duped', 0)}"
                          f"｜补全JD {stats.get('enriched', 0)}｜过滤拒绝 {stats['rejected']}")
                except (ValueError, json.JSONDecodeError) as e:
                    print(f"❌ 采集结果入库失败：{e}")
                    rc = 1
        else:
            print(f"❌ 未知信源环境：{t}（可用：wenke / xiaozhao / leads / all）")
            rc = 1
    if rc == 0:
        print("\n下一步：python3 core/cli.py search          # 看评分榜单")
        print("        python3 core/cli.py apply --job <id>  # 给心仪岗位出投递材料")
    else:
        print("\n⚠️ 有信源未完成（见上方输出）。已入库的数据不受影响，可直接 search 查看。")
    return rc


def cmd_sources(args) -> int:
    """信源健康面板：线索索引 + 最近一次采集的分源健康。"""
    leads_path = store.DATA / "feeds" / "jobradar_leads.json"
    if leads_path.exists():
        leads = json.loads(leads_path.read_text(encoding="utf-8")).get("leads", [])
        print(f"🗂  校招垂直渠道线索（{len(leads)} 个，来自 job-radar 信源清单）：")
        for lead in leads:
            print(f"  · [{lead['adapter']:<14}] {lead['company_name']:<16} {lead['city_scope']:<14} {lead['notes'][:40]}")
    else:
        print("（尚无线索索引。运行 python3 core/cli.py fetch --env leads 生成）")
    health_path = store.DATA / "feeds" / "wenke_health.json"
    if health_path.exists():
        health = json.loads(health_path.read_text(encoding="utf-8"))
        ok = [h for h in health if h["ok"]]
        bad = [h for h in health if not h["ok"]]
        print(f"\n🔗 wenke 采集健康（最近一次：成功 {len(ok)} / 失败 {len(bad)}）")
        for h in bad:
            print(f"  ❌ {h['source']}: {h.get('error', '未知')}")
        if not bad:
            print("  全部正常 ✅")
    else:
        print("\n（尚无 wenke 采集记录。运行 python3 core/cli.py fetch --env wenke）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="campus-job-agent", description="2027届秋招自动化求职系统（本地运行）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profile", help="导入/校验用户画像")
    sp.add_argument("--validate", action="store_true", help="校验证据锚点完整性")
    sp.add_argument("--init", action="store_true", help="从模板创建画像（不覆盖已有）")
    sp.add_argument("--show", action="store_true", help="显示画像摘要")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("ingest", help="岗位导入（清洗/去重/校招过滤）")
    sp.add_argument("--file", required=True, help="岗位 JSON 文件路径")
    sp.add_argument("--source", default="manual", help="来源平台标识（boss/liepin/shixiseng/manual...）")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("search", help="岗位评分排序")
    sp.add_argument("--min-score", type=float, default=50)
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--keyword", default=None)
    sp.add_argument("--city", default=None)
    sp.add_argument("--job", default=None, help="查看单岗评分详情")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("apply", help="生成投递材料（简历+求职信+话术）")
    sp.add_argument("--job", required=True)
    sp.add_argument("--send", action="store_true", help="通过风控后记录投递并更新黑名单")
    sp.add_argument("--allow-no-jd", action="store_true", help="岗位无JD全文时仍生成通用版材料（明确接受非定制）")
    sp.set_defaults(func=cmd_apply)

    sub.add_parser("pipeline", help="投递进度看板").set_defaults(func=cmd_pipeline)

    sp = sub.add_parser("interview", help="面试题库")
    sp.add_argument("--job", default=None)
    sp.add_argument("--skills", default="", help="无岗位时按技能关键词生成")
    sp.set_defaults(func=cmd_interview)

    sp = sub.add_parser("upskill", help="技能缺口与学习计划")
    sp.add_argument("--job", default=None)
    sp.add_argument("--keyword", default=None)
    sp.set_defaults(func=cmd_upskill)

    sp = sub.add_parser("blacklist", help="投递黑名单管理")
    sp.add_argument("--add", default=None)
    sp.set_defaults(func=cmd_blacklist)

    sp = sub.add_parser("fetch", help="信源采集（wenke官方接口/校招种子/渠道线索）")
    sp.add_argument("--env", default="all", help="wenke,xiaozhao,leads 逗号组合或 all")
    sp.add_argument("--only", default=None, help="wenke 调试：只跑指定源（逗号分隔，如 京东,小米）")
    sp.set_defaults(func=cmd_fetch)

    sub.add_parser("sources", help="信源健康面板（渠道线索+采集健康）").set_defaults(func=cmd_sources)

    def cmd_dashboard(_args) -> int:
        store.ensure_defaults()
        out = store.DATA / "pipeline_dashboard.html"
        out.write_text(dashboard.render_dashboard(), encoding="utf-8")
        print(f"📊 看板已生成 → {out}（浏览器打开；离线单文件，可打印）")
        return 0
    sub.add_parser("dashboard", help="生成投递进度离线看板（单文件HTML）").set_defaults(func=cmd_dashboard)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
