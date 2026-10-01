-- 009_workbench: 备面工作台地基 + Step 1 能力-证据矩阵（PRD 3.4.1 / §5.9 P09）
--
-- 三个刻意的设计：
--   1) 矩阵存 **JSONB 整块**（带 revision），不拆子表。
--      理由：矩阵是「整体替换」的高频编辑对象，行结构随 AI 输出浮动（能力项可能来自
--      JD 也可能来自简历），拆表要给每行维护 seq、还要处理 AI 重生成时的差量更新；
--      整块替换 + 版本号最简单也最不容易出现半截状态。
--   2) 时长（duration_minutes）挂在会话上而不是职位上：
--      同一职位的一面 45 分钟、二面 60 分钟是常态，且会话已经做了轮次快照，
--      时长同样属于「派单那一刻定下来的东西」，流程后续改动不追溯。
--   3) matrix_updated_at 单独留一列：revision 只回答「改了几次」，
--      回答不了「什么时候改的」，页面上的「上次保存于」要它。

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS duration_minutes INTEGER NOT NULL DEFAULT 45;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS matrix_json JSONB;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS matrix_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS matrix_updated_at TIMESTAMPTZ;

-- 矩阵行结构（每行）：
--   {"id": "c1", "source": "jd|resume|manual", "capability": "...", "weight": 40,
--    "evidence": "...", "status": "sufficient|verify|missing", "focus": "...", "is_key": false}
-- 顶层：{"rows": [...], "generated": true, "generated_at": "...", "model": "..."}
COMMENT ON COLUMN interview_sessions.matrix_json IS 'Step1 能力-证据矩阵整块（带 revision）';
COMMENT ON COLUMN interview_sessions.duration_minutes IS '本轮面试时长（分钟），默认 45';
