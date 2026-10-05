"""AI 备面工作台 Step 5 面评校准与提交（PRD §3.4.5 / §5.13 P13）。

LLM 与向量检索一律 monkeypatch（`submission._call_llm` / `submission.retrieve`），
测试锁的是**口径**而不是模型输出：

- **提交不变更候选人阶段**（BR-06）：面试官只交结论，下一轮由 HR 在看板处置
- **完整性在提交时才拦**（BR-07）：Step4 保存放行、提交拦截，且要说清缺哪一项
- **面评本身要过合规检查**：「年纪偏大」写进综合评价同样阻断提交（规则层零容忍）
- **模型编的片段要丢弃**，受保护特征模型无权降级
- **提交是终态**：置 SUBMITTED 后连面试官本人都只能回看，且必须二次确认
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.database import SessionLocal, engine
from app.models.session import InterviewSession
from app.models.user import User
from app.schemas.workbench import EvaluationIn, SubmissionIn
from app.services import evaluation as ev
from app.services import fairness as fs
from app.services import question_chain as qc
from app.services import submission as sub
from app.services import workbench as wb

# seed：7 = interviewer（陈技术），5 = hr
IV_R1 = 7
HR = 5

COMPETENCIES = [
    {"text": "系统设计", "weight": 40},
    {"text": "编码能力", "weight": 35},
    {"text": "协作沟通", "weight": 25},
]

COMPLIANCE_TITLES = [
    "公序良俗：婚姻与生育相关问题一律禁问",
    "公司招聘制度：面试提问红线清单",
]


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def _items(sid: int) -> list[dict]:
    async def _inner() -> list[dict]:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return [i.model_dump() for i in ev.evaluation_out(session).items]

    return _run(_inner())


def _save(sid: int, payload: dict, user_id: int = IV_R1):
    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, user_id)
            return await ev.save_draft(db, user, session, EvaluationIn(**payload))

    return _run(_inner())


def _full_payload(
    sid: int,
    note: str = "讲清了取舍",
    summary: str = "整体达到岗位要求",
    score: int = 4,
) -> dict:
    return {
        "items": [
            {
                "row_id": r["row_id"],
                "capability": r["capability"],
                "score": score,
                "evidences": [{"id": "e1", "text": "候选人描述了限流方案的取舍", "quote": True}],
                "note": note,
            }
            for r in _items(sid)
        ],
        "summary": summary,
    }


# ---------------------------------------------------------------- 造一个走到 Step4 的会话


def _ready_session(note: str = "讲清了取舍", summary: str = "整体达到岗位要求") -> int:
    """矩阵（s2）→ 问题链（s3）→ 公平性通过 → 评分填完（s4）。"""
    from test_evaluation import _seed_matrix
    from test_question_chain import _fake_hits, _fake_node

    sid = _seed_matrix()

    # Step2：问题链（同步生成，不走 Celery）
    async def _chain() -> None:
        async def _fake_chain_llm(
            prompt: str, system: str = qc.CHAIN_SYSTEM_PROMPT
        ) -> qc._ChainLLM:
            return qc._ChainLLM(
                nodes=[_fake_node(c["text"]) for c in COMPETENCIES[:2]]
            )

        original_llm, original_retrieve = qc._call_llm, qc.retrieve
        qc._call_llm = _fake_chain_llm
        qc.retrieve = lambda capability, focus, top_k=3: _fake_hits("")
        try:
            async with SessionLocal() as db:
                session = await db.get(InterviewSession, sid)
                user = await db.get(User, IV_R1)
                await qc.generate_chain(db, user, session)
        finally:
            qc._call_llm, qc.retrieve = original_llm, original_retrieve

    _run(_chain())

    # Step3：公平性扫描（模型不报任何命中 → pass）
    async def _fairness() -> None:
        from app.ai import rag

        def _fake_retrieve(
            node: dict, category: str = "", *, degraded: set[str] | None = None
        ) -> list:
            return [
                rag.AssetHit(title=COMPLIANCE_TITLES[0], text="禁问婚育…", score=0.62),
                rag.AssetHit(title=COMPLIANCE_TITLES[1], text="不得作为评价依据。", score=0.51),
            ]

        async def _fake_llm(prompt: str) -> fs._ScanLLM:
            return fs._ScanLLM(findings=[])

        orig_llm, orig_retrieve = fs._call_llm, fs.retrieve
        fs._call_llm = _fake_llm
        fs.retrieve = _fake_retrieve
        try:
            async with SessionLocal() as db:
                session = await db.get(InterviewSession, sid)
                user = await db.get(User, IV_R1)
                out = await fs.scan(db, user, session)
                assert out.result == "pass", out.result
        finally:
            fs._call_llm, fs.retrieve = orig_llm, orig_retrieve

    _run(_fairness())

    # Step4：评分填完
    _save(sid, _full_payload(sid, note=note, summary=summary))
    return sid


def _status(sid: int) -> str:
    async def _inner() -> str:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return session.status

    return _run(_inner())


def _submit(sid: int, conclusion: str = "pass", confirm: bool = True, user_id: int = IV_R1):
    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, user_id)
            return await sub.submit(
                db, user, session, SubmissionIn(conclusion=conclusion, confirm=confirm)
            )

    return _run(_inner())


def _scan(sid: int, user_id: int = IV_R1):
    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, user_id)
            return await sub.scan(db, user, session)

    return _run(_inner())


def _fake_compliance_scan(findings: list | None = None):
    """统一的假扫描：检索返回两条真实资料，模型按给定 findings 输出。"""
    from app.ai import rag

    def _fake_retrieve(fields, rule_flags) -> list:
        return [
            rag.AssetHit(title=COMPLIANCE_TITLES[0], text="禁问婚育…", score=0.62),
            rag.AssetHit(title=COMPLIANCE_TITLES[1], text="不得作为评价依据。", score=0.51),
        ]

    async def _fake_llm(prompt: str) -> sub._ScanLLM:
        return sub._ScanLLM(findings=findings or [])

    return _fake_retrieve, _fake_llm


# ---------------------------------------------------------------- 扫描


def test_scan_passes_clean_evaluation(monkeypatch):
    """干净的面评：扫描通过，落库快照（含扫描时间与扫了几段文本）。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    out = _scan(sid)
    assert out.result == "pass"
    assert out.flags == []
    assert out.scanned is True
    assert out.fields_scanned >= 4  # 综合评价 + 3×(证据 + 快记)
    assert len(out.rag_hits) == 2


