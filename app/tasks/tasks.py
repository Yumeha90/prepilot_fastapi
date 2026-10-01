"""任务定义。

示例任务 add 由原 main.py 迁移而来，用于验证 Celery + RabbitMQ 链路是否打通：
    celery -A app.tasks.celery_app worker -l info
"""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="prepilot.add")
def add(x: int, y: int) -> int:
    return x + y


@celery_app.task(name="prepilot.match.explain", bind=True, max_retries=1)
def explain_match(self, match_score_id: int) -> bool:
    """异步补写匹配分的 AI 总结（BR-21）。

    分数与构成**早在同步阶段就落库了**，这里只补解释：失败不重试第二次，
    只把 `summary_status` 置 failed，等 HR 下次点「重新计算」再来。
    """
    import asyncio

    from app.core.database import SessionLocal
    from app.services import match as match_svc

    async def _run() -> bool:
        async with SessionLocal() as db:
            return await match_svc.generate_summary(db, match_score_id)

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        try:
            raise self.retry(exc=exc, countdown=20)
        except self.MaxRetriesExceededError:  # type: ignore[attr-defined]
            return False


@celery_app.task(name="prepilot.lifecycle.purge_expired", bind=True, max_retries=1)
def purge_expired_resumes(self) -> dict:
    """定时粉碎到期简历（BR-10 / §7.7：后台每小时扫描一次）。

    由 celery beat 每小时投递（schedule 见 celery_app.conf.beat_schedule）。
    策略停用时照样执行、只是不删人，并更新 `last_run_at`，
    这样页面上能区分「定时任务没跑」与「跑了但策略停用了」。
    """
    import asyncio

    from app.core.database import SessionLocal
    from app.services import lifecycle as lifecycle_svc

    async def _run() -> dict:
        async with SessionLocal() as db:
            result = await lifecycle_svc.run_auto_purge(db)
            return {
                "enabled": result.enabled,
                "days": result.days,
                "scanned": result.scanned,
                "purged": result.purged,
            }

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=60)


def _err_message(exc: BaseException) -> str:
    """取出带 code 的错误体里的中文 message（页面要显示给人看）。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or exc)
    return str(exc)


@celery_app.task(name="prepilot.workbench.generate_chain", bind=True, max_retries=0)
def generate_question_chain(self, session_id: int, user_id: int) -> dict:
    """异步生成 Step2 问题链（PRD §6.4，BR-12 异步 + 轮询）。

    不重试：一次生成 = 3~6 次向量检索 + 1~2 次模型调用，自动重试会把失败放大成
    几十秒的静默等待。失败直接把 `chain_status` 置 failed 并写上原因，
    面试官点一下「重新生成」即可 —— 让他决定要不要再来一次。
    """
    import asyncio

    from app.core.database import SessionLocal
    from app.models.session import InterviewSession
    from app.models.user import User
    from app.services import question_chain as qc

    async def _run() -> dict:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, session_id)
            if session is None:
                return {"ok": False, "error": "会话不存在"}
            user = await db.get(User, user_id)
            if user is None:
                await qc.mark_failed(db, session, "操作人不存在")
                return {"ok": False, "error": "操作人不存在"}
            try:
                chain = await qc.generate_chain(db, user, session)
            except Exception as exc:  # noqa: BLE001
                await qc.mark_failed(db, session, _err_message(exc))
                return {"ok": False, "error": _err_message(exc)}
            return {"ok": True, "nodes": len(chain.nodes)}

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 —— 兜底：连库都失败时也要留下原因
        return {"ok": False, "error": _err_message(exc)}


@celery_app.task(name="prepilot.mail.send", bind=True, max_retries=2)
def send_mail(self, to: str, subject: str, body: str) -> None:
    """异步发信（忘记密码验证码）。失败重试 2 次，仍失败则记录日志。"""
    from app.services.email import get_email_sender

    try:
        get_email_sender().send(to, subject, body)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=10)
