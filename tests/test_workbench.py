"""AI 备面工作台 Step 1 能力-证据矩阵（PRD 3.4.1 / §5.9 P09）。

与同目录其它测试同构：接口链路走 httpx + ASGITransport，每个用例一个 asyncio.run。
**LLM 一律 monkeypatch**（`workbench._call_llm`）—— 测试不该烧 token，
也不该因为云端抖动而红。这里锁的是「编排与落库」，不是模型输出质量。

三件必须锁死的事：
- **谁能写**：只有被指派的面试官；HR / HR 主管只读（2026-10-01 拍板）
- **重新生成不冲掉人工勾选**：`is_key` 按能力项名继承
- **生成的前置**：JD 未确认 / 简历为空时不许生成（对着空气备面没意义）
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.position import Position
from app.models.session import InterviewSession
from app.services import workbench as wb

RESUME = """郑十
邮箱 zhengshi@example.com  电话 13800138000  深圳
工作年限：5 年
2019-2022 G公司 后端工程师 订单系统 Java MySQL
2022-至今 H公司 高级工程师 微服务治理 Kubernetes Kafka
项目：库存中心重构 核心开发 Java Redis
技能：Java Redis Kafka MySQL Kubernetes
教育：某大学 软件工程 本科 2015-2019"""

# seed：1 admin / 3 hr_lead / 5 hr / 7 interviewer（陈技术）/ 8 interviewer2（刘架构）
IV_R1 = 7
IV_R2 = 8

COMPETENCIES = [
    {"text": "Java", "weight": 40},
    {"text": "数据库", "weight": 35},
    {"text": "微服务", "weight": 25},
]


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def _fake_llm(prompt: str, system: str = wb.MATRIX_SYSTEM_PROMPT) -> wb._MatrixLLM:
    """替掉真实 LLM 调用：测试锁的是编排与落库，不是模型输出质量。"""
    return _fake_rows()


def _fake_rows() -> wb._MatrixLLM:
    return wb._MatrixLLM(
        rows=[
            wb._RowLLM(
                capability="Java",
                source="jd",
                evidence="2022-至今 高级工程师 微服务治理",
                status="sufficient",
                focus="挑一个他主导的 Java 服务，问清线程模型与 GC 调优的实际收益",
            ),
            wb._RowLLM(
                capability="数据库",
                source="jd",
                evidence="库存中心重构 使用 MySQL Redis",
                status="verify",
                focus="追问一次真实慢查询的定位过程与索引取舍",
            ),
            wb._RowLLM(
                capability="1. 微服务",
                source="jd",
                evidence="",
                status="missing",
                focus="问一次线上故障的排查链路，判断治理经验是否来自自己动手",
            ),
            wb._RowLLM(
                capability="Kafka",
                source="resume",
                evidence="技能栏列出 Kafka",
                status="verify",
                focus="确认是消费端还是生产端经验，问清消息积压的处理方式",
            ),
        ]
    )


async def _login(client: AsyncClient, email: str = "hr@prepilot.dev") -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _new_position(client: AsyncClient, headers: dict[str, str]) -> int:
    created = await client.post(
        "/api/positions", json={"name": f"pytest-备面{uuid.uuid4().hex[:6]}"}, headers=headers
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
                {"type": "r2", "interviewer_id": IV_R2},
                {"type": "hr", "interviewer_id": 3},
            ]
        },
        headers=headers,
    )
    assert rounds.status_code == 200, rounds.text
    return pid


async def _session_id(client: AsyncClient, headers: dict[str, str], pid: int) -> int:
    """建候选人 → 确认（确认即派单）→ 返回首轮会话 id。"""
    mail = f"wb-{uuid.uuid4().hex[:12]}@example.com"
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
    sessions = confirmed.json()["sessions"]
    assert len(sessions) == 1
    return sessions[0]["id"]


def _set_jd_status(position_id: int, status: str) -> None:
    async def _inner() -> None:
        async with SessionLocal() as db:
            position = (await db.scalars(select(Position).where(Position.id == position_id))).first()
            assert position is not None
            position.jd_status = status
            await db.commit()

    _run(_inner())


# ---------------------------------------------------------------- 权限


async def _scenario_permissions() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)

        base = f"/api/workbench/sessions/{sid}"
        # 1) 被指派的面试官：可编辑
        iv = await _login(client, "interviewer@prepilot.dev")
        view = await client.get(base, headers=iv)
        assert view.status_code == 200, view.text
        body = view.json()
        assert body["can_edit"] is True
        assert body["read_only_reason"] == ""
        assert body["duration_minutes"] == 45
        assert body["blocked_reason"] == ""
        assert body["round_type"] == "r1"
        # 已实现的 5 步里第 1 步 current、其余 todo（未实现才标 disabled）
        assert [s["state"] for s in body["steps"]] == [
            "current",
            "todo",
            "todo",
            "todo",
            "todo",
        ]

        # 2) HR 能看不能写
        hr_view = await client.get(base, headers=hr)
        assert hr_view.status_code == 200
        assert hr_view.json()["can_edit"] is False
        assert hr_view.json()["read_only_reason"] == "read_only"

        denied = await client.put(
            base + "/matrix", json={"rows": [{"capability": "Java"}]}, headers=hr
        )
        assert denied.status_code == 400
        assert denied.json()["detail"]["code"] == "workbench.read_only"

        dur = await client.put(base + "/duration", json={"duration_minutes": 60}, headers=hr)
        assert dur.status_code == 400
        assert dur.json()["detail"]["code"] == "workbench.read_only"

        # 3) 没被指派的面试官：连看都不行（BR-16）
        iv2 = await _login(client, "interviewer2@prepilot.dev")
        other = await client.get(base, headers=iv2)
        assert other.status_code == 403

        # 4) 面试官本人改时长生效；越界走带 code 的 400（不是 422）
        ok = await client.put(base + "/duration", json={"duration_minutes": 60}, headers=iv)
        assert ok.status_code == 200, ok.text
        assert ok.json()["duration_minutes"] == 60

        too_short = await client.put(base + "/duration", json={"duration_minutes": 9}, headers=iv)
        assert too_short.status_code == 400
        assert too_short.json()["detail"]["code"] == "workbench.duration_invalid"


def test_workbench_permissions():
    """HR / HR 主管只读，未指派的面试官连看都看不到。

    注：这个用例一度因为 `def` 行被误删而从未执行过（只剩一个孤立 docstring），
    里面的 steps 断言还停在「只实现两步」的旧口径，恢复时一并更正。
    """
    _run(_scenario_permissions())


# ---------------------------------------------------------------- 生成与保存


async def _scenario_generate(monkeypatch) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        base = f"/api/workbench/sessions/{sid}"

        monkeypatch.setattr(wb, "_call_llm", _fake_llm)

        gen = await client.post(base + "/matrix/generate", headers=iv)
        assert gen.status_code == 200, gen.text
        body = gen.json()
        assert body["stale"] is False
        matrix = body["matrix"]
        assert matrix["generated"] is True
        assert matrix["revision"] == 1
        assert len(matrix["rows"]) == 4

        by_name = {r["capability"]: r for r in matrix["rows"]}
        # 权重来自 JD：简历补的行没有权重（前端留空，不显示 0）
        assert by_name["Java"]["weight"] == 40
        assert by_name["Kafka"]["source"] == "resume"
        assert by_name["Kafka"]["weight"] is None
        # 模型偶发吐出的编号前缀要被洗掉
        assert "微服务" in by_name
        # 未知状态兜底为「待核实」而不是自信地判充足/缺失
        assert by_name["微服务"]["status"] in ("sufficient", "verify", "missing")

        # 关掉页面再进来：草稿还在（P10 的「中断友好」前提）
        again = await client.get(base, headers=iv)
        assert again.status_code == 200
        assert len(again.json()["matrix"]["rows"]) == 4
        assert again.json()["matrix"]["revision"] == 1

        # 人工勾选「本轮重点」后重新生成：勾选不能被冲掉
        rows = again.json()["matrix"]["rows"]
        for r in rows:
            if r["capability"] == "Java":
                r["is_key"] = True
        saved = await client.put(
            base + "/matrix", json={"rows": rows, "revision": 1}, headers=iv
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["matrix"]["revision"] == 2
        assert saved.json()["stale"] is False

        gen2 = await client.post(base + "/matrix/generate", headers=iv)
        assert gen2.status_code == 200, gen2.text
        keys = [r["capability"] for r in gen2.json()["matrix"]["rows"] if r["is_key"]]
        assert keys == ["Java"]

        # revision 落后时保存：照常覆盖，但回 stale 让前端提示
        stale = await client.put(
            base + "/matrix",
            json={"rows": gen2.json()["matrix"]["rows"], "revision": 1},
            headers=iv,
        )
        assert stale.status_code == 200
        assert stale.json()["stale"] is True


def test_generate_then_regenerate_keeps_key_flags(monkeypatch):
    """C3 生成落库；重新生成按能力项名继承「本轮重点」。"""
    _run(_scenario_generate(monkeypatch))


async def _scenario_save_validation() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        iv = await _login(client, "interviewer@prepilot.dev")
        base = f"/api/workbench/sessions/{sid}"

        # 矩阵不能为空
        empty = await client.put(base + "/matrix", json={"rows": []}, headers=iv)
        assert empty.status_code == 400
        assert empty.json()["detail"]["code"] == "workbench.matrix_invalid"

        # 只有空能力名的行：过滤后等价为空，同样拦住
        blank = await client.put(
            base + "/matrix", json={"rows": [{"capability": "  "}]}, headers=iv
        )
        assert blank.status_code == 400
        assert blank.json()["detail"]["code"] == "workbench.matrix_invalid"

        # 手加行标 manual，未知状态兜底 verify（下限 2 项，BR-07 兜底）
        ok = await client.put(
            base + "/matrix",
            json={
                "rows": [
                    {
                        "capability": "沟通表达",
                        "source": "manual",
                        "status": "unknown-status",
                        "focus": "让候选人复述一次跨团队推动的经历",
                    },
                    {
                        "capability": "系统设计",
                        "source": "manual",
                        "status": "verify",
                        "focus": "给出一个高并发场景让他拆解",
                    },
                ]
            },
            headers=iv,
        )
        assert ok.status_code == 200, ok.text
        row = ok.json()["matrix"]["rows"][0]
        assert row["source"] == "manual"
        assert row["status"] == "verify"
        assert row["weight"] is None


def test_save_matrix_validation():
    """空矩阵 / 空能力名被拦；手加行与未知状态有兜底。"""
    _run(_scenario_save_validation())


async def _scenario_setup() -> tuple[int, int]:
    """建好「职位（JD 已确认 + 三轮）+ 候选人 + 首轮会话」，返回 (pid, sid)。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)
        return pid, sid


