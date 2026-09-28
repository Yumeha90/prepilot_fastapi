"""腾讯云 COS（简历原件暂存）。

Phase 1 仅留接口位：cos-python-sdk-v5 尚未加入 requirements.txt，
真正接入简历原件存储时再补依赖与实现。
备选方案（若 COS 成本/复杂度不划算）：原件改存自建 PostgreSQL 的大字段表。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@lru_cache
def get_cos_client() -> Any | None:
    if not (
        settings.COS_SECRET_ID and settings.COS_SECRET_KEY and settings.COS_BUCKET
    ):
        logger.info("COS 未配置，跳过")
        return None

    logger.warning("COS 客户端尚未实现（Phase 1 占位），返回 None")
    return None


def build_object_key(bucket_dir: str, filename: str) -> str:
    """约定对象键：resumes/<dir>/<filename>，避免桶内平铺。"""
    return f"resumes/{bucket_dir.strip('/')}/{filename.lstrip('/')}"
