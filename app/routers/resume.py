"""简历文件抽取与 AI 解析（PRD 3.3 第一段）。

与 3.2 的 /api/jd/* 刻意分开：
- 权限不同：简历走 `candidate:upload_resume`，JD 走 `position:edit`
- 错误码不同：简历报 `candidate.resume_*`，否则前端三语会指到职位那类文案上

两者都是**同步**接口（D2）：抽取 <2s，解析关掉 thinking 后约 3~6s，
满足 §9.2「简历解析 ≤12s（P95）」，因此不引入任务表 / 轮询 / DLQ。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.deps import require_perm
from app.core.errors import ErrorCode
from app.models.user import User
from app.schemas.candidate import ResumeExtractOut, ResumeParseIn, ResumeParseOut
from app.services import jd_extract, resume_ai

router = APIRouter(prefix="/resume", tags=["resume"])


@router.post("/extract", response_model=ResumeExtractOut)
async def extract_resume(
    file: UploadFile = File(...),
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> ResumeExtractOut:
    """上传简历并同步抽取纯文本。

    只支持文本型 PDF / DOCX / TXT / MD（≤5MB），**不做 OCR**；
    文件抽取完即丢弃，不落库、不进 COS（D13）。
    """
    data = await file.read()
    text = await jd_extract.extract_text_async(
        file.filename or "",
        data,
        too_large=ErrorCode.RESUME_FILE_TOO_LARGE,
        unsupported=ErrorCode.RESUME_FILE_UNSUPPORTED,
        no_text=ErrorCode.RESUME_FILE_NO_TEXT,
    )
    return ResumeExtractOut(filename=file.filename or "", text=text)


@router.post("/parse", response_model=ResumeParseOut)
async def parse_resume(
    payload: ResumeParseIn,
    user: User = Depends(require_perm("candidate:upload_resume")),
) -> ResumeParseOut:
    """AI 解析简历（C2，L0 单次调用 + 超长降级 L1 分块）。

    结果不落库：返回给前端填充 P05 右栏，人工纠错后点确认才生效（BR-04）。
    """
    return ResumeParseOut(**await resume_ai.parse_resume(payload.raw_text))
