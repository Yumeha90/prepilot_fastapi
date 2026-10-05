"""LLM 统一入口（阿里云百炼 DashScope，兼容 OpenAI 协议）。

2026-09-30 实测结论（模型 `qwen3.8-flash`）：
- `with_structured_output(method="function_calling")` **不可用**：qwen3 默认开启
  thinking，`tool_choice=required` 会被服务端拒绝（400）。
- `method="json_schema"` 可用。
- 额外传 `extra_body={"enable_thinking": False}`：10.1s → **3.1s**，
  且输出更保守、更贴合 C1「只拆不扩」的要求。
  ⚠️ 必须是 `extra_body`，用 `model_kwargs` 会报
  `Completions.parse() got an unexpected keyword argument`。

分档使用，不为用而用：
- L0（本文件）单次结构化调用 —— C1 JD 拆解
- L2（LangGraph 状态图）有循环 / 条件 / 中断 —— C4 问题链、C5 公平性扫描

后续升到 L2 时**只替换本文件内部实现**，调用方不动。

失败策略：与「Milvus 不可用直接失败不降级」同一口径 —— LLM 失败直接抛错，
不做"假装成功"的兜底。

可观测性（2026-10-05）：
每次调用都向 Langfuse 报一条 `generation`（模型 / 提示词 / 输出 / token / 耗时），
并向 Prometheus 报调用次数、耗时、token 与失败数。
**generation 而不是普通 span** 是硬性要求 —— 只有 generation 才会进入 Langfuse 的
模型维度分析与自动成本计算。
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager, nullcontext
from typing import TypeVar

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ai.langfuse_client import get_langfuse
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request
from app.observability import metrics, tracing

logger = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)

# 未配置 key 时给占位值，避免 ChatOpenAI 在**构造阶段**就抛错；
# 真正调用时会拿到 401，由 structured_call 转成 LLM_FAILED。
_PLACEHOLDER_KEY = "sk-not-configured"

# 进 Langfuse 的提示词 / 输出截断长度：过长既撑爆存储也不利于阅读，
# 完整内容仍在应用日志与数据库里，可观测性只放「够定位问题」的量。
TRACE_INPUT_CHARS = 4000
TRACE_OUTPUT_CHARS = 4000


class _UsageCapture(BaseCallbackHandler):
    """从 langchain 的 LLMResult 里抠 token 用量。

    **为什么需要它**：`with_structured_output` 只把解析后的 Pydantic 对象返回出来，
    usage 信息在链路上被丢掉了。没有 token 数，Langfuse 就算不出成本，
    Grafana 上也看不到「这个能力烧了多少 token」。
    重试时多次调用会累加 —— 重试真的烧了钱，就该记进去。
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.calls = 0

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: D102
        try:
            self.calls += 1
            for generation in response.generations:
                for candidate in generation:
                    message = getattr(candidate, "message", None)
                    if message is None:
                        continue
                    meta = getattr(message, "usage_metadata", None) or {}
                    raw = getattr(message, "response_metadata", None) or {}
                    token_usage = raw.get("token_usage") or {}
                    self.input_tokens += int(
                        meta.get("input_tokens") or token_usage.get("prompt_tokens") or 0
                    )
                    self.output_tokens += int(
                        meta.get("output_tokens")
                        or token_usage.get("completion_tokens")
                        or 0
                    )
                    self.total_tokens += int(
                        meta.get("total_tokens") or token_usage.get("total_tokens") or 0
                    )
        except Exception:  # noqa: BLE001 —— 取不到用量不影响调用本身
            logger.debug("读取 token 用量失败", exc_info=True)

    def as_dict(self) -> dict[str, int]:
        total = self.total_tokens or (self.input_tokens + self.output_tokens)
        return {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "total": total,
        }


def get_chat_model(
    *,
    temperature: float | None = None,
    timeout: int | None = None,
    thinking: bool = False,
) -> ChatOpenAI:
    """构造聊天模型。默认关闭 thinking（实测快 3 倍且输出更干净）。"""
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.OPENAI_API_KEY or _PLACEHOLDER_KEY,
        base_url=settings.OPENAI_BASE_URL or None,
        temperature=settings.LLM_TEMPERATURE if temperature is None else temperature,
        timeout=timeout or settings.LLM_TIMEOUT,
        # 重试交给 tenacity 统一控制，避免两层重试放大耗时
        max_retries=0,
        extra_body={"enable_thinking": thinking},
    )


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _invoke_structured(
    schema: type[T],
    system: str,
    user: str,
    *,
    callbacks: list[BaseCallbackHandler] | None = None,
    **kwargs,
) -> T:
    model = get_chat_model(**kwargs)
    # callbacks 必须在这里挂上去：用量只在 langchain 的回调里能拿到，
    # 而 model 是每次重试新建的，所以只能走 invoke 的 config 传，不能挂在 get_chat_model 上。
    config = {"callbacks": list(callbacks or [])}
    return model.with_structured_output(schema, method="json_schema").invoke(
        [("system", system), ("user", user)], config=config
    )


