# career-ops 提取说明（career-ops-hq/career-ops，MIT (c) 2026 Santiago Fernández de Valderrama）

原始位置：repos/career-ops/（1438 files 完整归档）；本目录为 modes/ 技能文件与关键架构文档副本。

## 定位
本系统以 career-ops 的工作流框架为主体底座（提示词 2.1.1）。其模式化命令组织
（scan→triage→deep→apply→interview→outcome 的 mode 流水线）是六命令路由的设计蓝本。

## 已吸收进本系统的机制
| career-ops 机制 | 本系统落地 |
|----------------|-----------|
| DATA_CONTRACT 用户层/系统层分离（用户数据永不被更新覆盖） | core/store.py 的 data/ 契约 + profile.json 归用户所有 |
| config/cv-facts.json（事实白名单+禁语表）+ verify-cv-facts.mjs | core/factcheck.py 的 evidence_index 硬校验 |
| mode 流水线（scan/triage/deep/apply/interview/upskill/outcome） | 六命令（/search /apply /interview /upskill /pipeline /profile） |
| scan-history 的 SimHash 去重 + trust score + 岗位活性检测（liveness） | core/ingest.py 去重键 + expired 过滤 |
| jd-skill-gap.mjs / upskill.mjs | core/upskill.py |
| tracker.mjs（applications.md 状态机） | core/risk.py 投递台账 |
| validate-untrusted-content-coverage.mjs（反注入覆盖验证） | Campus-Job-Agent.SKILL.md 反注入铁律 |

## 未吸收（如实说明）
- Node.js 工具链本身（本系统核心引擎用 Python 标准库重写，保证零依赖离线可用）
- LaTeX/PDF 构建链（用户可用上游原版生成；本系统输出 Markdown 交付）