def test_scan_blocks_red_line_in_summary(monkeypatch):
    """面评里写「年纪偏大」同样被拦 —— 题目合规而面评不合规等于没做门禁。"""
    sid = _ready_session(summary="候选人基础扎实，但年纪偏大可能学不动新框架。")
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    out = _scan(sid)
    assert out.result == "block"
    assert [f.category for f in out.flags] == ["age"]
    assert out.flags[0].field == "summary"
    assert out.flags[0].source == "rule"


def test_scan_drops_fabricated_snippet(monkeypatch):
    """模型编的片段一律丢弃：页面上出现一条不存在的依据，比没有依据更糟。"""
    sid = _ready_session()
    retrieve, _ = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)

    async def _fake_llm(prompt: str) -> sub._ScanLLM:
        return sub._ScanLLM(
            findings=[
                sub._FlagLLM(
                    category="health",
                    level="warn",
                    snippet="候选人自述有慢性病",  # 面评里根本没有这句
                    reason="涉及健康",
                    refs=[COMPLIANCE_TITLES[0]],
                )
            ]
        )

    monkeypatch.setattr(sub, "_call_llm", _fake_llm)
    out = _scan(sid)
    assert out.result == "pass"
    assert out.flags == []


def test_model_cannot_downgrade_protected_category(monkeypatch):
    """受保护特征模型无权降级成警告：模型判 warn 也按 block 处理。"""
    sid = _ready_session()
    retrieve, _ = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)

    async def _fake_llm(prompt: str) -> sub._ScanLLM:
        return sub._ScanLLM(
            findings=[
                sub._FlagLLM(
                    category="gender",
                    level="warn",
                    snippet="岗位要求",
                    reason="隐含性别倾向",
                    refs=[COMPLIANCE_TITLES[1]],
                )
            ]
        )

    monkeypatch.setattr(sub, "_call_llm", _fake_llm)
    out = _scan(sid)
    assert out.result == "block"
    assert out.flags[0].level == "block"
    assert out.flags[0].refs == [COMPLIANCE_TITLES[1]]


