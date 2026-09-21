"""模拟面试练习器（本地会话 + 逐字稿 + 可选 AI 复盘）。

诚实语义：
- 会话与逐字稿是本地事实：逐轮记录（interviewer/candidate + 时间戳），永不外发，
  永不进日志全文（与简历/证据同级隐私）；
- stats 是确定性计算：轮次/问题数/回答数/未接问题/回答长度/时长；
- AI 复盘是显式 opt-in 的远程任务（AIService.run_task 的 ack_egress 门，本地模式
  返回降级说明），复盘结果落库并标注 ai_generated=1；
- 自评（review_self）是确定性替代：勾选项直接成为评分/亮点/短板，不冒充 AI。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from jobhater.db.connection import transaction
from jobhater.domain.models import InterviewReview, InterviewSession
from jobhater.services.ai import AIService, EgressNotAcknowledged
from jobhater.services.storage import insert_model, new_id, row_to_model

_SESSION_JSON = {"transcript": ("transcript_json", [])}
_REVIEW_JSON = {
    "scores": ("scores_json", {}),
    "strengths": ("strengths_json", []),
    "gaps": ("gaps_json", []),
    "practice_items": ("practice_items_json", []),
}

_ROLES = ("interviewer", "candidate")
_MODES = ("mock", "real_record")

# AI 复盘的输出契约（json_schema 传给 provider；本地校验同一形状）
_REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "overall": {"type": "number"},
        "scores": {
            "type": "object",
            "properties": {
                "structure": {"type": "number"},
                "clarity": {"type": "number"},
                "technical": {"type": "number"},
                "evidence_consistency": {"type": "number"},
            },
            "required": ["structure", "clarity", "technical", "evidence_consistency"],
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "practice_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall", "scores", "strengths", "gaps", "practice_items"],
}


class InterviewKitError(ValueError):
    """非法练习器操作（会话不存在、已结束、角色非法等）。"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class InterviewKitService:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.ai = AIService(con)

    # ---------- 会话 ----------

    def create_session(
        self, interview_id: str, *, mode: str = "mock", persona: str | None = None,
        difficulty: int | None = None,
    ) -> InterviewSession:
        """创建练习会话。必须挂在真实面试上（经 interview → application → 岗位），
        复盘才有岗位上下文；无岗位的自由练习不支持——本地产品不提供无上下文闲聊。"""
        if mode not in _MODES:
            raise InterviewKitError(f"mode 必须是 {'/'.join(_MODES)}: {mode!r}")
        if difficulty is not None and not 1 <= difficulty <= 5:
            raise InterviewKitError("difficulty 取值 1-5")
        with transaction(self.con):
            iv = self.con.execute(
                "SELECT id, application_id FROM interviews WHERE id=?", (interview_id,)
            ).fetchone()
            if not iv:
                raise InterviewKitError(f"面试不存在: {interview_id}")
            session = InterviewSession(
                id=new_id("ivs"), interview_id=interview_id, mode=mode,
                persona=persona, difficulty=difficulty,
                started_at=_now(), transcript=[],
            )
            insert_model(self.con, "interview_sessions", session, _SESSION_JSON)
        return session

    def get_session(self, session_id: str) -> InterviewSession:
        row = self.con.execute(
            "SELECT * FROM interview_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise InterviewKitError(f"会话不存在: {session_id}")
        return row_to_model(InterviewSession, row, _SESSION_JSON)

    def list_sessions(self, application_id: str) -> list[InterviewSession]:
        rows = self.con.execute(
            """SELECT s.* FROM interview_sessions s
               JOIN interviews i ON i.id = s.interview_id
               WHERE i.application_id=?
               ORDER BY s.started_at DESC""",
            (application_id,),
        ).fetchall()
        return [row_to_model(InterviewSession, r, _SESSION_JSON) for r in rows]

    def add_turn(self, session_id: str, role: str, content: str) -> InterviewSession:
        """追加一轮（面试官提问或候选人回答）。已结束的会话拒绝追加。"""
        if role not in _ROLES:
            raise InterviewKitError(f"role 必须是 {'/'.join(_ROLES)}: {role!r}")
        content = (content or "").strip()
        if not content:
            raise InterviewKitError("内容不能为空")
        with transaction(self.con):
            row = self.con.execute(
                "SELECT ended_at, transcript_json FROM interview_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if not row:
                raise InterviewKitError(f"会话不存在: {session_id}")
            if row["ended_at"]:
                raise InterviewKitError("会话已结束，不能继续追加")
            transcript = json.loads(row["transcript_json"] or "[]")
            transcript.append({"role": role, "content": content, "at": _now()})
            self.con.execute(
                "UPDATE interview_sessions SET transcript_json=? WHERE id=?",
                (json.dumps(transcript, ensure_ascii=False), session_id),
            )
        return self.get_session(session_id)

    def end_session(self, session_id: str) -> InterviewSession:
        with transaction(self.con):
            cur = self.con.execute(
                "UPDATE interview_sessions SET ended_at=? WHERE id=? AND ended_at IS NULL",
                (_now(), session_id),
            )
            if cur.rowcount == 0:
                raise InterviewKitError(f"会话不存在或已结束: {session_id}")
        return self.get_session(session_id)

    # ---------- 确定性统计 ----------

    def stats(self, session_id: str) -> dict:
        s = self.get_session(session_id)
        turns = s.transcript
        questions = [t for t in turns if t["role"] == "interviewer"]
        answers = [t for t in turns if t["role"] == "candidate"]
        lengths = [len(t["content"]) for t in answers]
        # 未接问题：结尾连续的 interviewer 轮（问了但没答）
        unanswered = 0
        for t in reversed(turns):
            if t["role"] == "interviewer":
                unanswered += 1
            else:
                break
        end_ref = s.ended_at or (turns[-1]["at"] if turns else None)
        duration_min: int | None = None
        if s.started_at and end_ref:
            try:
                start = dt.datetime.fromisoformat(s.started_at.replace("Z", "+00:00"))
                end = dt.datetime.fromisoformat(end_ref.replace("Z", "+00:00"))
                duration_min = max(int((end - start).total_seconds() // 60), 0)
            except ValueError:
                duration_min = None
        return {
            "session_id": s.id,
            "ended": bool(s.ended_at),
            "turns": len(turns),
            "questions": len(questions),
            "answers": len(answers),
            "unanswered_trailing": unanswered,
            "answer_chars": {
                "avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
                "min": min(lengths) if lengths else 0,
                "max": max(lengths) if lengths else 0,
            },
            "duration_min": duration_min,
        }

    # ---------- 复盘 ----------

    def review_self(
        self, session_id: str, *, scores: dict[str, float],
        strengths: list[str] | None = None, gaps: list[str] | None = None,
        practice_items: list[str] | None = None,
    ) -> InterviewReview:
        """确定性自评：用户勾选/打分直接落库，ai_generated=0。"""
        s = self.get_session(session_id)
        review = InterviewReview(
            id=new_id("ivr"), session_id=s.id,
            overall=None, scores={k: float(v) for k, v in scores.items()},
            strengths=strengths or [], gaps=gaps or [], practice_items=practice_items or [],
            ai_generated=False,
        )
        with transaction(self.con):
            insert_model(self.con, "interview_reviews", review, _REVIEW_JSON)
        return review

    def review_ai(self, session_id: str, *, ack_egress: bool = False) -> dict:
        """AI 复盘（远程 opt-in）。本地模式返回降级说明，不落库。"""
        s = self.get_session(session_id)
        context = self._role_context(s)
        system = (
            "你是一名资深校招面试官。基于逐字稿给出复盘：overall（0-10）、scores"
            "（structure/clarity/technical/evidence_consistency，0-10）、strengths、gaps、"
            "practice_items（每项一句可执行练习建议）。只评价有证据支撑的结论，"
            "逐字稿没有的信息明确说'无证据'。用中文。"
        )
        user = json.dumps(
            {"岗位": context, "逐字稿": s.transcript},
            ensure_ascii=False,
        )
        result = self.ai.run_task(
            "interview_review", system, user, ack_egress=ack_egress,
            json_schema=_REVIEW_SCHEMA, max_tokens=1500,
        )
        if not result.get("executed"):
            return result  # local_mode 降级：如实告知，不伪造复盘
        data = self._coerce_review(result["result"])
        review = InterviewReview(
            id=new_id("ivr"), session_id=s.id,
            overall=data["overall"], scores=data["scores"],
            strengths=data["strengths"], gaps=data["gaps"],
            practice_items=data["practice_items"], ai_generated=True,
        )
        with transaction(self.con):
            insert_model(self.con, "interview_reviews", review, _REVIEW_JSON)
        return {"executed": True, "review": review.model_dump()}

    def list_reviews(self, session_id: str) -> list[InterviewReview]:
        rows = self.con.execute(
            "SELECT * FROM interview_reviews WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [row_to_model(InterviewReview, r, _REVIEW_JSON) for r in rows]

    # ---------- 内部 ----------

    def _role_context(self, s: InterviewSession) -> dict:
        """会话关联的岗位/公司/轮次（提示词上下文；不含简历全文）。"""
        row = self.con.execute(
            """
            SELECT p.title, p.employer_name, p.description, i.round, i.kind
            FROM interview_sessions s
            LEFT JOIN interviews i ON i.id = s.interview_id
            LEFT JOIN applications a ON a.id = i.application_id
            LEFT JOIN job_postings p ON p.id = a.job_id
            WHERE s.id=?
            """,
            (s.id,),
        ).fetchone()
        if not row or not row["title"]:
            raise InterviewKitError("该会话未关联面试/岗位，无法复盘（请创建时关联 interview）")
        jd_head = (row["description"] or "")[:1200]
        return {
            "title": row["title"], "employer": row["employer_name"],
            "round": row["round"], "kind": row["kind"], "jd_head": jd_head,
        }

    @staticmethod
    def _coerce_review(raw: object) -> dict:
        """provider 返回 → InterviewReview 字段。形状不符即拒绝（不静默截断）。"""
        data = raw if isinstance(raw, dict) else {}
        try:
            scores = {k: float(v) for k, v in dict(data["scores"]).items()}
            return {
                "overall": float(data["overall"]) if data.get("overall") is not None else None,
                "scores": scores,
                "strengths": [str(x) for x in list(data["strengths"])],
                "gaps": [str(x) for x in list(data["gaps"])],
                "practice_items": [str(x) for x in list(data["practice_items"])],
            }
        except (KeyError, TypeError, ValueError) as e:
            raise InterviewKitError(f"AI 复盘结果形状不符: {e}") from e


__all__ = ["InterviewKitService", "InterviewKitError", "EgressNotAcknowledged"]
