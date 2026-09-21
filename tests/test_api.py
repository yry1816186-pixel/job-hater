"""API 集成测试：经 TestClient 走完整主链（建档→导入→匹配→投递→确认→Offer）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jobhater import config
from jobhater.api import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config.set_data_dir(tmp_path)
    app = create_app()
    with TestClient(app) as c:  # with 触发 lifespan（自动迁移）
        yield c
    config.set_data_dir(None)


def test_full_user_journey(client):
    # 1. 健康检查
    assert client.get("/api/health").json()["ok"] is True

    # 2. 建档 + 画像
    r = client.post("/api/profiles", json={"display_name": "小林", "headline": "2027届·设计×AI"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    client.post(f"/api/profiles/{pid}/educations",
                json={"school": "南京某大学", "degree": "本科", "major": "环境设计"})
    client.post(f"/api/profiles/{pid}/skills",
                json={"name": "Figma", "level": 3, "aliases": ["figma"]})
    ev = client.post(f"/api/profiles/{pid}/evidence",
                     json={"original_text": "校级设计竞赛一等奖", "fact_type": "award"}).json()
    assert client.post(f"/api/profiles/{pid}/evidence/confirm",
                       json={"evidence_ids": [ev["id"]]}).json()["confirmed"] == 1

    # 3. 偏好
    preset = client.post(f"/api/profiles/{pid}/presets", json={
        "name": "秋招", "employment_types": ["campus"],
        "target_roles": ["AI产品", "产品设计"], "target_cities": ["南京"],
        "salary_min_k": 10, "graduation_year": 2027, "max_experience_years_required": 0,
    }).json()
    client.post(f"/api/presets/{preset['id']}/activate")

    # 4. 导入岗位
    stats = client.post("/api/jobs/import", json={"jobs": [
        {"title": "AI产品设计师（2027届）", "company": "星河科技", "city": "南京",
         "salary": "12-18K", "description": "2027届校园招聘，AI产品设计方向，要求Figma"},
        {"title": "资深Java工程师", "company": "云图信息", "city": "杭州",
         "salary": "30-50K", "experience_required": "5年以上", "description": "社招 Java 架构"},
    ]}).json()
    assert stats["added"] == 2, stats

    # 5. 匹配
    match = client.post("/api/match/run", json={"profile_id": pid}).json()
    assert match["evaluated"] == 2
    top = match["results"][0]
    assert top["job_id"] and top["dims"] and top["gate_reasons"] is not None

    # 6. 拿到 AI 岗 id（eligible 的那个）
    ai_job = next(
        r for r in match["results"]
        if r["eligible"] and "产品" in client.get(f"/api/jobs/{r['job_id']}").json()["title"]
    )
    # 7. 建投递 → 推进 → 确认投递
    app_id = client.post("/api/applications",
                         json={"job_id": ai_job["job_id"], "profile_id": pid}).json()["id"]
    client.post(f"/api/applications/{app_id}/transition", json={"status": "saved"})
    client.post(f"/api/applications/{app_id}/transition", json={"status": "preparing"})
    client.post(f"/api/applications/{app_id}/transition", json={"status": "materials_ready"})
    client.post(f"/api/applications/{app_id}/transition", json={"status": "ready_to_apply"})
    final = client.post(f"/api/applications/{app_id}/confirm-applied",
                        json={"channel": "官网"}).json()
    assert final["status"] == "applied_confirmed" and final["applied_at"]
    # 8. 事件审计
    assert len(client.get(f"/api/applications/{app_id}/events").json()) >= 5

    # 9. 面试 + Offer + 比较
    client.post(f"/api/applications/{app_id}/interviews",
                json={"round": 1, "kind": "hr", "scheduled_at": "2026-10-10T10:00:00Z"})
    offer = client.post("/api/offers", json={
        "application_id": app_id, "base_salary_k": 15, "salary_months": 15,
        "city": "南京", "benefits": ["五险一金"],
    }).json()
    cmp = client.post("/api/offers/compare", json={"offer_ids": [offer["id"]]}).json()
    assert cmp[0]["annual_base_k"] == 225.0

    # 10. 反馈闭环
    client.post("/api/feedback", json={"profile_id": pid, "kind": "interested",
                                       "job_id": ai_job["job_id"]})
    assert len(client.get("/api/feedback", params={"profile_id": pid}).json()) == 1
    assert client.delete("/api/feedback", params={"profile_id": pid}).json()["deleted"] == 1


def test_error_semantics(client):
    # 不存在的画像 404；非法转移 422
    client.post("/api/jobs/import", json={"jobs": [
        {"title": "测试岗", "company": "某公司"}]})
    job_id = client.get("/api/jobs").json()["items"][0]["id"]
    p2 = client.post("/api/profiles", json={"display_name": "二号"}).json()["id"]
    app_id = client.post("/api/applications",
                         json={"job_id": job_id, "profile_id": p2}).json()["id"]
    assert client.post(f"/api/applications/{app_id}/transition",
                       json={"status": "offer"}).status_code == 422
    assert client.get("/api/profiles/nonexistent").status_code == 404
    assert client.get("/api/jobs/nonexistent").status_code == 404


def test_ai_local_mode_semantics(client):
    # 默认本地模式：providers 空，egress 明确说"不发送"
    assert client.get("/api/ai/providers").json() == []
    d = client.get("/api/ai/egress", params={"task": "resume_rewrite"}).json()["disclosure"]
    assert "本地模式" in d
    assert client.get("/api/ai/egress", params={"task": "bogus"}).status_code == 422
