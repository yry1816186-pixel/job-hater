"""job-hater 命令行：与 Web UI 完全同一 services 层（§25），只做参数绑定与展示。"""
from __future__ import annotations

import argparse
import json
import sys

from jobhater import __version__, config
from jobhater.db import apply_all, connect, current_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-hater", description="本地求职管理系统")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", help="数据目录（默认 ~/.jobhater 或 .jobhater/）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="环境自检（数据库/迁移/信源）")
    p_serve = sub.add_parser("serve", help="启动本地 Web 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)
    p_import = sub.add_parser("import", help="从 JSON 文件导入岗位")
    p_import.add_argument("file", help="JSON 文件（岗位数组或 {jobs:[...]}）")
    p_import.add_argument("--source", default="manual")
    p_match = sub.add_parser("match", help="对画像运行匹配排序")
    p_match.add_argument("--profile", required=True)
    p_match.add_argument("--preset")
    p_match.add_argument("--limit", type=int, default=0,
                         help="最多评估岗位数；0=全部在库岗位（默认）")
    p_migrate = sub.add_parser("migrate-v1", help="从 v1 JSON 数据目录一次性迁移")
    p_migrate.add_argument("old_data_dir", help="旧版 data/ 目录路径")
    p_migrate.add_argument("--db", default=None, help="目标 SQLite 文件（默认数据目录）")
    sub.add_parser("backup", help="备份数据库到 exports/backups（WAL checkpoint 后复制）")
    p_fetch = sub.add_parser("fetch", help="从官方接口信源抓取岗位（wenke：米哈游/百度/网易）")
    p_fetch.add_argument("--companies", nargs="*", help="公司名列表（默认全部）")
    p_fetch.add_argument("--dry-run", action="store_true", help="只抓不入库（健康检查）")
    p_paste = sub.add_parser("paste", help="从 stdin 读 JD 原文解析入库（粘贴主链的 CLI 入口）")
    p_paste.add_argument("--save", action="store_true", help="直接入库（默认只打印草稿）")
    p_paste.add_argument("--url", default=None, help="岗位链接（可选）")
    p_enrich = sub.add_parser("enrich", help="批量补全岗位 JD 原文（官方接口直读，可中断续跑）")
    p_enrich.add_argument("--limit", type=int, default=50, help="本次最多处理条数")
    p_enrich.add_argument("--provider", default=None, help="只跑指定 provider（byteportal/zhiye/…）")
    p_enrich.add_argument("--stats", action="store_true", help="只看覆盖率统计，不跑补全")
    p_live = sub.add_parser("liveness", help="判活：官方接口+静态页双阶梯确认岗位存活，死岗标 expired")
    p_live.add_argument("--limit", type=int, default=20, help="本次最多检查条数")
    p_live.add_argument("--job-id", default=None, help="只查单个岗位")
    p_live.add_argument("--stats", action="store_true", help="只看判活统计，不检查")
    p_inbox = sub.add_parser("inbox", help="文件收件箱：拖入表格/JD 文件/截图 → 解析 → 过目入库")
    p_inbox.add_argument("--scan", action="store_true", help="立即扫描 inbox/ 目录（同步）")
    p_inbox.add_argument("--stats", action="store_true", help="看最近一次解析结果概要")
    p_jobs = sub.add_parser("jobs", help="列出/检索岗位")
    p_jobs.add_argument("--q", default="", help="检索词")
    p_jobs.add_argument("--limit", type=int, default=30)
    p_apps = sub.add_parser("applications", help="列出投递跟踪")
    p_apps.add_argument("--profile", required=True)
    p_transition = sub.add_parser("transition", help="推进投递状态（漏斗内可跳步；applied_confirmed 除外）")
    p_transition.add_argument("application_id")
    p_transition.add_argument("status")
    p_transition.add_argument("--note", default=None)
    p_confirm = sub.add_parser("confirm-applied", help="用户确认已投递（进入投后阶段的唯一入口）")
    p_confirm.add_argument("application_id")
    p_confirm.add_argument("--channel", default=None, help="投递渠道（官网/BOSS/内推…）")

    p_profile = sub.add_parser("profile", help="画像管理：列出/建立（简历文本可自动入档）")
    profile_sub = p_profile.add_subparsers(dest="profile_cmd", required=True)
    profile_sub.add_parser("list", help="列出全部画像（拿 profile_id）")
    p_pc = profile_sub.add_parser("create", help="建立画像；--resume-file 简历文本自动提取技能/经历/教育")
    p_pc.add_argument("--name", default="", help="姓名（不给则从简历文本识别）")
    p_pc.add_argument("--headline", default="", help="一句话介绍/求职意向")
    p_pc.add_argument("--resume-file", default=None, help="简历文本文件（txt/md）")

    p_preset = sub.add_parser("preset", help="设定求职偏好（匹配的全部依据；更新激活项）")
    p_preset.add_argument("--profile", required=True)
    p_preset.add_argument("--name", default="求职偏好")
    p_preset.add_argument("--role", action="append", default=[], help="目标角色，可多次")
    p_preset.add_argument("--city", action="append", default=[], help="目标城市，可多次")
    p_preset.add_argument("--type", action="append", default=[], help="internship/campus/social，可多次")
    p_preset.add_argument("--salary-min", type=float, default=0, help="薪资底线（K/月）")
    p_preset.add_argument("--min-education", default="", help="最低学历")
    p_preset.add_argument("--graduation-year", type=int, default=0)

    p_harvest = sub.add_parser("harvest", help="网页采集：种子 URL→放大抓取→提取→入库（幂等可重跑）")
    p_harvest.add_argument("--urls", help="种子 URL 文件（每行一条；1 条即可，引擎自动沿相关职位链接放大）")
    p_harvest.add_argument("--url", default=None, help="单条种子 URL（与 --urls 二选一）")
    p_harvest.add_argument("--depth", type=int, default=1, help="相关职位链接跟进跳数（0-2，默认 1）")
    p_harvest.add_argument("--max-pages", type=int, default=50, help="本次最多抓取页数（默认 50）")
    p_harvest.add_argument("--dry-run", action="store_true", help="只提取不入库")

    args = parser.parse_args(argv)
    if args.data_dir:
        config.set_data_dir(args.data_dir)

    if args.cmd == "doctor":
        apply_all()
        con = connect()
        try:
            from jobhater.services.jobs import JobService

            jobs = JobService(con).count()
            sources = JobService(con).list_sources()
            print(f"✅ 数据库就绪：{config.db_path()}")
            print(f"   schema 版本：{current_version(con)}；岗位：{jobs} 条；信源：{len(sources)} 个")
        finally:
            con.close()
        return 0

    if args.cmd == "serve":
        import os

        import uvicorn

        from jobhater.api import create_app

        if args.host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
            "JOBHATER_ALLOWED_HOSTS"
        ):
            print(
                f"[警告] 监听 {args.host}：API 无账号体系，绑定非回环地址意味着同网段可访问你的全部求职数据。\n"
                "       私网 IP 直连默认放行；域名部署请设置 JOBHATER_ALLOWED_HOSTS（逗号分隔）。\n"
                "       有外层防护/反向代理时可设 JOBHATER_GUARD=0 关闭本机安全线（见 SECURITY.md）。",
                file=sys.stderr,
            )
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
        return 0

    if args.cmd == "backup":
        import shutil

        src = config.db_path()
        dest_dir = config.exports_dir() / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"jobhater-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        con = connect()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # 合并 WAL，得到自洽副本
        finally:
            con.close()
        shutil.copy2(src, dest)
        print(f"✅ 备份完成：{dest}")
        return 0

    if args.cmd == "fetch":
        from jobhater.services.jobs import JobService
        from jobhater.services.sources import WenkeAdapter

        adapter = WenkeAdapter(companies=args.companies or None)
        raw = list(adapter.produce())
        for company, report in adapter.last_fetch_report.items():
            mark = "✅" if report.ok else "❌"
            print(f"  {mark} {company}: {report.message}")
        if args.dry_run:
            print(f"dry-run：共获取 {len(raw)} 条，未入库")
            return 0
        apply_all()
        con = connect()
        try:
            stats = JobService(con).ingest(raw, source_id="wenke")
            js = JobService(con)
            ok_any = any(r.ok for r in adapter.last_fetch_report.values())
            js.record_source_health(
                "wenke", ok=ok_any,
                message=adapter.health_check().message,
            )
        finally:
            con.close()
        print(
            f"收到 {stats.received}：入库 {stats.added}，精确去重 {stats.deduped_exact}，"
            f"近似 {stats.deduped_near}，补全 {stats.enriched}，拒绝 {stats.rejected}"
        )
        return 0

    if args.cmd == "migrate-v1":
        from jobhater.migrate_v1 import migrate

        report = migrate(args.old_data_dir, args.db)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    apply_all()
    con = connect()
    try:
        if args.cmd == "import":
            from jobhater.services.jobs import JobService

            with open(args.file, encoding="utf-8") as f:
                data = json.load(f)
            raw = data if isinstance(data, list) else data.get("jobs")
            if not isinstance(raw, list):
                print("❌ JSON 应为岗位数组或含 jobs 键的对象", file=sys.stderr)
                return 2
            stats = JobService(con).ingest(raw, source_id=args.source)
            print(
                f"收到 {stats.received}：入库 {stats.added}，精确去重 {stats.deduped_exact}，"
                f"近似 {stats.deduped_near}，补全 {stats.enriched}，拒绝 {stats.rejected}"
            )
            for d in stats.details[:10]:
                print(f"  - [{d['result']}] {d.get('title')} @ {d.get('company')}")
            return 0

        if args.cmd == "match":
            from jobhater.services.jobs import JobService
            from jobhater.services.matching import MatchService
            from jobhater.services.profile import ProfileService

            ps = ProfileService(con)
            preset = (
                ps.get_preset(args.preset) if args.preset else ps.active_preset(args.profile)
            )
            if preset is None:
                print("❌ 该画像没有求职偏好（preset），先在 Web UI 或 API 创建", file=sys.stderr)
                return 2
            jobs = JobService(con).search("", statuses=["active"], limit=args.limit or 10**9)
            outcomes = MatchService(con).rank_jobs(ps.match_view(args.profile), preset, jobs)
            js = JobService(con)
            print(f"偏好「{preset.name}」评估 {len(outcomes)} 岗，合格 "
                  f"{sum(1 for o in outcomes if o.eligible)}：")
            for o in outcomes[:20]:
                job = js.get(o.job_id)
                mark = "✅" if o.eligible else "⛔"
                rel = f" rel={o.relevance_score:g}" if o.relevance_score is not None else ""
                print(f"  {mark} {o.rank_score or 0:5.1f}{rel} [{o.verdict}] "
                      f"{job.title} @ {job.employer_name}（{job.city or '城市未知'}）")
            return 0

        if args.cmd == "paste":
            from jobhater.services.sources import parse_jd_text

            text = sys.stdin.read()
            try:
                draft = parse_jd_text(text, url=args.url)
            except ValueError as e:
                print(f"❌ {e}", file=sys.stderr)
                return 2
            if not args.save:
                print(json.dumps(draft, ensure_ascii=False, indent=2))
                return 0
            if not draft.get("title") or not draft.get("company"):
                print("❌ 草稿缺 title/company，请补全后用 --save 重试", file=sys.stderr)
                return 2
            raw = {k: v for k, v in draft.items()
                   if k not in ("parse_notes", "needs_review_fields")}
            stats = JobService(con).ingest([raw], source_id="manual")
            print(f"收到 {stats.received}：入库 {stats.added}，去重 {stats.deduped_exact}")
            return 0

        if args.cmd == "enrich":
            from jobhater.services.enrich import EnrichmentEngine

            engine = EnrichmentEngine(con)
            if args.stats:
                st = engine.stats()
                print(f"在库 {st['active_jobs']} 岗，已有 JD 正文 {st['with_jd']}"
                      f"（覆盖率 {st['coverage_pct']}%）")
                for provider, log in sorted(st["log"].items()):
                    print(f"  {provider}: {log}")
                return 0
            try:
                out = engine.run(limit=args.limit, provider_id=args.provider)
            except ValueError as e:
                print(f"❌ {e}", file=sys.stderr)
                return 2
            print(f"补全成功 {out['total_ok']}，失败 {out['total_failed']}，"
                  f"标题不匹配 {out['total_no_match']}")
            for pid, s in out["providers"].items():
                print(f"  {pid}: ok={s['ok']} failed={s['failed']} no_match={s['no_match']}"
                      f"（正文 {s['jd_chars']} 字符）")
            if out["total_ok"]:
                print("提示：匹配结果基于旧数据——运行 `job-hater match` 或在总览页重新匹配。")
            return 0

        if args.cmd == "liveness":
            from jobhater.services.liveness import LivenessService

            svc = LivenessService(con)
            if args.stats:
                st = svc.stats()
                print(f"active 且带链接 {st['active_with_url']} 岗，从未判活 {st['never_checked']}；"
                      f"已判：{st['verdicts']}")
                return 0
            if args.job_id:
                out = svc.probe(args.job_id)
                if not out.get("ok"):
                    print(f"❌ {out.get('error')}", file=sys.stderr)
                    return 2
                mark = {"active": "✅ 在招", "expired": "⏹ 已下线", "uncertain": "❓ 不确定"}[out["verdict"]]
                suffix = "（已标记 expired）" if out["status_changed"] else ""
                print(f"{mark} · {out['title']} [{out['code']}]{suffix}")
                if out["evidence"]:
                    print(f"   证据：{out['evidence']}")
                return 0
            out = svc.run(limit=args.limit)
            c = out["counts"]
            print(f"检查 {out['checked']} 岗：在招 {c['active']}，下线 {c['expired']}，"
                  f"不确定 {c['uncertain']}")
            for e in out["expired_jobs"]:
                print(f"  ⏹ {e['title']} [{e['code']}] {e['job_id']}")
            return 0

        if args.cmd == "inbox":
            from jobhater.services.inbox import InboxService

            svc = InboxService(con)
            if args.scan:
                out = svc.scan()
                print(f"扫描 {out['files']} 个文件：解析出 {out['items']} 条岗位"
                      f"（缺公司需补 {out['needs_review']} 条）")
                print(f"目录：{svc.inbox_dir()}")
                print("入库请在 Web 界面「文件收件箱」页勾选导入（过目后生效）。")
                return 0
            res = svc.results()
            print(f"最近解析：{len(res['items'])} 条（来自 {len(res['files'])} 个文件）")
            for i, it in enumerate(res["items"][:20]):
                mark = "⚠️" if not it.get("company") else " "
                print(f"  [{i}] {mark} {it.get('title', '')[:26]} @ {it.get('company', '（缺公司）')}"
                      f" {it.get('city', '')[:8]}")
            return 0

        if args.cmd == "jobs":
            from jobhater.services.jobs import JobService as _JS

            jobs = _JS(con).search(args.q, limit=args.limit)
            for j in jobs:
                mark = "⏹" if j.status.value == "expired" else " "
                print(f"  {mark} {j.title} @ {j.employer_name}"
                      f"（{j.city or '城市未知'} · {j.salary_text or '薪资未标注'}）[{j.id}]")
            return 0

        if args.cmd == "applications":
            from jobhater.services.lifecycle import ApplicationService

            for a in ApplicationService(con).list(args.profile):
                print(f"  [{a['status']}] {a.get('job_title') or a['job_id']}"
                      f" @ {a.get('employer_name') or ''} [{a['id']}]")
            return 0

        if args.cmd == "transition":
            from jobhater.services.lifecycle import ApplicationService, LifecycleError

            try:
                app = ApplicationService(con).transition(
                    args.application_id, args.status, note=args.note
                )
            except LifecycleError as e:
                print(f"❌ {e}", file=sys.stderr)
                return 2
            print(f"✅ 已推进到 {app['status']}")
            return 0

        if args.cmd == "profile":
            from pathlib import Path

            from jobhater.services.profile import ProfileService
            from jobhater.services.resume import ResumeService
            from jobhater.services.resume_import import parse_resume_text

            ps = ProfileService(con)
            if args.profile_cmd == "list":
                profiles = ps.list_profiles()
                if not profiles:
                    print("还没有画像——job-hater profile create --name 姓名 [--resume-file 简历.txt]")
                for p in profiles:
                    print(
                        f"  [{p.id}] {p.display_name} · {p.headline or '无介绍'} ·"
                        f" 技能 {len(ps.list_skills(p.id))} ·"
                        f" 偏好 {len(ps.list_presets(p.id))}"
                    )
                return 0
            resume_text = Path(args.resume_file).read_text(encoding="utf-8") if args.resume_file else ""
            draft, warnings = None, []
            if resume_text.strip():
                parsed = parse_resume_text(resume_text)
                draft, warnings = parsed["draft"], parsed["warnings"]
                basics = draft.get("basics") or {}
                args.name = args.name or basics.get("name") or ""
                args.headline = args.headline or basics.get("label") or ""
            if not args.name.strip():
                print("❌ 缺少姓名：--name 或简历文本中的姓名至少提供一个", file=sys.stderr)
                return 2
            profile = ps.create_profile(args.name.strip(), args.headline.strip() or None)
            counts = ResumeService(con).import_json_resume(profile.id, draft) if draft else {}
            print(f"✅ 画像已建立：{profile.id}（{profile.display_name}）")
            if counts:
                print(f"   简历入档：{counts}")
            for w in warnings:
                print(f"   ⚠️ {w}")
            print("下一步：job-hater preset --profile <id> 设定求职偏好，然后 match")
            return 0

        if args.cmd == "preset":
            from jobhater.services.profile import ProfileService

            ps = ProfileService(con)
            if ps.get_profile(args.profile) is None:
                print(f"❌ 画像不存在: {args.profile}", file=sys.stderr)
                return 2
            bad_types = [t for t in args.type if t not in ("internship", "campus", "social")]
            if bad_types:
                print(f"❌ 未知类型 {bad_types}（合法值：internship/campus/social）", file=sys.stderr)
                return 2
            kw: dict = {}
            if args.role:
                kw["target_roles"] = args.role
            if args.city:
                kw["target_cities"] = args.city
            if args.type:
                kw["employment_types"] = args.type
            if args.salary_min > 0:
                kw["salary_min_k"] = args.salary_min
            if args.min_education:
                kw["min_education"] = args.min_education
            if args.graduation_year > 0:
                kw["graduation_year"] = args.graduation_year
            current = ps.active_preset(args.profile)
            if current is not None:
                preset = ps.update_preset(current.id, **kw) if kw else current
            else:
                preset = ps.create_preset(args.profile, args.name, **kw)
                ps.activate_preset(args.profile, preset.id)
            print(
                f"✅ 偏好「{preset.name}」已生效：角色={preset.target_roles} 城市={preset.target_cities}"
                f" 类型={preset.employment_types} 薪资底线={preset.salary_min_k or '不限'}K"
            )
            return 0

        if args.cmd == "harvest":
            from pathlib import Path  # noqa: PLC0415 —— 与 profile 处理器同款局部导入

            from jobhater.services.webharvest import WebHarvestService

            seeds = []
            if args.url:
                seeds.append(args.url)
            if args.urls:
                seeds.extend(
                    ln.strip() for ln in Path(args.urls).read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")
                )
            if not seeds:
                print("❌ 需要 --url <URL> 或 --urls <文件>（每行一条种子 URL）", file=sys.stderr)
                return 2
            svc = WebHarvestService(con)
            try:
                out = svc.harvest(seeds, depth=args.depth, max_pages=args.max_pages,
                                  ingest=not args.dry_run)
            finally:
                svc.close()
            if args.dry_run:
                for raw in out["jobs"][:10]:
                    print(f"  · {raw['title']} @ {raw.get('company') or '（公司未识别）'}"
                          f" {raw.get('city') or ''} {raw.get('salary') or ''}")
                print(f"dry-run：提取 {len(out['jobs'])} 条，未入库"
                      f"（页面 {out['pages_fetched']}，方法 {out['methods']}，"
                      f"降级 {len(out['degraded'])}，失败 {len(out['failed'])}）")
                return 0
            ig = out.get("ingest", {})
            print(f"页面 {out['pages_fetched']}：提取 {out['extracted']}"
                  f"（方法 {out['methods']}），入库 {ig.get('added', 0)}，"
                  f"去重精 {ig.get('deduped_exact', 0)} 近 {ig.get('deduped_near', 0)}，"
                  f"拒绝 {ig.get('rejected', 0)}，跳过已入库 {out['skipped_existing']}")
            for d in out["degraded"][:8]:
                print(f"  ⚠️ {d['reason'][:60]} — {d['url'][:70]}")
            for f in out["failed"][:8]:
                print(f"  ✗ 提取失败 {f['reason'][:40]} — {f['url'][:70]}")
            return 0

        if args.cmd == "confirm-applied":
            from jobhater.services.lifecycle import ApplicationService, LifecycleError

            try:
                app = ApplicationService(con).confirm_applied(
                    args.application_id, channel=args.channel
                )
            except LifecycleError as e:
                print(f"❌ {e}", file=sys.stderr)
                return 2
            print(f"✅ 已记录用户确认投递（applied_at={app.get('applied_at')}）")
            return 0
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
