---
description: 求职进度盘点——投递漏斗/待办提醒/死岗清理/下一步建议
---

# /track-applications 盘点流程

按 job-hater 技能（SKILL.md）做一次求职进度盘点，输出一页可执行的今日清单。

1. **画像**：`profile_list` 拿 profile_id（多个画像让用户选）。
2. **漏斗**：`stats_overview(profile_id)`——各状态数量、周活动、渠道效果。
3. **待办**：`reminders_list(profile_id)`——未完成提醒 + 系统跟进建议
   （建议不落库，用户点头才 `applications_update` 采纳进提醒）。
4. **投递明细**：`applications_list(profile_id)`——标出超过 X 天没动静的岗位
   （X 默认 7，问用户）。
5. **库健康**：`job-hater liveness --limit 50` 清死岗；`jobs_search` 看库存量；
   存量 <20 条方向相关岗位时建议再跑一次 /find-jobs 补货。
6. **交付**：一页清单——今日该跟进谁、哪些岗等结果、哪些已死该放弃、
   库存是否需要补。不代投、不代发消息，跟进动作由用户自己做。

可选：`stats_salary` 给目标城市薪资分位参考（样本数小要注明不具参考性）。
