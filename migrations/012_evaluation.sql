-- 012_evaluation: Step 4 行为化评分与面评（PRD 3.4.4 / §6.6 P12）
--
-- 四个刻意的设计：
--   1) 评分与面评同样存 **JSONB 整块**（带 revision），与矩阵、问题链、公平性同一口径。
--      一次自动保存就是一整块草稿（BR-13），逐项接口会把 30 秒一次的保存拆成 N 个请求，
--      失败一次就留下半截状态 —— 那比不自动保存更糟。
--   2) **润色稿与原文并存**（`summary` / `summary_original` / `polished`），
--      BR-08 要求 AI 润色不覆盖原文、由面试官显式采纳才覆写；
--      `summary_original` 是采纳那一刻留下的原文，供 P17「原文 / 润色稿双栏对比」展示。
--   3) `polished_flags` 存**二次合规扫描**的命中（PRD §6.6）：润色稿同样可能带出
--      「年纪偏大」这类红线表述，扫出来落库，页面常驻警示 —— 不落库的话，
--      「这份面评当年是否踩过线」事后无从追溯。
--   4) 不单独加 `score` 列：评分是**逐能力项**的（每个矩阵行一个 1–5 星），
--      单列表达不了，且能力项本身就在矩阵 JSONB 里，两边互相关联不如整块存。

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS evaluation_json JSONB;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS evaluation_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE interview_sessions
    ADD COLUMN IF NOT EXISTS evaluation_updated_at TIMESTAMPTZ;

-- 评分与面评结构：
--   {"items": [{"row_id": "m1", "capability": "系统设计", "score": 4,
--               "evidences": [{"id": "e1", "text": "候选人说…", "quote": true}],
--               "note": "整体思路清楚，边界考虑不足"}],
--    "summary": "综合评价正文（采纳后才被润色稿覆写）",
--    "summary_original": "采纳前的原文",
--    "polished": "【总评】…【能力项】…【综合建议】建议推进【风险提示】…",
--    "polished_at": "2026-10-03T16:00:00",
--    "polished_model": "qwen3.8-flash",
--    "polished_adopted": false,
--    "polished_flags": [{"category": "age", "snippet": "年纪偏大", "reason": "…"}],
--    "complete": false}
COMMENT ON COLUMN interview_sessions.evaluation_json IS 'Step4 评分与面评草稿（逐能力项评分/证据 + 综合评价 + 润色稿与原文并存）';
