"""HTTP 层埋点中间件（trace 根 + 指标 + 结构化访问日志）。

**为什么写成纯 ASGI 中间件而不是 BaseHTTPMiddleware**：
Langfuse SDK v4 基于 OpenTelemetry，靠 contextvar 传递父子关系。
`BaseHTTPMiddleware` 会把下游处理放到独立的 anyio task 里跑，虽然 contextvar 会被复制
进去，但它自身的异常处理会把链路切断，trace 层级容易断成孤儿。纯 ASGI 中间件在同一个
task 里 `await self.app(...)`，父子关系天然正确。

**为什么根 span 建在这里**：
这是唯一能同时拿到「谁（user_id）+ 调了什么（route）+ 结果如何（status）」的地方。
有了根 span，服务里那些 `@observe` 装饰的 AI 步骤才会正确地挂在它下面，
形成 `HTTP → 业务步骤 → 检索 → 模型调用` 的完整树。
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.observability import metrics, tracing

logger = logging.getLogger("prepilot.access")

# 自身与探活不打点：
# - /api/metrics 自采会产生自指噪声，且 Alloy 每 15s 抓一次
# - /api/health 是容器健康检查，几秒一次，全建 trace 会把真正的 AI 链路淹掉
# 注意应用整体挂在 /api 前缀下，排除表必须写全路径，只写 /metrics 是匹配不到的。
SKIP_ROUTES = {"/api/metrics", "/metrics", "/api/health", "/health"}
SKIP_PREFIXES = ("/static", "/assets")

# 路由 → 业务能力名（tags 用它做分组，比 route 更稳：route 改了 tag 不变）
FEATURE_MAP: dict[str, str] = {
    "/api/jd": "jd",
    "/api/resume": "resume",
    "/api/candidates": "candidate",
    "/api/positions": "position",
    "/api/sessions": "session",
    "/api/board": "board",
    "/api/match": "match",
    "/api/workbench": "workbench",
    "/api/lifecycle": "lifecycle",
    "/api/evaluations": "evaluation",
    "/api/auth": "auth",
}


def _feature_of(route: str) -> str:
    for prefix, name in FEATURE_MAP.items():
        if route == prefix or route.startswith(prefix + "/"):
            return name
    return "other"


def _token_user_id(scope: dict) -> str:
    """从 Authorization 头解出 user id。

    只为埋点服务：解不出来就留空，**绝不因此拒绝请求**（鉴权是依赖层的事）。
    """
    headers = scope.get("headers") or []
    raw = None
    for key, value in headers:
        if key.lower() == b"authorization":
            raw = value.decode("latin-1", errors="ignore")
            break
    if not raw or not raw.lower().startswith("bearer "):
        return ""
    try:
        from app.core.security import decode_access_token

        subject = decode_access_token(raw.split(" ", 1)[1].strip())
        return str(subject) if subject else ""
    except Exception:  # noqa: BLE001 —— 埋点不应影响请求
        return ""


class ObservabilityMiddleware:
    """一次请求 = 一个 Langfuse trace（HTTP 根 span）+ 一组 Prometheus 指标 + 一行 JSON 日志。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.settings = get_settings()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        route = metrics._route_of(scope)
        if route in SKIP_ROUTES or path.startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "?")
        feature = _feature_of(route)
        request_id = self._request_id(scope)
        user_id = _token_user_id(scope)

        token_rid = tracing.REQUEST_ID.set(request_id)
        token_uid = tracing.USER_ID.set(user_id)
        token_feat = tracing.FEATURE.set(feature)

        state = {"status": 500}
        start = time.perf_counter()
        metrics.HTTP_IN_FLIGHT.inc()
        langfuse = tracing_enabled()
        # ⚠️ trace_id 必须在 span **还活着**的时候取：span 一关，
        # `get_current_trace_id()` 就返回空并让 SDK 打一条 "No active span" 警告。
        # 而访问日志是在 finally 里写的（那时 span 早关了），只能先把值存下来。
        trace_id = ""

        async def _send(message):
            if message.get("type") == "http.response.start":
                state["status"] = int(message.get("status") or 500)
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            if langfuse is None:
                await self.app(scope, receive, _send)
            else:
                with langfuse.start_as_current_observation(
                    as_type="span",
                    name=f"{method} {route}",
                    input={"method": method, "route": route, "query": _query(scope)},
                ):
                    with _propagate(
                        user_id=user_id,
                        feature=feature,
                        env=self.settings.ENV,
                        request_id=request_id,
                    ):
                        trace_id = tracing.current_trace_id()
                        await self.app(scope, receive, _send)
        except Exception:
            state["status"] = 500
            raise
        finally:
            cost = time.perf_counter() - start
            status = state["status"]
            metrics.HTTP_IN_FLIGHT.dec()
            metrics.HTTP_REQUESTS.labels(
                method=method, route=route, status=_status_class(status)
            ).inc()
            metrics.HTTP_LATENCY.labels(method=method, route=route).observe(cost)
            logger.info(
                "%s %s -> %s (%.1f ms)",
                method,
                route,
                status,
                cost * 1000,
                extra={
                    "http_method": method,
                    "http_route": route,
                    "http_status": status,
                    "duration_ms": round(cost * 1000, 1),
                    "feature": feature,
                    "request_id": request_id,
                    "trace_id": trace_id,
                },
            )
            tracing.REQUEST_ID.reset(token_rid)
            tracing.USER_ID.reset(token_uid)
            tracing.FEATURE.reset(token_feat)

    @staticmethod
    def _request_id(scope: dict) -> str:
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-request-id":
                got = value.decode("latin-1", errors="ignore").strip()
                if got:
                    return got[:64]
        return uuid.uuid4().hex[:12]


def _query(scope: dict) -> str:
    raw = scope.get("query_string")
    if not raw:
        return ""
    try:
        text = raw.decode("latin-1") if isinstance(raw, bytes) else str(raw)
    except Exception:  # noqa: BLE001
        return ""
    # 查询串里可能带邮箱、token 之类，只留前 200 字符且不做解析
    return text[:200]


def _status_class(status: int) -> str:
    """状态码归成类：避免 404/422/500 每种一条序列，也避免 Grafana 面板图例爆炸。"""
    return f"{status // 100}xx"


def tracing_enabled():
    try:
        from app.ai.langfuse_client import get_langfuse

        return get_langfuse()
    except Exception:  # noqa: BLE001
        return None


def _propagate(*, user_id: str, feature: str, env: str, request_id: str):
    from contextlib import nullcontext

    try:
        from langfuse import propagate_attributes

        return propagate_attributes(
            user_id=user_id or None,
            tags=[f"feature:{feature}", f"env:{env}"],
            metadata={"request_id": request_id, "feature": feature, "env": env},
        )
    except Exception:  # noqa: BLE001
        return nullcontext()
