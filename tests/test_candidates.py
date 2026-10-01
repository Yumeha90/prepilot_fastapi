"""候选人模块测试（PRD 3.3 第一段：上传 → 解析 → 确认）。

分两类：
1) 纯函数：解析的分块 / 合并 / 清洗 —— 不需要 DB，也不需要 LLM；
2) 接口链路：hr / interviewer 两种身份跑一遍「上传 → 解析 → 确认」，
   AI 全部 monkeypatch 打桩（测试不依赖外网，也不烧 token）。

⚠️ 同 test_positions：接口链路不能用 starlette TestClient（跨 loop 会挂），
   一律走 httpx + ASGITransport + 单个 asyncio.run。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app
from app.services import resume_ai

RESUME = """张三
邮箱 zhangsan@example.com  电话 13800138000  上海
工作年限：5 年
2019-2022 A公司 后端工程师 负责订单系统 Go MySQL
2022-至今 B公司 高级工程师 微服务治理 Kubernetes
项目：订单中心重构 负责人 Go Kafka
技能：Go MySQL Redis Kubernetes Kafka
教育：某某大学 计算机科学与技术 本科 2015-2019"""


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


# ---------------------------------------------------------------- 纯函数


def test_norm_keeps_leading_letter_company():
    """「A公司」曾被前导字母规则剥成「公司」—— 简历字段必须保留。

    JD 拆解的 normalize_items 会剥孤立前导字母（模型偶发吐 `L本科及以上`），
    但简历里公司名合法地以字母开头，照搬就是实打实的数据损坏。
    """
    assert resume_ai._norm("A公司") == "A公司"
    assert resume_ai._norm("1. 三年经验") == "三年经验"
    assert resume_ai._norm("- Go 开发") == "Go 开发"


def test_split_chunks_breaks_on_line_boundary():
    text = "\n".join(f"第{i}行内容啊啊啊啊" for i in range(2000))
    chunks = resume_ai._split_chunks(text)
    assert len(chunks) > 1
    # 只能在换行处断开，不能把一行切两半
    assert all(c.endswith("啊啊啊啊") or c == chunks[-1] for c in chunks[:-1])
    assert sum(len(c) for c in chunks) <= len(text) + len(chunks)


def test_merge_takes_first_nonempty_basic_and_union_skills():
    parts = [
        {
            "basic": {"name": "张三", "email": "", "phone": "", "location": "", "years": ""},
            "work": [{"company": "A公司"}],
            "projects": [],
            "skills": ["Go", "Python"],
            "education": [],
            "confidence": {"basic": 0.9, "work": 0.8},
        },
        {
            "basic": {"name": "", "email": "z@e.com", "phone": "", "location": "", "years": ""},
            "work": [{"company": "B公司"}],
            "projects": [],
            "skills": ["Go", "Java"],
            "education": [],
            "confidence": {"basic": 0.4, "work": 0.6},
        },
    ]
    merged = resume_ai._merge(parts)
    assert merged["basic"]["name"] == "张三"
    assert merged["basic"]["email"] == "z@e.com"
    assert [w["company"] for w in merged["work"]] == ["A公司", "B公司"]
    assert merged["skills"] == ["Go", "Python", "Java"]
    # 分块意味着至少有一块是半份简历，置信度取各块最低
    assert merged["confidence"]["basic"] == 0.4
    assert merged["confidence"]["work"] == 0.6


def test_parse_resume_rejects_too_short_and_too_long():
    with pytest.raises(Exception):
        asyncio.run(resume_ai.parse_resume("太短"))
    with pytest.raises(Exception):
        asyncio.run(resume_ai.parse_resume("字" * (resume_ai.L1_MAX_CHARS + 1)))


async def _stub_call(text: str):
    """桩：不联网、不烧 token，返回一份固定结构。"""
    return resume_ai.ResumeStructure(
        basic=resume_ai.ResumeBasic(name="张三", email="zhangsan@example.com"),
        skills=["Go", "MySQL"],
        confidence={"basic": 0.95, "work": 0.5},
    )


def test_parse_resume_l0_single_call(monkeypatch):
    """短简历走 L0：只调一次 LLM。"""
    calls: list[int] = []

    async def stub(text: str):
        calls.append(len(text))
        return await _stub_call(text)

    monkeypatch.setattr(resume_ai, "_call", stub)
    out = asyncio.run(resume_ai.parse_resume(RESUME))
    assert len(calls) == 1
    assert out["chunks"] == 1
    assert out["notice"] == ""
    assert out["profile"]["basic"]["name"] == "张三"
    assert out["profile"]["confidence"]["work"] == 0.5


def test_parse_resume_l1_chunked(monkeypatch):
    """长简历走 L1：多次调用后合并，并给出"已分段解析"提示。"""
    seen: list[int] = []

    async def stub(text: str):
        seen.append(len(text))
        return resume_ai.ResumeStructure(
            basic=resume_ai.ResumeBasic(name="张三" if not seen[:-1] else ""),
            skills=["Go"],
            confidence={"basic": 0.9, "skills": 0.9},
        )

    monkeypatch.setattr(resume_ai, "_call", stub)
    # 必须带换行：分块只在行边界断开，一整段无换行的文本切不出第二块
    long_resume = "\n".join(
        f"第{i}段：负责后端服务的设计与开发，使用 Go 与 MySQL，配合 Kubernetes 上线。"
        for i in range(400)
    )
    assert len(long_resume) > resume_ai.L0_MAX_CHARS
    out = asyncio.run(resume_ai.parse_resume(long_resume))
    assert out["chunks"] == len(seen) > 1
    assert "段解析" in out["notice"]


# ---------------------------------------------------------------- 接口链路


async def _login(client: AsyncClient, email: str = "hr@prepilot.dev") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _confirmed_position(client: AsyncClient, headers: dict[str, str]) -> int:
    listed = await client.get("/api/positions", params={"page_size": 50}, headers=headers)
    for p in listed.json()["items"]:
        if p["jd_status"] == "confirmed":
            return p["id"]
    raise AssertionError("测试库里没有 JD 已确认的职位，请先跑 seed")


async def _scenario_upload_parse_confirm() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = await _login(client)
        pid = await _confirmed_position(client, headers)

        # 1) BR-09 未勾选授权 → 拦截
        denied = await client.post(
            "/api/candidates",
            json={
                "position_id": pid,
                "contact_email": "nobody@example.com",
                "raw_text": RESUME,
                "auth_tick": False,
            },
            headers=headers,
        )
        assert denied.status_code == 400
        assert denied.json()["detail"]["code"] == "candidate.auth_required"

        # 2) 姓名必填（空）→ 拦截
        no_name = await client.post(
            "/api/candidates",
            json={"position_id": pid, "contact_email": "nobody@example.com",
                  "raw_text": RESUME, "auth_tick": True},
            headers=headers,
        )
        assert no_name.status_code == 400
        assert no_name.json()["detail"]["code"] == "candidate.name_required"

        # 2b) D14 邮箱格式不合法 → 拦截
        bad_mail = await client.post(
            "/api/candidates",
            json={"position_id": pid, "name": "张三", "contact_email": "not-an-email",
                  "raw_text": RESUME, "auth_tick": True},
            headers=headers,
        )
        assert bad_mail.status_code == 400
        assert bad_mail.json()["detail"]["code"] == "candidate.email_taken"

        # 邮箱是 D14 的唯一键且库里唯一，用 uuid 保证每次运行互不干扰
        mail = f"case-{uuid.uuid4().hex[:12]}@example.com"

        # 3) 正常上传：一步建候选人 + pending 应聘记录
        created = await client.post(
            "/api/candidates",
            json={
                "position_id": pid,
                "name": "张三",
                "contact_email": mail.upper(),  # 大小写不同，服务端须归一化
                "contact_phone": "13800138000",
                "raw_text": RESUME,
                "file_name": "张三.pdf",
                "auth_tick": True,
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        cid = created.json()["id"]
        assert created.json()["profile_status"] == "uploading"
        assert created.json()["contact_email"] == mail  # 归一化
        assert created.json()["applications"][0]["stage"] == "pending"

        # 4) D3 一人一职位：同邮箱再投 → 拦截
        dup = await client.post(
            "/api/candidates",
            json={"position_id": pid, "name": "张三", "contact_email": mail,
                  "raw_text": RESUME, "auth_tick": True},
            headers=headers,
        )
        assert dup.status_code == 400
        assert dup.json()["detail"]["code"] == "candidate.already_applied"

        # 5) AI 解析（打桩）：不落库，只返回
        async def stub(text: str):
            return resume_ai.ResumeStructure(
                basic=resume_ai.ResumeBasic(name="张三", email=mail),
                skills=["Go", "MySQL"],
                confidence={"basic": 0.95, "work": 0.4},
            )

        import app.services.resume_ai as mod

        original = mod._call
        mod._call = stub
        try:
            parsed = await client.post("/api/resume/parse", json={"raw_text": RESUME}, headers=headers)
            assert parsed.status_code == 200, parsed.text
            assert parsed.json()["profile"]["basic"]["name"] == "张三"

            reparse = await client.post(f"/api/candidates/{cid}/parse", headers=headers)
            assert reparse.status_code == 200
            assert reparse.json()["profile"]["skills"] == ["Go", "MySQL"]
        finally:
            mod._call = original

        # 6) BR-04：缺姓名不允许确认
        no_name = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": ""}}},
            headers=headers,
        )
        assert no_name.status_code == 400
        assert no_name.json()["detail"]["code"] == "candidate.not_confirmed"

        # 7) 确认：写档案 + 置 confirmed + confirmed_at
        ok = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": "张三", "email": mail},
                              "skills": ["Go"], "confidence": {"basic": 0.9}}},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["profile_status"] == "confirmed"
        assert ok.json()["confirmed_at"]
        assert ok.json()["parsed_profile"]["basic"]["name"] == "张三"

        # 8) 重复确认 → 拦截
        again = await client.post(
            f"/api/candidates/{cid}/confirm",
            json={"profile": {"basic": {"name": "张三"}}},
            headers=headers,
        )
        assert again.status_code == 400
        assert again.json()["detail"]["code"] == "candidate.already_confirmed"

        # 9) 列表能查到，且带上职位与阶段
        # S2 起确认即派单：流程配置齐全 → 推进到首轮；没配流程 → 停在 pending 并给出原因
        app_row = ok.json()["applications"][0]
        if ok.json()["dispatch_notice"]:
            assert app_row["stage"] == "pending"
        else:
            assert app_row["stage"] == "in_r1"
            assert app_row["interviewer_name"]
        listed = await client.get("/api/candidates", params={"keyword": "张三"}, headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1
        row = next(x for x in listed.json()["items"] if x["id"] == cid)
        assert row["position_id"] == pid
        assert row["stage"] == app_row["stage"]


async def _scenario_visibility() -> None:
    """方案 a：面试官只看自己上传的；他人上传的 403。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        hr = await _login(client, "hr@prepilot.dev")
        iv = await _login(client, "interviewer@prepilot.dev")
        pid = await _confirmed_position(client, hr)

        mine = await client.post(
            "/api/candidates",
            json={"position_id": pid, "name": "面试官上传", "contact_email": f"iv-own-{uuid.uuid4().hex[:8]}@example.com",
                  "raw_text": RESUME, "auth_tick": True},
            headers=iv,
        )
        # 面试官只能选被指派的职位；该职位若未指派给他会被 403，跳过即可
        if mine.status_code != 200:
            pytest.skip("该演示职位未指派给 interviewer，跳过可见性断言")

        others = await client.post(
            "/api/candidates",
            json={"position_id": pid, "name": "HR上传", "contact_email": f"hr-own-{uuid.uuid4().hex[:8]}@example.com",
                  "raw_text": RESUME, "auth_tick": True},
            headers=hr,
        )
        assert others.status_code == 200

        listed = await client.get("/api/candidates", headers=iv)
        ids = {x["id"] for x in listed.json()["items"]}
        assert mine.json()["id"] in ids  # 自己上传的能看到（方案 a）
        assert others.json()["id"] not in ids  # 他人的看不到

        denied = await client.get(f"/api/candidates/{others.json()['id']}", headers=iv)
        assert denied.status_code == 403


def test_candidate_upload_parse_confirm():
    _run(_scenario_upload_parse_confirm())


def test_candidate_visibility():
    _run(_scenario_visibility())
