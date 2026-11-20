---
description: 全领域找岗位——建档→多渠道发现→匹配→交付带依据的岗位短清单
argument-hint: "[方向/城市/类型，如：后端开发 杭州 社招]"
---

# /find-jobs 作战流程

按 job-hater 技能（SKILL.md）执行一次完整的岗位发现作战。参数 $ARGUMENTS 里给了
方向/城市/类型就直接用，缺的当面向用户确认，不要猜。

1. **检查**：`job-hater doctor` 失败 → 先让用户装引擎（plugin/README.md）。
2. **建档**：`profile_list`；无画像 → 请用户给简历文本（粘贴即可），
   `profile_create(resume_text=…)`，warnings 如实转告并请用户补。
3. **偏好**：`preset_set`——方向/城市/类型（internship/campus/social，
   社招用户只给 social）。薪资底线、学历门槛问一句。
4. **发现**（重点，目标 ≥30 条方向相关岗位）：按 references/channels.md 组合——
   ATS 公开板（目标公司清单）+ 浏览器/搜索浏览招聘平台与公司官网 +
   `sources_fetch`。每条 JD 过 `paste_parse` 后 `jobs_import`。
   限速礼貌抓取；需要登录的站点请用户自己登录。
5. **匹配**：`match_run` → 取 top 10，每条附公司/城市/薪资/分数/verdict/
   failed_gates。被拦的岗位说明拦因。
6. **交付**：表格呈现，问用户哪几条建跟踪（`applications_create`）。
   顺手对 top 1-2 条跑 `ats_scan`（需先 `resumes_prepare`）。
7. **收尾**：告诉用户库里有 N 条可随时 `match_run` 重排；死岗会由
   `job-hater liveness` 定期清理。

铁律：绝不代投；AI 增强先调 `ai_egress_disclosure` 征得同意；不规避反检测。
