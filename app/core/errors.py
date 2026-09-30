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
