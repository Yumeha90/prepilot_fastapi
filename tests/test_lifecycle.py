"""数据生命周期 P15（PRD 3.5.1 / §5.14 / §7.7 / BR-10）。

接口链路走 httpx + ASGITransport + 单个 asyncio.run，不碰 LLM。
重点锁死四件事：
- **权限**：策略查看与编辑 `system:lifecycle`、粉碎 `system:purge`，HR 一律 403
- **BR-10「未入职」判定**：到期但已录用（accepted）的不进扫描、也不被粉碎
- **粉碎只清内容、行保留**：简历原文与解析结果清空，看板不再出现，但记录还在
- **定时任务**：策略停用时照跑（更新 last_run_at）但一条都不删
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.candidate import Application, Candidate

RESUME = """钱到期
邮箱 old@example.com  电话 13700137000  上海
工作年限：5 年
2019-2022 C公司 后端工程师 交易系统 Go MySQL
2022-至今 D公司 资深工程师 服务治理 Kubernetes
技能：Java Redis Kafka MySQL Kubernetes
教育：某大学 计算机 本科 2015-2019"""

IV_R1 = 7
HR_LEAD = 3
BASE = "/api/system/lifecycle"


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
        "/api/positions", json={"name": f"pytest-生命周期{uuid.uuid4().hex[:6]}"}, headers=headers
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    jd = await client.put(
        f"/api/positions/{pid}/jd",
        json={
            "raw_text": "后端工程师",
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
    rounds = await client.put(
        f"/api/positions/{pid}/rounds",
        json={"rounds": [{"type": "r1", "interviewer_id": IV_R1},
                          {"type": "hr", "interviewer_id": HR_LEAD}]},
        headers=headers,
    )
    assert rounds.status_code == 200, rounds.text
    return pid


async def _new_candidate(
    client: AsyncClient, headers: dict[str, str], pid: int, *, confirm: bool = True
) -> tuple[int, int]:
    mail = f"lc-{uuid.uuid4().hex[:12]}@example.com"
    created = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": "钱到期",
            "contact_email": mail,
            "contact_phone": "13700137000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    application_id = created.json()["applications"][0]["id"]
    if confirm:
        confirmed = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={
                "profile": {
                    "basic": {"name": "钱到期", "years": "5 年"},
                    "skills": ["Java", "Redis", "MySQL", "Kubernetes"],
                    "work": [{"company": "D公司", "title": "资深工程师", "period": "2022-至今",
                              "desc": "服务治理"}],
                    "projects": [],
                    "education": [{"school": "某大学", "major": "计算机", "degree": "本科"}],
                    "confidence": {"basic": 0.9, "work": 0.8, "skills": 0.9},
                }
            },
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
    return cid, application_id


async def _age_candidate(candidate_id: int, days: int) -> None:
    """把保留期起算点往前拨（D6：确认时间，未确认退回入库时间）。

    测试只能改库来造「90 天前」：确认过的档案光改 `created_at` 没用，
    起算点取的是 `confirmed_at`，两个都得一起拨。
    """
    async with SessionLocal() as db:
        candidate = await db.get(Candidate, candidate_id)
        assert candidate is not None
        aged = datetime.now(timezone.utc) - timedelta(days=days)
        candidate.created_at = aged
        if candidate.confirmed_at is not None:
            candidate.confirmed_at = aged
        await db.commit()


async def _candidate_row(candidate_id: int) -> Candidate:
    async with SessionLocal() as db:
        row = await db.get(Candidate, candidate_id)
        assert row is not None
        db.expunge(row)
        return row


# ---------------------------------------------------------------- 权限与默认策略


async def _scenario_permissions() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client, "hr@prepilot.dev")
        assert (await client.get(BASE, headers=hr)).status_code == 403
        assert (await client.get(f"{BASE}/scan", headers=hr)).status_code == 403
        assert (
            await client.post(f"{BASE}/purge", json={"candidate_ids": [1], "confirm": True},
                              headers=hr)
        ).status_code == 403

        admin = await _login(client, "admin@prepilot.dev")
        resp = await client.get(BASE, headers=admin)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 默认策略：90 天 + 启用，库里没有时懒创建
        assert body["policy"]["days"] == 90
        assert body["policy"]["enabled"] is True
        assert body["policy"]["status"] == "current"


async def _scenario_scan_and_manual_purge() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        admin = await _login(client, "admin@prepilot.dev")
        pid = await _new_position(client, hr)

        # 1) 刚入库的不该被扫到
        fresh_id, _ = await _new_candidate(client, hr, pid)
        scan = await client.get(f"{BASE}/scan", params={"days": 90}, headers=admin)
        assert scan.status_code == 200, scan.text
        assert fresh_id not in [i["candidate_id"] for i in scan.json()["items"]]

        # 2) 入库 100 天前 → 到期（默认 90 天）
        old_id, old_app = await _new_candidate(client, hr, pid)
        await _age_candidate(old_id, 100)
        scan = await client.get(f"{BASE}/scan", params={"days": 90}, headers=admin)
        items = scan.json()["items"]
        hit = next(i for i in items if i["candidate_id"] == old_id)
        assert hit["days_overdue"] >= 9
        # D6：起算点是确认时间而不是入库时间（两个都被拨到 100 天前，取到的应是确认时间）
        assert hit["since_at"][:10] == (
            datetime.now(timezone.utc) - timedelta(days=100)
        ).date().isoformat()

        # 3) 二次确认：不带 confirm 直接 400，且简历还在
        denied = await client.post(
            f"{BASE}/purge", json={"candidate_ids": [old_id], "confirm": False}, headers=admin
        )
        assert denied.status_code == 400, denied.text
        assert denied.json()["detail"]["code"] == "lifecycle.confirm_required"
        assert (await _candidate_row(old_id)).resume_raw_text != ""

        # 4) 手动粉碎：清空内容、行保留、看板消失
        done = await client.post(
            f"{BASE}/purge", json={"candidate_ids": [old_id], "confirm": True}, headers=admin
        )
        assert done.status_code == 200, done.text
        assert done.json()["purged"] == 1

        row = await _candidate_row(old_id)
        assert row.purged_at is not None
        assert row.resume_raw_text == ""
        assert row.parsed_profile is None
        assert row.profile_status == "archived"
        assert row.purge_source == "manual"

        board = await client.get("/api/board", headers=hr)
        assert board.status_code == 200, board.text
        shown = [
            c["application_id"]
            for col in board.json()["columns"]
            for c in col["cards"]
        ]
        assert old_app not in shown

        # 5) 重复粉碎跳过（不报错，也不重复计数）
        again = await client.post(
            f"{BASE}/purge", json={"candidate_ids": [old_id], "confirm": True}, headers=admin
        )
        assert again.json()["purged"] == 0
        assert again.json()["skipped_purged"] == 1


async def _scenario_accepted_protected() -> None:
    """BR-10 是「未入职」：已录用（accepted）的简历不能被粉碎。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        admin = await _login(client, "admin@prepilot.dev")
        pid = await _new_position(client, hr)
        cid, application_id = await _new_candidate(client, hr, pid)
        await _age_candidate(cid, 100)

        hired = await client.post(
            f"/api/board/applications/{application_id}/transition",
            json={"action": "accept"},
            headers=hr,
        )
        assert hired.status_code == 200, hired.text

        scan = await client.get(f"{BASE}/scan", params={"days": 90}, headers=admin)
        assert cid not in [i["candidate_id"] for i in scan.json()["items"]]

        # 手动绕过扫描直接指定也要被挡
        done = await client.post(
            f"{BASE}/purge", json={"candidate_ids": [cid], "confirm": True}, headers=admin
        )
        assert done.json()["purged"] == 0
        assert done.json()["skipped_accepted"] == 1
        assert (await _candidate_row(cid)).resume_raw_text != ""


