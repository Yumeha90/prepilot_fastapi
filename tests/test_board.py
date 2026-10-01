"""候选人看板 P08 与阶段流转（PRD 3.3.3 / §5.8）。

接口链路走 httpx + ASGITransport + 单个 asyncio.run，全程不碰 LLM。
重点锁死三件事：
- **BR-15 列可见性**：面试官只能拿到 r1 / r2，且列由后端下发
- **推进按流程配置自动找下一轮并派单**，不是人选
- **offer 轮不建会话**（终结处置节点，不产生面评）
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app

RESUME = """吴九
邮箱 wuqiu@example.com  电话 13700137000  上海
工作年限：7 年
2017-2020 E公司 后端工程师 交易系统 Go MySQL
2020-至今 F公司 资深工程师 服务治理 Kubernetes
项目：清算系统重构 负责人 Java Redis
技能：Java Redis Kafka MySQL Kubernetes
教育：某大学 计算机 本科 2013-2017"""

# seed：1 admin / 3 hr_lead / 5 hr / 7 interviewer（陈技术）/ 8 interviewer2（刘架构）
IV_R1 = 7
IV_R2 = 8
HR_LEAD = 3


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


async def _new_position(
    client: AsyncClient, headers: dict[str, str], rounds: list[dict]
) -> int:
    created = await client.post(
        "/api/positions", json={"name": f"pytest-看板{uuid.uuid4().hex[:6]}"}, headers=headers
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "高级后端工程师",
            "hard_gates": ["本科及以上"],
            "competencies": [
                {"text": "Java", "weight": 40},
                {"text": "数据库", "weight": 35},
                {"text": "微服务", "weight": 25},
            ],
            "bonuses": [],
            "confirm": True,
        },
        headers=headers,
    )
    assert jd.status_code == 200, jd.text

    if rounds:
        resp = await client.put(
            f"/api/positions/{pid}/rounds", json={"rounds": rounds}, headers=headers
        )
        assert resp.status_code == 200, resp.text
    return pid


async def _confirm_candidate(
    client: AsyncClient, headers: dict[str, str], pid: int, name: str = "吴九"
) -> tuple[int, int]:
    """建候选人 → 确认（确认即派单）。返回 `(candidate_id, application_id)`。"""
    mail = f"board-{uuid.uuid4().hex[:12]}@example.com"
    created = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": name,
            "contact_email": mail,
            "contact_phone": "13700137000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]

    confirmed = await client.post(
        f"/api/candidates/{cid}/confirm",
        json={"profile": {"basic": {"name": name}, "skills": ["Java"], "confidence": {}}},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return cid, confirmed.json()["applications"][0]["id"]


async def _transition(
    client: AsyncClient, headers: dict[str, str], app_id: int, action: str
):
    return await client.post(
        f"/api/board/applications/{app_id}/transition",
        json={"action": action},
        headers=headers,
    )


# ---------------------------------------------------------------- 列可见性


async def _scenario_columns() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(
            client,
            headers,
            [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "r2", "interviewer_id": IV_R2},
                {"type": "hr", "interviewer_id": HR_LEAD},
            ],
        )
        _, app_id = await _confirm_candidate(client, headers, pid)

        # HR：8 列，8 个动作
        hr = await client.get("/api/board", headers=headers)
        assert hr.status_code == 200, hr.text
        stages = [c["stage"] for c in hr.json()["columns"]]
        assert stages == [
            "pending",
            "in_r1",
            "in_r2",
            "in_hr",
            "in_offer",
            "rejected",
            "in_pool",
            "archived",
        ]
        assert hr.json()["actions"] == [
            "advance",
            "rollback",
            "accept",
            "reject",
            "pool",
            "archive",
        ]

        # 面试官：只有 r1 / r2，且没有处置动作
        iv = await _login(client, "interviewer@prepilot.dev")
        board = await client.get("/api/board", headers=iv)
        assert board.status_code == 200
        assert [c["stage"] for c in board.json()["columns"]] == ["in_r1", "in_r2"]
        assert board.json()["actions"] == []
        assert any(c["application_id"] == app_id for c in board.json()["columns"][0]["cards"])

        # r2 面试官此刻还没派给他：r2 列应为空（BR-14）
        iv2 = await _login(client, "interviewer2@prepilot.dev")
        board2 = await client.get("/api/board", headers=iv2)
        assert board2.json()["columns"][0]["cards"] == []  # r1 列
        assert board2.json()["columns"][1]["cards"] == []  # r2 列


def test_board_columns_differ_by_role():
    _run(_scenario_columns())


# ---------------------------------------------------------------- 推进 / 退回


async def _scenario_advance() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(
            client,
            headers,
            [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "r2", "interviewer_id": IV_R2},
                {"type": "hr", "interviewer_id": HR_LEAD},
            ],
        )
        _, app_id = await _confirm_candidate(client, headers, pid)

        # 推进：in_r1 → in_r2，面试官换成 r2 的刘架构
        adv = await _transition(client, headers, app_id, "advance")
        assert adv.status_code == 200, adv.text
        assert adv.json()["stage"] == "in_r2"

        board = await client.get("/api/board", params={"position_id": pid}, headers=headers)
        r2_cards = [c for c in board.json()["columns"] if c["stage"] == "in_r2"][0]["cards"]
        assert len(r2_cards) == 1
        assert r2_cards[0]["interviewer_name"] == "刘架构"
        assert r2_cards[0]["session_status"] == "s1_draft"

        # 退回：in_r2 → in_r1，**复用**原来的会话（不新建）
        sessions_before = len(
            (await client.get("/api/sessions", params={"position_id": pid}, headers=headers)).json()
        )
        back = await _transition(client, headers, app_id, "rollback")
        assert back.status_code == 200, back.text
        assert back.json()["stage"] == "in_r1"
        sessions_after = len(
            (await client.get("/api/sessions", params={"position_id": pid}, headers=headers)).json()
        )
        assert sessions_after == sessions_before

        # 一路推进到 hr，再推进 → 已是最后一轮，要求做终结处置
        await _transition(client, headers, app_id, "advance")  # → in_r2
        to_hr = await _transition(client, headers, app_id, "advance")  # → in_hr
        assert to_hr.json()["stage"] == "in_hr"
        over = await _transition(client, headers, app_id, "advance")
        assert over.status_code == 400
        assert over.json()["detail"]["code"] == "candidate.stage_invalid"


def test_advance_follows_round_config_and_rollback_reuses_session():
    _run(_scenario_advance())


async def _scenario_offer_round() -> None:
    """offer 是终结处置节点：推进到 offer 不建会话（PRD 3.2）。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(
            client,
            headers,
            [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "offer", "interviewer_id": HR_LEAD},
            ],
        )
        _, app_id = await _confirm_candidate(client, headers, pid, name="郑十")

        to_offer = await _transition(client, headers, app_id, "advance")
        assert to_offer.status_code == 200, to_offer.text
        assert to_offer.json()["stage"] == "in_offer"

        sessions = (
            await client.get("/api/sessions", params={"position_id": pid}, headers=headers)
        ).json()
        assert [s["round_type"] for s in sessions] == ["r1"]


