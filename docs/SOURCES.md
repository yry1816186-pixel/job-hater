# 信源适配器插件契约（SourceAdapter Plugin Contract）

本文面向想给 Job Hater 贡献新信源适配器的开发者。粘贴导入是永不失效的主链
（`manual` 信源内建），适配器是渐进增强——所以每个适配器都必须能优雅失败。

## 设计原则（先读这个）

1. **fail-closed**：登录墙 / 验证码 / 反爬升级 → 如实报 `degraded`/`down`，
   绝不绕过（这是产品红线，见 SECURITY.md）。
2. **限速礼貌**：官方接口按页间隔（参考 `PAGE_INTERVAL_S = 1.2`），不做并发轰炸。
3. **故障隔离**：单公司/单源失败只记健康状态，不拖垮整批。
4. **数据如实**：抓不到的字段就是缺（`None`），不猜不编。

## 契约（`jobhater/services/sources/base.py`）

```python
class SourceAdapter(Protocol):
    id: str                    # 信源标识（入库 job_postings.source_id）
    display_name: str          # UI 展示名

    def capabilities(self) -> dict:
        """声明能力面。约定键：
        trigger: "user_paste" | "cli_fetch"        # 触发方式
        needs_login: bool                          # 是否需要登录（True=暂不接入）
        rate_policy: {"page_interval_s": 1.2, "max_pages": 30}
        timeout_s: 15
        """

    def produce(self) -> Iterable[dict]:
        """产出 raw job dict 流（统一 ingest 契约，见下方键表）。
        失败时：抛异常=整批失败（不推荐）；更好——返回已成功的部分 +
        把失败原因写进 health_report（参考 WenkeAdapter.last_fetch_report）。"""

    def health_check(self) -> HealthReport:
        """无副作用探活。HealthReport(ok, message)——如实，不粉饰。"""
```

## raw job dict 键表（与 `JobService.normalize` 对应）

| 键 | 类型 | 必填 | 说明 |
|----|------|------|------|
| `title` | str | ✅ | 岗位标题 |
| `company` | str | ✅ | 公司名（雇主表自动归一） |
| `city` | str |  | 城市（与检索/城市 gate 相关） |
| `salary` | str |  | 薪资原文（"20-35K·16薪"，引擎解析 K/万/日薪/N薪） |
| `description` | str |  | JD 全文（FTS 检索与匹配的主要语料） |
| `education` | str |  | 学历要求（"本科"/"硕士"…） |
| `experience` | str |  | 经验要求原文（"3-5年"） |
| `url` | str |  | 岗位链接（canonical_url） |
| `source_job_id` | str |  | 信源侧 ID（同源去重第一层） |
| `published_at` | str |  | 发布日期 ISO（YYYY-MM-DD） |
| `deadline` | str |  | 截止日期 ISO（过期判定+日历导出） |
| `keywords` | list[str] |  | 结构化技能词（材料工坊缺口分析用） |

未知键会被忽略（`extras.inferences` 保留推断依据，可审计可反驳）。

## 四层去重（你不用做，但要知道）

入库统一走：同源同 ID → 跨源同岗键（norm 公司|标题）→ 近似标题（bigram
Jaccard ≥0.7，**入库+标记人工复核**，不静默丢弃）→ 内容指纹。适配器只管产出
raw dict，去重是 ingest 的事。

## 注册

在 `jobhater/services/sources/__init__.py` 的 `REGISTRY` 加一行：

```python
REGISTRY.register(YourAdapter())
```

## 参考实现

- `paste.py` — 最简形态（用户触发，不主动产出）
- `wenke.py` — 官方 JSON API 抓取形态（分页/限速/逐公司故障隔离/健康上报），
  移植自 MIT 项目 wenke-radar（版权声明见文件头）

## 验收清单（PR 前自检）

- [ ] `capabilities()` 如实声明（含 rate_policy）
- [ ] 登录墙/验证码路径 fail-closed（有测试或说明）
- [ ] 分页有上限、页间有限速
- [ ] `tests/test_<your>_adapter.py`：正常解析 + 健康检查（外网 smoke 用
      `pytest.mark.skipif` 条件保护，参考 test_wenke_adapter.py）
- [ ] 不引入新的第三方运行时依赖（标准库 + httpx 优先）
