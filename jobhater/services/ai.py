"""AI Provider 抽象（§12）+ 出境披露（§13）。

原则：
- 默认 local-only：没有启用任何 provider 时，一切 AI 功能明确报"未配置"，
  非 AI 能力完全不受影响（§30 降级）；
- API key 永不落库、永不入日志：存 OS keyring（服务名 jobhater/<provider_id>），
  数据库里只有 api_key_ref 引用名；
- 每类任务调用前可查询 egress_disclosure：会发送什么类别的数据，一目了然；
- 超时/重试来自 provider 配置；provider 失败抛 AIError，不损坏本地数据。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from jobhater.db.connection import transaction
from jobhater.services.storage import new_id

try:  # keyring 为运行时依赖；缺失时显式降级并拒绝存 key（不静默回退到明文）
    import keyring

    HAVE_KEYRING = True
except ImportError:  # pragma: no cover
    HAVE_KEYRING = False


class AIError(RuntimeError):
    """Provider 调用失败（超时/HTTP/响应不合法）。本地数据不受影响。"""


class AINotConfigured(AIError):
    """本地模式：未启用任何远程 provider。调用方应走非 AI 降级路径。"""


# 任务 → 出境数据类别披露（UI 在调用前展示；保持与实际请求一致是硬要求）
EGRESS_DISCLOSURES: dict[str, str] = {
    "job_deep_review": "发送：该岗位的标题/公司/JD 全文 + 你的画像摘要（技能、经历标签），不含联系方式",
    "resume_rewrite": "发送：目标岗位 JD + 所选简历版本的结构化内容（含你写在简历里的全部文本）",
    "cover_letter": "发送：目标岗位 JD + 简历要点摘要",
    "interview_mock": "发送：面试官角色设定 + 对话历史（含你在模拟中的回答）",
    "interview_review": "发送：模拟面试完整转录文本",
    "fact_extraction": "发送：你导入的原始文档全文（用于提取候选事实）",
}


@dataclass
class TaskRequest:
    task: str                    # EGRESS_DISCLOSURES 的键
    system: str
    user: str
    max_tokens: int = 2048
    temperature: float = 0.3
    json_schema: dict | None = None  # 要求结构化输出时的 JSON Schema


class AIProvider(Protocol):
    adapter_kind: str

    def capabilities(self) -> dict: ...
    def complete(self, req: TaskRequest) -> Any: ...


class NoneProvider:
    """本地模式的显式空实现：能力清单如实为空，调用即抛 AINotConfigured。"""

    adapter_kind = "none"

    def capabilities(self) -> dict:
        return {"enabled": False, "offline": True, "structured_output": False}

    def complete(self, req: TaskRequest) -> Any:
        raise AINotConfigured(
            "未配置 AI Provider（本地模式）。该功能需要远程模型；"
            "在 设置→AI Provider 中启用并填入自己的 API Key。"
        )


class OpenAICompatProvider:
    """OpenAI 兼容协议：覆盖 OpenAI / Zhipu GLM / DeepSeek / Qwen DashScope兼容模式 /
    Kimi / Ollama / 任意本地 vLLM·llama.cpp 端点。"""

    adapter_kind = "openai_compatible"

    def __init__(
        self, *, base_url: str, model: str, api_key: str | None,
        timeout_s: int = 60, max_retries: int = 1, display_name: str = "",
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.display_name = display_name
        self._client = httpx.Client(timeout=timeout_s)

    def capabilities(self) -> dict:
        return {
            "enabled": True, "offline": False, "protocol": "openai_chat",
            "model": self.model, "structured_output": True,
        }

    def complete(self, req: TaskRequest) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                resp = self._client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if req.json_schema is not None:
                    return _load_json_content(content)
                return content
            except Exception as e:  # httpx.HTTPError / KeyError / ValueError
                last_err = e
        raise AIError(f"AI 调用失败（{self.display_name or self.model}）：{last_err}") from last_err


class AnthropicProvider:
    adapter_kind = "anthropic"

    def __init__(
        self, *, base_url: str = "https://api.anthropic.com/v1", model: str,
        api_key: str, timeout_s: int = 60, max_retries: int = 1, display_name: str = "",
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.display_name = display_name
        self._client = httpx.Client(timeout=timeout_s)

    def capabilities(self) -> dict:
        return {"enabled": True, "offline": False, "protocol": "anthropic_messages",
                "model": self.model, "structured_output": "tool_json"}

    def complete(self, req: TaskRequest) -> Any:
        payload = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
            "temperature": req.temperature,
        }
        last_err: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                resp = self._client.post(
                    f"{self.base_url}/messages",
                    json=payload,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                resp.raise_for_status()
                content = "".join(
                    b.get("text", "") for b in resp.json().get("content", [])
                )
                if req.json_schema is not None:
                    return _load_json_content(content)
                return content
            except Exception as e:
                last_err = e
        raise AIError(f"AI 调用失败（{self.display_name or self.model}）：{last_err}") from last_err


def _load_json_content(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise AIError(f"模型未返回合法 JSON：{content[:120]!r}") from e


class AIService:
    """Provider 注册表：读写 ai_providers 表，构造 provider 实例。

    key 存取经 OS keyring（服务名 = api_key_ref 列的值）。
    """

    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    # ---- 配置 CRUD ----

    def add_provider(
        self, *, adapter_kind: str, display_name: str, base_url: str | None,
        model: str, capabilities: dict | None = None,
        timeout_s: int = 60, max_retries: int = 1, cost_note: str | None = None,
        enabled: bool = False,
    ) -> str:
        pid = new_id("aip")
        key_ref = f"jobhater/{pid}"
        with transaction(self.con):
            self.con.execute(
                """INSERT INTO ai_providers (id, adapter_kind, display_name, base_url, model,
                     api_key_ref, enabled, capabilities_json, timeout_s, max_retries, cost_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid, adapter_kind, display_name, base_url, model, key_ref, 1 if enabled else 0,
                    json.dumps(capabilities or {}, ensure_ascii=False), timeout_s, max_retries, cost_note,
                ),
            )
        return pid

    def set_enabled(self, provider_id: str, enabled: bool) -> None:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE ai_providers SET enabled=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (1 if enabled else 0, provider_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"provider 不存在: {provider_id}")

    def list_providers(self) -> list[dict]:
        rows = self.con.execute("SELECT * FROM ai_providers ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["capabilities"] = json.loads(d.pop("capabilities_json") or "{}")
            d["has_api_key"] = self._read_key(d.pop("api_key_ref")) is not None if d["adapter_kind"] != "ollama" else True
            out.append(d)
        return out

    # ---- keyring ----

    def _read_key(self, key_ref: str) -> str | None:
        if not HAVE_KEYRING or not key_ref:
            return None
        try:
            return keyring.get_password(key_ref, "api_key")
        except Exception:
            return None

    def set_api_key(self, provider_id: str, api_key: str) -> None:
        row = self.con.execute(
            "SELECT api_key_ref, adapter_kind FROM ai_providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"provider 不存在: {provider_id}")
        if not HAVE_KEYRING:
            raise AIError(
                "当前环境缺少 keyring 库，无法安全保存 API Key。"
                "请 pip install jobhater[ai] 后重试；我们不会把 key 存入数据库或明文文件。"
            )
        keyring.set_password(row["api_key_ref"], "api_key", api_key)

    # ---- 实例化 ----

    def active_provider(self) -> AIProvider:
        """当前启用的 provider；未启用任何远程模型时返回 NoneProvider（本地模式）。"""
        row = self.con.execute(
            "SELECT * FROM ai_providers WHERE enabled=1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return NoneProvider()
        cfg = dict(row)
        key = self._read_key(cfg["api_key_ref"] or "") if cfg["adapter_kind"] != "ollama" else None
        if cfg["adapter_kind"] in ("openai_compatible", "ollama") and key is None and cfg["adapter_kind"] == "openai_compatible":
            return NoneProvider()  # 启用了但 key 缺失：如实按本地模式处理，UI 提示补 key
        if cfg["adapter_kind"] == "anthropic":
            if not key:
                return NoneProvider()
            return AnthropicProvider(
                base_url=cfg["base_url"] or "https://api.anthropic.com/v1",
                model=cfg["model"], api_key=key,
                timeout_s=cfg["timeout_s"], max_retries=cfg["max_retries"],
                display_name=cfg["display_name"],
            )
        return OpenAICompatProvider(
            base_url=cfg["base_url"] or "https://api.openai.com/v1",
            model=cfg["model"], api_key=key,
            timeout_s=cfg["timeout_s"], max_retries=cfg["max_retries"],
            display_name=cfg["display_name"],
        )

    def egress_disclosure(self, task: str) -> str:
        if task not in EGRESS_DISCLOSURES:
            raise KeyError(f"未知任务类型: {task}")
        provider = self.active_provider()
        if isinstance(provider, NoneProvider):
            return "本地模式：不会发送任何数据。"
        return EGRESS_DISCLOSURES[task] + "（接收方：你启用的 AI Provider）"
