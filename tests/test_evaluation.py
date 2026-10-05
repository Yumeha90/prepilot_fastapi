"""AI 备面工作台 Step 4 行为化评分与面评（PRD §3.4.4 / §6.6 P12，C6）。

LLM 一律 monkeypatch（`evaluation._call_llm`），测试锁的是**口径**而不是模型输出：

- **写权限只给被指派的面试官**（HR 保存 → 400），会话提交后谁都不能写
- **自动保存不拦截不完整内容**：写一半就被拦在门外，等于逼面试官先编一条证据出来
- **完整性按「有分 + 有证据或笔记」算**（BR-07 口径），缺失项要说清缺的是分还是依据
- **润色稿不覆盖原文**（BR-08），采纳时才覆写，且原文留进 `summary_original`
- **润色稿过二次合规扫描**：面评里写出「年纪偏大」同样要被标出来
- 评分越界与孤儿 row_id 一律丢弃，不留「看起来有效」的假数据
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.session import InterviewSession
from app.models.user import User
from app.services import evaluation as ev
from app.services import workbench as wb

# seed：7 = interviewer（陈技术），5 = hr
IV_R1 = 7
HR = 5

COMPETENCIES = [
    {"text": "系统设计", "weight": 40},
    {"text": "编码能力", "weight": 35},
    {"text": "协作沟通", "weight": 25},
]


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_matrix() -> int:
    """造一个「有矩阵」的会话（不依赖 Celery worker）。"""

    async def _inner() -> int:
        from test_question_chain import _new_position, _session_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            hr = await _login(client, "hr@prepilot.dev")
            pid = await _new_position(client, hr)
            sid = await _session_id(client, hr, pid)

        async def _fake_matrix(prompt: str, system: str = wb.MATRIX_SYSTEM_PROMPT):
            return wb._MatrixLLM(
                rows=[
                    wb._RowLLM(capability=c["text"], source="jd", evidence="", status="verify", focus="")
                    for c in COMPETENCIES
                ]
            )

        original = wb._call_llm
        wb._call_llm = _fake_matrix
        try:
            async with SessionLocal() as db:
                session = await db.get(InterviewSession, sid)
                user = await db.get(User, IV_R1)
                await wb.generate_matrix(db, user, session)
        finally:
            wb._call_llm = original
        return sid

    return _run(_inner())


def _items(sid: int) -> list[dict]:
    async def _inner() -> list[dict]:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return [i.model_dump() for i in ev.evaluation_out(session).items]

    return _run(_inner())


def _save(sid: int, payload: dict, user_id: int = IV_R1):
    from app.schemas.workbench import EvaluationIn

    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, user_id)
            return await ev.save_draft(db, user, session, EvaluationIn(**payload))

    return _run(_inner())


# ---------------------------------------------------------------- 评分表与保存


def test_items_follow_matrix_rows():
    """评分表跟随矩阵：矩阵行变了，评分项跟着变（孤儿项保留在末尾）。"""
    sid = _seed_matrix()
    items = _items(sid)
    assert [i["capability"] for i in items] == ["系统设计", "编码能力", "协作沟通"]
    assert all(i["score"] is None for i in items)
    assert all(not i["complete"] for i in items)


def test_save_does_not_block_incomplete_content():
    """自动保存不拦截不完整内容 —— 拦了等于逼面试官先编一条证据出来。"""
    sid = _seed_matrix()
    out = _save(
        sid,
        {
            "items": [
                {
                    "row_id": _items(sid)[0]["row_id"],
                    "capability": "系统设计",
                    "score": 4,
                    "evidences": [{"id": "e1", "text": "讲清了限流与熔断的取舍", "quote": True}],
                    "note": "",
                }
            ],
            "summary": "整体不错",
        },
    )
    assert out.stale is False
    assert out.evaluation.incomplete_count == 2
    assert out.evaluation.complete is False
    assert out.evaluation.summary == "整体不错"


def test_missing_reason_distinguishes_score_and_evidence():
    """缺什么要说清：没打分 vs 没写依据，一句话混着说面试官不知道该补哪个。"""
    sid = _seed_matrix()
    rows = _items(sid)
    _save(
        sid,
        {
            "items": [
                {"row_id": rows[0]["row_id"], "capability": "系统设计", "score": 4, "evidences": [], "note": ""},
                {"row_id": rows[1]["row_id"], "capability": "编码能力", "score": None, "evidences": [], "note": "写得挺快"},
            ],
            "summary": "",
        },
    )
    items = {i["capability"]: i for i in _items(sid)}
    assert items["系统设计"]["missing"] == ["no_evidence"]
    assert items["编码能力"]["missing"] == ["no_score"]
    assert items["协作沟通"]["missing"] == ["no_score", "no_evidence"]


def test_out_of_range_score_is_dropped():
    """越界评分直接丢弃而不是夹到边界：夹出来的是一个「看起来有效」的假分数。"""
    sid = _seed_matrix()
    rows = _items(sid)
    _save(
        sid,
        {
            "items": [{"row_id": rows[0]["row_id"], "capability": "系统设计", "score": 99, "evidences": [], "note": "x"}],
            "summary": "",
        },
    )
    assert _items(sid)[0]["score"] is None


def test_orphan_row_is_dropped():
    """对不上矩阵行的项不落库：存进去再也渲染不出来。"""
    sid = _seed_matrix()
    _save(
        sid,
        {"items": [{"row_id": "不存在", "capability": "幽灵项", "score": 3, "evidences": [], "note": "x"}], "summary": ""},
    )
    assert [i["capability"] for i in _items(sid)] == ["系统设计", "编码能力", "协作沟通"]


def test_evidence_quote_flag_is_kept():
    """「是否引用原话」要留住：复述与原话的可信度不同，HR 复核时看得出来。"""
    sid = _seed_matrix()
    rows = _items(sid)
    _save(
        sid,
        {
            "items": [
                {
                    "row_id": rows[0]["row_id"],
                    "capability": "系统设计",
                    "score": 4,
                    "evidences": [
                        {"id": "e1", "text": "当时 QPS 从 2 千涨到 1 万", "quote": True},
                        {"id": "e2", "text": "他说自己主导了重构", "quote": False},
                    ],
                    "note": "",
                }
            ],
            "summary": "",
        },
    )
    evidences = _items(sid)[0]["evidences"]
    assert [e["quote"] for e in evidences] == [True, False]


def _set_status(sid: int, status: str) -> None:
    """直接把会话推到指定状态：省掉为测状态机而重跑一遍前面几个步骤。"""
    import sqlalchemy as sa

    async def _inner() -> None:
        async with SessionLocal() as db:
            await db.execute(
                sa.text("UPDATE interview_sessions SET status = :s WHERE id = :i"),
                {"s": status, "i": sid},
            )
            await db.commit()

    _run(_inner())


def test_save_advances_status_to_s4():
    """有评分产物即推进 s3 → s4；前面的步骤没做完时**不抢跑**到 s4。"""
    sid = _seed_matrix()
    rows = _items(sid)
    payload = {
        "items": [{"row_id": rows[0]["row_id"], "capability": "系统设计", "score": 4, "evidences": [], "note": "不错"}],
        "summary": "",
    }
    _save(sid, payload)

    async def _status() -> str:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return session.status

    # 只生成了矩阵（s2），直接存评分不该把会话跳到 s4
    assert _run(_status()) == "s2_draft"
    _set_status(sid, "s3_draft")
    _save(sid, payload)
    assert _run(_status()) == "s4_draft"


def test_hr_cannot_write():
    """只有被指派的面试官能写（HR 一律只读）。"""
    sid = _seed_matrix()
    rows = _items(sid)
    with pytest.raises(Exception) as exc:
        _save(
            sid,
            {"items": [{"row_id": rows[0]["row_id"], "capability": "系统设计", "score": 4}], "summary": ""},
            user_id=HR,
        )
    assert "workbench.read_only" in str(exc.value)


# ---------------------------------------------------------------- C6 润色


def _full_payload(sid: int) -> dict:
    rows = _items(sid)
    return {
        "items": [
            {"row_id": r["row_id"], "capability": r["capability"], "score": 4, "evidences": [], "note": "讲清了取舍"}
            for r in rows
        ],
        "summary": "整体还行，系统设计的边界考虑不足",
    }


def test_polish_does_not_overwrite_original(monkeypatch):
    """BR-08：润色稿并存，不覆盖原文。"""
    sid = _seed_matrix()
    _save(sid, _full_payload(sid))

    async def _fake_llm(prompt: str) -> ev._PolishLLM:
        return ev._PolishLLM(
            summary="具备扎实的后端设计能力，边界场景交代不足。",
            items=[ev._ItemLLM(capability="系统设计", score=4, evidence="讲清了限流与熔断的取舍")],
            recommendation="hold",
            risks=["沟通项 4 分但系统设计 2 分"],
        )

    monkeypatch.setattr(ev, "_call_llm", _fake_llm)

    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await ev.polish(db, user, session)
            return out

    out = _run(_inner())
    assert out.summary == "整体还行，系统设计的边界考虑不足"  # 原文还在
    assert "【总评】" in out.polished
    assert "【综合建议】待定" in out.polished
    assert "【风险提示】" in out.polished
    assert out.polished_adopted is False
    assert out.recommendation == "hold"


def test_polish_scans_compliance_again(monkeypatch):
    """二次合规扫描：面评里写出「年纪偏大」同样要被标出来。"""
    sid = _seed_matrix()
    _save(sid, _full_payload(sid))

    async def _fake_llm(prompt: str) -> ev._PolishLLM:
        return ev._PolishLLM(
            summary="候选人基础扎实，但年纪偏大可能学不动新框架。",
            items=[ev._ItemLLM(capability="系统设计", score=4, evidence="讲清了取舍")],
            recommendation="reject",
            risks=[],
        )

    monkeypatch.setattr(ev, "_call_llm", _fake_llm)

    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            return await ev.polish(db, user, session)

    out = _run(_inner())
    assert [f.category for f in out.polished_flags] == ["age"]
    assert out.recommendation == "reject"


def test_adopt_overwrites_summary_and_keeps_original(monkeypatch):
    """采纳才覆写，且原文留进 summary_original（P17 双栏对比要用）。"""
    sid = _seed_matrix()
    _save(sid, _full_payload(sid))

    async def _fake_llm(prompt: str) -> ev._PolishLLM:
        return ev._PolishLLM(summary="结构化后的总评。", items=[], recommendation="proceed", risks=[])

    monkeypatch.setattr(ev, "_call_llm", _fake_llm)

    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            await ev.polish(db, user, session)
            return await ev.adopt_polished(db, user, session)

    out = _run(_inner())
    assert out.polished_adopted is True
    assert "结构化后的总评" in out.summary
    assert out.summary_original == "整体还行，系统设计的边界考虑不足"


def test_polish_requires_content(monkeypatch):
    """一个分数一条笔记都没有就润色，等于让 AI 编一份面评出来。"""
    sid = _seed_matrix()
    with pytest.raises(Exception) as exc:
        _run(_polish(sid))
    assert "workbench.eval_empty" in str(exc.value)


async def _polish(sid: int):
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, sid)
        user = await db.get(User, IV_R1)
        return await ev.polish(db, user, session)


def test_adopt_requires_polished():
    sid = _seed_matrix()
    _save(sid, _full_payload(sid))

    async def _inner():
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            return await ev.adopt_polished(db, user, session)

    with pytest.raises(Exception) as exc:
        _run(_inner())
    assert "workbench.eval_no_polished" in str(exc.value)


# ---------------------------------------------------------------- 完整性与步骤


def test_complete_flag_after_all_items_filled():
    """全填完 → complete=True，步骤指示器上 Step4 变「已完成」。"""
    sid = _seed_matrix()
    _set_status(sid, "s3_draft")
    payload = {
        "items": [
            {"row_id": r["row_id"], "capability": r["capability"], "score": 4, "evidences": [], "note": "有依据"}
            for r in _items(sid)
        ],
        "summary": "整体不错",
    }
    out = _save(sid, payload)
    assert out.evaluation.complete is True
    assert out.evaluation.incomplete_count == 0

    async def _inner() -> list[tuple[str, str]]:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            return [(s.key, s.state) for s in wb._steps(session)]

    steps = _run(_inner())
    assert dict(steps)["step4"] == "done"


def test_render_polished_keeps_interviewer_score():
    """评分以面试官给的为准：模型无权改分（评分是他的判断）。"""
    from app.schemas.workbench import EvaluationItem

    items = [EvaluationItem(row_id="m1", capability="系统设计", score=2)]
    data = ev._PolishLLM(
        summary="总评。",
        items=[ev._ItemLLM(capability="系统设计", score=5, evidence="讲清了取舍")],
        recommendation="hold",
        risks=[],
    )
    text = ev.render_polished(data, items)
    assert "2/5" in text
    assert "5/5" not in text