def test_offer_round_creates_no_session():
    _run(_scenario_offer_round())


# ---------------------------------------------------------------- 终结处置


async def _scenario_settle() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(
            client,
            headers,
            [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "hr", "interviewer_id": HR_LEAD},
            ],
        )
        _, app_id = await _confirm_candidate(client, headers, pid, name="钱一")

        rejected = await _transition(client, headers, app_id, "reject")
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["stage"] == "rejected"

        board = await client.get("/api/board", params={"position_id": pid}, headers=headers)
        cols = {c["stage"]: c["cards"] for c in board.json()["columns"]}
        assert [c["application_id"] for c in cols["rejected"]] == [app_id]
        assert cols["in_r1"] == []

        # 已终结处置的不能推进 / 退回
        again = await _transition(client, headers, app_id, "advance")
        assert again.status_code == 400
        assert again.json()["detail"]["code"] == "candidate.stage_invalid"

        # 录用后离开看板列，只进 summary 计数
        _, app_id2 = await _confirm_candidate(client, headers, pid, name="赵二")
        accepted = await _transition(client, headers, app_id2, "accept")
        assert accepted.json()["stage"] == "accepted"
        board = await client.get("/api/board", params={"position_id": pid}, headers=headers)
        assert board.json()["summary"].get("accepted") == 1
        stages = [c["stage"] for c in board.json()["columns"]]
        assert "accepted" not in stages


def test_settle_moves_card_and_accepted_only_counts():
    _run(_scenario_settle())


async def _scenario_interviewer_cannot_dispose() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(
            client,
            headers,
            [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "hr", "interviewer_id": HR_LEAD},
            ],
        )
        _, app_id = await _confirm_candidate(client, headers, pid, name="孙甲")

        iv = await _login(client, "interviewer@prepilot.dev")
        resp = await _transition(client, iv, app_id, "advance")
        assert resp.status_code == 403


def test_interviewer_cannot_transition():
    _run(_scenario_interviewer_cannot_dispose())


async def _scenario_no_rounds() -> None:
    """没配流程就推进 → 400，HR 拿得到明确原因。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, [])
        cid, app_id = await _confirm_candidate(client, headers, pid, name="周乙")
        board = await client.get("/api/board", params={"position_id": pid}, headers=headers)
        cols = {c["stage"]: c["cards"] for c in board.json()["columns"]}
        assert [c["application_id"] for c in cols["pending"]] == [app_id]

        resp = await _transition(client, headers, app_id, "advance")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "session.no_round"


def test_advance_without_rounds_rejected():
    _run(_scenario_no_rounds())
