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
    p_match.add_argument("--limit", type=int, default=100)
    p_migrate = sub.add_parser("migrate-v1", help="从 v1 JSON 数据目录一次性迁移")
    p_migrate.add_argument("old_data_dir", help="旧版 data/ 目录路径")
    p_migrate.add_argument("--db", default=None, help="目标 SQLite 文件（默认数据目录）")
    sub.add_parser("backup", help="备份数据库到 exports/backups（WAL checkpoint 后复制）")
    p_fetch = sub.add_parser("fetch", help="从官方接口信源抓取岗位（wenke：米哈游/百度/网易）")
    p_fetch.add_argument("--companies", nargs="*", help="公司名列表（默认全部）")
    p_fetch.add_argument("--dry-run", action="store_true", help="只抓不入库（健康检查）")

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
        import uvicorn

        from jobhater.api import create_app

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
            jobs = JobService(con).search("", statuses=["active"], limit=args.limit)
            outcomes = MatchService(con).rank_jobs(ps.match_view(args.profile), preset, jobs)
            js = JobService(con)
            print(f"偏好「{preset.name}」评估 {len(outcomes)} 岗，合格 "
                  f"{sum(1 for o in outcomes if o.eligible)}：")
            for o in outcomes[:20]:
                job = js.get(o.job_id)
                mark = "✅" if o.eligible else "⛔"
                print(f"  {mark} {o.rank_score or 0:5.1f} [{o.verdict}] "
                      f"{job.title} @ {job.employer_name}（{job.city or '城市未知'}）")
            return 0
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
