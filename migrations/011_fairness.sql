-- 011_fairness: Step 3 公平性检查（PRD 3.4.3 / §6.5 P11）
--
-- 三个刻意的设计：
--   1) 扫描结果同样存 **JSONB 整块**（带 revision），与矩阵、问题链同一口径。
--      结论 + 命中项 + 每项的处置（改写 / 保留并记录原因）是一次扫描的完整产物，
--      拆表就要维护「命中项 ↔ 问题节点」的外键，而节点本身也在 JSONB 里 ——
--      两边都是 JSONB 时互相关联只会让一致性更难保证。
--   2) fairness_json.result 三态（pass / warn / block），**不单独加 status 列**：
--      扫描是同步的（P95 ≤ 10s），不存在「中间态要轮询」，加状态列纯属冗余。
--      （对比问题链：那一个是异步生成，必须有 idle/running/ready/failed。）
--   3) 命中项里保留 `disposition` 与 `accepted_reason`：
--      BR-05 只要求阻断项必改，警告级允许「保留并记录原因」——
--      不留痕的话「这条风险是谁放行的、理由是什么」事后无从追溯。

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS fairness_json JSONB;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS fairness_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS fairness_updated_at TIMESTAMPTZ;

-- 扫描结果结构：
--   {"result": "block", "scanned_at": "...", "model": "...", "nodes_scanned": 6,
--    "checks": [{"key": "marriage", "level": "block", "count": 1}, ...],
--    "findings": [{"id": "f1", "node_id": "n2", "capability": "系统设计",
--                  "level": "block", "category": "marriage", "field": "main_question",
--                  "snippet": "你打算什么时候要孩子", "reason": "...",
--                  "suggestion": "...", "source": "rule", "refs": ["..."],
--                  "disposition": "pending", "accepted_reason": ""}],
--    "rag_hits": {"n2": [{"title": "...", "score": 0.62, "snippet": "..."}]}}
COMMENT ON COLUMN interview_sessions.fairness_json IS 'Step3 公平性扫描结果（三态结论 + 命中项 + 处置留痕）';