async def _scenario_auto_purge_and_policy() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        admin = await _login(client, "admin@prepilot.dev")
        pid = await _new_position(client, hr)

        cid, _ = await _new_candidate(client, hr, pid, confirm=False)
        await _age_candidate(cid, 120)

        # 1) 立即执行一次定时扫描（不等整点）
        ran = await client.post(f"{BASE}/purge/auto", headers=admin)
        assert ran.status_code == 200, ran.text
        body = ran.json()
        assert body["enabled"] is True
        assert body["ran_at"] is not None
        # 断言落在「这个人被清了」上，而不是总数：库里可能有别的到期候选人
        assert (await _candidate_row(cid)).purged_at is not None
        row = await _candidate_row(cid)
        assert row.purge_source == "auto"
        assert row.purged_by is None

        overview = await client.get(BASE, headers=admin)
        assert overview.json()["policy"]["last_purged"] >= 1
        assert overview.json()["purged_total"] >= 1

        # 2) 停用策略后：任务照跑（last_run_at 更新），但一条都不删
        cid2, _ = await _new_candidate(client, hr, pid, confirm=False)
        await _age_candidate(cid2, 120)
        disabled = await client.put(
            f"{BASE}/policy", json={"enabled": False}, headers=admin
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False

        ran2 = await client.post(f"{BASE}/purge/auto", headers=admin)
        assert ran2.json()["enabled"] is False
        assert ran2.json()["purged"] == 0
        assert (await _candidate_row(cid2)).resume_raw_text != ""

        # 3) 改天数：旧版本转历史，新版本生效
        updated = await client.put(f"{BASE}/policy", json={"days": 30, "enabled": True},
                                   headers=admin)
        assert updated.status_code == 200, updated.text
        assert updated.json()["days"] == 30
        history = (await client.get(BASE, headers=admin)).json()["history"]
        assert len(history) >= 2
        assert 90 in [h["days"] for h in history]

        # 4) 天数越界直接拦
        bad = await client.put(f"{BASE}/policy", json={"days": 0}, headers=admin)
        assert bad.status_code == 400, bad.text
        assert bad.json()["detail"]["code"] == "lifecycle.days_invalid"

        # 还原成 90 天，避免污染其它用例
        await client.put(f"{BASE}/policy", json={"days": 90, "enabled": True}, headers=admin)


@pytest.fixture(autouse=True)
def _reset_test_candidates():
    """清掉上一轮用例造的候选人（`lc-` 前缀邮箱）。

    为什么不能只清「已粉碎的」：用例 4 里的第二个候选人是为了验证「策略停用不删人」
    造的，它既没被粉碎也没被录用，会一直留在库里 —— 下次跑全量时它已经满 90 天，
    会被定时任务顺手清掉，导致断言对不上。测试数据自己造的就得自己收。
    """
    async def _reset():
        # 应聘记录由关系的 delete-orphan 级联带走
        async with SessionLocal() as db:
            rows = list(
                await db.scalars(
                    select(Candidate).where(Candidate.contact_email.like("lc-%"))
                )
            )
            for row in rows:
                await db.delete(row)
            await db.commit()

    asyncio.run(_reset())
    yield


def test_lifecycle_permissions():
    _run(_scenario_permissions())


def test_lifecycle_scan_and_manual_purge():
    _run(_scenario_scan_and_manual_purge())


def test_lifecycle_accepted_not_purged():
    _run(_scenario_accepted_protected())


def test_lifecycle_auto_purge_and_policy():
    _run(_scenario_auto_purge_and_policy())
