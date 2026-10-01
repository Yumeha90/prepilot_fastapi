-- 007_match: 人岗匹配分落地（PRD §6.7 / §6.8 / §5.16 P18）
--
-- 3.2 第三段只建了 match_scores 的表壳与 STALE 埋点；本文件补齐真正算分需要的两处：
--   1) summary_status —— v1.11 定的同步/异步边界：**判定与加权同步落定，
--      AI 总结异步补写**。分数先可用，总结没回来不代表这次算分失败。
--   2) match_feedbacks —— BR-22 反馈留痕，强绑定一张不可变评分快照。

ALTER TABLE match_scores
    ADD COLUMN IF NOT EXISTS summary_status VARCHAR(16) NOT NULL DEFAULT 'pending';

-- 无法从简历核实的硬性门槛（信息缺失 ≠ 不满足）：不触发一票否决，
-- 但要摆在 P18 上让 HR 人工确认，所以单独存一列而不是混进 vetoed_gates
ALTER TABLE match_scores
    ADD COLUMN IF NOT EXISTS unknown_gates JSONB NOT NULL DEFAULT '[]';

-- pending 生成中 / ready 已生成 / failed 生成失败（不重试，等下次重算）
COMMENT ON COLUMN match_scores.summary_status IS 'pending/ready/failed';

-- 一票否决时没有加权过程可解释，总结直接由规则产出，不需要 LLM
UPDATE match_scores SET summary_status = 'ready'
WHERE summary_status = 'pending' AND tier = 'vetoed';

CREATE TABLE IF NOT EXISTS match_feedbacks (
    id                SERIAL       PRIMARY KEY,
    match_score_id    INTEGER      REFERENCES match_scores (id) ON DELETE SET NULL,
    application_id    INTEGER      REFERENCES applications (id) ON DELETE CASCADE,
    candidate_id      INTEGER      REFERENCES candidates (id) ON DELETE CASCADE,
    position_id       INTEGER      REFERENCES positions (id) ON DELETE CASCADE,
    -- 问题类型：偏高 / 偏低 / 依据引用错误 / 证据遗漏 / 权重不合理 / 其他
    kind              VARCHAR(32)  NOT NULL,
    expected_low      INTEGER,
    expected_high     INTEGER,
    comment           TEXT         NOT NULL DEFAULT '',
    -- 提交瞬间的评分快照（分数 / 构成 / 权重 / 版本 / 证据），不可变
    snapshot_json     JSONB        NOT NULL DEFAULT '{}',
    created_by        INTEGER      REFERENCES users (id),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_match_feedbacks_score ON match_feedbacks (match_score_id);
CREATE INDEX IF NOT EXISTS ix_match_feedbacks_application ON match_feedbacks (application_id);
