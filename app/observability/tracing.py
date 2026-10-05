"""Langfuse 埋点辅助层。

埋点位置怎么定的（参考 Langfuse 官方 instrumentation 基线 + 业界实践，2026-10-05）：

**基线（每个 trace 都必须有）**
- 模型名、输入、输出
- token 用量（input / output）—— 有了它 Langfuse 才能自动算成本
- 有意义的 trace 名（`jd.parse` 而不是 `trace-1`）
- LLM 调用标成 `generation` 而不是普通 span —— 否则模型维度的分析全用不了
- 多步流程用嵌套 span 表达父子关系 —— 否则只知道慢，不知道哪一步慢

**本项目额外加的上下文**
- `user_id`：谁触发的（HR / 哪个面试官），用于成本归因与「谁踩到了坑」
- `session_id`：一场面试一个会话，用来把 Step1~Step5 的产物串成一条时间线
- `tags=[feature, env]`：按能力维度看调用量与成本
- `metadata`：职位 / 候选人 / 会话 id，出问题能直接定位到单子
- `score`：人工是否采纳了 AI 建议（BR：面试官对 AI 结果负责），用于质量回归

**敏感数据**：简历原文、候选人姓名手机号都**不进 Langfuse** —— 只进长度与结构化字段名。
合规上「可观测」不能变成「多存一份个人信息」。
"""
from __future__ import annotations

import functools
import inspect
import logging
from contextvars import ContextVar

from app.ai.langfuse_client import get_langfuse, observe  # noqa: F401 —— 对外统一出口

logger = logging.getLogger(__name__)

# 当前业务能力名（如 `jd.parse`）。由各服务入口设置，LLM 层读它给 generation 命名，
# 这样即使所有模型调用都走同一个 structured_call，也能在 Langfuse 里按能力分开看。
FEATURE: ContextVar[str] = ContextVar("prepilot_feature", default="unknown")
# 一次请求内共享的上下文（供日志与 trace 对齐）
REQUEST_ID: ContextVar[str] = ContextVar("prepilot_request_id", default="")
USER_ID: ContextVar[str] = ContextVar("prepilot_user_id", default="")
SESSION_ID: ContextVar[str] = ContextVar("prepilot_session_id", default="")


def ai_step(name: str, *, as_type: str = "span"):
    """业务步骤埋点装饰器：`@ai_step("jd.parse")`。

    同时做两件事：
    1. 建一个 Langfuse span，名字就是业务能力名（trace 树里一眼看出是哪一步慢/错）；
    2. 把能力名写进 `FEATURE` contextvar —— LLM 层读它给 generation 命名，
       于是「问题链生成烧了多少 token」这种问题才答得出来。

    未配置 Langfuse 时 `observe` 是透明空装饰器，只留下 contextvar 的效果。
    """

    def _decorate(func):
        observed = observe(name=name, as_type=as_type)(func)
        token_holder = (name,)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def _async_wrapper(*args, **kwargs):
                token = FEATURE.set(token_holder[0])
                try:
                    return await observed(*args, **kwargs)
                finally:
                    FEATURE.reset(token)

            return _async_wrapper

        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs):
            token = FEATURE.set(token_holder[0])
            try:
                return observed(*args, **kwargs)
            finally:
                FEATURE.reset(token)

        return _sync_wrapper

    return _decorate


def _has_active_span() -> bool:
    """当前是否真有 span 在活动。

    **为什么需要它**：Langfuse SDK 在没有活动 span 时不是抛异常，而是自己打一条
    WARNING（"No active span in current context"）。`update_current_span` 之类调用
    在定时任务、脚本、以及 HTTP 请求收尾（span 已关闭）之后都会撞上它，
    于是一条假告警刷满 Loki，把真正的 WARNING 淹掉。
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        return span is not None and span.get_span_context().is_valid
    except Exception:  # noqa: BLE001
        return False


def enabled() -> bool:
    """Langfuse 是否已配置。未配置时所有埋点自动跳过。"""
    return get_langfuse() is not None


def current_trace_id() -> str:
    """取当前 trace id（写进 JSON 日志，Grafana 里可跳回 Langfuse）。"""
    client = get_langfuse()
    if client is None or not _has_active_span():
        return ""
    try:
        return client.get_current_trace_id() or ""
    except Exception:  # noqa: BLE001
        return ""


def add_metadata(**fields) -> None:
    """给当前 span 追加元数据。失败静默 —— 埋点不是业务。"""
    client = get_langfuse()
    if client is None or not _has_active_span():
        return
    try:
        client.update_current_span(metadata=fields)
    except Exception:  # noqa: BLE001
        pass


def mark_degraded(reason: str, **fields) -> None:
    """标记一次「降级 / 兜底」—— 这类事件必须能被查出来，否则等于没记。"""
    client = get_langfuse()
    if client is None or not _has_active_span():
        return
    try:
        client.update_current_span(
            level="WARNING",
            status_message=reason,
            metadata={"degraded": True, "reason": reason, **fields},
        )
    except Exception:  # noqa: BLE001
        pass


def score_trace(name: str, value: float, comment: str = "") -> None:
    """给当前 trace 打质量分（例如「面试官采纳了 AI 建议」= 1，否决 = 0）。"""
    client = get_langfuse()
    if client is None or not _has_active_span():
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment)
    except Exception:  # noqa: BLE001
        pass


def flush() -> None:
    """进程退出前冲刷，避免 trace 丢在缓冲区里。"""
    from app.ai.langfuse_client import flush_langfuse

    flush_langfuse()
