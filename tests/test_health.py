"""Phase 1 冒烟测试：只验证脚手架本身，不涉及业务域。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    """云端 nginx 与 Jenkins 冒烟探测依赖此契约：GET /api/health -> {"status":"ok"}"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_ready():
    """就绪探针：DB 可达 -> 200；infra 未启动（DB 不可达）-> 503。
    Phase 1 阶段 infra 尚未启动，故两种状态都视为正确。"""
    resp = client.get("/api/health/ready")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body


def test_openapi_schema_exposes_api_prefix():
    """确认所有业务路由都挂在 /api 前缀下（nginx 按 /api/ 反代）。"""
    paths = client.get("/openapi.json").json()["paths"]
    assert paths, "OpenAPI 未生成任何路由"
    for p in paths:
        assert p.startswith("/api"), f"路由未挂 /api 前缀: {p}"
