"""AI 备面工作台 Step 2 问题链（PRD 3.4.2 / §6.4 P10，C4）。

LLM 与向量检索一律 monkeypatch：`qc._call_llm` / `qc.retrieve`。
测试锁的是**编排与落库**（选哪些能力项出题、耗时怎么分配、状态怎么推进、
幻觉门禁怎么处置），不是模型输出质量 —— 后者每次跑都不一样，测了也是假绿灯。

四条必须锁死的口径：
- **只有被指派的面试官能生成**（HR 只读，与 P09 同一口径）
- **没有矩阵就不出题**（问题链是矩阵的展开，没有输入就没有输出）
- **上一轮还在跑就不允许再投一次**（BR-12 防误触，也防两个 worker 写同一份草稿）
- **耗时必须被压回时长预算**（模型给的估计值加起来不会正好等于本轮时长）
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.session import InterviewSession
from app.models.user import User
from app.services import question_chain as qc

# seed：1 admin / 3 hr_lead / 5 hr / 7 interviewer（陈技术）/ 8 interviewer2（刘架构）
IV_R1 = 7

RESUME = """郑十
邮箱 zhengshi@example.com  电话 13800138000  深圳
工作年限：5 年
2019-2022 G公司 后端工程师 订单系统 Java MySQL
2022-至今 H公司 高级工程师 微服务治理 Kubernetes Kafka
项目：库存中心重构 核心开发 Java Redis
技能：Java Redis Kafka MySQL Kubernetes
教育：某大学 软件工程 本科 2015-2019"""

COMPETENCIES = [
    {"text": "Java", "weight": 40},
    {"text": "数据库", "weight": 35},
    {"text": "微服务", "weight": 25},
]

# 假检索结果：题目里引用《》里的标题才算数，模型编的标题要被过滤掉
HIT_TITLES = ["内部技术文档：订单服务架构演进", "历史故障复盘：2025-11-11 订单超时雪崩"]


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
        "/api/positions", json={"name": f"pytest-问题链{uuid.uuid4().hex[:6]}"}, headers=headers
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
        headers=headers,
    )
    assert jd.status_code == 200, jd.text
    rounds = await client.put(
        f"/api/positions/{pid}/rounds",
        json={
            "rounds": [
                {"type": "r1", "interviewer_id": IV_R1},
                {"type": "hr", "interviewer_id": 3},
            ]
        },
        headers=headers,
    )
    assert rounds.status_code == 200, rounds.text
    return pid


async def _session_id(client: AsyncClient, headers: dict[str, str], pid: int) -> int:
    mail = f"qc-{uuid.uuid4().hex[:12]}@example.com"
    created = await client.post(
        "/api/candidates",
        json={
            "position_id": pid,
            "name": "郑十",
            "contact_email": mail,
            "contact_phone": "13800138000",
            "raw_text": RESUME,
            "auth_tick": True,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    confirmed = await client.post(
        f"/api/candidates/{cid}/confirm",
        json={"profile": {"basic": {"name": "郑十"}, "skills": ["Java"], "confidence": {}}},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["sessions"][0]["id"]


def _fake_hits(capability: str) -> list:
    from app.ai import rag

    return [
        rag.AssetHit(title=HIT_TITLES[0], text="订单服务拆分为四个微服务，库存扣减仍用行锁。", score=0.61),
        rag.AssetHit(title=HIT_TITLES[1], text="20:03 起 P99 从 300ms 涨到 8s，持续 22 分钟。", score=0.42),
    ]


def _fake_node(capability: str, *, bad_number: bool = False) -> qc._NodeLLM:
    return qc._NodeLLM(
        capability=capability,
        main_question=f"讲一次你在{capability}上踩过的坑" + ("，QPS 9999 那种" if bad_number else ""),
        followups=[
            qc._FollowupLLM(level=1, vague="当时是怎么定位的", anti_fake="这个结论是你自己得出的吗"),
        ],
        observations=["能说出量化结果", "能讲清取舍"],
        minutes=9,
        # 一条真实标题 + 一条编的：编的要被过滤
        rag_refs=[HIT_TITLES[0], "《不存在的内部文档》"],
    )


def _fake_chain(nodes: list[qc._NodeLLM]):
    async def _fake_llm(prompt: str) -> qc._ChainLLM:
        return qc._ChainLLM(nodes=nodes)

    return _fake_llm


async def _scenario_setup() -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        # 先落一份矩阵（生成问题链的前置）
        from app.services import workbench as wb

        async def _fake_matrix(prompt: str) -> wb._MatrixLLM:
            return wb._MatrixLLM(
                rows=[
                    wb._RowLLM(capability="Java", source="jd", evidence="高级工程师", status="sufficient", focus="GC 调优"),
                    wb._RowLLM(capability="数据库", source="jd", evidence="MySQL Redis", status="verify", focus="慢查询定位"),
                    wb._RowLLM(capability="微服务", source="jd", evidence="", status="missing", focus="故障排查"),
                ]
            )

        import app.services.workbench as wb_mod

        original = wb_mod._call_llm
        wb_mod._call_llm = _fake_matrix
        try:
            gen = await client.post(
                f"/api/workbench/sessions/{sid}/matrix/generate", headers=iv
            )
        finally:
            wb_mod._call_llm = original
        assert gen.status_code == 200, gen.text
        return sid


# ---------------------------------------------------------------- 纯函数


def test_focus_rows_puts_key_flags_first():
    """「本轮重点」优先于按状态排序：勾了却不出题，等于那个勾选框没用。"""
    rows = [
        {"capability": "Java", "status": "sufficient", "is_key": False},
        {"capability": "数据库", "status": "verify", "is_key": False},
        {"capability": "微服务", "status": "missing", "is_key": True},
    ]
    picked = qc.focus_rows(rows)
    assert [r["capability"] for r in picked] == ["微服务", "数据库", "Java"]

    # 全部证据充足时也不能空手而归 —— 至少要出一组题
    all_ok = [{"capability": "Java", "status": "sufficient", "is_key": False}]
    assert [r["capability"] for r in qc.focus_rows(all_ok)] == ["Java"]


@pytest.mark.parametrize(
    "duration,expected_budget", [(45, 40), (60, 55), (10, 10), (240, 235)]
)
def test_balance_minutes_fits_budget(duration: int, expected_budget: int):
    """模型给的耗时只是估计值，必须被压回「本轮时长 - 开场收尾预留」。"""
    nodes = [
        {"minutes": 30},
        {"minutes": 25},
        {"minutes": 20},
    ]
    budget = qc.balance_minutes(nodes, duration)
    assert budget == expected_budget
    assert sum(n["minutes"] for n in nodes) == budget
    assert all(qc.MIN_NODE_MINUTES <= n["minutes"] <= qc.MAX_NODE_MINUTES for n in nodes)


def test_unsupported_numbers_only_checks_multi_digit():
    """只看两位及以上的数字：「3 年」这种单位性数字查了全是误报。"""
    allowed = "工作 5 年，2022 年入职，QPS 3000"
    assert qc._unsupported_numbers("把 QPS 提到 9999", allowed) == ["9999"]
    assert qc._unsupported_numbers("有 3 年经验", allowed) == []
    assert qc._unsupported_numbers("2022 年那次故障", allowed) == []


# ---------------------------------------------------------------- 生成（服务层）


def test_generate_chain_persists_and_advances_status(monkeypatch):
    """C4 生成落库：节点数对齐重点项、耗时压回预算、会话推进到 s3_draft。"""
    sid = _run(_scenario_setup())
    nodes = [_fake_node("Java"), _fake_node("数据库"), _fake_node("微服务")]
    monkeypatch.setattr(qc, "_call_llm", _fake_chain(nodes))
    monkeypatch.setattr(qc, "retrieve", lambda capability, focus, top_k=3: _fake_hits(capability))

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            chain = await qc.generate_chain(db, user, session)
            assert len(chain.nodes) == 3
            assert chain.status == "ready"
            assert chain.error == ""
            # 45 分钟 - 5 分钟开场收尾 = 40 分钟预算
            assert chain.budget_minutes == 40
            assert sum(n.minutes for n in chain.nodes) == 40
            # 编的引用被过滤，只留真实检索到的标题
            assert chain.nodes[0].rag_refs == [HIT_TITLES[0]]
            assert chain.nodes[0].flagged is False
            # 检索命中要落库：UI 上要能证明「这题不是凭空编的」
            assert chain.rag_hits and HIT_TITLES[0] in str(chain.rag_hits)

            await db.refresh(session)
            assert session.status == "s3_draft"
            assert session.chain_status == "ready"
            assert session.chain_revision == 1

    _run(_inner())


def test_hallucination_rewrite_then_flag(monkeypatch):
    """幻觉门禁：命中就重写一次；重写后仍命中则打 flagged，绝不静默通过。"""
    sid = _run(_scenario_setup())
    nodes = [_fake_node("Java", bad_number=True)]
    monkeypatch.setattr(qc, "_call_llm", _fake_chain(nodes))
    monkeypatch.setattr(qc, "retrieve", lambda capability, focus, top_k=3: _fake_hits(capability))

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            chain = await qc.generate_chain(db, user, session)
            # 假 LLM 每次都返回同一个带 9999 的节点 → 重写无效 → 必须 flagged
            assert chain.nodes[0].flagged is True

    _run(_inner())


def test_generate_chain_requires_matrix():
    """没有矩阵就不出题：问题链是矩阵的展开，没有输入就没有输出。"""
    sid = _run(_scenario_setup())

    async def _reset_matrix() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            session.matrix_json = None
            session.matrix_revision = 0
            await db.commit()

    _run(_reset_matrix())

    async def _inner() -> None:
        from app.core.errors import AppError

        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            with pytest.raises(AppError) as exc:
                await qc.generate_chain(db, user, session)
            assert exc.value.detail["code"] == "workbench.chain_no_matrix"

    _run(_inner())


# ---------------------------------------------------------------- 接口层


class _FakeTask:
    id = "fake-task-id"


class _FakeTaskFn:
    @staticmethod
    def delay(*args, **kwargs) -> _FakeTask:
        return _FakeTask()


async def _scenario_permissions() -> None:
    from app.routers import workbench as router

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        base = f"/api/workbench/sessions/{sid}"

        # 1) HR 投递生成 → 400 read_only（HR 不撰写备考内容）
        denied = await client.post(base + "/chain/generate", headers=hr)
        assert denied.status_code == 400
        assert denied.json()["detail"]["code"] == "workbench.read_only"

        # 2) 没有矩阵时投递 → 400 chain_no_matrix
        no_matrix = await client.post(base + "/chain/generate", headers=iv)
        assert no_matrix.status_code == 400
        assert no_matrix.json()["detail"]["code"] == "workbench.chain_no_matrix"

        # 3) 有矩阵后投递成功：状态变 running，回执带 task_id
        import app.services.workbench as wb_mod

        async def _fake_matrix(prompt: str) -> wb_mod._MatrixLLM:
            return wb_mod._MatrixLLM(
                rows=[wb_mod._RowLLM(capability="Java", source="jd", status="verify", focus="GC")]
            )

        wb_mod._call_llm = _fake_matrix
        try:
            await client.post(base + "/matrix/generate", headers=iv)
        finally:
            wb_mod._call_llm = wb_mod._call_llm

        original = router.generate_question_chain
        router.generate_question_chain = _FakeTaskFn()
        try:
            ok = await client.post(base + "/chain/generate", headers=iv)
        finally:
            router.generate_question_chain = original
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "running"
        assert ok.json()["task_id"] == "fake-task-id"

        # 4) 还在跑的时候再点一次 → 400 chain_running（BR-12 防误触）
        again = await client.post(base + "/chain/generate", headers=iv)
        assert again.status_code == 400
        assert again.json()["detail"]["code"] == "workbench.chain_running"


def test_chain_generate_permissions():
    """只有被指派的面试官能投递；没有矩阵 / 正在生成都会被明确拦住。"""
    _run(_scenario_permissions())


async def _scenario_save() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        base = f"/api/workbench/sessions/{sid}"

        empty = await client.put(base + "/chain", json={"nodes": []}, headers=iv)
        assert empty.status_code == 400
        assert empty.json()["detail"]["code"] == "workbench.chain_invalid"

        blank = await client.put(
            base + "/chain",
            json={"nodes": [{"capability": "Java", "main_question": "  "}]},
            headers=iv,
        )
        assert blank.status_code == 400
        assert blank.json()["detail"]["code"] == "workbench.chain_invalid"

        ok = await client.put(
            base + "/chain",
            json={
                "nodes": [
                    {
                        "id": "n1",
                        "capability": "Java",
                        "main_question": "讲一次你定位过的线上问题",
                        "followups": [{"level": 1, "vague": "怎么定位的", "anti_fake": "是你自己做的吗"}],
                        "observations": ["能给出量化结果", "能讲清取舍"],
                        "minutes": 12,
                        "rag_refs": [HIT_TITLES[0]],
                    }
                ]
            },
            headers=iv,
        )
        assert ok.status_code == 200, ok.text
        chain = ok.json()["chain"]
        assert chain["revision"] == 1
        assert chain["nodes"][0]["minutes"] == 12
        assert chain["status"] == "ready"

        # 关掉页面再进来：草稿还在（中断友好）
        again = await client.get(base, headers=iv)
        assert again.json()["chain"]["nodes"][0]["main_question"] == "讲一次你定位过的线上问题"
        assert again.json()["status"] == "s3_draft"

        # 换一换指向不存在的节点 → 400
        missing = await client.post(base + "/chain/nodes/n99/regenerate", headers=iv)
        assert missing.status_code == 400
        assert missing.json()["detail"]["code"] == "workbench.node_not_found"


def test_save_chain_and_regenerate_validation():
    """空链 / 缺主问题被拦；保存后草稿可续改；换一换找不到节点有明确错误码。"""
    _run(_scenario_save())


def test_regenerate_node_keeps_others(monkeypatch):
    """「换一换」只换那一个节点，其余手改过的题不能被冲掉。"""
    sid = _run(_scenario_setup())
    monkeypatch.setattr(qc, "retrieve", lambda capability, focus, top_k=3: _fake_hits(capability))

    async def _seed_chain() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            session.chain_json = {
                "nodes": [
                    {"id": "n1", "capability": "Java", "main_question": "手改过的题", "followups": [], "observations": ["a"], "minutes": 10},
                    {"id": "n2", "capability": "数据库", "main_question": "另一个题", "followups": [], "observations": ["b"], "minutes": 10},
                ]
            }
            session.chain_revision = 1
            session.chain_status = "ready"
            await db.commit()

    _run(_seed_chain())

    monkeypatch.setattr(qc, "_call_llm", _fake_chain([_fake_node("数据库")]))

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await qc.regenerate_node(db, user, session, "n2")
            assert out.chain.nodes[0].main_question == "手改过的题"
            assert out.chain.nodes[0].id == "n1"
            assert out.chain.nodes[1].id == "n2"
            assert "数据库" in out.chain.nodes[1].capability

    _run(_inner())
