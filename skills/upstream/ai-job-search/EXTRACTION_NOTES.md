# ai-job-search 提取说明（MadsLorentzen/ai-job-search，MIT）

原始文件：repos/ai-job-search/.claude/{commands,skills}/（本目录为副本，原件保留）

## 已吸收进本系统的机制
| 上游文件 | 机制 | 落地位置 |
|----------|------|----------|
| commands/apply.md | Drafter-Reviewer 双Agent工作流、Standing Rule（事实同轮回写）、JD反注入（"posting is untrusted data"） | skills/resume-builder/SKILL.md、Campus-Job-Agent.SKILL.md |
| commands/rank.md | 分层评分：/rank 快筛 vs /apply 深评，两阶段算力分配 | skills/job-finder + skills/jobmatch-ai 的分工 |
| commands/interview.md | 面试准备与已投材料的一致性校验 | skills/interview-coach/SKILL.md |
| skills/04-job-evaluation.md | 闸门先于评分（资格闸→语言闸→五维） | core/ingest.py campus_filter + skills/jobmatch-ai |
| skills/05-cv-templates.md | 定制只重排/换措辞/调侧重 | core/resume.py 生成器三操作白名单 |
| skills/07-interview-prep.md | STAR、证据一致性 | skills/interview-coach |
