"""第三段：JD 变更影响处理（版本快照 + STALE 埋点）。

覆盖三件事：
1) `jd_versions` 每次实质变更落一行，且变更摘要是可读的人话（不是「JD 结构化变更」）
2) 原样保存不制造空版本（jd_hash 相同的保护）
3) JD 确认后该职位的存量匹配分置 STALE，预检的影响面数字与之一致

STALE 部分直接往 `match_scores` 插数据（候选人与应聘记录表属 3.3，本期没有），
3.3 接入后只需换掉造数据的那段，断言不用动。
"""
from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.candidate import Candidate
from app.models.match_score import CURRENT, STALE, MatchScore


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


JD_V1 = {
    "raw_text": "中级后端工程师",
    "hard_gates": ["本科及以上"],
    "competencies": [
        {"text": "Go", "weight": 50},
        {"text": "SQL", "weight": 30},
        {"text": "微服务", "weight": 20},
    ],
    "bonuses": ["Kubernetes"],
}

JD_V2 = {
    "raw_text": "中级后端工程师",
    "hard_gates": ["本科及以上", "3 年以上经验"],
    "competencies": [
        {"text": "Go", "weight": 60},
        {"text": "SQL", "weight": 25},
        {"text": "微服务", "weight": 15},
    ],
    "bonuses": ["Kubernetes", "云原生"],
}


async def _scenario_versions() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        created = await client.post(
            "/api/positions", json={"name": "pytest-JD版本历史"}, headers=headers
        )
        pid = created.json()["id"]

        # 首次确认：v1，摘要写明「首次确认」
        first = await client.put(
            f"/api/positions/{pid}/jd", json={**JD_V1, "confirm": True}, headers=headers
        )
        assert first.json()["version"] == 1, first.text

        # 原样再保存一次：不应制造空版本
        again = await client.put(
            f"/api/positions/{pid}/jd", json={**JD_V1, "confirm": True}, headers=headers
        )
        assert again.json()["version"] == 1

        # 实质变更：v2，摘要应包含权重调整与新增项
        second = await client.put(
            f"/api/positions/{pid}/jd", json={**JD_V2, "confirm": True}, headers=headers
        )
        assert second.json()["version"] == 2, second.text

        listed = await client.get(f"/api/positions/{pid}/jd/versions", headers=headers)
        assert listed.status_code == 200, listed.text
        versions = listed.json()
        assert [v["version"] for v in versions] == [2, 1]  # 倒序

        v1 = next(v for v in versions if v["version"] == 1)
        v2 = next(v for v in versions if v["version"] == 2)
        assert v1["change_summary"] == "JD 首次确认"
        assert "权重调整" in v2["change_summary"]
        assert v2["changed_by_name"]

        # 详情：快照内容与保存时一致（P18 回溯「按哪版标准算的分」）
        detail = await client.get(
            f"/api/positions/{pid}/jd/versions/2", headers=headers
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["hard_gates"] == JD_V2["hard_gates"]
        assert {c["text"]: c["weight"] for c in body["competencies"]} == {
            c["text"]: c["weight"] for c in JD_V2["competencies"]
        }

        # 不存在的版本 → 404
        assert (
            await client.get(f"/api/positions/{pid}/jd/versions/99", headers=headers)
        ).status_code == 404

        # 面试官没被指派 → 403（数据范围）
        other = await _login(client, "interviewer2@prepilot.dev")
        assert (
            await client.get(f"/api/positions/{pid}/jd/versions", headers=other)
        ).status_code == 403

        await client.delete(f"/api/positions/{pid}", headers=headers)


def test_jd_version_history():
    """版本落库 + 人话摘要 + 只读快照接口。"""
    _run(_scenario_versions())


async def _scenario_stale() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        created = await client.post(
            "/api/positions", json={"name": "pytest-匹配分STALE"}, headers=headers
        )
        pid = created.json()["id"]
        await client.put(
            f"/api/positions/{pid}/jd", json={**JD_V1, "confirm": True}, headers=headers
        )

        # 造两条「当前有效」的匹配分。
        # ⚠️ 005 迁移后 match_scores.candidate_id 有了 candidates 外键，
        #    不能再随便填 901/902 这种不存在的 id，必须先建真候选人。
        import uuid

        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as db:
            cands = []
            for i in range(2):
                c = Candidate(
                    name=f"pytest-候选人{i}",
                    contact_email=f"stale-{suffix}-{i}@example.com",
                    profile_status="confirmed",
                )
                db.add(c)
                cands.append(c)
            await db.flush()
            for c in cands:
                db.add(
                    MatchScore(
                        candidate_id=c.id,
                        position_id=pid,
                        jd_version=1,
                        score=78.5,
                        tier="strong",
                        algorithm_version="rule-v1",
                        model_version="qwen3.8-flash",
                        status=CURRENT,
                    )
                )
            await db.commit()
            cand_ids = [c.id for c in cands]

        # 预检：影响面 = 2 条
        pv = await client.post(
            f"/api/positions/{pid}/save-preview",
            headers=headers,
            json={"jd": JD_V2, "rounds": []},
        )
        assert pv.json()["jd_changed"] is True
        assert pv.json()["affected_candidates"] == 2, pv.text

        # 改 JD 并确认 → 两条全置 STALE
        saved = await client.put(
            f"/api/positions/{pid}/jd", json={**JD_V2, "confirm": True}, headers=headers
        )
        assert saved.json()["version"] == 2

        async with SessionLocal() as db:
            rows = (
                await db.execute(
                    select(MatchScore).where(MatchScore.position_id == pid)
                )
            ).scalars().all()
            assert len(rows) == 2
            assert all(r.status == STALE for r in rows)
            assert all(r.jd_version == 1 for r in rows)  # 历史不改，重算才写新行

        # 影响面归零
        pv2 = await client.post(
            f"/api/positions/{pid}/save-preview",
            headers=headers,
            json={"jd": JD_V2, "rounds": []},
        )
        assert pv2.json()["affected_candidates"] == 0

        # 变更摘要里带上待重算条数
        versions = (
            await client.get(f"/api/positions/{pid}/jd/versions", headers=headers)
        ).json()
        assert "2 条匹配分待重算" in next(
            v["change_summary"] for v in versions if v["version"] == 2
        )

        async with SessionLocal() as db:
            await db.execute(delete(MatchScore).where(MatchScore.position_id == pid))
            await db.execute(delete(Candidate).where(Candidate.id.in_(cand_ids)))
            await db.commit()
        await client.delete(f"/api/positions/{pid}", headers=headers)


def test_jd_change_marks_scores_stale():
    """JD 实质变更 → 存量匹配分置 STALE，且预检的影响面数字一致。"""
    _run(_scenario_stale())
