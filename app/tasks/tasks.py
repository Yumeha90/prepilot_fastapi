"""任务定义。

示例任务 add 由原 main.py 迁移而来，用于验证 Celery + RabbitMQ 链路是否打通：
    celery -A app.tasks.celery_app worker -l info
"""
from __future__ import annotations

import functools
import logging
import time

from app.observability import metrics, tracing
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _root_span(name: str):
    """给任务开一个 Langfuse 根 span。

    **为什么必须开**：异步任务没有 HTTP 请求入口，不自建根 span 的话，
    任务内部那些 `@ai_step` 装饰的业务步骤会各自成为孤儿 trace，
    而且 `add_metadata` 之类调用会报「No active span」，日志里全是噪音。
    开了之后，Celery 里跑的 AI 链路（问题链生成等）在 Langfuse 里和 HTTP 请求一样是一棵完整的树。
    """
    from contextlib import nullcontext

    try:
        from app.ai.langfuse_client import get_langfuse
        from app.core.config import get_settings

        client = get_langfuse()
        if client is None:
            return nullcontext()
        from langfuse import propagate_attributes

        return _Stack(
            client.start_as_current_observation(
                as_type="span", name=f"celery:{name}", input={"task": name}
            ),
            propagate_attributes(tags=[f"task:{name}", f"env:{get_settings().ENV}"]),
        )
    except Exception:  # noqa: BLE001
        return nullcontext()


class _Stack:
    """两个上下文管理器叠一起（ExitStack 在这里没必要引入额外依赖）。"""

    def __init__(self, *cms):
        self._cms = cms

    def __enter__(self):
        for cm in self._cms:
            cm.__enter__()
        return self

    def __exit__(self, *exc):
        ok = False
        for cm in reversed(self._cms):
            ok = cm.__exit__(*exc) or ok
        return ok


def _instrument(name: str):
    """Celery 任务埋点：耗时、成败，以及给内部 AI 步骤一个Feature 上下文。

    **必须放在 `@celery_app.task` 下面（先执行）**：Celery 的装饰器会把函数换成 Task
    对象，再包一层就取不到签名了。

    任务末尾显式 flush：worker 是长驻进程，Langfuse SDK 虽然会定时批量发送，
    但任务间隔可能很长，不 flush 的话最后几条 trace 要等很久才出现在界面上。
    """

    def _decorate(func):
        @functools.wraps(func)
        def _wrapper(*args, **kwargs):
            start = time.perf_counter()
            status = "ok"
            token = tracing.FEATURE.set(name)
            try:
                with _root_span(name):
                    return func(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                metrics.CELERY_TASKS.labels(task=name, status=status).inc()
                metrics.CELERY_LATENCY.labels(task=name).observe(
                    time.perf_counter() - start
                )
                tracing.FEATURE.reset(token)
                try:
                    tracing.flush()
                except Exception:  # noqa: BLE001
                    pass

        return _wrapper

    return _decorate


def _run_async(coro_fn):
    """在独立事件循环里跑协程，跑完**必须释放连接池**。

    Celery prefork worker 复用进程，而 `app.core.database.engine` 是模块级单例：
    连接一旦建立就绑定到创建它的那个 loop。`asyncio.run` 每次新建 loop，于是
    下一个任务拿到的是上个（已关闭）loop 的连接 —— 表现为
    `got Future attached to a different loop`。实测定时粉碎每小时都先失败一次、
    60s 重试才成功（2026-10-03 排障）。

    所以在同一个 loop 里 dispose：下次任务就会在新 loop 里重新建连接。
    """

    import asyncio

    from app.core.database import engine

    async def _wrapped():
        try:
            return await coro_fn()
        finally:
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001 —— 释放失败不影响任务结果
                logger.warning("释放数据库连接池失败", exc_info=True)

    return asyncio.run(_wrapped())


@celery_app.task(name="prepilot.add")
@_instrument("add")
def add(
    x: int, y: int) -> int:
    return x + y


@celery_app.task(name="prepilot.match.explain", bind=True, max_retries=1)
@_instrument("match.explain")
def explain_match(
    self, match_score_id: int) -> bool:
    """异步补写匹配分的 AI 总结（BR-21）。

    分数与构成**早在同步阶段就落库了**，这里只补解释：失败不重试第二次，
    只把 `summary_status` 置 failed，等 HR 下次点「重新计算」再来。
    """
    from app.core.database import SessionLocal
    from app.services import match as match_svc

    async def _run() -> bool:
        async with SessionLocal() as db:
            return await match_svc.generate_summary(db, match_score_id)

    try:
        return _run_async(_run)
    except Exception as exc:  # noqa: BLE001
        try:
            raise self.retry(exc=exc, countdown=20)
        except self.MaxRetriesExceededError:  # type: ignore[attr-defined]
            return False


@celery_app.task(name="prepilot.lifecycle.purge_expired", bind=True, max_retries=1)
@_instrument("lifecycle.purge_expired")
def purge_expired_resumes(
    self) -> dict:
    """定时粉碎到期简历（BR-10 / §7.7：后台每小时扫描一次）。

    由 celery beat 每小时投递（schedule 见 celery_app.conf.beat_schedule）。
    策略停用时照样执行、只是不删人，并更新 `last_run_at`，
    这样页面上能区分「定时任务没跑」与「跑了但策略停用了」。
    """
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
        return _run_async(_run)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=60)