@contextmanager
def _generation(system: str, user: str, temperature: float | None, timeout: int | None):
    """包住一次 LLM 调用：Langfuse generation + Prometheus 指标。

    Langfuse 未配置时整块退化为「只记指标」，业务行为完全不变。
    """
    feature = tracing.FEATURE.get()
    model_name = settings.LLM_MODEL
    langfuse = get_langfuse()
    capture = _UsageCapture()
    start = time.perf_counter()

    if langfuse is None:
        ctx = nullcontext()
    else:
        ctx = langfuse.start_as_current_observation(
            as_type="generation",
            name=f"llm:{feature}",
            model=model_name,
            model_parameters={
                "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
                "timeout": timeout or settings.LLM_TIMEOUT,
                "thinking": False,
                "response_format": "json_schema",
            },
            input={"system": system[:TRACE_INPUT_CHARS], "user": user[:TRACE_INPUT_CHARS]},
            metadata={"feature": feature, "schema_len": len(system) + len(user)},
        )

    status = "ok"
    generation = None
    try:
        with ctx as generation:
            # 产出 (capture, generation)：调用方要在**块内**写 output，
            # 一旦出了这个 with，generation 就结束，再 update 就静默丢失。
            yield capture, generation
            elapsed = time.perf_counter() - start
            if generation is not None:
                try:
                    generation.update(
                        usage_details=capture.as_dict(),
                        metadata={
                            "attempts": capture.calls,
                            "duration_s": round(elapsed, 2),
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("更新 Langfuse generation 失败", exc_info=True)
    except Exception as exc:
        status = "error"
        if generation is not None:
            try:
                generation.update(
                    level="ERROR",
                    status_message=f"{type(exc).__name__}: {exc}"[:500],
                )
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        elapsed = time.perf_counter() - start
        usage = capture.as_dict()
        metrics.LLM_CALLS.labels(feature=feature, model=model_name, status=status).inc()
        metrics.LLM_LATENCY.labels(feature=feature, model=model_name).observe(elapsed)
        for direction in ("input", "output", "total"):
            value = usage[direction]
            if value:
                metrics.LLM_TOKENS.labels(
                    feature=feature, model=model_name, direction=direction
                ).inc(value)
        if status == "error":
            metrics.AI_DEGRADED.labels(feature=feature, reason="llm_failed").inc()
        logger.info(
            "LLM 调用完成 feature=%s status=%s %.1fs tokens=%s",
            feature,
            status,
            elapsed,
            usage,
            extra={
                "llm_feature": feature,
                "llm_model": model_name,
                "llm_status": status,
                "llm_duration_s": round(elapsed, 2),
                "llm_tokens": usage,
            },
        )


def structured_call(
    schema: type[T],
    system: str,
    user: str,
    *,
    temperature: float | None = None,
    timeout: int | None = None,
) -> T:
    """单次结构化调用（L0）。返回已通过 Pydantic 校验的 schema 实例。"""
    if not settings.OPENAI_API_KEY:
        raise bad_request(ErrorCode.LLM_FAILED, "未配置大模型密钥，无法调用 AI 能力")
    try:
        with _generation(system, user, temperature, timeout) as (capture, generation):
            result = _invoke_structured(
                schema,
                system,
                user,
                temperature=temperature,
                timeout=timeout,
                callbacks=[capture],
            )
            _report_output(result, generation)
        return result
    except Exception as exc:  # noqa: BLE001 —— 统一转成中文错误，不降级
        logger.warning("LLM 调用失败：%s: %s", type(exc).__name__, exc)
        raise bad_request(ErrorCode.LLM_FAILED, f"AI 服务调用失败：{exc}") from exc


def _report_output(result, generation=None) -> None:
    """把结构化输出回写到当前 generation（Langfuse 的 output 字段）。

    **必须在 generation 上下文内调用** —— 出了上下文再 update 不会报错，但也不会写进去，
    Langfuse 上就只看到输入看不到输出，排查时最难受。
    """
    if generation is not None:
        try:
            payload = result.model_dump() if isinstance(result, BaseModel) else result
            generation.update(output=str(payload)[:TRACE_OUTPUT_CHARS])
            return
        except Exception:  # noqa: BLE001
            logger.debug("回写 Langfuse generation 输出失败", exc_info=True)
    # 兜底：拿不到 generation 对象就退回「更新当前 generation」
    langfuse = get_langfuse()
    if langfuse is None:
        return
    try:
        payload = result.model_dump() if isinstance(result, BaseModel) else result
        langfuse.update_current_generation(output=str(payload)[:TRACE_OUTPUT_CHARS])
    except Exception:  # noqa: BLE001
        logger.debug("回写 Langfuse generation 输出失败", exc_info=True)
