"""投递生命周期域端点：投递/Offer/面试/提醒/联系人/标签。

自 routes.py 纯搬移（BC-007）：URL/方法/schema/错误语义逐字不变。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from jobhater.api.deps import _err, get_con
from jobhater.api.schemas import (
    ApplicationIn,
    ConfirmAppliedIn,
    ContactIn,
    ContactPatchIn,
    InterviewFinishIn,
    InterviewIn,
    LinkResumeIn,
    OfferCompareIn,
    OfferIn,
    OfferStatusIn,
    ReminderDoneIn,
    ReminderIn,
    TransitionIn,
)
from jobhater.services.contacts import ContactsService
from jobhater.services.lifecycle import ApplicationService, LifecycleError
from jobhater.services.reminders import RemindersService


def register(app: FastAPI) -> None:
    # ---------- 投递生命周期 ----------

    @app.post("/api/applications")
    def create_application(body: ApplicationIn, con=Depends(get_con)):
        try:
            app_id = ApplicationService(con).create(
                body.job_id, body.profile_id, status=body.status
            )
            return {"id": app_id}
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications")
    def list_applications(profile_id: str, con=Depends(get_con)):
        return ApplicationService(con).list(profile_id)

    @app.get("/api/applications/{app_id}")
    def get_application(app_id: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).get(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/resume")
    def link_resume(app_id: str, body: LinkResumeIn, con=Depends(get_con)):
        try:
            ApplicationService(con).link_resume(app_id, body.resume_version_id)
            return ApplicationService(con).get(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/transition")
    def transition(app_id: str, body: TransitionIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).transition(app_id, body.status, note=body.note)
        except LifecycleError as e:
            raise _err(e) from e  # 状态机语义错误：422 并保留完整信息
        except ValueError as e:
            # 非法状态串（ApplicationStatus 枚举外）：422 且不回显内部异常文本
            raise HTTPException(422, f"非法投递状态：{body.status}") from e
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/confirm-applied")
    def confirm_applied(app_id: str, body: ConfirmAppliedIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).confirm_applied(app_id, channel=body.channel)
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications/{app_id}/events")
    def app_events(app_id: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).events(app_id)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/applications/{app_id}/interviews")
    def schedule_interview(app_id: str, body: InterviewIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).schedule_interview(
                app_id, **body.model_dump()
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/applications/{app_id}/interviews")
    def list_interviews(app_id: str, con=Depends(get_con)):
        return [i.model_dump() for i in ApplicationService(con).list_interviews(app_id)]

    @app.post("/api/interviews/{interview_id}/finish")
    def finish_interview(interview_id: str, body: InterviewFinishIn, con=Depends(get_con)):
        try:
            ApplicationService(con).finish_interview(
                interview_id, outcome=body.outcome, notes=body.notes
            )
            return {"ok": True, "interview_id": interview_id, "outcome": body.outcome}
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/offers")
    def add_offer(body: OfferIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).add_offer(
                body.application_id, **body.model_dump(exclude={"application_id"})
            ).model_dump()
        except Exception as e:
            raise _err(e) from e

    @app.get("/api/offers")
    def list_offers(profile_id: str, con=Depends(get_con)):
        return ApplicationService(con).list_offers(profile_id)

    @app.post("/api/offers/compare")
    def compare_offers(body: OfferCompareIn, con=Depends(get_con)):
        try:
            return ApplicationService(con).compare_offers(body.offer_ids)
        except Exception as e:
            raise _err(e) from e

    @app.post("/api/offers/{offer_id}/status")
    def set_offer_status(offer_id: str, body: OfferStatusIn, con=Depends(get_con)):
        try:
            ApplicationService(con).set_offer_status(offer_id, body.status)
            return {"ok": True}
        except Exception as e:
            raise _err(e) from e

    # ---------- 联系人（轻量 CRM） ----------

    @app.get("/api/contacts")
    def list_contacts(
        application_id: str | None = None, employer_id: str | None = None,
        q: str | None = None, con=Depends(get_con),
    ):
        return ContactsService(con).list(
            application_id=application_id, employer_id=employer_id, q=q
        )

    @app.post("/api/contacts")
    def add_contact(body: ContactIn, con=Depends(get_con)):
        try:
            return ContactsService(con).add(
                body.name, application_id=body.application_id, employer_id=body.employer_id,
                role=body.role, phone=body.phone, email=body.email,
                wechat=body.wechat, note=body.note,
            )
        except ValueError as e:
            raise _err(e) from e

    @app.get("/api/contacts/{contact_id}")
    def get_contact(contact_id: int, con=Depends(get_con)):
        c = ContactsService(con).get(contact_id)
        if c is None:
            raise HTTPException(404, f"联系人不存在: {contact_id}")
        return c

    @app.patch("/api/contacts/{contact_id}")
    def patch_contact(contact_id: int, body: ContactPatchIn, con=Depends(get_con)):
        try:
            return ContactsService(con).update(
                contact_id, name=body.name, role=body.role, phone=body.phone,
                email=body.email, wechat=body.wechat, note=body.note,
            )
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/contacts/{contact_id}")
    def delete_contact(contact_id: int, con=Depends(get_con)):
        try:
            ContactsService(con).delete(contact_id)
            return {"ok": True}
        except ValueError as e:
            raise _err(e) from e

    # ---------- 提醒与跟进建议 ----------

    @app.get("/api/reminders")
    def list_reminders(
        include_done: bool = False, done: bool = False,
        owner_kind: str | None = None, owner_id: str | None = None,
        con=Depends(get_con),
    ):
        return RemindersService(con).list(
            done=done, include_done=include_done, owner_kind=owner_kind, owner_id=owner_id
        )

    @app.get("/api/reminders/suggestions")
    def reminder_suggestions(profile_id: str | None = None, con=Depends(get_con)):
        """确定性规则推导的跟进建议（不落库，采纳后 POST 成为真实提醒）。"""
        return RemindersService(con).suggestions(profile_id=profile_id)

    @app.post("/api/reminders")
    def add_reminder(body: ReminderIn, con=Depends(get_con)):
        try:
            return RemindersService(con).create(
                body.owner_kind, body.owner_id, body.due_at, body.title, kind=body.kind
            )
        except ValueError as e:
            raise _err(e) from e

    @app.patch("/api/reminders/{reminder_id}")
    def patch_reminder(reminder_id: int, body: ReminderDoneIn, con=Depends(get_con)):
        try:
            return RemindersService(con).set_done(reminder_id, body.done)
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/reminders/{reminder_id}")
    def delete_reminder(reminder_id: int, con=Depends(get_con)):
        try:
            RemindersService(con).delete(reminder_id)
            return {"ok": True}
        except ValueError as e:
            raise _err(e) from e

    # ---------- 投递标签（0004） ----------

    @app.post("/api/applications/{app_id}/tags/{tag}")
    def add_app_tag(app_id: str, tag: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).add_tag(app_id, tag)
        except ValueError as e:
            raise _err(e) from e

    @app.delete("/api/applications/{app_id}/tags/{tag}")
    def remove_app_tag(app_id: str, tag: str, con=Depends(get_con)):
        try:
            return ApplicationService(con).remove_tag(app_id, tag)
        except ValueError as e:
            raise _err(e) from e
