"""JD 文件上传抽取与 AI 拆解（PRD 3.2 第二段）。

刻意**不挂在 /positions/{id} 下**：新建职位时还没有 id，
而「上传 JD → AI 拆解」在填写阶段就要可用。
路由前缀为独立的 /api/jd，权限仍按 position:edit 收敛。

两者都是**同步**接口：
- 抽取 <2s；AI 拆解关掉 thinking 后实测约 3s。
- 因此不引入 Celery 任务表 / task_id 轮询 / DLQ 一整套异步体系。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.deps import require_perm
from app.models.user import User
from app.schemas.position import JdExtractOut, JdParseIn, JdParseOut
from app.services import jd_ai, jd_extract

router = APIRouter(prefix="/jd", tags=["jd"])


@router.post("/extract", response_model=JdExtractOut)
async def extract_jd(
    file: UploadFile = File(...),
    user: User = Depends(require_perm("position:edit")),
) -> JdExtractOut:
    """上传 JD 文件并同步抽取纯文本。

    只支持文本型 PDF / DOCX / TXT / MD（≤5MB），**不做 OCR**；
    文件抽取完即丢弃，不落库、不进 COS。
    """
    data = await file.read()
    text = await jd_extract.extract_text_async(file.filename or "", data)
    return JdExtractOut(filename=file.filename or "", text=text)


@router.post("/parse", response_model=JdParseOut)
async def parse_jd(
    payload: JdParseIn,
    user: User = Depends(require_perm("position:edit")),
) -> JdParseOut:
    """AI 拆解 JD（C1，L0 单次调用）。

    结果不落库：返回给前端填充三区块，HR 编辑后点保存才生效（BR-01）。
    """
    return JdParseOut(**await jd_ai.parse_jd(payload.raw_text))
