-- 006_session: 面试会话（PRD 3.3 第二段 / 3.4 工作台载体）
--
-- 决策依据：
--   D5 确认即派单 —— 简历确认后按流程配置派到首轮面试官，并建首轮会话
--   PRD §11.1 状态机：Session: S1_DRAFT → S2_DRAFT → S3_DRAFT → S4_DRAFT → S5_DRAFT → SUBMITTED
--   PRD §8.1 InterviewSession: id, appId, candidateId, roundId, interviewerId, status, …
--
-- 三个刻意的设计：
--   1) 会话围绕 application 转（不是 candidate）：一人一职位本期成立，但模型不锁死；
--      后续若放开多职位，会话天然挂在某条应聘记录上。
--   2) 轮次信息做**快照**（round_type / round_name）：流程配置变更只对未来的应聘记录
--      生效，已派单的会话不跟随（PRD 3.2）。只留 round_id 外键会读到「改过之后」的值。
--   3) candidate_id / position_id 冗余进来：面试官视角要按 interviewer_id 直接反查，
--      看板也要按 position_id 过滤，避免每次都 join application。
--
-- 本期不建的字段（留给各自阶段）：scores / quick_notes / final_note / conclusion /
-- ai_logs 属 Step1–Step5 的产物，随各自迁移加，不在骨架里预铺空列。

CREATE TABLE IF NOT EXISTS interview_sessions (
    id              SERIAL       PRIMARY KEY,
    application_id  INTEGER      NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    candidate_id    INTEGER      NOT NULL REFERENCES candidates (id) ON DELETE CASCADE,
    position_id     INTEGER      NOT NULL REFERENCES positions (id) ON DELETE CASCADE,
    round_id        INTEGER      NOT NULL REFERENCES position_rounds (id),
    -- 轮次快照：派单那一刻的轮次类型与名称，流程后续改动不影响本会话
    round_type      VARCHAR(8)   NOT NULL,
    round_name      VARCHAR(100) NOT NULL DEFAULT '',
    interviewer_id  INTEGER      NOT NULL REFERENCES users (id),
    -- s1_draft → s2_draft → s3_draft → s4_draft → s5_draft → submitted
    status          VARCHAR(16)  NOT NULL DEFAULT 's1_draft',
    submitted_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 面试官视角「我被派了哪些面试」靠这个复合索引
CREATE INDEX IF NOT EXISTS ix_sessions_interviewer_status
    ON interview_sessions (interviewer_id, status);
CREATE INDEX IF NOT EXISTS ix_sessions_application ON interview_sessions (application_id);
CREATE INDEX IF NOT EXISTS ix_sessions_candidate ON interview_sessions (candidate_id);
CREATE INDEX IF NOT EXISTS ix_sessions_position ON interview_sessions (position_id);

-- 同一轮次可被派多次（HR 退回后重新派单），因此不设 (application_id, round_id) 唯一约束
