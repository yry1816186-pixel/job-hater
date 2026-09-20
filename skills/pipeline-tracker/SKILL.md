---
name: pipeline-tracker
description: 投递进度看板与状态管理。触发词：pipeline、投递进度、看板、状态更新、进度。管理 applied→interviewing→offer/rejected 全生命周期，今日投递计数与风控余量一目了然。
---

# Pipeline Tracker — 投递进度看板

## 命令

```bash
python3 core/cli.py pipeline                # 看板 + 待投递高优推荐
python3 core/cli.py blacklist               # 查看黑名单
python3 core/cli.py blacklist --add 某公司   # 手动拉黑（如用户明确不想去）
```

## 状态流转

```
discovered → scored → applied → interviewing → offer
                  ↘ rejected（记录原因，沉淀规律）
```

用户口头汇报进展（"XX约我下周面试""XX挂了"）时，立即更新状态：
```python
# 由Claude直接调用（等效操作）
import sys; sys.path.insert(0, '.')
from core import risk
risk.update_status('<job_id>', 'interviewing', note='2026-09-27 一面')
```
或在对话中用 `python3 -c` 一行调用。**状态不更新，看板就会撒谎。**

## 看板输出规范

1. 今日投递数（分平台）+ 距25份上限余量；
2. 各状态分组列表（offer 最前，rejected 最后但**必须展示**——用户需要看到全貌）；
3. 待投递高优岗位 top5（评分排序、自动排除已投公司）；
4. 一句话建议：优先推进哪个（评分+时效综合，如"该岗 deadline 还剩5天"）。

## 周复盘（用户问"这周情况怎么样"或每周首次调用时主动做）

- 投递→面试转化率、各平台响应差异；
- rejected 岗位共性（城市/薪资/技能缺口），反哺 /upskill 优先级；
- 黑名单与重复投递检查（系统已强制，复核一次）。
