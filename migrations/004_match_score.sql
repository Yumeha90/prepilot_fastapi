-- 004_match_score: 人岗匹配分（PRD §6.7 / §11.2）
--
-- 3.2 第三段只做「建表 + STALE 埋点」：JD 权重变更后把存量分数置 STALE，
-- 由 HR 手动触发重算（PRD v1.6 定：不自动重算）。真正的算分链路属 3.3 / §6.7。
--
-- 字段对齐 PRD §11.2 的 MatchScore：
--   id, appId, candidateId, positionId, jdVersion, score, tier,
--   algorithmVersion, modelVersion, stale, vetoedGates[], breakdown[],
--   evidences[], bonuses[], summary{}
--
-- 两点妥协（本期候选人与应聘记录表还没建，属 3.3）：
--   1) application_id / candidate_id 先落成裸 INTEGER + 索引，不加外键，
--      3.3 建表后用一个 ALTER 补上即可（见文件末尾注释）。
--   2) 不用布尔 stale 而用 status VARCHAR：PRD §8.2 的状态机是
--      CURRENT → STALE → 重算，重算后**保留历史**，因此同一候选人同一职位
--      可能有多行（旧行留 STALE，新行 CURRENT），不能加 (position, candidate) 唯一约束。

CREATE TABLE IF NOT EXISTS match_scores (
    id                SERIAL       PRIMARY KEY,
    application_id    INTEGER,
    candidate_id      INTEGER      NOT NULL,
    position_id       INTEGER      NOT NULL REFERENCES positions (id) ON DELETE CASCADE,
    jd_version        INTEGER      NOT NULL,
    score             NUMERIC(5, 2) NOT NULL DEFAULT 0,
    -- 分档：strong 强匹配 / possible 可考虑 / weak 不匹配（§6.7 判定口径）
    tier              VARCHAR(16)  NOT NULL DEFAULT '',
    algorithm_version VARCHAR(32)  NOT NULL DEFAULT '',
    model_version     VARCHAR(64)  NOT NULL DEFAULT '',
    -- CURRENT 有效 / STALE 岗位标准已变更，需重算
    status            VARCHAR(16)  NOT NULL DEFAULT 'CURRENT',
    vetoed_gates      JSONB        NOT NULL DEFAULT '[]',
    breakdown         JSONB        NOT NULL DEFAULT '[]',
    evidences         JSONB        NOT NULL DEFAULT '[]',
    bonuses           JSONB        NOT NULL DEFAULT '[]',
    summary           JSONB        NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 置 STALE 时按 (position_id, status) 批量捞；列表页按候选人回查自己的分
CREATE INDEX IF NOT EXISTS ix_match_scores_position_status ON match_scores (position_id, status);
CREATE INDEX IF NOT EXISTS ix_match_scores_candidate      ON match_scores (candidate_id);

-- 3.3 建好 applications / candidates 后补外键（本文件不重复执行，手工补或写在 005 里）：
--   ALTER TABLE match_scores
--     ADD CONSTRAINT fk_match_scores_application FOREIGN KEY (application_id)
--     REFERENCES applications (id) ON DELETE CASCADE;
--   ALTER TABLE match_scores
--     ADD CONSTRAINT fk_match_scores_candidate FOREIGN KEY (candidate_id)
--     REFERENCES candidates (id) ON DELETE CASCADE;
