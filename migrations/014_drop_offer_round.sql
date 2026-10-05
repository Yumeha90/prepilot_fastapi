-- 014_drop_offer_round: 删除 offer 轮（PRD v1.26）
--
-- 为什么要删：offer 轮不建会话、不进工作台，推进过去只改一个 stage 再发条通知，
-- 而 HR 本来就能在任意阶段直接点「录用」—— 这一步天然被绕过，留着只是个空转列，
-- 还让「末轮须为 HR 面试或 Offer 审批」这条发布校验多出一个说不清的选项。
--
-- 数据处置两条：
--   1) `position_rounds` 里 type='offer' 的行删掉 —— 留在库里会让推进走到
--      `ROUND_STAGE['offer']` 直接 KeyError（代码里映射已删）。
--      offer 轮不建会话，所以没有 interview_sessions 外键要处理。
--   2) 停在 `in_offer` 的应聘记录退回 `in_hr` —— 这些人本来就是在等 Offer 审批，
--      现在由 HR 直接在 HR 面列做终结处置。若该职位流程里没有 hr 轮则退回 pending，
--      避免「阶段指向一个已经不存在的轮次」。
--
-- 幂等：两条都带 WHERE 条件，重复执行无副作用（migrate.py 本身也会按记录跳过）。

DELETE FROM position_rounds WHERE type = 'offer';

UPDATE applications a
SET stage = CASE
        WHEN EXISTS (
            SELECT 1 FROM position_rounds r
            WHERE r.position_id = a.position_id AND r.type = 'hr'
        ) THEN 'in_hr'
        ELSE 'pending'
    END,
    current_round_id = CASE
        WHEN EXISTS (
            SELECT 1 FROM position_rounds r
            WHERE r.position_id = a.position_id AND r.type = 'hr'
        ) THEN (
            SELECT r.id FROM position_rounds r
            WHERE r.position_id = a.position_id AND r.type = 'hr'
            ORDER BY r.seq LIMIT 1
        )
        ELSE NULL
    END,
    updated_at = NOW()
WHERE a.stage = 'in_offer';
