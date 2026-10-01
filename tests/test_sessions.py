"""派单与会话测试（PRD 3.3 第二段：确认即派单 + 会话骨架）。

与 test_candidates 同构：接口链路走 httpx + ASGITransport + 单个 asyncio.run，
AI 全部不参与（本阶段没有 LLM 调用）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.core.errors import AppError
from app.main import app
from app.models.candidate import Application
from app.models.position import PositionRound
from app.services import dispatch as dispatch_svc

RESUME = """孙七
邮箱 sunqi@example.com  电话 13600136000  北京
工作年限：6 年
2018-2021 C公司 后端工程师 订单系统 Go MySQL
2021-至今 D公司 资深工程师 微服务治理 Kubernetes
项目：支付网关重构 负责人 Java Redis
技能：Java Redis Kafka MySQL Kubernetes
教育：某大学 软件工程 硕士 2015-2018"""

# seed：1 admin / 3 hr_lead / 5 hr / 7 interviewer（陈技术）/ 8 interviewer2（刘架构）
IV_R1 = 7
IV_R2 = 8


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


# ---------------------------------------------------------------- 纯函数


def test_pick_first_round_skips_offer():
    """首轮 = seq 最小且不是 offer 的轮次。offer 是终结节点，不能当首轮派出去。"""
    rounds = [
        PositionRound(position_id=1, seq=1, type="offer", interviewer_id=IV_R1),
        PositionRound(position_id=1, seq=2, type="r1", interviewer_id=IV_R1),
        PositionRound(position_id=1, seq=3, type="hr", interviewer_id=3),
    ]
    assert dispatch_svc.pick_first_round(rounds).type == "r1"


def test_pick_first_round_requires_interview_round():
    """只有 offer 轮 → 无从派单。"""
    with pytest.raises(AppError) as exc:
        dispatch_svc.pick_first_round(
            [PositionRound(position_id=1, seq=1, type="offer", interviewer_id=IV_R1)]
        )
    assert exc.value.detail["code"] == "session.no_round"


def test_pick_first_round_rejects_empty():
    with pytest.raises(AppError) as exc:
        dispatch_svc.pick_first_round([])
    assert exc.value.detail["code"] == "session.no_round"


# ---------------------------------------------------------------- 接口链路


async def _login(client: AsyncClient, email: str = "hr@prepilot.dev") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _new_position(
    client: AsyncClient, headers: dict[str, str], *, with_rounds: bool
) -> int:
    created = await client.post(
        "/api/positions", json={"name": "pytest-派单职位"}, headers=headers
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "高级后端工程师",
            "hard_gates": ["本科及以上", "3 年以上后端经验"],
            "competencies": [
                {"text": "Java", "weight": 40},
                {"text": "数据库与 SQL 优化", "weight": 35},
                {"text": "微服务与消息队列", "weight": 25},
            ],
            "bonuses": [],
            "confirm": True,
        },
        headers=headers,
    )
    assert jd.status_code == 200, jd.text

    if with_rounds:
        rounds = await client.put(
            f"/api/positions/{pid}/rounds",
            json={
                "rounds": [
                    {"type": "r1", "interviewer_id": IV_R1},
                    {"type": "r2", "interviewer_id": IV_R2},
                    {"type": "hr", "interviewer_id": 3},
                ]
            },
            headers=headers,
        )
        assert rounds.status_code == 200, rounds.text
    return pid


async def _upload(
    client: AsyncClient, headers: dict[str, str], pid: int, name: str = "孙七"
) -> int:
    mail = f"s2-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": name,
            "contact_email": mail,
            "contact_phone": "13600136000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _scenario_dispatch() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, with_rounds=True)
        cid = await _upload(client, headers, pid)

        # 1) D5 确认即派单：阶段推进 + 建首轮会话
        confirmed = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": "孙七"}, "skills": ["Java"], "confidence": {}}},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["dispatch_notice"] == ""
        app_out = body["applications"][0]
        assert app_out["stage"] == "in_r1"
        assert app_out["interviewer_name"] == "陈技术"

        assert len(body["sessions"]) == 1
        s = body["sessions"][0]
        assert s["round_type"] == "r1"
        assert s["round_name"] == "技术一面"  # 轮次名称快照
        assert s["interviewer_name"] == "陈技术"
        assert s["status"] == "s1_draft"

        # 2) 面试官（r1）能看到候选人与自己的会话
        iv = await _login(client, "interviewer@prepilot.dev")
        # 面试官能在看板（r1 列）看到派给自己的候选人
        board = await client.get("/api/board", headers=iv)
        assert cid in [
            c["candidate_id"] for col in board.json()["columns"] for c in col["cards"]
        ]

        sessions = await client.get("/api/sessions", headers=iv)
        assert sessions.status_code == 200
        # 库里可能有历史会话，只断言「派给他的这条在里面」
        assert s["id"] in [x["id"] for x in sessions.json()]

        notes = await client.get(
            "/api/me/notifications", params={"unread": "true"}, headers=iv
        )
        assert any(n["type"] == "assignment" for n in notes.json())

        # 3) r2 面试官不该看到还没派给他的会话（BR-16）
        iv2 = await _login(client, "interviewer2@prepilot.dev")
        sessions2 = await client.get("/api/sessions", headers=iv2)
        assert s["id"] not in [x["id"] for x in sessions2.json()]

        # 4) 重复派单 → 拦截（同一个 candidate 再派一次）
        dup = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": "孙七"}, "skills": ["Java"], "confidence": {}}},
            headers=headers,
        )
        assert dup.status_code == 400
        assert dup.json()["detail"]["code"] == "candidate.already_confirmed"

def test_confirm_dispatches_first_round():
    """D5 确认即派单全链路。"""
    _run(_scenario_dispatch())


async def _scenario_confirm_without_rounds() -> None:
    """职位没配轮次：确认照常生效，派单失败只回原因，候选人停在待派单。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, with_rounds=False)
        cid = await _upload(client, headers, pid, name="周八")

        confirmed = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": "周八"}, "skills": [], "confidence": {}}},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        # 确认本身生效（不回滚派单以外已经落库的部分）
        assert body["profile_status"] == "confirmed"
        assert body["applications"][0]["stage"] == "pending"
        assert body["sessions"] == []
        assert "面试轮次" in body["dispatch_notice"]


def test_confirm_without_rounds_keeps_pending():
    _run(_scenario_confirm_without_rounds())


async def _scenario_dispatch_twice() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, with_rounds=True)
        cid = await _upload(client, headers, pid)

    # 出了 httpx 上下文再开 DB 会话：ASGITransport 内部有自己的 portal，
    # 在它里面套 SessionLocal 会踩 MissingGreenlet
    async with SessionLocal() as db:
        application = (
            await db.scalars(select(Application).where(Application.candidate_id == cid))
        ).first()
        await dispatch_svc.dispatch_first_round(db, application)
        await db.commit()

        with pytest.raises(AppError) as exc:
            await dispatch_svc.dispatch_first_round(db, application)
        assert exc.value.detail["code"] == "session.already_dispatched"


def test_dispatch_twice_rejected():
    """同一条应聘记录不能重复派单。"""
    _run(_scenario_dispatch_twice())
