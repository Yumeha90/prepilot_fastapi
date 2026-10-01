-- 008_lifecycle: 数据生命周期（PRD 3.5.1 P15 / BR-10 / §7.7）
--
-- 合规能力在 v1.3 已收敛为两项：授权勾选（BR-09，随简历上传落地）+ 90 天自动粉碎。
-- 本文件落地第二项：
--   1) retention_policies —— 保留策略（当前 + 历史）。PRD §5.14：默认策略不可删、
--      可编辑，编辑后旧版本转为只读历史，页面要能回答「当年按多少天保留的」。
--   2) candidates.purge_source / purged_by —— 区分自动粉碎与超管手动粉碎。
--      粉碎本身清空的字段（resume_raw_text / parsed_profile / profile_status）
--      沿用 005 已有的列，不新增。
--
-- 注意：**粉碎只清内容、行保留**（D7）。看板与历史会话都靠 candidate_id 外键，
-- 删行会让它们指向空；行留着才能回答「这个人当时走到哪一轮」。

CREATE TABLE IF NOT EXISTS retention_policies (
    id           SERIAL       PRIMARY KEY,
    name         VARCHAR(100) NOT NULL,
    -- 本期只有简历一类数据；留字段是为了后续接 JD / 面评时不用改表
    resource     VARCHAR(32)  NOT NULL DEFAULT 'resume',
    days         INTEGER      NOT NULL DEFAULT 90,
    -- 关掉后定时任务不再粉碎（页面可停用，但不给删除入口）
    enabled      BOOLEAN      NOT NULL DEFAULT true,
    status       VARCHAR(16)  NOT NULL DEFAULT 'current',
    created_by   INTEGER      REFERENCES users (id),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- 最近一次定时扫描：页面要能一眼看出「定时任务到底跑没跑、跑掉了几个人」
    last_run_at  TIMESTAMPTZ,
    last_purged  INTEGER      NOT NULL DEFAULT 0
);

COMMENT ON COLUMN retention_policies.status IS 'current 生效中 / history 历史版本（只读）';
COMMENT ON COLUMN retention_policies.days IS '自入库起保留天数，到期且未入职即粉碎（BR-10）';

-- 同一类数据同时只能有一条生效策略（历史版本不参与）
CREATE UNIQUE INDEX IF NOT EXISTS ux_retention_policies_current
    ON retention_policies (resource) WHERE status = 'current';

CREATE INDEX IF NOT EXISTS ix_retention_policies_status ON retention_policies (status);

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS purge_source VARCHAR(16);
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS purged_by INTEGER REFERENCES users (id);

COMMENT ON COLUMN candidates.purge_source IS 'auto 定时任务 / manual 超管手动';
