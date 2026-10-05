-- 013_submission: Step 5 面评校准与提交（PRD §3.4.5 / §5.13 P13）
--
-- 三个刻意的设计：
--   1) `conclusion` 单独成列而不是塞进 JSONB：它是**查询维度**（P17 面评详情要按结论
--      展示，后续统计也可能按结论筛），JSONB 里的字段没法直接 where。
--   2) `submission_json` 存**提交那一刻的合规快照**（命中项 + 依据 + 扫描时间 + 模型），
--      不存扫描过程：提交是终态动作，事后要能回答「这份面评当年扫出了什么、为什么放行」。
--      结论三态沿用 Step3 的 pass / warn / block —— 两条链用的是同一套判定口径。
--   3) **不加 status 列**：提交只有「未提交 / 已提交」两态，会话的 `status=submitted`
--      已经表达了，再加一列就是两处状态要同步（迟早不一致）。
--
-- 口径（PRD §7.8 / BR-06）：提交**不自动变更候选人阶段** —— 面试官只交结论与面评，
-- 不指定下一轮；阶段流转（下一轮 / Offer / 淘汰 / 人才库）一律由 HR 在 P08 处置。
-- 因此这里只有会话置 SUBMITTED，`applications.stage` 不动。

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS conclusion VARCHAR(16);

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS submission_json JSONB;

-- 提交快照结构：
--   {"result": "pass", "scanned_at": "2026-10-04T10:00:00", "model": "qwen3.8-flash",
--    "fields_scanned": 7,
--    "flags": [{"category": "age", "level": "block", "field": "summary",
--               "snippet": "年纪偏大", "reason": "…", "suggestion": "…", "source": "rule",
--               "refs": ["公司制度：…"]}],
--    "rag_hits": [{"title": "…", "score": 0.62, "snippet": "…"}]}
COMMENT ON COLUMN interview_sessions.conclusion IS 'Step5 面试结论：pass 通过 / pending 待定 / fail 不通过（提交后写入）';
COMMENT ON COLUMN interview_sessions.submission_json IS 'Step5 提交时的合规扫描快照（命中项与依据），提交后只读';