async def _scenario_generate_blocked(sid: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        iv = await _login(client, "interviewer@prepilot.dev")
        base = f"/api/workbench/sessions/{sid}"

        # JD 被撤回到未确认：没有可对齐的岗位标准，不许生成
        blocked = await client.get(base, headers=iv)
        assert blocked.json()["blocked_reason"] == "jd_not_confirmed"

        gen = await client.post(base + "/matrix/generate", headers=iv)
        assert gen.status_code == 400
        assert gen.json()["detail"]["code"] == "workbench.no_jd"


def test_generate_requires_confirmed_jd():
    """JD 未确认时不生成矩阵（前置不满足，而不是给一份编造的矩阵）。"""
    pid, sid = _run(_scenario_setup())
    # 改库必须在独立的事件循环里做：ASGITransport 内部有自己的 portal，
    # 在 client 上下文里套 SessionLocal 会踩 MissingGreenlet
    _set_jd_status(pid, "draft")
    _run(_scenario_generate_blocked(sid))


# ---------------------------------------------------------------- 看板入口


async def _scenario_board_entry() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hr = await _login(client)
        pid = await _new_position(client, hr)
        sid = await _session_id(client, hr, pid)

        # 面试官本人：卡片带会话 id 且可备面
        iv = await _login(client, "interviewer@prepilot.dev")
        board = await client.get("/api/board", headers=iv)
        assert board.status_code == 200
        cards = [c for col in board.json()["columns"] for c in col["cards"]]
        mine = [c for c in cards if c["session_id"] == sid]
        assert len(mine) == 1
        assert mine[0]["can_prepare"] is True

        # HR 视角看同一张卡：能看进度，不能备面（HR 不撰写面评）
        hr_board = await client.get("/api/board", headers=hr)
        hr_cards = [c for col in hr_board.json()["columns"] for c in col["cards"]]
        hr_mine = [c for c in hr_cards if c["session_id"] == sid]
        assert len(hr_mine) == 1
        assert hr_mine[0]["can_prepare"] is False


def test_board_card_exposes_prepare_entry():
    """看板卡片下发 session_id 与 can_prepare：只有本人被指派的会话能进。"""
    _run(_scenario_board_entry())


@pytest.mark.parametrize("minutes,ok", [(10, True), (240, True), (9, False), (241, False)])
def test_duration_bounds(monkeypatch, minutes: int, ok: bool):
    """时长上下限走服务层校验，返回带 code 的 400（不是 422）。"""
    from app.core.errors import AppError

    async def _inner() -> None:
        async with SessionLocal() as db:
            from app.models.session import InterviewSession
            from app.models.user import User
            from app.schemas.workbench import DurationIn

            session = InterviewSession(id=0, interviewer_id=IV_R1, status="s1_draft")
            user = await db.get(User, IV_R1)
            if ok:
                got = await wb.update_duration(db, user, session, DurationIn(duration_minutes=minutes))
                assert got == minutes
            else:
                with pytest.raises(AppError) as exc:
                    await wb.update_duration(db, user, session, DurationIn(duration_minutes=minutes))
                assert exc.value.detail["code"] == "workbench.duration_invalid"

    _run(_inner())


# ---------------------------------------------------------------- 进度条


def _blank_session(**kw) -> InterviewSession:
    """内存里的会话壳子：`_steps` 是纯函数，不需要真的落库。"""
    base = dict(
        id=1,
        interviewer_id=IV_R1,
        status="s1_draft",
        matrix_json={},
        chain_json={},
        chain_status="idle",
        fairness_json={},
        evaluation_json={},
    )
    base.update(kw)
    return InterviewSession(**base)


def test_steps_only_follow_artifacts():
    """进度条只认产物：会话状态跑到产物前面，也不许提前打勾。

    踩过的坑：早期按 `session.status` 换算第几步、再用 `i < current` 打勾 ——
    面评写完后状态推进到 s4/s5，Step3 就被判成 done。于是「问题链刚重新生成，
    公平性却显示已通过」：面试官看到绿灯就往下走，合规检查等于白设。
    """
    s = _blank_session()
    assert [x.state for x in wb._steps(s)] == ["current", "todo", "todo", "todo", "todo"]

    s.matrix_json = {"rows": [{"capability": "Java"}]}
    assert [x.state for x in wb._steps(s)] == ["done", "current", "todo", "todo", "todo"]

    s.chain_status = "running"  # 生成中不算完成
    assert [x.state for x in wb._steps(s)] == ["done", "current", "todo", "todo", "todo"]
    s.chain_status = "ready"
    s.chain_json = {"nodes": [{"id": "n1"}]}
    assert [x.state for x in wb._steps(s)] == ["done", "done", "current", "todo", "todo"]

    s.fairness_json = {"result": "block", "scanned_at": "2026-10-04T00:00:00"}
    assert [x.state for x in wb._steps(s)] == ["done", "done", "current", "todo", "todo"]
    s.fairness_json = {"result": "warn", "scanned_at": "2026-10-04T00:00:00"}
    assert [x.state for x in wb._steps(s)] == ["done", "done", "done", "current", "todo"]

    # 关键：状态已经跑到 s5，产物却只到 Step3 —— 不许因为状态提前打勾
    s.status = "s5_draft"
    assert [x.state for x in wb._steps(s)] == ["done", "done", "done", "current", "todo"]

    s.evaluation_json = {"complete": True}
    assert [x.state for x in wb._steps(s)] == ["done", "done", "done", "done", "current"]

    # 提交是终态：五步全打勾，不再区分产物
    s.status = "submitted"
    assert [x.state for x in wb._steps(s)] == ["done"] * 5


def test_regenerating_chain_invalidates_fairness():
    """改了题目，上一次的公平性结论就作废 —— 它是对旧文本给的。"""
    s = _blank_session(
        status="s4_draft",
        matrix_json={"rows": [{"capability": "Java"}]},
        chain_status="ready",
        chain_json={"nodes": [{"id": "n1"}]},
        fairness_json={"result": "pass", "scanned_at": "2026-10-04T00:00:00"},
    )
    assert [x.state for x in wb._steps(s)][2] == "done"

    wb.invalidate_fairness(s)
    assert s.fairness_json == {}
    assert s.fairness_revision == 0
    # 会话状态不回退：只是要重新扫一次，不需要从头备面
    assert s.status == "s4_draft"
    assert [x.state for x in wb._steps(s)] == ["done", "done", "current", "todo", "todo"]