def test_warn_does_not_block(monkeypatch):
    """警告级只提示不拦截：诱导性表述不该把整份面评堵死。"""
    sid = _ready_session()
    retrieve, _ = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)

    async def _fake_llm(prompt: str) -> sub._ScanLLM:
        return sub._ScanLLM(
            findings=[
                sub._FlagLLM(
                    category="leading",
                    level="warn",
                    snippet="整体达到岗位要求",
                    reason="主观定性，缺少依据",
                )
            ]
        )

    monkeypatch.setattr(sub, "_call_llm", _fake_llm)
    out = _scan(sid)
    assert out.result == "warn"
    assert out.flags[0].level == "warn"


# ---------------------------------------------------------------- 提交


def test_submit_requires_confirm(monkeypatch):
    """提交是不可逆终态：后端再挡一道（前端弹窗不是凭证）。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid, confirm=False)
    assert "workbench.confirm_required" in str(exc.value)


def test_submit_requires_conclusion(monkeypatch):
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid, conclusion="maybe")
    assert "workbench.conclusion_invalid" in str(exc.value)


def test_submit_blocks_incomplete_evaluation(monkeypatch):
    """BR-06：完整性在提交时才拦。缺哪一项要写进错误信息里。"""
    sid = _ready_session()
    # 把其中一项的评分与依据清掉，让它不完整
    rows = _items(sid)
    payload = _full_payload(sid)
    payload["items"][0] = {
        "row_id": rows[0]["row_id"],
        "capability": rows[0]["capability"],
        "score": None,
        "evidences": [],
        "note": "",
    }
    _save(sid, payload)

    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid)
    assert "workbench.eval_incomplete" in str(exc.value)
    assert rows[0]["capability"] in str(exc.value)


def test_submit_requires_summary(monkeypatch):
    """综合评价为空不能提交：一份只有分数的表不是面评。"""
    sid = _ready_session()
    _save(sid, _full_payload(sid, summary=""))

    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid)
    assert "workbench.eval_no_summary" in str(exc.value)


def _mock_scan(monkeypatch) -> None:
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)


def test_submit_forces_fail_when_average_below_two(monkeypatch):
    """BR-06：均分 < 2 时结论只能是「不通过」。

    落成拦截而不是静默改写结论 —— 提交不可逆，面试官选了通过却被系统悄悄改成
    不通过，比让他多改一次糟糕得多。
    """
    sid = _ready_session()
    _save(sid, _full_payload(sid, score=1))
    _mock_scan(monkeypatch)

    with pytest.raises(Exception) as exc:
        _submit(sid, conclusion="pass")
    assert "workbench.conclusion_forced_fail" in str(exc.value)
    assert _status(sid) == "s4_draft"  # 没提交成功


def test_submit_fail_needs_weak_evidence(monkeypatch):
    """BR-07：结论「不通过」需 ≥2 项评分 ≤ 2 且写明依据 —— 差评必须站得住。"""
    sid = _ready_session()
    _mock_scan(monkeypatch)

    with pytest.raises(Exception) as exc:
        _submit(sid, conclusion="fail")  # 全是 4 分，没有低分项
    assert "workbench.fail_needs_evidence" in str(exc.value)
    assert _status(sid) == "s4_draft"


def test_submit_fail_ok_with_two_weak_items(monkeypatch):
    """两条低分 + 依据齐了才允许提交「不通过」。"""
    sid = _ready_session()
    payload = _full_payload(sid)
    for item in payload["items"][:2]:
        item["score"] = 1
        item["evidences"] = [
            {"id": "e1", "text": "答不出限流的基本思路，追问后仍说不清", "quote": False}
        ]
    _save(sid, payload)
    _mock_scan(monkeypatch)

    _submit(sid, conclusion="fail")
    assert _status(sid) == "submitted"

    async def _avg() -> float:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return sub.average_score(session) or 0

    assert _run(_avg()) == pytest.approx((1 + 1 + 4) / 3)


def test_submit_blocked_by_red_line(monkeypatch):
    """面评命中阻断级 → 提交被拦，并说清命中了什么。"""
    sid = _ready_session(summary="年纪偏大，学新框架会吃力。")
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid)
    assert "workbench.submission_blocked" in str(exc.value)
    assert "年纪偏大" in str(exc.value)
    assert _status(sid) == "s4_draft"  # 没提交成功


def test_submit_sets_terminal_state_and_keeps_stage(monkeypatch):
    """提交：会话置 submitted + 结论落库；**候选人阶段一个字都不动**（BR-06）。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    async def _stage() -> str:
        from app.models.candidate import Application

        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            application = await db.get(Application, session.application_id)
            return application.stage

    before = _run(_stage())
    out = _submit(sid, conclusion="pending")
    assert out.conclusion == "pending"
    assert out.submitted_at is not None
    assert _status(sid) == "submitted"
    assert _run(_stage()) == before


