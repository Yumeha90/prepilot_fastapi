"""P17 面评列表与详情（PRD §5.15 / BR-17）。

不走完整五步（要调 LLM 与向量库，测试既慢又不稳）：派单后直接把会话写成
「已提交」状态并塞一份面评产物，测的是**可见性与只读视图**这两件事。

BR-17 三条断言：
- HR / HR 主管 / 超管：全部面评
- 面试官：仅本人撰写的（他人面评连 URL 直达都 403）
- 未提交的是草稿，不出现在列表里
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.main import app

RESUME = """周八
邮箱 zhouba@example.com  电话 13700137000  上海
工作年限：5 年
2019-2022 E公司 后端工程师 交易系统 Java MySQL
2022-至今 F公司 高级工程师 服务治理 Kafka
技能：Java MySQL Kafka Redis
教育：某大学 计算机 本科"""

IV_R1 = 7  # 陈技术
IV_R2 = 8  # 刘架构


def _run(coro):
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


async def _new_position(client: AsyncClient, headers: dict[str, str]) -> int:
    created = await client.post(
        "/api/positions", json={"name": "pytest-面评职位"}, headers=headers
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "后端工程师",
            "hard_gates": ["本科及以上", "3 年以上后端经验"],
            "competencies": [
                {"text": "Java", "weight": 40},
                {"text": "数据库与 SQL 优化", "weight": 35},
                {"text": "消息队列", "weight": 25},
            ],
            "bonuses": [],
            "confirm": True,
        },
        headers=headers,
    )
    assert jd.status_code == 200, jd.text
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


async def _dispatch(client: AsyncClient, headers: dict[str, str], pid: int) -> tuple[int, int]:
    """上传 + 确认派单，返回 (候选人 id, 首轮会话 id)。"""
    mail = f"p17-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": "周八",
            "contact_email": mail,
            "contact_phone": "13700137000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    cid = resp.json()["id"]
    confirmed = await client.post(
        f"/api/candidates/{cid}/confirm",
        json={"profile": {"basic": {"name": "周八"}, "skills": ["Java"], "confidence": {}}},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return cid, confirmed.json()["sessions"][0]["id"]


EVAL_JSON = {
    "items": [
        {
            "row_id": "m1",
            "capability": "Java",
            "score": 4,
            "evidences": [{"id": "e1", "text": "讲清了 GC 调优过程", "quote": True}],
            "note": "基础扎实",
        },
        {
            "row_id": "m2",
            "capability": "数据库与 SQL 优化",
            "score": 3,
            "evidences": [{"id": "e2", "text": "索引失效排查说得比较笼统", "quote": False}],
            "note": "",
        },
    ],
    "summary": "整体达到高级工程师水平，数据库深度略欠。",
    "summary_original": "这人还行，数据库一般。",
    "complete": True,
}


async def _mark_submitted(session_id: int, *, purged: bool = False) -> None:
    """把会话直接写成已提交（跳过 LLM / 向量库）。"""
    import json

    data = dict(EVAL_JSON)
    if purged:
        data = {
            "items": [
                {"row_id": "m1", "capability": "Java", "score": 4, "evidences": [], "note": ""},
                {"row_id": "m2", "capability": "数据库与 SQL 优化", "score": 3, "evidences": [], "note": ""},
            ],
            "summary": "",
            "complete": True,
            "content_purged": True,
            "purged_at": datetime.now().isoformat(),
        }
    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE interview_sessions SET status='submitted', conclusion='pass', "
                "submitted_at=now(), evaluation_json=CAST(:payload AS jsonb), "
                "fairness_json=CAST(:fairness AS jsonb) WHERE id=:sid"
            ),
            {
                "payload": json.dumps(data, ensure_ascii=False),
                "fairness": json.dumps(
                    {"result": "pass", "scanned_at": datetime.now().isoformat()},
                    ensure_ascii=False,
                ),
                "sid": session_id,
            },
        )
        await db.commit()


async def _scenario() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        _, session_id = await _dispatch(client, hr, pid)

        # 未提交的草稿不出现在列表里（草稿不是面评）
        listed = await client.get("/api/evaluations", headers=hr)
        assert listed.status_code == 200, listed.text
        assert session_id not in [x["session_id"] for x in listed.json()]

        await _mark_submitted(session_id)

        # 1) HR 能看到全部已提交面评
        listed = await client.get("/api/evaluations", headers=hr)
        assert listed.status_code == 200
        row = next((x for x in listed.json() if x["session_id"] == session_id), None)
        assert row is not None, "HR 看不到已提交的面评"
        assert row["conclusion"] == "pass"
        assert row["position_name"]
        assert row["content_purged"] is False

        # 2) HR 详情：评分表 + 综合评价 + 双栏原文
        detail = await client.get(f"/api/evaluations/{session_id}", headers=hr)
        assert detail.status_code == 200, detail.text
        d = detail.json()
        assert len(d["items"]) == 2
        assert d["summary"] == EVAL_JSON["summary"]
        assert d["summary_original"] == EVAL_JSON["summary_original"]
        assert d["fairness"]["result"] == "pass"
        assert d["read_only"] is True

        # 3) 撰写本人（陈技术 = r1 面试官）能看到自己的面评
        iv = await _login(client, "interviewer@prepilot.dev")
        own = await client.get("/api/evaluations", headers=iv)
        assert own.status_code == 200
        assert session_id in [x["session_id"] for x in own.json()]
        mine = await client.get(f"/api/evaluations/{session_id}", headers=iv)
        assert mine.status_code == 200, mine.text

        # 4) BR-17：他人面评连 URL 直达都 403
        iv2 = await _login(client, "interviewer2@prepilot.dev")
        others = await client.get("/api/evaluations", headers=iv2)
        assert others.status_code == 200
        assert session_id not in [x["session_id"] for x in others.json()]
        denied = await client.get(f"/api/evaluations/{session_id}", headers=iv2)
        assert denied.status_code == 403, denied.text

        # 5) 粉碎后：行还在、正文清空、标记可见（BR-10 / v1.19）
        await _mark_submitted(session_id, purged=True)
        purged = await client.get(f"/api/evaluations/{session_id}", headers=hr)
        assert purged.status_code == 200
        pd = purged.json()
        assert pd["content_purged"] is True
        assert pd["summary"] == ""
        assert all(not it["evidences"] and it["note"] == "" for it in pd["items"])
        # 评分与能力项名是流程事实，必须保留
        assert [it["score"] for it in pd["items"]] == [4, 3]
        assert [it["capability"] for it in pd["items"]][0] == "Java"

        # 收尾：终结处置，别留在流程里污染别的用例
        board = await client.get("/api/board", headers=hr)
        app_id = next(
            c["application_id"]
            for col in board.json()["columns"]
            for c in col["cards"]
            if c["candidate_id"]
            and any(x["session_id"] == session_id for x in [{"session_id": session_id}])
        )
        done = await client.post(
            f"/api/board/applications/{app_id}/transition",
            json={"action": "reject"},
            headers=hr,
        )
        assert done.status_code == 200, done.text


async def _history_scenario() -> None:
    """推进到下一轮后，上一轮的面评还能不能找到（BR-16 + BR-17）。

    看板卡片只讲「当前卡在哪一轮」，所以推进后一面面评必然从卡片上消失 ——
    消失是对的，但入口必须另给一条：HR 拿全部，面试官只拿自己写的那份。
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        cid, r1_session = await _dispatch(client, hr, pid)
        await _mark_submitted(r1_session)

        board = await client.get("/api/board", headers=hr)
        assert board.status_code == 200
        app_id = next(
            c["application_id"]
            for col in board.json()["columns"]
            for c in col["cards"]
            if c["candidate_id"] == cid
        )

        # 推进前：卡片上就是当轮（r1）的面评，不该另有"历史"入口
        before = next(
            c
            for col in board.json()["columns"]
            for c in col["cards"]
            if c["application_id"] == app_id
        )
        assert before["session_id"] == r1_session
        assert before["history_evaluation_session_id"] is None

        advanced = await client.post(
            f"/api/board/applications/{app_id}/transition",
            json={"action": "advance"},
            headers=hr,
        )
        assert advanced.status_code == 200, advanced.text

        # 1) HR：卡片切到二面会话，一面面评改从 history 入口进
        board = await client.get("/api/board", headers=hr)
        card = next(
            c
            for col in board.json()["columns"]
            for c in col["cards"]
            if c["application_id"] == app_id
        )
        assert card["session_id"] != r1_session, "推进后卡片仍停在上一轮会话"
        assert card["history_evaluation_session_id"] == r1_session
        detail = await client.get(
            f"/api/evaluations/{card['history_evaluation_session_id']}", headers=hr
        )
        assert detail.status_code == 200, detail.text

        # 2) 一面面试官（陈技术）：自己写的那份仍要能看到（view_own）
        iv1 = await _login(client, "interviewer@prepilot.dev")
        b1 = await client.get("/api/board", headers=iv1)
        card1 = next(
            (
                c
                for col in b1.json()["columns"]
                for c in col["cards"]
                if c["application_id"] == app_id
            ),
            None,
        )
        assert card1 is not None, "一面面试官看不到自己面过的人了"
        assert card1["history_evaluation_session_id"] == r1_session

        # 3) 二面面试官（刘架构）：别人的历史面评连入口都不下发（BR-17）
        iv2 = await _login(client, "interviewer2@prepilot.dev")
        b2 = await client.get("/api/board", headers=iv2)
        card2 = next(
            (
                c
                for col in b2.json()["columns"]
                for c in col["cards"]
                if c["application_id"] == app_id
            ),
            None,
        )
        assert card2 is not None, "二面面试官看不到自己当轮的卡片"
        assert card2["session_id"] != r1_session
        assert card2["history_evaluation_session_id"] is None

        # 收尾
        done = await client.post(
            f"/api/board/applications/{app_id}/transition",
            json={"action": "reject"},
            headers=hr,
        )
        assert done.status_code == 200, done.text


def test_evaluation_visibility_and_detail():
    """P17：列表 + 详情 + BR-17 可见性 + 粉碎后仍可看结构。"""
    _run(_scenario())


def test_past_evaluation_reachable_after_advance():
    """推进到下一轮后，上一轮面评仍有入口：HR 全部，面试官仅本人（BR-16 / BR-17）。"""
    _run(_history_scenario())
