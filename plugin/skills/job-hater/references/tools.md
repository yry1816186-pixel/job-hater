# 工具速查（MCP 23 个 + CLI 等价）

MCP 优先（结构化、无需 shell）；harness 没接 MCP 时 CLI 全覆盖。
MCP 服务器：`job-hater-mcp`（stdio）。CLI：`job-hater <命令>`。
工具返回全部是 JSON 字符串；`error` 键 = 失败，原因在值里，如实转告用户。

## 建档与偏好

| MCP 工具 | 作用 | CLI 等价 |
|----------|------|----------|
| `profile_list()` | 列画像拿 profile_id（第一步永远先调） | `job-hater profile list` |
| `profile_get(profile_id)` | 画像全貌（技能/经历/教育/项目） | — |
| `profile_create(display_name?, headline?, resume_text?)` | 建画像；带简历文本自动入档，warnings 如实返回 | `job-hater profile create --name … --resume-file 简历.txt` |
| `preset_get(profile_id)` | 当前生效偏好 | — |
| `preset_set(profile_id, target_roles_json, target_cities_json, employment_types_json, salary_min_k, min_education, graduation_year, remote_ok)` | 设定/更新激活偏好。类型 internship/campus/social | `job-hater preset --profile … --role 后端 --city 杭州 --type social` |

## 岗位：入库与检索

| MCP 工具 | 作用 | CLI 等价 |
|----------|------|----------|
| `paste_parse(jd_text, url?)` | JD 原文→结构化草稿（不落库），parse_notes 说明字段依据 | `echo "JD原文" \| job-hater paste` |
| `jobs_import(raw_jobs_json, source_id?)` | 岗位数组入库（自动精确+近似去重，幂等） | `job-hater import 文件.json --source …` |
| `jobs_search(query?, city?, limit?)` | 中文全文+结构化检索 | `job-hater jobs --q …` |
| `jobs_get(job_id, profile_id?)` | 岗位详情+JD 原文（带 profile_id 附匹配解释） | — |
| `sources_fetch(companies_json?, dry_run?)` | 内置 wenke 校招官方源抓取入库 | `job-hater fetch [--dry-run]` |
| — | JD 补全（大厂官方端点，按 URL） | `job-hater enrich --limit 50` |
| — | 判活（死岗标 expired） | `job-hater liveness --limit 50` |
| — | 文件收件箱扫描（表格/截图/文档） | `job-hater inbox --scan` |

## 匹配与材料

| MCP 工具 | 作用 | CLI 等价 |
|----------|------|----------|
| `match_run(profile_id, limit?)` | 全库匹配排序；返回含 eligible/rank_score/verdict/failed_gates | `job-hater match --profile …` |
| `resumes_list(profile_id)` | 简历与版本列表 | — |
| `resumes_prepare(profile_id, name?)` | 从画像生成主简历+事实校验报告 | — |
| `ats_scan(job_id, resume_version_id)` | 简历↔JD 关键词覆盖报告（分数/缺失词/证据链） | — |
| `materials_interview_questions(profile_id, job_id)` | 面试题库（只引用画像真实条目） | — |
| `ai_egress_disclosure(task)` | AI 任务出境披露文本（用 AI 前必须先给用户看） | — |

## 投递跟踪（状态机）

| MCP 工具 | 作用 | CLI 等价 |
|----------|------|----------|
| `applications_create(job_id, profile_id)` | 建跟踪（初始 discovered；只跟踪不投递） | — |
| `applications_list(profile_id)` | 投递列表+时间线 | `job-hater applications --profile …` |
| `applications_update(application_id, status, note?)` | 推进状态；**applied_confirmed 被拒**（用户亲自确认） | `job-hater transition …` |
| — | 用户确认已投递（唯一入口，用户本人操作） | `job-hater confirm-applied …` |
| `offers_compare(offer_ids_json)` | Offer 并排比较（结构化事实） | — |
| `reminders_list(profile_id?)` | 提醒+跟进建议（建议不落库） | — |
| `stats_overview(profile_id?)` | 漏斗/周活动/渠道效果 | — |
| `stats_salary(city?)` | 薪资分位（样本数随行） | — |

## 环境与运维

- `job-hater doctor`：环境自检（数据库/迁移/信源）——装完引擎先跑这个。
- `job-hater serve`：本地 Web UI（127.0.0.1:8787），画像核对/简历编辑有界面更顺手。
- `job-hater backup`：SQLite 备份（WAL checkpoint 后复制）。
