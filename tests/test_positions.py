"""职位模块测试（PRD 3.2 第一段：CRUD + 复制 + 删除）。

分两类：
1) 纯函数：jd_hash 规范化、BR-20 权重校验、BR-23 轮次约束 —— 不需要 DB；
2) 接口链路：用 hr@prepilot.dev 登录跑一遍「建 → 配 JD → 配流程 → 发布 → 复制 → 关 → 删」，
   依赖本地 PG（55432）与 seed 数据。

⚠️ 接口链路**不能用 starlette 的 TestClient**：它每个请求新开一个 event loop，
   异步 SQLAlchemy 连接池跨 loop 复用会报
   "got Future attached to a different loop"。
   这里改用 httpx + ASGITransport，在**单个 asyncio.run 的 loop 内**跑完整场
   景，跑完 dispose 掉引擎，保证连接不跨 loop 残留。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app
from app.schemas.position import CompetencyIn, RoundIn
from app.services.position import (
    even_weights,
    jd_hash_of,
    normalize_items,
    validate_jd,
    validate_rounds,
)


def _run(coro):
    """在单个事件循环内执行协程，结束后释放连接池（避免跨 loop 残留）。"""

    async def wrapper():
        # 先清空连接池：其他用例（如 TestClient 冒烟）可能在已关闭的 loop 里
        # 建过连接，跨 loop 复用会直接报 "attached to a different loop"。
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


# ---------------------------------------------------------------- 纯函数


def test_jd_hash_ignores_row_order():
    """行序只影响展示，▲/▼ 调序不应触发版本 bump。"""
    a = [CompetencyIn(text="Go", weight=40), CompetencyIn(text="SQL", weight=60)]
    b = [CompetencyIn(text="SQL", weight=60), CompetencyIn(text="Go", weight=40)]
    assert jd_hash_of(["本科"], a, []) == jd_hash_of(["本科"], b, [])


def test_jd_hash_changes_on_weight_change():
    a = [CompetencyIn(text="Go", weight=40), CompetencyIn(text="SQL", weight=60)]
    b = [CompetencyIn(text="Go", weight=50), CompetencyIn(text="SQL", weight=50)]
    assert jd_hash_of(["本科"], a, []) != jd_hash_of(["本科"], b, [])


def test_normalize_items_cleans_and_caps():
    """实测模型偶发输出 `L本科及以上` 这类脏前缀，必须被清洗；每类 ≤5 项。"""
    out = normalize_items(["L本科及以上", " 3 年以上经验 ", "3 年以上经验", ""], "gate")
    assert out == ["本科及以上", "3 年以上经验"]
    assert len(normalize_items([f"x{i}" for i in range(9)], "gate")) == 5


def test_br20_weight_rules():
    ok = [CompetencyIn(text=f"c{i}", weight=w) for i, w in enumerate([40, 35, 25])]
    validate_jd(["本科"], ok)

    with pytest.raises(Exception):
        validate_jd(["本科"], [CompetencyIn(text="a", weight=40)])
    with pytest.raises(Exception):
        validate_jd(["本科"], [CompetencyIn(text="a", weight=37)] * 3)
    with pytest.raises(Exception):
        validate_jd([], ok)


def test_even_weights():
    assert even_weights(3) == [34, 33, 33]
    assert even_weights(4) == [25, 25, 25, 25]
    assert sum(even_weights(7)) == 100


def _rounds(*types, interviewer: int | None = 1) -> list[RoundIn]:
    return [RoundIn(type=t, interviewer_id=interviewer) for t in types]


def test_br23_rounds():
    validate_rounds([])
    validate_rounds(_rounds("r1", "r2", "hr"))
    validate_rounds(_rounds("r1", "r2", "offer"))  # 允许跳过 HR 面

    with pytest.raises(Exception):
        validate_rounds(_rounds("r1", "r2", "hr", "offer", "r1"))  # 超过 4 轮
    with pytest.raises(Exception):
        validate_rounds(_rounds("r1", "r1", "hr"))  # 同类型重复
    with pytest.raises(Exception):
        validate_rounds(_rounds("r1", "r2"))  # 末位不是 hr/offer
    with pytest.raises(Exception):
        validate_rounds(_rounds("r2", "hr"))  # r2 前必须有 r1
    with pytest.raises(Exception):
        validate_rounds(_rounds("r1", "hr", interviewer=None))  # 缺面试官


# ---------------------------------------------------------------- 接口链路


async def _login(client: AsyncClient, email: str = "hr@prepilot.dev") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _scenario_lifecycle() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)

        created = await client.post(
            "/api/positions", json={"name": "pytest-职位"}, headers=headers
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]

        listed = await client.get("/api/positions", headers=headers)
        assert listed.status_code == 200
        assert any(p["id"] == pid for p in listed.json()["items"])

        # JD：确认后生效（BR-01），版本号 +1
        jd = await client.put(
            f"/api/positions/{pid}/jd",
            json={
                "raw_text": "中级后端工程师",
                "hard_gates": ["本科及以上", "3 年以上后端经验"],
                "competencies": [
                    {"text": "Go/Python", "weight": 40},
                    {"text": "数据库与 SQL 优化", "weight": 35},
                    {"text": "微服务与消息队列", "weight": 25},
                ],
                "bonuses": ["Kubernetes"],
                "confirm": True,
            },
            headers=headers,
        )
        assert jd.status_code == 200, jd.text
        assert jd.json()["status"] == "confirmed"
        assert jd.json()["version"] == 1  # 首次确认保持 v1，不额外 bump

        rounds = await client.put(
            f"/api/positions/{pid}/rounds",
            json={
                "rounds": [
                    {"type": "r1", "interviewer_id": 7},
                    {"type": "r2", "interviewer_id": 8},
                    {"type": "hr", "interviewer_id": 3},
                ]
            },
            headers=headers,
        )
        assert rounds.status_code == 200, rounds.text
        assert [r["type"] for r in rounds.json()] == ["r1", "r2", "hr"]

        published = await client.post(f"/api/positions/{pid}/publish", headers=headers)
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "open"

        # 复制：面试官必须清空（BR-24）
        dup = await client.post(f"/api/positions/{pid}/duplicate", headers=headers)
        assert dup.status_code == 201, dup.text
        body = dup.json()
        assert body["name"].endswith("- 副本")
        assert body["status"] == "draft"
        assert body["jd_version"] == 1
        assert [r["type"] for r in body["rounds"]] == ["r1", "r2", "hr"]
        assert all(r["interviewer_id"] is None for r in body["rounds"])

        # 非草稿不可删除
        del_open = await client.delete(f"/api/positions/{pid}", headers=headers)
        assert del_open.status_code == 400
        assert del_open.json()["detail"]["code"] == "position.not_deletable"

        # 关闭后不可删除，可重新打开
        closed = await client.post(f"/api/positions/{pid}/close", headers=headers)
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"
        assert (
            await client.delete(f"/api/positions/{pid}", headers=headers)
        ).status_code == 400
        assert (
            await client.post(f"/api/positions/{pid}/reopen", headers=headers)
        ).status_code == 200

        # 草稿副本可真删除
        assert (
            await client.delete(f"/api/positions/{body['id']}", headers=headers)
        ).status_code == 200


def test_position_lifecycle():
    _run(_scenario_lifecycle())


async def _scenario_visibility() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        hr_headers = await _login(client)
        created = await client.post(
            "/api/positions", json={"name": "pytest-可见性"}, headers=hr_headers
        )
        pid = created.json()["id"]
        await client.put(
            f"/api/positions/{pid}/rounds",
            json={
                "rounds": [
                    {"type": "r1", "interviewer_id": 7},
                    {"type": "hr", "interviewer_id": 3},
                ]
            },
            headers=hr_headers,
        )

        other = await _login(client, "interviewer2@prepilot.dev")  # id=8，未被指派
        listed = await client.get("/api/positions", headers=other)
        assert pid not in [p["id"] for p in listed.json()["items"]]
        assert (
            await client.get(f"/api/positions/{pid}", headers=other)
        ).status_code == 403

        assigned = await _login(client, "interviewer@prepilot.dev")  # id=7，被指派 r1
        assert (
            await client.get(f"/api/positions/{pid}", headers=assigned)
        ).status_code == 200

        await client.delete(f"/api/positions/{pid}", headers=hr_headers)


def test_interviewer_only_sees_assigned():
    """面试官数据范围 = assigned：只能看到自己被指派的职位。"""
    _run(_scenario_visibility())


# ---------------------------------------------------------------- 编辑页支撑接口


async def _scenario_form_support():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        hr = await _login(client)

        # 面试官候选：既要能选到技术面试官，也要能选到 HR / HR 主管
        # （evaluation:submit 只授予面试官角色，若只按它筛，HR 面与 Offer 轮将无人可选）
        interview = await client.get("/api/users/options?purpose=interview", headers=hr)
        assert interview.status_code == 200
        roles = {u["role_code"] for u in interview.json()}
        assert {"interviewer", "hr", "hr_lead"} <= roles

        owner = await client.get("/api/users/options?purpose=owner", headers=hr)
        assert owner.status_code == 200
        assert all(u["role_code"] != "interviewer" for u in owner.json())

        created = await client.post("/api/positions", json={"name": "pytest-表单支撑"}, headers=hr)
        pid = created.json()["id"]

        jd = {
            "hard_gates": ["本科及以上"],
            "competencies": [
                {"text": "Go", "weight": 50},
                {"text": "SQL", "weight": 30},
                {"text": "微服务", "weight": 20},
            ],
            "bonuses": [],
        }
        rounds = [{"type": "r1", "interviewer_id": 7}, {"type": "hr", "interviewer_id": 3}]

        # 首次保存：JD 与流程都算变更
        first = await client.post(
            f"/api/positions/{pid}/save-preview", headers=hr, json={"jd": jd, "rounds": rounds}
        )
        assert first.json()["jd_changed"] is True
        assert first.json()["rounds_changed"] is True
        assert first.json()["jd_error"] == ""
        assert first.json()["rounds_error"] == ""

        await client.put(f"/api/positions/{pid}/jd", headers=hr, json={**jd, "confirm": True})
        await client.put(f"/api/positions/{pid}/rounds", headers=hr, json={"rounds": rounds})

        # 原样再保存：不应再判定为变更
        again = await client.post(
            f"/api/positions/{pid}/save-preview", headers=hr, json={"jd": jd, "rounds": rounds}
        )
        assert again.json()["jd_changed"] is False
        assert again.json()["rounds_changed"] is False

        # 非法 JD（权重合计 90）与非法流程（末位不是 hr/offer）应被预检拦下
        bad_jd = {**jd, "competencies": [{"text": "Go", "weight": 50}, {"text": "SQL", "weight": 30}, {"text": "MQ", "weight": 10}]}
        bad = await client.post(
            f"/api/positions/{pid}/save-preview",
            headers=hr,
            json={"jd": bad_jd, "rounds": [{"type": "r1", "interviewer_id": 7}]},
        )
        assert bad.json()["jd_error"]
        assert bad.json()["rounds_error"]

        await client.delete(f"/api/positions/{pid}", headers=hr)


def test_position_form_support_apis():
    """可选人员列表 + 保存前预检（编辑页依赖）。"""
    _run(_scenario_form_support())
