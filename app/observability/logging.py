"""结构化日志（给 Loki 用）。

为什么改成 JSON 行：
纯文本日志进 Loki 只能整行当消息，没法按字段过滤。改成一行一个 JSON 后，Alloy 在
pipeline 里把 `level` / `logger` / `trace_id` / `feature` 提成标签，Grafana 里就能
「只看 ERROR」「只看某个 trace 的日志」，而不用靠正则去捞。

**`trace_id` 是这条链的关键**：日志里带着它，就能从 Grafana 的一行报错直接跳到
Langfuse 里那次完整的 AI 调用（提示词、输出、token、耗时全在里面）。

兼容性：未配置 Langfuse 时 `trace_id` 为空串，日志结构不变，不会打乱既有日志分析。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from logging import LogRecord

from app.core.config import get_settings

# 由中间件 / 服务入口写入，这里只读
REQUEST_ID: ContextVar[str] | None = None  # 延迟绑定，避免循环导入
USER_ID: ContextVar[str] | None = None
FEATURE: ContextVar[str] | None = None

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}

# 超长文本（简历原文 / 模型输出）只留长度，避免 Loki 单条 1MB 上限与存储浪费
MAX_MESSAGE_CHARS = 4000


class JsonFormatter(logging.Formatter):
    """一行一个 JSON。异常栈原样保留在 `exc` 字段（多行会破坏 JSON，故转义成字符串）。"""

    def format(self, record: LogRecord) -> str:
        payload: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": self._safe(record.getMessage())[:MAX_MESSAGE_CHARS],
        }
        # 业务代码用 logger.info("...", extra={"candidate_id": 1}) 带的字段
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = self._safe(value)

        payload["env"] = get_settings().ENV
        if REQUEST_ID is not None:
            rid = REQUEST_ID.get()
            if rid:
                payload["request_id"] = rid
        if USER_ID is not None:
            uid = USER_ID.get()
            if uid:
                payload["user_id"] = uid
        if FEATURE is not None:
            feat = FEATURE.get()
            if feat and feat != "unknown":
                payload["feature"] = feat
        tid = _trace_id()
        if tid:
            payload["trace_id"] = tid

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _safe(value) -> object:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [JsonFormatter._safe(v) for v in value[:50]]
        if isinstance(value, dict):
            return {str(k): JsonFormatter._safe(v) for k, v in list(value.items())[:50]}
        return str(value)


def _trace_id() -> str:
    try:
        from app.observability.tracing import current_trace_id

        return current_trace_id()
    except Exception:  # noqa: BLE001
        return ""


def configure(level: int = logging.INFO) -> None:
    """把 root logger 换成 JSON 输出到 stdout（容器日志 → Alloy → Loki）。

    只在没有 JSON handler 时挂载，避免 uvicorn --reload 重复叠加导致日志翻倍。
    """
    from app.observability import tracing

    global REQUEST_ID, USER_ID, FEATURE
    REQUEST_ID = tracing.REQUEST_ID
    USER_ID = tracing.USER_ID
    FEATURE = tracing.FEATURE

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and isinstance(
            handler.formatter, JsonFormatter
        ):
            handler.setLevel(level)
            root.setLevel(level)
            return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn / sqlalchemy 等第三方 logger 默认 propagate=True，会一起走 root handler。
    # 但 uvicorn 自己挂了 handler 且 propagate 可能 False，这里只保证应用日志是 JSON。
