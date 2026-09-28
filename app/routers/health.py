from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


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
