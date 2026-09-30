-- 005_candidate: 候选人与应聘记录（PRD 3.3 第一段）
--
-- 决策依据（2026-09-30 拍板）：
--   D1 上传简历必须选职位 → 一步建 candidate + application
--   D3 本期禁止一人投多个职位 → UNIQUE(candidate_id, position_id)
--   D14 候选人唯一识别键 = contact_email（必填 + UNIQUE）
--   D13 简历原件不落 COS → 只留 resume_raw_text，无外部对象，粉碎不联动删除
--
-- 两个刻意的设计：
--   1) application 独立成表而不是挂在 candidate 上：候选人与职位是多对多关系
--      （本期用唯一约束收成一比一，但模型不锁死），且 3.3 的看板 / 派单 / 会话
--      全部围绕 application 转。
--   2) interview_sessions 留给 S2，本文件不建。

CREATE TABLE IF NOT EXISTS candidates (
    id                SERIAL       PRIMARY KEY,
    name              VARCHAR(100) NOT NULL DEFAULT '',
    -- D14 候选人唯一识别键：必填 + 唯一。没有它「禁止一人多职」落不了地
    contact_email     VARCHAR(200) NOT NULL,
    contact_phone     VARCHAR(50)  NOT NULL DEFAULT '',
    -- upload 文件上传 / paste 手动粘贴
    source            VARCHAR(16)  NOT NULL DEFAULT 'upload',
    resume_raw_text   TEXT         NOT NULL DEFAULT '',
    resume_file_name  VARCHAR(255) NOT NULL DEFAULT '',
    -- AI 解析 + HR 纠错后的结构化档案（确认时写入）
    parsed_profile    JSONB,
    -- uploading 已上传 / parsed 已解析 / confirmed 已确认 / archived 已粉碎
    profile_status    VARCHAR(16)  NOT NULL DEFAULT 'uploading',
    -- BR-09 数据处理授权
    auth_tick         BOOLEAN      NOT NULL DEFAULT FALSE,
    auth_at           TIMESTAMPTZ,
    confirmed_at      TIMESTAMPTZ,
    created_by        INTEGER      REFERENCES users (id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- D7 粉碎只清简历字段，行保留并标记，避免看板 / 历史会话出现空洞外键
    purged_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidates_email ON candidates (contact_email);
CREATE INDEX IF NOT EXISTS ix_candidates_status ON candidates (profile_status);
CREATE INDEX IF NOT EXISTS ix_candidates_created_by ON candidates (created_by);

CREATE TABLE IF NOT EXISTS applications (
    id               SERIAL      PRIMARY KEY,
    candidate_id     INTEGER     NOT NULL REFERENCES candidates (id) ON DELETE CASCADE,
    position_id      INTEGER     NOT NULL REFERENCES positions (id) ON DELETE CASCADE,
    -- pending 待派单 / in_r1 / in_r2 / in_hr / in_offer / accepted
    -- 分支：rejected 淘汰 / in_pool 人才库 / archived 归档
    stage            VARCHAR(16) NOT NULL DEFAULT 'pending',
    -- 当前所在轮次（S2 派单后写入）
    current_round_id INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ
);

-- D3 一人一职位
CREATE UNIQUE INDEX IF NOT EXISTS uq_application_candidate_position
    ON applications (candidate_id, position_id);
CREATE INDEX IF NOT EXISTS ix_applications_position_stage ON applications (position_id, stage);
CREATE INDEX IF NOT EXISTS ix_applications_candidate ON applications (candidate_id);

-- 004 里承诺在 3.3 建表后补的外键，一并在这里补上：
--   application_id → applications.id
--   candidate_id   → candidates.id
ALTER TABLE match_scores
    DROP CONSTRAINT IF EXISTS fk_match_scores_application;
ALTER TABLE match_scores
    ADD CONSTRAINT fk_match_scores_application FOREIGN KEY (application_id)
    REFERENCES applications (id) ON DELETE CASCADE;

ALTER TABLE match_scores
    DROP CONSTRAINT IF EXISTS fk_match_scores_candidate;
ALTER TABLE match_scores
    ADD CONSTRAINT fk_match_scores_candidate FOREIGN KEY (candidate_id)
    REFERENCES candidates (id) ON DELETE CASCADE;
