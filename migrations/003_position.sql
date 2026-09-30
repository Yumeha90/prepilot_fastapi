-- 003_position: 职位与 JD 管理（PRD 3.2）建表
--   1) positions        职位主表（JD 原文 / 结构化字段 / 版本与 hash / 草稿区）
--   2) position_rounds  面试轮次（BR-23：长度 ≤ 4，r1/r2/hr/offer 每类型至多 1 个）
--   3) jd_versions      JD 结构化快照历史（2026-09-30 定：方案 B）
--
-- 主键约定：全部 SERIAL（int 自增）。
--
-- 设计说明（与 PRD v1.5 / 3.2 实现方案对齐）：
--   - jd_draft_json 存「AI 生成或 HR 编辑中、未确认」的结构化结果；
--     BR-01 要求人工确认后才生效，确认时才搬进 jd_hard_gates/jd_competencies/jd_bonuses。
--   - jd_hash 是结构化字段的规范化 sha256，用于判定「是否实质变更」，
--     避免 HR 反复点保存制造空版本（只改原文/名称/状态不 bump）。
--   - rounds 拆子表而非 JSONB：面试官视角要按 interviewer_id 反查被指派职位
--     （position:view_assigned），且 BR-23 的「每类型至多 1 个」由唯一约束天然保证。

-- ---------- 1) 职位主表 ----------
CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL       PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    -- draft 草稿 / open 招聘中 / paused 已暂停 / closed 已关闭
    status          VARCHAR(16)  NOT NULL DEFAULT 'draft',
    owner_id        INTEGER      REFERENCES users (id),
    jd_raw_text     TEXT         NOT NULL DEFAULT '',
    jd_hard_gates   JSONB        NOT NULL DEFAULT '[]',
    jd_competencies JSONB        NOT NULL DEFAULT '[]',
    jd_bonuses      JSONB        NOT NULL DEFAULT '[]',
    -- 未确认的 AI 拆解结果 / HR 编辑中内容
    jd_draft_json   JSONB,
    -- empty 未录入 / draft 有草稿未确认 / confirmed 已确认生效
    jd_status       VARCHAR(16)  NOT NULL DEFAULT 'empty',
    jd_version      INTEGER      NOT NULL DEFAULT 1,
    jd_hash         VARCHAR(64)  NOT NULL DEFAULT '',
    copied_from_id  INTEGER      REFERENCES positions (id),
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_positions_status ON positions (status);
CREATE INDEX IF NOT EXISTS ix_positions_owner  ON positions (owner_id);

-- ---------- 2) 面试轮次 ----------
CREATE TABLE IF NOT EXISTS position_rounds (
    id             SERIAL       PRIMARY KEY,
    position_id    INTEGER      NOT NULL REFERENCES positions (id) ON DELETE CASCADE,
    seq            INTEGER      NOT NULL,
    name           VARCHAR(100) NOT NULL DEFAULT '',
    -- r1 技术一面 / r2 技术二面 / hr HR 面 / offer Offer 审批（终结节点，不是面试）
    type           VARCHAR(8)   NOT NULL,
    interviewer_id INTEGER      REFERENCES users (id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_position_round_type UNIQUE (position_id, type),
    CONSTRAINT uq_position_round_seq  UNIQUE (position_id, seq)
);

-- 面试官视角「我被指派了哪些职位」靠这个索引
CREATE INDEX IF NOT EXISTS ix_position_rounds_interviewer ON position_rounds (interviewer_id);

-- ---------- 3) JD 版本快照 ----------
CREATE TABLE IF NOT EXISTS jd_versions (
    id             SERIAL       PRIMARY KEY,
    position_id    INTEGER      NOT NULL REFERENCES positions (id) ON DELETE CASCADE,
    version        INTEGER      NOT NULL,
    snapshot_json  JSONB        NOT NULL DEFAULT '{}',
    change_summary VARCHAR(255) NOT NULL DEFAULT '',
    changed_by     INTEGER      REFERENCES users (id),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_jd_version UNIQUE (position_id, version)
);
