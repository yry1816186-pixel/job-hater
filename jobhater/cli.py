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
