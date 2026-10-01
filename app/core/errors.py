"""统一错误码。

后端只维护中文 message（便于日志排查），前端按 code 查三语映射表展示，
避免「后端翻一半、前端翻一半」导致的文案不一致。

响应体形如：{"detail": {"code": "auth.invalid_credentials", "message": "邮箱或密码不正确"}}
"""
from __future__ import annotations

from fastapi import HTTPException, status


class ErrorCode:
    # 认证
    INVALID_CREDENTIALS = "auth.invalid_credentials"
    EMAIL_TAKEN = "auth.email_taken"
    USER_DISABLED = "auth.user_disabled"
    USER_NOT_FOUND = "auth.user_not_found"
    TOKEN_INVALID = "auth.token_invalid"
    REFRESH_INVALID = "auth.refresh_invalid"
    WEAK_PASSWORD = "auth.weak_password"
    PASSWORD_MISMATCH = "auth.password_mismatch"

    # 忘记密码
    CODE_INVALID = "auth.code_invalid"
    RESEND_TOO_SOON = "auth.resend_too_soon"
    DAILY_LIMIT = "auth.daily_limit"
    MAIL_UNAVAILABLE = "auth.mail_unavailable"

    # 权限
    FORBIDDEN = "auth.forbidden"
    NOT_FOUND = "common.not_found"
    ROLE_NOT_FOUND = "auth.role_not_found"

    # 职位与 JD（PRD 3.2）
    POSITION_NOT_FOUND = "position.not_found"
    POSITION_NOT_DELETABLE = "position.not_deletable"
    POSITION_NAME_TAKEN = "position.name_taken"
    POSITION_STATUS_INVALID = "position.status_invalid"
    JD_INVALID = "position.jd_invalid"
    ROUNDS_INVALID = "position.rounds_invalid"
    PUBLISH_BLOCKED = "position.publish_blocked"
    CLOSE_BLOCKED = "position.close_blocked"

    # JD 文件抽取与 AI 拆解（PRD 3.2 第二段）
    JD_FILE_TOO_LARGE = "position.jd_file_too_large"
    JD_FILE_UNSUPPORTED = "position.jd_file_unsupported"
    JD_FILE_NO_TEXT = "position.jd_file_no_text"
    JD_TEXT_TOO_SHORT = "position.jd_text_too_short"
    LLM_FAILED = "common.llm_failed"

    # 候选人与简历（PRD 3.3）
    CANDIDATE_NOT_FOUND = "candidate.not_found"
    CANDIDATE_EMAIL_TAKEN = "candidate.email_taken"
    CANDIDATE_ALREADY_APPLIED = "candidate.already_applied"
    CANDIDATE_AUTH_REQUIRED = "candidate.auth_required"
    CANDIDATE_JD_NOT_CONFIRMED = "candidate.jd_not_confirmed"
    CANDIDATE_NAME_REQUIRED = "candidate.name_required"
    CANDIDATE_NOT_CONFIRMED = "candidate.not_confirmed"
    CANDIDATE_ALREADY_CONFIRMED = "candidate.already_confirmed"
    # 简历已粉碎（行保留、内容清空）：再解析/再确认都没有意义
    CANDIDATE_PURGED = "candidate.purged"
    RESUME_FILE_TOO_LARGE = "candidate.resume_file_too_large"
    RESUME_FILE_UNSUPPORTED = "candidate.resume_file_unsupported"
    RESUME_FILE_NO_TEXT = "candidate.resume_file_no_text"
    RESUME_TEXT_TOO_SHORT = "candidate.resume_text_too_short"
    RESUME_TOO_LONG = "candidate.resume_too_long"

    # 派单与会话（PRD 3.3 第二段 / 3.4）
    SESSION_NOT_FOUND = "session.not_found"
    SESSION_FORBIDDEN = "session.forbidden"
    # 职位还没配面试轮次（只有 offer 轮也算没配）：派单无从下手
    SESSION_NO_ROUND = "session.no_round"
    # 轮次存在却没有面试官：PRD 3.2 已收紧为矛盾状态，这里是防御性兜底
    SESSION_NO_INTERVIEWER = "session.no_interviewer"
    SESSION_ALREADY_DISPATCHED = "session.already_dispatched"

    # 看板阶段流转（PRD 3.3.3 P08）
    STAGE_INVALID = "candidate.stage_invalid"
    APPLICATION_NOT_FOUND = "application.not_found"

    # 人岗匹配（PRD §6.7 / §6.8 P18）
    MATCH_NOT_FOUND = "match.not_found"
    MATCH_INVALID_RANGE = "match.invalid_range"
    # 删除轮次时该轮已有在跑的会话：删了会让会话指向空轮次
    ROUND_IN_USE = "position.round_in_use"


class AppError(HTTPException):
    """带错误码的 HTTP 异常。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


def bad_request(code: str, message: str) -> AppError:
    return AppError(status.HTTP_400_BAD_REQUEST, code, message)


def unauthorized(code: str = ErrorCode.TOKEN_INVALID, message: str = "凭证无效或已过期") -> AppError:
    return AppError(status.HTTP_401_UNAUTHORIZED, code, message)


def forbidden(message: str = "无权限执行该操作") -> AppError:
    return AppError(status.HTTP_403_FORBIDDEN, ErrorCode.FORBIDDEN, message)


def not_found(message: str = "资源不存在") -> AppError:
    return AppError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, message)
