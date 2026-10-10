"""AI 域端点：AI Provider/任务执行、简历导入(onboarding)、反馈。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, UploadFile

from jobhater.api.deps import _err, get_con
from jobhater.api.schemas import (
    AICompleteIn,
    AIProviderIn,
    APIKeyIn,
    EnabledIn,
    FeedbackIn,
    ResumeAIParseIn,
    ResumeTextInput,
)
from jobhater.services.ai import EGRESS_DISCLOSURES, AIService, EgressNotAcknowledged
from jobhater.services.lifecycle import FeedbackService
from jobhater.services.resume_import import ResumeImportError, ResumeImportService


def register(app: FastAPI) -> None:
    # ---------- AI ----------

    @app.get("/api/ai/providers")
    def list_ai_providers(con=Depends(get_con)):
        return AIService(con).list_providers()

    @app.post("/api/ai/providers")
    def add_ai_provider(body: AIProviderIn, con=Depends(get_con)):
        pid = AIService(con).add_provider(
            adapter_kind=body.adapter_kind, display_name=body.display_name,
            base_url=body.base_url, model=body.model, timeout_s=body.timeout_s,
            max_retries=body.max_retries, cost_note=body.cost_note, enabled=body.enabled,
        )
        return {"id": pid}

    @app.post("/api/ai/providers/{provider_id}/key")
    def set_api_key(provider_id: str, body: APIKeyIn, con=Depends(get_con)):
        try:
            AIService(con).set_api_key(provider_id, body.api_key)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/ai/providers/{provider_id}/enabled")
    def set_ai_enabled(provider_id: str, body: EnabledIn, con=Depends(get_con)):
        try:
            AIService(con).set_enabled(provider_id, body.enabled)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/ai/egress")
    def ai_egress(task: str, con=Depends(get_con)):
        if task not in EGRESS_DISCLOSURES:
            raise HTTPException(422, f"未知任务类型，可选：{sorted(EGRESS_DISCLOSURES)}")
        return {"task": task, "disclosure": AIService(con).egress_disclosure(task)}

    @app.get("/api/ai/tasks")
    def ai_tasks(con=Depends(get_con)):
        """任务目录：label + 当前是否可用 + 各自出境披露。"""
        return AIService(con).list_tasks()

    @app.post("/api/ai/complete")
    def ai_complete(body: AICompleteIn, con=Depends(get_con)):
        """AI 任务执行（远程 opt-in 唯一入口）。

        - 本地模式 → 200 {executed:false, reason:"local_mode"}（诚实状态，非错误）
        - 未确认披露 → 428 {disclosure}（调用方先展示、用户确认后带 ack_egress 重试）
        """
        try:
            return AIService(con).run_task(
                body.task, body.system, body.user,
                ack_egress=body.ack_egress, max_tokens=body.max_tokens,
                temperature=body.temperature, json_schema=body.json_schema,
            )
        except EgressNotAcknowledged as e:
            raise HTTPException(428, detail={"disclosure": str(e), "task": body.task}) from e
        except KeyError as e:
            raise _err(e) from e
        except Exception as e:
            raise _err(e) from e

    # ---------- 简历导入（冷启动主链） ----------

    @app.post("/api/onboarding/parse-resume")
    async def parse_resume_file(file: UploadFile, con=Depends(get_con)):
        """上传简历文件（pdf/docx/txt/md/json）→ 结构化草稿。不落库——
        草稿必须经用户核对编辑，再走 POST /api/profiles + import/json-resume 落库。"""
        data = await file.read()
        try:
            return ResumeImportService(con).parse_file(file.filename or "", data)
        except ResumeImportError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/onboarding/parse-resume-text")
    def parse_resume_text(body: ResumeTextInput, con=Depends(get_con)):
        """粘贴简历文本 → 结构化草稿（与文件上传同一解析器，同样不落库）。"""
        if not body.text.strip():
            raise HTTPException(422, "简历文本不能为空")
        return ResumeImportService(con).parse_paste(body.text)

    @app.post("/api/onboarding/ai-parse")
    def ai_parse_resume(body: ResumeAIParseIn, con=Depends(get_con)):
        """AI 精解析（远程 opt-in，428 语义同 /api/ai/complete）：
        本地模式 executed=False；结果同样只是草稿，需用户核对。"""
        if not body.text.strip():
            raise HTTPException(422, "简历文本不能为空")
        try:
            return ResumeImportService(con).ai_parse(body.text, ack_egress=body.ack_egress)
        except EgressNotAcknowledged as e:
            raise HTTPException(428, detail={"disclosure": str(e), "task": "resume_parse"}) from e
        except ResumeImportError as e:
            raise HTTPException(422, str(e)) from e

    # ---------- 反馈 ----------

    @app.post("/api/feedback")
    def record_feedback(body: FeedbackIn, con=Depends(get_con)):
        FeedbackService(con).record(body.profile_id, body.kind, job_id=body.job_id, note=body.note)
        return {"ok": True}

    @app.get("/api/feedback")
    def feedback_history(profile_id: str, con=Depends(get_con)):
        return FeedbackService(con).history(profile_id)

    @app.delete("/api/feedback")
    def reset_feedback(profile_id: str, con=Depends(get_con)):
        return {"deleted": FeedbackService(con).reset(profile_id)}
