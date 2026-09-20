# sources — 信源采集适配层

> 分层约定：本目录可以依赖 `core/` 与独立 venv（`.venv-sources`）的第三方库；
> `core/` 永远不反向依赖本目录。核心引擎零第三方依赖的不变量由此守护。

## 文件与职责

| 文件 | 职责 | 依赖 |
|------|------|------|
| `mapping.py` | 外部数据 → 统一岗位契约的纯映射（无网络、无DB、无过滤） | 标准库 |
| `wenke_bridge.py` | 调用 wenke-radar 官方接口抓取器（28 源），收割 JD 字段并映射输出 | venv（requests/bs4/pycryptodome/openpyxl） |
| `import_xiaozhao_seed.py` | 27届校招种子数据集一次性导入（批次进去重身份，过时批次透明跳过） | 标准库 |
| `jobradar_leads.py` | job-radar 信源清单 → 渠道线索索引（不自动入库） | 标准库 |
| `semantic_enrich.py` | 可选：BGE-small-zh 本地向量相似度写入 extras.semantic_sim（仅参考旗标，不改五维权重） | venv（fastembed） |
| `setup_env.sh` | 创建 `.venv-sources` 并安装采集/增强依赖 | bash + python3 venv |

## 采集纪律（来自 wenke-radar 的实践，全系统遵守）

1. 只调公开接口，限速，每日一次；
2. 单源失败不拖垮整轮，失败源在 `data/feeds/wenke_health.json` 留痕；
3. 硬壁垒（签名/登录/验证码）探明即放弃并记录——见 `docs/降级路径说明.md`；
4. 服务端"总数"不等于可抓总数，完整性存疑的源在健康面板标注"疑似不完整"。

## 已知诚实边界（2026-09-20 实测）

- 通用 HTML 抓 JD 详情实测仅 1/39 成功（国内校招 ATS 几乎全为前端渲染）——该方案已按
  "不留假能力"原则移除；JD 全文靠①列表接口收割（部分源 100% 带回）②粘贴导入（永远可用）。
- 字节跳动等部分源的列表接口不带 JD 正文；这些岗位 `/apply` 会明确要求粘贴 JD 或显式
  `--allow-no-jd` 生成通用版。
