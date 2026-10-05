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
    # 职位还没配面试轮次：派单无从下手
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

    # 数据生命周期（PRD 3.5.1 P15）
    LIFECYCLE_DAYS_INVALID = "lifecycle.days_invalid"
    LIFECYCLE_CONFIRM_REQUIRED = "lifecycle.confirm_required"
    LIFECYCLE_NOTHING_SELECTED = "lifecycle.nothing_selected"

    # AI 备面工作台（PRD 3.4）
    # 只有被指派的面试官能写；HR / HR 主管进来看可以，改不行
    WORKBENCH_READ_ONLY = "workbench.read_only"
    # 会话已提交：连面试官本人都只能回看
    WORKBENCH_SUBMITTED = "workbench.submitted"
    # 矩阵生成的前置：职位 JD 未确认 / 候选人简历已粉碎或为空，没有可对齐的材料
    WORKBENCH_NO_JD = "workbench.no_jd"
    WORKBENCH_NO_RESUME = "workbench.no_resume"
    WORKBENCH_MATRIX_INVALID = "workbench.matrix_invalid"
    WORKBENCH_DURATION_INVALID = "workbench.duration_invalid"

    # Step2 问题链（PRD §3.4.2 / §6.4）
    # 上一轮生成还在跑就又点了一次：BR-12 防误触，也避免两个 worker 写同一份草稿
    WORKBENCH_CHAIN_RUNNING = "workbench.chain_running"
    # 还没有矩阵就生成问题链：问题链是「矩阵重点项 → 题目」的展开，没有输入
    WORKBENCH_CHAIN_NO_MATRIX = "workbench.chain_no_matrix"
    WORKBENCH_CHAIN_INVALID = "workbench.chain_invalid"
    # 节点不存在（换一换 / 删除指向了已被删掉的节点）
    WORKBENCH_NODE_NOT_FOUND = "workbench.node_not_found"
    # 向量库不可用：与「Milvus 不可用直接失败、不降级」同一口径（PRD §11.4）
    WORKBENCH_RAG_UNAVAILABLE = "workbench.rag_unavailable"

    # Step3 公平性检查（PRD §3.4.3 / §6.5）
    # 还没有问题链就做公平性检查：没有检查对象
    WORKBENCH_FAIRNESS_NO_CHAIN = "workbench.fairness_no_chain"
    # 处置的命中项不存在（扫描结果已被新一轮覆盖）
    WORKBENCH_FINDING_NOT_FOUND = "workbench.finding_not_found"
    # 阻断项不允许「保留」：BR-05 要求必改写
    WORKBENCH_FAIRNESS_BLOCK_REQUIRED = "workbench.fairness_block_required"
    # 保留警告项却没写原因：留痕是这个动作的唯一意义
    WORKBENCH_FAIRNESS_REASON_REQUIRED = "workbench.fairness_reason_required"
    # 没有可用的改写建议（模型没给，也没手动填）
    WORKBENCH_FAIRNESS_NO_SUGGESTION = "workbench.fairness_no_suggestion"

    # Step4 评分与面评（PRD §3.4.4 / §6.6）
    # 没有矩阵就没有可评分的能力项
    WORKBENCH_EVAL_NO_MATRIX = "workbench.eval_no_matrix"
    # 一个分数一条笔记都没有，润色没有输入
    WORKBENCH_EVAL_EMPTY = "workbench.eval_empty"
    # 还没生成过润色稿就点采纳
    WORKBENCH_EVAL_NO_POLISHED = "workbench.eval_no_polished"

    # Step5 校准与提交（PRD §3.4.5 / §5.13）
    # 提交是不可逆终态，前端弹窗之外后端再挡一道（与粉碎的 confirm 同口径）
    WORKBENCH_CONFIRM_REQUIRED = "workbench.confirm_required"
    # 结论下拉没选或取值非法
    WORKBENCH_CONCLUSION_INVALID = "workbench.conclusion_invalid"
    # BR-07 的完整性要求只在提交时拦：还有能力项没打分或没写依据
    WORKBENCH_EVAL_INCOMPLETE = "workbench.eval_incomplete"
    # 综合评价为空：面评没有定性，提交出去等于一份只有分数的表
    WORKBENCH_EVAL_NO_SUMMARY = "workbench.eval_no_summary"
    # Step3 没扫过 / 还是阻断状态：题目都没合规就交面评，等于跳过闸门
    WORKBENCH_FAIRNESS_REQUIRED = "workbench.fairness_required"
    # BR-06：各项均分 < 2 时结论只能是不通过（拦下来让他改选，不静默改写）
    WORKBENCH_CONCLUSION_FORCED_FAIL = "workbench.conclusion_forced_fail"
    # BR-07：结论不通过必须 ≥2 项评分 ≤2 且写明依据
    WORKBENCH_FAIL_NEEDS_EVIDENCE = "workbench.fail_needs_evidence"
    # 面评本身命中阻断级红线（「年纪偏大」这类），必须改完再交
    WORKBENCH_SUBMISSION_BLOCKED = "workbench.submission_blocked"


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