def _err_message(exc: BaseException) -> str:
    """取出带 code 的错误体里的中文 message（页面要显示给人看）。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or exc)
    return str(exc)


@celery_app.task(name="prepilot.workbench.generate_chain", bind=True, max_retries=0)
@_instrument("chain.generate")
def generate_question_chain(
    self, session_id: int, user_id: int) -> dict:
    """异步生成 Step2 问题链（PRD §6.4，BR-12 异步 + 轮询）。

    不重试：一次生成 = 3~6 次向量检索 + 1~2 次模型调用，自动重试会把失败放大成
    几十秒的静默等待。失败直接把 `chain_status` 置 failed 并写上原因，
    面试官点一下「重新生成」即可 —— 让他决定要不要再来一次。
    """
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
            # 这里**不做公平性预扫描**（2026-10-04 删）。
            #
            # 曾经在生成后顺手扫一次，理由是"省掉面试官点一下"。代价是它把
            # `_persist_chain` 刚刚作废掉的结论又写了回去：题目一变，进度条的 Step3
            # 立刻打勾，面试官以为检查过了 —— 其实是系统替他对着新题目盖了个章。
            # 合规检查的意义在于**有人为这次的题目负责**，所以结论只能由面试官在
            # Step3 点「开始扫描」产生（BR-25 / BR-26）。
            return {"ok": True, "nodes": len(chain.nodes)}

    async def _mark_failed(message: str) -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, session_id)
            if session is not None:
                await qc.mark_failed(db, session, message)

    try:
        return _run_async(_run)
    except Exception as exc:  # noqa: BLE001 —— 兜底：连库都失败时也要留下原因
        # 走到这里说明连协程都没跑起来（如连接池/DB 不可用）。
        # 必须把会话标成 failed，否则前端会一直轮询一个永远不出结果的 pending。
        msg = _err_message(exc)
        try:
            _run_async(lambda: _mark_failed(msg))
        except Exception:  # noqa: BLE001
            logger.warning("标记问题链失败状态失败：session_id=%s", session_id, exc_info=True)
        return {"ok": False, "error": msg}


@celery_app.task(name="prepilot.mail.send", bind=True, max_retries=2)
@_instrument("mail.send")
def send_mail(
    self, to: str, subject: str, body: str) -> None:
    """异步发信（忘记密码验证码）。失败重试 2 次，仍失败则记录日志。"""
    from app.services.email import get_email_sender

    try:
        get_email_sender().send(to, subject, body)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=10)
