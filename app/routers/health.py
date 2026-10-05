from fastapi import APIRouter, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.core.database import engine
from app.observability import metrics as obs_metrics
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/metrics", summary="Prometheus 指标（云端 Alloy 抓取用）", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """暴露 Prometheus 文本格式指标。

    **不鉴权**：它只有聚合数字，没有任何业务数据；而 Alloy 抓取不该被 JWT 拦住。
    路径被中间件排除在埋点之外，避免「抓指标」本身再产生指标。
    """
    body, content_type = obs_metrics.render_metrics()
    return Response(content=body, media_type=content_type)


@router.get("/health", response_model=HealthResponse, summary="存活探针（云端冒烟探测用）")
async def health() -> HealthResponse:
    """返回体固定为 {"status":"ok"}，Jenkins / Ansible 冒烟依赖它，勿改结构。"""
    return HealthResponse()


@router.get("/health/ready", summary="就绪探针（检查数据库连通性）")
async def ready() -> JSONResponse:
    """数据库不可用时返回 503，便于本地联调时快速判断 infra 是否起来了。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "detail": str(exc)},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})
