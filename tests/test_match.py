"""人岗匹配 P18（PRD §6.7 / §6.8 / §5.16）。

接口链路走 httpx + ASGITransport，LLM 在测试里一律打桩（Celery 派发也拦掉）。
重点锁死四件事：
- **BR-19 一票否决**：硬性门槛不满足 → 不加权、不给分
- **BR-21 分数可复算**：总分必须等于构成表按公式重算的结果
- **重算保留历史**：旧行 STALE、新行 CURRENT，不覆盖
- **BR-22 反馈只追加**：落库后原分数不变，且带不可变快照
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app
from app.services import match as match_svc

RESUME = """陈匹配
邮箱 chen@example.com  电话 13800138000  北京
工作年限：6 年
2018-2021 A公司 后端工程师 订单系统 Go MySQL 数据库设计
2021-至今 B公司 资深工程师 分布式服务治理 Kubernetes 微服务
项目：支付系统重构 负责人 Java Redis Kafka，带过 3 人小组
技能：Java Redis Kafka MySQL Kubernetes Go 分布式系统
教育：某大学 计算机 本科 2014-2018"""

IV_R1 = 7
HR_LEAD = 3

# 测试里不派发 Celery（本地没有 broker，delay 会去连 RabbitMQ 并阻塞）
_NO_CELERY = lambda _score_id: None  # noqa: E731


def _run(coro):
    async def wrapper():
        await engine.dispose()
        match_svc._enqueue_explain = _NO_CELERY
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
    client: AsyncClient,
    headers: dict[str, str],
    *,
    gates: list[str] | None = None,
    competencies: list[dict] | None = None,
    bonuses: list[str] | None = None,
    rounds: bool = True,
) -> int:
    created = await client.post(
        "/api/positions", json={"name": f"pytest-匹配{uuid.uuid4().hex[:6]}"}, headers=headers
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "高级后端工程师",
            "hard_gates": gates if gates is not None else ["本科及以上"],
            "competencies": competencies
            if competencies is not None
            else [
                {"text": "Java", "weight": 40},
                {"text": "分布式系统", "weight": 30},
                {"text": "数据库设计", "weight": 20},
                {"text": "团队协作", "weight": 10},
            ],
            "bonuses": bonuses or [],
            "confirm": True,
        },
        headers=headers,
    )
    assert jd.status_code == 200, jd.text

    if rounds:
        resp = await client.put(
            f"/api/positions/{pid}/rounds",
            json={
                "rounds": [
                    {"type": "r1", "interviewer_id": IV_R1},
                    {"type": "hr", "interviewer_id": HR_LEAD},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
    return pid


async def _new_candidate(
    client: AsyncClient, headers: dict[str, str], pid: int, *, confirm: bool = True
) -> tuple[int, int]:
    mail = f"match-{uuid.uuid4().hex[:12]}@example.com"
    created = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": "陈匹配",
            "contact_email": mail,
            "contact_phone": "13800138000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    app_id = created.json()["applications"][0]["id"]
    if not confirm:
        return cid, app_id

    confirmed = await client.post(
        f"/api/candidates/{cid}/confirm",
        json={
            "profile": {
                "basic": {"name": "陈匹配", "years": "6 年"},
                "skills": ["Java", "Redis", "Kafka", "MySQL", "Kubernetes", "Go", "分布式系统"],
                "work": [
                    {
                        "company": "A公司",
                        "title": "后端工程师",
                        "period": "2018-2021",
                        "desc": "订单系统 Go MySQL 数据库设计",
                    },
                    {
                        "company": "B公司",
                        "title": "资深工程师",
                        "period": "2021-至今",
                        "desc": "分布式服务治理 Kubernetes 微服务",
                    },
                ],
                "projects": [
                    {"name": "支付系统重构", "role": "负责人", "desc": "Java Redis Kafka，带过 3 人小组"}
                ],
                "education": [{"school": "某大学", "major": "计算机", "degree": "本科"}],
                "confidence": {"basic": 0.9, "work": 0.8, "skills": 0.9},
            }
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return cid, app_id


# ---------------------------------------------------------------- 确认即算分


async def _scenario_confirmed_scored() -> None:
    """D12 方案 B：确认后**同步**就有分，看板卡片能拿到。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers)
        _, app_id = await _new_candidate(client, headers, pid)

        detail = await client.get(f"/api/match/applications/{app_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["has_score"] is True
        assert body["jd_version"] >= 1
        assert body["algorithm_version"] == "rule-v1"
        # 分数由构成表复算得出（BR-21），不是模型拍的
        assert body["score"] == pytest.approx(
            sum(b["contribution"] for b in body["breakdown"]), abs=0.01
        )
        # 证据必须能在简历原文里定位，定位不到就不给 quote
        for ev in body["evidences"]:
            if ev["quote"]:
                assert ev["segmentId"].startswith("L")

        board = await client.get("/api/board", params={"position_id": pid}, headers=headers)
        cards = [c for col in board.json()["columns"] for c in col["cards"]]
        card = next(c for c in cards if c["application_id"] == app_id)
        assert card["match_score"] == pytest.approx(body["score"], abs=0.01)
        assert card["match_tier"] == body["tier"]
        assert card["match_status"] == "CURRENT"


def test_confirm_computes_score_and_board_shows_it():
    _run(_scenario_confirmed_scored())


# ---------------------------------------------------------------- 一票否决


async def _scenario_veto() -> None:
    """BR-19：门槛不满足 → 不加权、score=0、tier=vetoed，且给出判定来源。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, gates=["博士及以上"])
        _, app_id = await _new_candidate(client, headers, pid)

        body = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        assert body["tier"] == "vetoed"
        assert body["score"] == 0
        assert body["breakdown"] == []  # 命中否决就不展示加权得分
        assert len(body["vetoed_gates"]) == 1
        assert "本科" in body["vetoed_gates"][0]["reason"]  # 判定来源要写清为什么
        # 否决时总结由规则直接产出，不等 LLM
        assert body["summary_status"] == "ready"
        assert body["summary"]["conclusion"]

        # 年限不足同样否决
        pid2 = await _new_position(client, headers, gates=["本科及以上", "10 年以上"])
        _, app_id2 = await _new_candidate(client, headers, pid2)
        body2 = (await client.get(f"/api/match/applications/{app_id2}", headers=headers)).json()
        assert body2["tier"] == "vetoed"
        assert "6 年" in body2["vetoed_gates"][0]["reason"]


def test_hard_gate_veto():
    _run(_scenario_veto())


async def _scenario_unknown_gate() -> None:
    """简历没写的信息 ≠ 不满足：标 unknown，不否决，但要提示人工核实。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, gates=["熟悉 Terraform", "本科及以上"])
        _, app_id = await _new_candidate(client, headers, pid)
        body = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        assert body["tier"] == "vetoed"
        assert body["vetoed_gates"][0]["text"] == "熟悉 Terraform"

        # 学历信息缺失（profile 里没 education）→ unknown，不算否决
        pid2 = await _new_position(client, headers, gates=["持有 PMP 证书"])
        cid, app_id2 = await _new_candidate(client, headers, pid2)
        _ = cid
        body2 = (await client.get(f"/api/match/applications/{app_id2}", headers=headers)).json()
        assert body2["tier"] == "vetoed"  # 简历确实没提 PMP → 真不满足


