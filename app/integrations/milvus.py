"""Milvus 向量库客户端（本地 k3d + Helm 部署）。

版本对齐（2026-09-28）：
实际部署为 helm `milvus-5.0.28`，APP VERSION **v3.0.1**（Milvus 3.0.x），
故客户端用 **pymilvus 3.0.2**（官方兼容表：Milvus 3.0.x -> PyMilvus 3.0.x）。
MilvusClient 的 uri / token / db_name 用法在 3.x 与 2.x 一致，代码无需改动。

注意：k3d 里 milvus 服务是 ClusterIP，宿主不可直达。本地联调需先开端口转发：
    kubectl -n milvus port-forward svc/milvus 19530:19530
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@lru_cache
def get_milvus_client() -> Any | None:
    """惰性连接 Milvus。

    连不上只返回 None 并告警，不抛异常 —— 保证 infra 未启动时应用仍能起来，
    由调用方决定降级策略。
    """
    try:
        from pymilvus import MilvusClient
    except Exception as exc:  # noqa: BLE001
        logger.warning("pymilvus 不可用：%s", exc)
        return None

    uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    token = (
        f"{settings.MILVUS_USER}:{settings.MILVUS_PASSWORD}"
        if settings.MILVUS_USER and settings.MILVUS_PASSWORD
        else None
    )
    try:
        return MilvusClient(uri=uri, token=token, db_name=settings.MILVUS_DB)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Milvus 连接失败（infra 未启动？）：%s", exc)
        return None
