"""Langfuse 可观测性（服务端 v4，Python SDK 4.x）。

版本对齐（2026-09-28）：
实际部署的 Langfuse 是 v4（langfuse_local: docker.langfuse.com/langfuse/langfuse:4），
因此 Python SDK 必须用 4.x —— Langfuse v4 服务端不兼容 2.x SDK。

API 变更（v2 -> v4）：
- `from langfuse.decorators import observe` 已移除，改为顶层 `from langfuse import observe`
- SDK 4.x 基于 OpenTelemetry，依赖仅 httpx / pydantic v2 / otel，不依赖 langchain，
  因此不会破坏本项目锁定的「langgraph 0.2.x + langchain-core 0.3.x」矩阵。

用法：
    from app.ai.langfuse_client import observe
    @observe()
    def my_llm_step(...): ...
未配置密钥时 observe 退化为透明空装饰器，业务代码无需改动。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@lru_cache
def get_langfuse() -> Any | None:
    """惰性创建客户端。未配置密钥时返回 None —— 埋点自动降级为空操作。"""
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        logger.info("Langfuse 未配置密钥，埋点已跳过")
        return None

    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
    )


def _noop_observe(*_args: Any, **_kwargs: Any):
    """透明空装饰器，支持 @observe() 与 @observe 两种写法。"""

    def _wrap(func):
        return func

    if len(_args) == 1 and callable(_args[0]) and not _kwargs:
        return _args[0]
    return _wrap


def get_observe():
    """返回 @observe 装饰器（未配置密钥时返回透明的空装饰器）。"""
    if get_langfuse() is None:
        return _noop_observe

    from langfuse import observe

    return observe


# 便于业务代码直接 `from app.ai.langfuse_client import observe`
observe = get_observe()


def flush_langfuse() -> None:
    """进程退出前冲刷，避免 trace 丢失。"""
    client = get_langfuse()
    if client is not None:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse flush 失败：%s", exc)
