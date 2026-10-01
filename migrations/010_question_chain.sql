-- 010_question_chain: Step 2 问题链（PRD 3.4.2 / §6.4 P10）
--
-- 四个刻意的设计：
--   1) 问题链同样存 **JSONB 整块**（带 revision），与矩阵同一口径。
--      理由一致：树状结构（主问题 → 追问层 → 观察点）是「整体替换」的编辑对象，
--      层级随 AI 输出浮动，拆成 nodes / followups / observations 三张子表
--      要维护 seq 与级联删除，收益不抵复杂度和半截状态风险。
--   2) chain_status 单独一列（idle / running / ready / failed）。
--      问题链走 **异步 Celery + 前端轮询**（BR-12）：带 RAG 检索后云端大概率 15~30s，
--      同步等待会撞 nginx 120s，期间页面还完全无反馈。
--      没有状态列前端就分不清「没生成过」和「生成失败了」。
--   3) chain_error 落库而不只写日志：失败原因要能在页面上告诉面试官
--      （「向量库不可用」和「AI 输出为空」该怎么处理完全不同），
--      只写日志的话面试官只会看到「一直转圈然后什么都没有」。
--   4) chain_task_id 留着做幂等：重复点「生成」时用它判断上一轮是否还在跑，
--      避免两个 worker 同时写同一份草稿（BR-12 防误触）。

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_json JSONB;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_updated_at TIMESTAMPTZ;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_status VARCHAR(16) NOT NULL DEFAULT 'idle';

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_task_id VARCHAR(64);

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS chain_error TEXT;

-- 问题链结构（每个节点）：
--   {"id": "n1", "capability": "Java 并发", "row_id": "m1", "status": "verify",
--    "main_question": "...", "followups": [{"level": 1, "vague": "...", "anti_fake": "..."}],
--    "observations": ["...", "..."], "minutes": 10, "rag_refs": ["..."], "flagged": false}
-- 顶层：{"nodes": [...], "generated": true, "generated_at": "...", "model": "...",
--        "budget_minutes": 40, "rag_hits": {"Java 并发": [{"title": "...", "score": 0.8}]}}
COMMENT ON COLUMN interview_sessions.chain_json IS 'Step2 问题链整块（树状节点，带 revision）';
COMMENT ON COLUMN interview_sessions.chain_status IS '问题链生成状态：idle / running / ready / failed';