def test_submitted_session_is_read_only(monkeypatch):
    """提交后连面试官本人都只能回看。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)
    _submit(sid)

    with pytest.raises(Exception) as exc:
        _submit(sid, conclusion="pass")
    assert "workbench.submitted" in str(exc.value)


def test_hr_cannot_submit(monkeypatch):
    """只有被指派的面试官能提交（HR 一律只读）。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)

    with pytest.raises(Exception) as exc:
        _submit(sid, user_id=HR)
    assert "workbench.read_only" in str(exc.value)


def test_submit_notifies_subscribers(monkeypatch):
    """提交后要有人知道：面评交上去没人处置等于没交。"""
    from sqlalchemy import func, select

    from app.models.notification import Notification

    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)
    _submit(sid)

    async def _count() -> int:
        async with SessionLocal() as db:
            return int(
                await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.type == "evaluation_submitted")
                )
                or 0
            )

    assert _run(_count()) >= 1


def test_steps_all_done_after_submit(monkeypatch):
    """提交后进度条五步全「已完成」。"""
    sid = _ready_session()
    retrieve, llm = _fake_compliance_scan()
    monkeypatch.setattr(sub, "retrieve", retrieve)
    monkeypatch.setattr(sub, "_call_llm", llm)
    _submit(sid)

    async def _inner() -> dict[str, str]:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return {s.key: s.state for s in wb._steps(session)}

    assert _run(_inner()) == {
        "step1": "done",
        "step2": "done",
        "step3": "done",
        "step4": "done",
        "step5": "done",
    }


async def _scenario_fairness_gate(sid: int):
    """题目还是阻断状态时不许提交：跳过闸门交面评没有意义。"""
    import sqlalchemy as sa

    async with SessionLocal() as db:
        await db.execute(
            sa.text("UPDATE interview_sessions SET fairness_json = :j WHERE id = :i"),
            {"j": '{"result": "block", "scanned_at": "2026-10-04T10:00:00"}', "i": sid},
        )
        await db.commit()

    async with SessionLocal() as db:
        session = await db.get(InterviewSession, sid)
        user = await db.get(User, IV_R1)
        try:
            await sub.submit(db, user, session, SubmissionIn(conclusion="pass", confirm=True))
        except Exception as exc:  # noqa: BLE001 —— 断言错误码
            return str(exc)
    return ""


def test_submit_requires_fairness_passed():
    # 造会话的三个动作各自开一个事件循环（_seed_matrix 内部也是 asyncio.run），
    # 必须放在 _run 之外 —— 嵌套 asyncio.run 会直接 RuntimeError
    sid = _ready_session()
    msg = _run(_scenario_fairness_gate(sid))
    assert "workbench.fairness_required" in msg


def test_workbench_view_exposes_submission():
    """Step5 的扫描结论与面试结论随会话快照一起下发（前端不再单独拉一次）。"""
    sid = _ready_session()

    async def _inner():
        async with SessionLocal() as db:
            user = await db.get(User, IV_R1)
            return await wb.workbench_view(db, user, sid)

    out = _run(_inner())
    assert out.submission.result == "idle"
    assert out.submission.conclusion == ""
    assert out.submission.rag_hits == []
