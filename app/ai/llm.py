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
"""
from __future__ import annotations

import logging
from typing import TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.ai.langfuse_client import observe
from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request

logger = logging.getLogger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)

# 未配置 key 时给占位值，避免 ChatOpenAI 在**构造阶段**就抛错；
# 真正调用时会拿到 401，由 structured_call 转成 LLM_FAILED。
_PLACEHOLDER_KEY = "sk-not-configured"


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


@observe()
@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _invoke_structured(schema: type[T], system: str, user: str, **kwargs) -> T:
    model = get_chat_model(**kwargs)
    return model.with_structured_output(schema, method="json_schema").invoke(
        [("system", system), ("user", user)]
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
        return _invoke_structured(
            schema, system, user, temperature=temperature, timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 —— 统一转成中文错误，不降级
        logger.warning("LLM 调用失败：%s: %s", type(exc).__name__, exc)
        raise bad_request(ErrorCode.LLM_FAILED, f"AI 服务调用失败：{exc}") from exc
