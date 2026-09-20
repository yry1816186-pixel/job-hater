"""AI Provider 抽象测试：本地模式默认、key 永不落库、出境披露、启停语义。"""
from __future__ import annotations

import json

import pytest

from jobhater.db import apply_all, connect
from jobhater.services.ai import (
    AINotConfigured,
    AIService,
    NoneProvider,
    TaskRequest,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    apply_all(tmp_path / "ai.db")
    con = connect(tmp_path / "ai.db")
    # 用内存假 keyring 隔离 OS（测试永不触真实凭据存储）
    store: dict[tuple[str, str], str] = {}
    import jobhater.services.ai as ai_mod

    class FakeKeyring:
        @staticmethod
        def set_password(service: str, user_: str, pw: str) -> None:
            store[(service, user_)] = pw

        @staticmethod
        def get_password(service: str, user_: str) -> str | None:
            return store.get((service, user_))

    monkeypatch.setattr(ai_mod, "HAVE_KEYRING", True)
    monkeypatch.setattr(ai_mod, "keyring", FakeKeyring)
    yield AIService(con), con
    con.close()


def test_local_only_by_default(svc):
    ai, _ = svc
    provider = ai.active_provider()
    assert isinstance(provider, NoneProvider)
    assert provider.capabilities()["offline"] is True
    with pytest.raises(AINotConfigured, match="本地模式"):
        provider.complete(TaskRequest(task="resume_rewrite", system="s", user="u"))
    assert ai.egress_disclosure("resume_rewrite") == "本地模式：不会发送任何数据。"


def test_provider_crud_and_keyring(svc):
    ai, con = svc
    pid = ai.add_provider(
        adapter_kind="openai_compatible", display_name="智谱GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4", model="glm-4-flash",
    )
    ai.set_api_key(pid, "sk-test-123")
    # key 只在 keyring，数据库永远只有引用名
    rows = con.execute("SELECT api_key_ref FROM ai_providers WHERE id=?", (pid,)).fetchall()
    db_text = json.dumps([dict(r) for r in rows])
    assert "sk-test-123" not in db_text
    assert rows[0]["api_key_ref"] == f"jobhater/{pid}"
    # 未启用 → 仍是本地模式
    assert isinstance(ai.active_provider(), NoneProvider)
    # 启用后 → OpenAI 兼容实例
    ai.set_enabled(pid, True)
    provider = ai.active_provider()
    assert provider.adapter_kind == "openai_compatible"
    assert provider.api_key == "sk-test-123"
    # 出境披露切换为远程描述
    assert "发送" in ai.egress_disclosure("job_deep_review")


def test_enabled_but_missing_key_degrades_to_local(svc):
    ai, _ = svc
    ai.add_provider(
        adapter_kind="openai_compatible", display_name="DeepSeek",
        base_url="https://api.deepseek.com/v1", model="deepseek-chat", enabled=True,
    )
    # 从未 set_api_key
    assert isinstance(ai.active_provider(), NoneProvider)


def test_ollama_local_endpoint_needs_no_key(svc):
    ai, _ = svc
    ai.add_provider(
        adapter_kind="ollama", display_name="本地Ollama",
        base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b", enabled=True,
    )
    provider = ai.active_provider()
    assert provider.adapter_kind == "openai_compatible"  # Ollama 走 OpenAI 兼容协议
    assert provider.api_key is None
