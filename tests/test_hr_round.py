"""轮次画像：同一份 JD + 简历，技术面和 HR 面要看的不是一回事（round_profile）。

锁三件事：

1. **HR 面的矩阵用 HR 维度，不用 JD 技术能力项**（用户实测反馈：HR 拿到
   「分布式事务：缺失」这种表，跟他这轮要判的事毫无关系）
2. **HR 面的问题链不检索组织技术资产库** —— 库里是技术文档与故障复盘，
   拿它去问「稳定性」只会逼模型编引用
3. **技术面的口径一个字都不能变**（r1/r2 仍检索、仍按 JD 能力项出题）

HR 会话走 service 层直接调用：接口层会因为「当前轮次还是 r1」拦下 HR 面的写操作
（`round_current=False`），那是 BR-16 的正确行为，但它挡住了这轮测试要验的东西 ——
轮次推进到 HR 面之后的行为，由用户在云端走主流程验证。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models.session import InterviewSession
from app.models.user import User
from app.services import question_chain as qc
from app.services import round_profile
from app.services import workbench as wb

COMPETENCIES = [
    {"text": "Java", "weight": 40},
    {"text": "数据库", "weight": 35},
    {"text": "微服务", "weight": 25},
]

RESUME = """郑十
邮箱 zhengshi@example.com  电话 13800138000  深圳
工作年限：5 年
2019-2022 G公司 后端工程师 订单系统 Java MySQL
2022-至今 H公司 高级工程师 微服务治理 Kubernetes Kafka
教育：某大学 软件工程 本科 2015-2019"""


def _run(coro):
    import asyncio

    from app.core.database import engine

    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def _login(client: AsyncClient, email: str = "hr@prepilot.dev") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _application_id(client: AsyncClient, headers: dict, candidate_id: int) -> int:
    board = await client.get("/api/board", headers=headers)
    for col in board.json()["columns"]:
        for card in col["cards"]:
            if card.get("candidate_id") == candidate_id:
                return int(card["application_id"])
    raise AssertionError("看板里找不到刚建的候选人")


async def _seed_sessions(client: AsyncClient) -> tuple[int, int]:
    """建一个「r1 + hr」两轮的职位与候选人，返回 (r1 会话 id, hr 会话 id)。

    建档那一刻只给当前轮（r1）派单，HR 会话要等推进到 hr 轮才建 ——
    所以这里走一次看板推进，而不是假设两个会话一开始都在。
    """
    hr = await _login(client)
    created = await client.post(
        "/api/positions", json={"name": f"pytest-轮次画像{uuid.uuid4().hex[:6]}"}, headers=hr
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "高级后端工程师",
            "hard_gates": ["本科及以上"],
            "competencies": COMPETENCIES,
            "bonuses": [],
            "confirm": True,
        },
        headers=hr,
    )
    assert jd.status_code == 200, jd.text

    rounds = await client.put(
        f"/api/positions/{pid}/rounds",
        json={
            "rounds": [
                {"type": "r1", "interviewer_id": 7},
                {"type": "hr", "interviewer_id": 3},
            ]
        },
        headers=hr,
    )
    assert rounds.status_code == 200, rounds.text

    cand = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": "郑十",
            "contact_email": f"rp-{uuid.uuid4().hex[:12]}@example.com",
            "contact_phone": "13800138000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=hr,
    )
    assert cand.status_code == 200, cand.text
    cid = cand.json()["id"]
    confirmed = await client.post(
        f"/api/candidates/{cid}/confirm",
        json={"profile": {"basic": {"name": "郑十"}, "skills": ["Java"], "confidence": {}}},
        headers=hr,
    )
    assert confirmed.status_code == 200, confirmed.text
    sessions = confirmed.json()["sessions"]
    r1_sid = next(s["id"] for s in sessions if s["round_type"] == "r1")

    app_id = await _application_id(client, hr, cid)
    advanced = await client.post(
        f"/api/board/applications/{app_id}/transition",
        json={"action": "advance"},
        headers=hr,
    )
    assert advanced.status_code == 200, advanced.text

    # 推进后卡片切到 hr 轮会话
    board = await client.get("/api/board", headers=hr)
    hr_sid = next(
        card["session_id"]
        for col in board.json()["columns"]
        for card in col["cards"]
        if card.get("application_id") == app_id
    )
    return r1_sid, hr_sid


def _hr_rows() -> "wb._MatrixLLM":
    return wb._MatrixLLM(
        rows=[
            wb._RowLLM(capability="稳定性与任职持久度", source="jd", evidence="两段各 3 年", status="sufficient", focus="核实跳槽原因"),
            wb._RowLLM(capability="薪资预期与匹配度", source="jd", evidence="", status="missing", focus="问期望区间"),
            wb._RowLLM(capability="到岗时间与入职约束", source="jd", evidence="", status="missing", focus="确认通知期"),
        ]
    )


def _tech_rows() -> "wb._MatrixLLM":
    return wb._MatrixLLM(
        rows=[
            wb._RowLLM(capability="Java", source="jd", evidence="高级工程师", status="sufficient", focus="GC 调优"),
            wb._RowLLM(capability="数据库", source="jd", evidence="MySQL", status="verify", focus="慢查询定位"),
        ]
    )


async def _generate_matrix(session_id: int, rows: "wb._MatrixLLM") -> dict:
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        assert session is not None
        user = await db.get(User, session.interviewer_id)
        captured: dict[str, str] = {}

        async def _fake(prompt: str, system: str = wb.MATRIX_SYSTEM_PROMPT) -> wb._MatrixLLM:
            captured["prompt"] = prompt
            captured["system"] = system
            return rows

        original = wb._call_llm
        wb._call_llm = _fake
        try:
            await wb.generate_matrix(db, user, session)
        finally:
            wb._call_llm = original
        return captured


async def _generate_chain(session_id: int) -> tuple[dict, int]:
    """返回 (捕获到的 prompt/system, 检索调用次数)。"""
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        assert session is not None
        user = await db.get(User, session.interviewer_id)
        captured: dict[str, str] = {}
        calls = {"n": 0}

        rows = (session.matrix_json or {}).get("rows") or []
        focus = qc.focus_rows(rows)

        def _fake_retrieve(capability: str, focus_text: str, top_k: int = 3):
            calls["n"] += 1
            return []

        async def _fake_llm(prompt: str, system: str = qc.CHAIN_SYSTEM_PROMPT) -> qc._ChainLLM:
            captured["prompt"] = prompt
            captured["system"] = system
            return qc._ChainLLM(
                nodes=[
                    qc._NodeLLM(
                        capability=str(r.get("capability") or ""),
                        main_question="讲一次具体经历",
                        followups=[qc._FollowupLLM(level=1, vague="当时怎么处理的", anti_fake="能核实吗")],
                        observations=["能说出量化结果", "能讲清取舍"],
                        minutes=8,
                    )
                    for r in focus
                ]
            )

        orig_retrieve, orig_llm = qc.retrieve, qc._call_llm
        qc.retrieve, qc._call_llm = _fake_retrieve, _fake_llm
        try:
            await qc.generate_chain(db, user, session)
        finally:
            qc.retrieve, qc._call_llm = orig_retrieve, orig_llm
        return captured, calls["n"]


# ---------------------------------------------------------------- 纯函数


def test_hr_dimensions_cover_the_basics():
    """六项维度是 HR 面的最小完备集：少了到岗约束会到发 offer 时才发现来不了。"""
    names = round_profile.hr_dimension_names()
    assert len(names) == 6
    joined = "".join(names)
    for must in ("稳定性", "抗压", "薪资", "离职原因", "协作", "到岗"):
        assert must in joined


def test_is_hr_round_only_matches_hr():
    assert round_profile.is_hr_round("hr")
    assert not round_profile.is_hr_round("r1")
    assert not round_profile.is_hr_round("r2")
    assert not round_profile.is_hr_round("unknown")
    assert not round_profile.is_hr_round(None)


def test_system_prompt_switches_by_round():
    assert wb.matrix_system_prompt("hr") == wb.MATRIX_SYSTEM_PROMPT_HR
    assert wb.matrix_system_prompt("r1") == wb.MATRIX_SYSTEM_PROMPT_TECH
    assert qc.chain_system_prompt("hr") == qc.CHAIN_SYSTEM_PROMPT_HR
    assert qc.chain_system_prompt("r2") == qc.CHAIN_SYSTEM_PROMPT_TECH
    # HR 版必须带合规红线，技术版不要求（它的红线由公平性扫描兜底）
    assert "婚育" in qc.CHAIN_SYSTEM_PROMPT_HR
    assert "内部资料" in qc.CHAIN_SYSTEM_PROMPT_TECH


# ---------------------------------------------------------------- 端到端（service 层）


def test_hr_matrix_prompt_uses_hr_dimensions():
    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _r1, hr_sid = await _seed_sessions(client)
            captured = await _generate_matrix(hr_sid, _hr_rows())
            return captured

    captured = _run(scenario())
    assert captured["system"] == wb.MATRIX_SYSTEM_PROMPT_HR
    assert "稳定性与任职持久度" in captured["prompt"]
    assert "薪资预期与匹配度" in captured["prompt"]
    # JD 技术能力项不该出现在 HR 面的输入里（简历原文里带 Java 是正常的）
    assert "【岗位核心能力项】" not in captured["prompt"]
    # 合规红线必须每次都喂给模型
    assert "婚育" in captured["prompt"]


def test_tech_matrix_prompt_unchanged():
    """技术面口径一个字都不许变：这条红了说明 HR 改动误伤了 r1/r2。"""

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1_sid, _hr = await _seed_sessions(client)
            return await _generate_matrix(r1_sid, _tech_rows())

    captured = _run(scenario())
    assert captured["system"] == wb.MATRIX_SYSTEM_PROMPT_TECH
    assert "【岗位核心能力项】" in captured["prompt"]
    assert "Java" in captured["prompt"]
    assert "稳定性与任职持久度" not in captured["prompt"]


def test_hr_chain_does_not_retrieve_tech_assets():
    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _r1, hr_sid = await _seed_sessions(client)
            await _generate_matrix(hr_sid, _hr_rows())
            return await _generate_chain(hr_sid)

    captured, calls = _run(scenario())
    # HR 面不检索组织技术资产库：库里没有「这个人稳不稳定」的资料，
    # 硬检索只会逼模型拿技术文档套 HR 维度，甚至为了凑 rag_refs 编引用
    assert calls == 0
    assert captured["system"] == qc.CHAIN_SYSTEM_PROMPT_HR
    assert "检索到的内部资料" not in captured["prompt"]
    assert "婚育" in captured["prompt"]


def test_tech_chain_still_retrieves_assets():
    """回归保护：HR 分支不能顺手把技术面的检索也关掉。"""

    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1_sid, _hr = await _seed_sessions(client)
            await _generate_matrix(r1_sid, _tech_rows())
            return await _generate_chain(r1_sid)

    captured, calls = _run(scenario())
    assert calls > 0
    assert captured["system"] == qc.CHAIN_SYSTEM_PROMPT_TECH
    assert "检索到的内部资料" in captured["prompt"]