def test_unknown_gate_not_treated_as_veto():
    _run(_scenario_unknown_gate())


# ---------------------------------------------------------------- 重算与历史


async def _scenario_recompute() -> None:
    """JD 变更 → 置 STALE；重算后旧行保留、新行 CURRENT。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers)
        _, app_id = await _new_candidate(client, headers, pid)

        first = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        score_id = first["score_id"]

        # JD 权重变更 → 存量分置 STALE（不自动重算，PRD v1.6 定）
        jd = await client.put(
            f"/api/positions/{pid}/jd",
            json={
                "raw_text": "高级后端工程师",
                "hard_gates": ["本科及以上"],
                "competencies": [
                    {"text": "Java", "weight": 70},
                    {"text": "分布式系统", "weight": 20},
                    {"text": "数据库设计", "weight": 10},
                ],
                "bonuses": [],
                "confirm": True,
            },
            headers=headers,
        )
        assert jd.status_code == 200, jd.text

        stale = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        assert stale["status"] == "STALE"

        again = await client.post(
            f"/api/match/applications/{app_id}/recompute", headers=headers
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "CURRENT"
        assert again.json()["score_id"] != score_id  # 新行，不是原地改
        assert again.json()["jd_version"] > first["jd_version"]

        # 旧行还在（保留历史，P18 才能回答"当年按哪版标准算的分"）
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.match_score import MatchScore

        async with SessionLocal() as db:
            rows = list(
                await db.scalars(
                    select(MatchScore).where(MatchScore.application_id == app_id)
                )
            )
        assert len(rows) == 2
        assert sorted(r.status for r in rows) == ["CURRENT", "STALE"]
        assert sorted(r.jd_version for r in rows) == [1, 2]


def test_recompute_keeps_history():
    _run(_scenario_recompute())


# ---------------------------------------------------------------- 反馈


async def _scenario_feedback() -> None:
    """BR-22：反馈只落库 + 存快照，不改原分数。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers)
        _, app_id = await _new_candidate(client, headers, pid)
        before = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        score_before = before["score"]

        too_short = await client.post(
            f"/api/match/applications/{app_id}/feedback",
            json={"kind": "lower", "comment": "偏低"},
            headers=headers,
        )
        assert too_short.status_code == 422  # 说明 ≥10 字

        ok = await client.post(
            f"/api/match/applications/{app_id}/feedback",
            json={
                "kind": "lower",
                "expected_low": 60,
                "expected_high": 75,
                "comment": "实际面试表现比分数差，数据库设计能力被高估了",
            },
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["kind"] == "lower"

        after = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        assert after["score"] == score_before  # 分数没被动过
        assert len(after["feedbacks"]) == 1
        assert after["feedbacks"][0]["expected_high"] == 75

        # 快照已绑定（分数 / 构成 / 版本齐全）
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.match_score import MatchFeedback

        async with SessionLocal() as db:
            fb = (
                await db.scalars(
                    select(MatchFeedback).where(MatchFeedback.application_id == app_id)
                )
            ).first()
        snap = fb.snapshot_json
        assert snap["score"] == score_before
        assert snap["breakdown"]
        assert snap["algorithm_version"] == "rule-v1"

        # 区间非法
        bad = await client.post(
            f"/api/match/applications/{app_id}/feedback",
            json={
                "kind": "higher",
                "expected_low": 90,
                "expected_high": 50,
                "comment": "区间写反了，应该拦一下",
            },
            headers=headers,
        )
        assert bad.status_code == 400
        assert bad.json()["detail"]["code"] == "match.invalid_range"


def test_feedback_appends_without_changing_score():
    _run(_scenario_feedback())


# ---------------------------------------------------------------- 权限与前置


async def _scenario_acl() -> None:
    """BR-18：面试官没有 match:view；简历未确认不给算。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers)
        _, app_id = await _new_candidate(client, headers, pid, confirm=False)

        # 未确认：详情给阻断原因，重算直接 400
        detail = await client.get(f"/api/match/applications/{app_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["blocked_reason"] == "profile_not_confirmed"
        assert detail.json()["has_score"] is False

        recompute = await client.post(
            f"/api/match/applications/{app_id}/recompute", headers=headers
        )
        assert recompute.status_code == 400
        assert recompute.json()["detail"]["code"] == "candidate.not_confirmed"

        # 面试官：路由级 403
        _, app_id2 = await _new_candidate(client, headers, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        resp = await client.get(f"/api/match/applications/{app_id2}", headers=iv)
        assert resp.status_code == 403


def test_match_acl_and_preconditions():
    _run(_scenario_acl())


# ---------------------------------------------------------------- 加分项


async def _scenario_bonus() -> None:
    """加分项：命中 +2 / 项，总分封顶 100。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = await _login(client)
        pid = await _new_position(client, headers, bonuses=["Kubernetes", "Kafka", "Golang"])
        _, app_id = await _new_candidate(client, headers, pid)
        body = (await client.get(f"/api/match/applications/{app_id}", headers=headers)).json()
        assert len(body["bonuses"]) == 3
        assert sum(b["delta"] for b in body["bonuses"]) == 6
        base = sum(b["contribution"] for b in body["breakdown"])
        assert body["score"] == pytest.approx(min(base + 6, 100), abs=0.01)


def test_bonus_capped():
    _run(_scenario_bonus())
