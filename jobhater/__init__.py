"""Job Hater — 本地优先的中国求职全流程管理系统。

架构原则（v2 重建）：
- 单一领域层：一套 domain models、一套 application services、一套 SQLite 存储；
  Web UI / CLI / MCP 三个接口共享同一服务层，禁止平行逻辑。
- Evidence-first：候选人与岗位的每条事实可溯源（provenance），AI 不得凭空创造经历。
- Honest degradation：外部信源/AI 失败时如实降级，绝不假装成功。
- Local-first：业务数据默认只存本机；启用远程 AI 时明确披露数据出境范围。
"""

__version__ = "2.2.0"

APP_NAME = "job-hater"
