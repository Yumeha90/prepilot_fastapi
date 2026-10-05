"""AI 备面工作台 Step 3 公平性检查（PRD §3.4.3 / §6.5 P11，C5）。

LLM 与向量检索一律 monkeypatch（`fairness._call_llm` / `fairness.retrieve`），
测试锁的是**判定口径与处置规则**，不是模型输出质量。

必须锁死的六条口径：
- **五类受保护特征命中即阻断**，且模型无权降级（BLOCK 级零容忍）
- **三语词典都要生效**（只覆盖中文等于另外两种语言完全不设防）
- **正常专业提问不能误报**（误报一多，面试官连真红线一起无视）
- **模型编的片段要丢弃**（页面出现一条不存在的依据，比没有依据更糟）
- **阻断项不允许「保留」**（BR-05：必须改写）
- **采纳改写只处置这一条**（2026-10-05 改）：只复检被改写的那一个节点，其他条目
  的状态原样保留；改写后仍踩同一条红线 → 该条回到待处置，不许放行
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import SessionLocal, engine
from app.core.errors import bad_request
from app.main import app
from app.models.session import InterviewSession
from app.models.user import User
from app.services import fairness as fs
from app.services import question_chain as qc

# seed：7 = interviewer（陈技术），3 = hr
IV_R1 = 7
HR = 5

COMPLIANCE_TITLES = [
    "公序良俗：婚姻与生育相关问题一律禁问",
    "公司招聘制度：面试提问红线清单",
]


def _run(coro):
    async def wrapper():
        await engine.dispose()
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "Prepilot@123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _fake_hits(node: dict, category: str = "", *, degraded: set[str] | None = None) -> list:
    from app.ai import rag

    return [
        rag.AssetHit(title=COMPLIANCE_TITLES[0], text="禁问婚育：结婚了吗、打算什么时候要孩子…", score=0.62),
        rag.AssetHit(title=COMPLIANCE_TITLES[1], text="一律不得询问、不得记录、不得作为评价依据。", score=0.51),
    ]


# ---------------------------------------------------------------- 规则层


@pytest.mark.parametrize(
    "text,category",
    [
        ("你打算什么时候要孩子？", "marriage"),
        ("结婚了吗？", "marriage"),
        ("Are you married?", "marriage"),
        ("結婚していますか？", "marriage"),
        ("你 35 岁还能适应高强度工作吗？", "age"),
        ("这个岗位限 30 岁以下", "age"),
        ("How old are you?", "age"),
        ("女生做技术后劲不足吧？", "gender"),
        ("该岗位限男性", "gender"),
        ("本地人优先，对吧？", "region"),
        ("你是哪个省的？", "region"),
        ("有没有既往病史？", "health"),
        ("体检乙肝这一项是什么结果？", "health"),
    ],
)
def test_rules_catch_hard_red_lines(text: str, category: str):
    """五类红线**三语**都要命中：只覆盖中文等于另外两种语言完全不设防。"""
    nodes = [{"id": "n1", "capability": "通用", "main_question": text}]
    hits = fs.fairness_rules.scan_nodes(nodes)
    assert [h.category for h in hits] == [category], f"{text} 未命中 {category}"
    assert hits[0].level == "block"


@pytest.mark.parametrize(
    "text",
    [
        "讲一次你推动多方达成一致的经历",
        "这个系统怎么排查 single point of failure？",
        "如何定位慢 SQL，有没有量化过优化前后的差异？",
        "说一次你在线上故障里做的取舍",
    ],
)
def test_rules_do_not_false_positive(text: str):
    """正常专业提问不能误报 —— 误报一多，面试官会对真正的红线也视而不见。"""
    nodes = [{"id": "n1", "capability": "系统设计", "main_question": text}]
    assert fs.fairness_rules.scan_nodes(nodes) == []


def test_rules_scan_every_field_of_node():
    """追问与观察点同样要扫：观察点是写进面评的判断依据，也能带歧视表述。"""
    nodes = [
        {
            "id": "n1",
            "capability": "协作",
            "main_question": "讲一次跨部门协作的经历",
            "followups": [{"level": 1, "vague": "当时谁带的孩子", "anti_fake": "怎么证明是你做的"}],
            "observations": ["能讲清取舍", "年纪偏大可能学不动"],
        }
    ]
    hits = fs.fairness_rules.scan_nodes(nodes)
    assert {(h.category, h.field) for h in hits} == {
        ("marriage", "followup:1:vague"),
        ("age", "observation:1"),
    }


def test_result_is_three_state():
    """三态结论：有未处置阻断 → block；只有警告 → warn；都处置完 → pass。"""
    assert fs.result_of([{"level": "block", "disposition": "pending"}]) == "block"
    assert fs.result_of([{"level": "warn", "disposition": "pending"}]) == "warn"
    assert fs.result_of([{"level": "warn", "disposition": "accepted"}]) == "pass"
    assert fs.result_of([]) == "pass"


# ---------------------------------------------------------------- 合并与校验


def _nodes_with(question: str) -> list[dict]:
    return [
        {
            "id": "n1",
            "capability": "系统设计",
            "main_question": question,
            "followups": [{"level": 1, "vague": "当时怎么定位的", "anti_fake": "怎么证明是你做的"}],
            "observations": ["能讲清取舍"],
        }
    ]


def test_merge_drops_fabricated_snippet():
    """模型编的片段要丢弃：页面出现一条不存在的依据，比没有依据更糟。"""
    nodes = _nodes_with("讲一次你推动多方达成一致的经历")
    fake = fs._ScanLLM(
        findings=[
            fs._FindingLLM(
                node_id="n1",
                category="leading",
                snippet="这句话根本不在原文里",
                reason="诱导性提问",
                suggestion="讲一次你推动多方达成一致的经历",
            )
        ]
    )
    merged = fs._merge(nodes, {"n1": _fake_hits(nodes[0])}, [], fake)
    assert merged == []


def test_llm_cannot_downgrade_protected_category():
    """模型无权把红线降级成警告（BLOCK 级零容忍）。"""
    nodes = _nodes_with("你打算什么时候要孩子？")
    fake = fs._ScanLLM(
        findings=[
            fs._FindingLLM(
                node_id="n1", category="marriage", level="warn",
                snippet="你打算什么时候要孩子", reason="婚育询问",
                suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                refs=[COMPLIANCE_TITLES[0], "《根本不存在的制度》"],
            )
        ]
    )
    merged = fs._merge(nodes, {"n1": _fake_hits(nodes[0])}, fs.fairness_rules.scan_nodes(nodes), fake)
    assert len(merged) == 1
    assert merged[0]["level"] == "block"
    assert merged[0]["source"] == "rule"
    assert merged[0]["field"] == "main_question"
    # 编的依据被过滤，只留真实检索到的标题
    assert merged[0]["refs"] == [COMPLIANCE_TITLES[0]]
    assert merged[0]["suggestion"].startswith("该岗位每月")


def test_checks_table_lists_all_categories():
    """六行检查表全列出来：没命中的类别也要出现，否则看不出到底查了什么。"""
    checks = fs._checks([{"category": "marriage", "level": "block", "disposition": "pending"}])
    assert [c["key"] for c in checks] == list(fs.fairness_rules.CHECK_ORDER)
    assert next(c for c in checks if c["key"] == "marriage")["count"] == 1
    assert next(c for c in checks if c["key"] == "age")["count"] == 0


# ---------------------------------------------------------------- 服务层


def _seed_chain(question: str) -> int:
    """同步地造好会话 + 问题链（不依赖 Celery worker）。"""

    async def _inner() -> int:
        from test_question_chain import _new_position, _session_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            hr = await _login(client, "hr@prepilot.dev")
            pid = await _new_position(client, hr)
            sid = await _session_id(client, hr, pid)

        import app.services.workbench as wb_mod

        async def _fake_matrix(
            prompt: str, system: str = wb_mod.MATRIX_SYSTEM_PROMPT
        ):
            return wb_mod._MatrixLLM(
                rows=[
                    wb_mod._RowLLM(capability="系统设计", source="jd", evidence="微服务治理", status="verify", focus="故障排查"),
                    # 下限 2 项（MIN_MATRIX_ROWS）：1 项会被生成拦下
                    wb_mod._RowLLM(capability="数据库", source="jd", evidence="MySQL 索引", status="verify", focus="慢查询"),
                ]
            )

        original_matrix = wb_mod._call_llm
        wb_mod._call_llm = _fake_matrix
        try:
            async with SessionLocal() as db:
                session = await db.get(InterviewSession, sid)
                user = await db.get(User, IV_R1)
                await wb_mod.generate_matrix(db, user, session)
        finally:
            wb_mod._call_llm = original_matrix

        async def _fake_chain(prompt: str, system: str = qc.CHAIN_SYSTEM_PROMPT):
            return qc._ChainLLM(
                nodes=[
                    qc._NodeLLM(
                        capability="系统设计",
                        main_question=question,
                        followups=[qc._FollowupLLM(level=1, vague="当时怎么定位的", anti_fake="怎么证明是你做的")],
                        observations=["能说出量化结果"],
                        minutes=10,
                    )
                ]
            )

        original_chain = qc._call_llm
        qc._call_llm = _fake_chain
        qc.retrieve = lambda capability, focus, top_k=3: []
        try:
            async with SessionLocal() as db:
                session = await db.get(InterviewSession, sid)
                user = await db.get(User, IV_R1)
                await qc.generate_chain(db, user, session)
        finally:
            qc._call_llm = original_chain
        return sid

    return _run(_inner())


def test_scan_degrades_when_compliance_library_down(monkeypatch):
    """向量库连不上不整块失败：规则层照判，只是没有依据资料，且必须显式标注。

    反过来「静默通过」比失败更糟 —— 面试官会以为查过且没问题。
    """
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(findings=[])

    def _down(query: str, top_k: int = 2, doc_types=None):
        raise bad_request(fs.ErrorCode.WORKBENCH_RAG_UNAVAILABLE, "向量库不可用")

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs.rag, "search_compliance", _down)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await fs.scan(db, user, session)
            # 规则层不依赖检索：婚育红线照旧命中并阻断
            assert out.result == "block"
            assert [f.category for f in out.findings] == ["marriage"]
            assert out.findings[0].refs == []
            assert out.rag_degraded is True

    _run(_inner())


def test_scan_still_fails_on_non_rag_error(monkeypatch):
    """只降级「向量库不可用」；别的错误照抛，不能把真故障也吞掉。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(findings=[])

    def _boom(query: str, top_k: int = 2, doc_types=None):
        raise bad_request("common.llm_failed", "别的故障")

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs.rag, "search_compliance", _boom)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            with pytest.raises(Exception) as exc:
                await fs.scan(db, user, session)
            assert "llm_failed" in str(exc.value.detail)

    _run(_inner())


def test_scan_blocks_and_locates_the_question(monkeypatch):
    """扫描出阻断项：结论三态、定位到字段、给出改写建议。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1",
                    category="marriage",
                    snippet="你打算什么时候要孩子",
                    reason="直接询问生育计划，违反妇女权益保障法第四十三条",
                    suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                    refs=[COMPLIANCE_TITLES[0]],
                )
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await fs.scan(db, user, session)
            assert out.result == "block"
            assert out.nodes_scanned == 1
            assert len(out.findings) == 1
            f = out.findings[0]
            assert f.category == "marriage"
            assert f.field == "main_question"
            # 卡片要给出整句原文：只给命中片段，面试官得自己回问题链里找那句话
            assert f.original_text == "你打算什么时候要孩子？"
            assert f.suggestion
            assert f.refs == [COMPLIANCE_TITLES[0]]
            assert out.rag_hits and COMPLIANCE_TITLES[0] in str(out.rag_hits)

    _run(_inner())


def test_block_cannot_be_accepted(monkeypatch):
    """BR-05：阻断级必须改写，不给「保留」这个口子。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(findings=[])

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await fs.scan(db, user, session)
            assert out.result == "block"
            finding_id = out.findings[0].id
            try:
                await fs.accept_finding(
                    db, user, session, finding_id, fs.FairnessAcceptIn(reason="就想问问")
                )
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "detail", {}).get("code") == "workbench.fairness_block_required"
            else:
                raise AssertionError("阻断项不该能被保留")

    _run(_inner())


def test_apply_rewrite_replaces_text_and_rescans(monkeypatch):
    """采纳改写 → 文本被替换 → **重新扫描**（改写本身也可能仍踩线）。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1",
                    category="marriage",
                    snippet="你打算什么时候要孩子",
                    reason="直接询问生育计划",
                    suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                )
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            first = await fs.scan(db, user, session)
            assert first.result == "block"
            out = await fs.apply_rewrite(db, user, session, first.findings[0].id)
            # 改写后已无命中 → 通过
            assert out.result == "pass"
            await db.refresh(session)
            assert "出差" in (session.chain_json or {})["nodes"][0]["main_question"]

    _run(_inner())


def test_apply_rewrite_only_touches_that_finding(monkeypatch):
    """采纳一条改写**只处置这一条**（2026-10-05 修）：其他条目的等级 / 片段 / 处置状态一律不变。

    整链重扫会让「我改了一处」变成「整份结论重新洗一遍」：其他命中因为模型这次没报
    出来而集体消失，页面直接变绿 —— 面试官看到的通过，其实只是重抽了一次签。
    """
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        # 复检时 prompt 里已经是改写后的问法 → 不再命中
        if "跨城出差" in prompt:
            return fs._ScanLLM(findings=[])
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1",
                    category="marriage",
                    snippet="你打算什么时候要孩子",
                    reason="直接询问生育计划",
                    suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                ),
                fs._FindingLLM(
                    node_id="n2",
                    category="leading",
                    level="warn",
                    snippet="你应该也认同加班是必须的",
                    reason="预设了候选人一定认同加班",
                    suggestion="你怎么看待项目紧张期的排期安排",
                ),
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            data = dict(session.chain_json or {})
            data["nodes"] = [
                data["nodes"][0],
                {
                    "id": "n2",
                    "capability": "协作",
                    "main_question": "你应该也认同加班是必须的，对吧",
                    "followups": [],
                    "observations": [],
                    "minutes": 5,
                },
            ]
            session.chain_json = data
            await db.commit()
            await db.refresh(session)

            user = await db.get(User, IV_R1)
            first = await fs.scan(db, user, session)
            assert first.result == "block"
            assert len(first.findings) == 2
            n1 = next(f for f in first.findings if f.node_id == "n1")
            n2 = next(f for f in first.findings if f.node_id == "n2")

            out = await fs.apply_rewrite(db, user, session, n1.id)

            # 被采纳的那条：已改写、不再计入结论
            new1 = next(f for f in out.findings if f.node_id == "n1")
            assert new1.disposition == "rewritten"
            assert "出差" in new1.rewritten_to
            # 改写前的原句要留着，和「已改写为」对照着看
            assert new1.original_text == "你打算什么时候要孩子？"
            # 另一条：一个字都没动（id / 等级 / 片段 / 状态全部保留）
            new2 = next(f for f in out.findings if f.node_id == "n2")
            assert new2.id == n2.id
            assert new2.level == n2.level == "warn"
            assert new2.category == n2.category == "leading"
            assert new2.snippet == n2.snippet
            assert new2.disposition == "pending"
            # 结论只由剩下的那一条决定：走掉的是阻断，剩下的警告 → warn
            assert out.result == "warn"
            # 第二个节点的题目没被动过
            await db.refresh(session)
            nodes = (session.chain_json or {})["nodes"]
            assert nodes[1]["main_question"] == "你应该也认同加班是必须的，对吧"

    _run(_inner())


def test_all_blocks_resolved_then_result_becomes_pass(monkeypatch):
    """阻断项一条条处置：处置完一条仍是 block，全部处置完才 pass。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        # 复检时 prompt 里已经是改写后的问法
        if "出差" in prompt or "倒班" in prompt:
            return fs._ScanLLM(findings=[])
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1", category="marriage",
                    snippet="你打算什么时候要孩子", reason="直接询问生育计划",
                    suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                ),
                fs._FindingLLM(
                    node_id="n2", category="marriage",
                    snippet="结婚了吗", reason="直接询问婚姻状况",
                    suggestion="该岗位需要倒班，你能否接受",
                ),
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            data = dict(session.chain_json or {})
            data["nodes"] = [
                data["nodes"][0],
                {
                    "id": "n2", "capability": "协作", "main_question": "结婚了吗？",
                    "followups": [], "observations": [], "minutes": 5,
                },
            ]
            session.chain_json = data
            await db.commit()
            await db.refresh(session)

            user = await db.get(User, IV_R1)
            first = await fs.scan(db, user, session)
            assert first.result == "block"
            assert len(first.findings) == 2

            f1, f2 = first.findings
            out = await fs.apply_rewrite(db, user, session, f1.id)
            # 还有一条没处置 → 结论不能变绿
            assert out.result == "block"
            assert [f.disposition for f in out.findings] == ["rewritten", "pending"]

            out = await fs.apply_rewrite(db, user, session, f2.id)
            assert out.result == "pass"
            assert {f.disposition for f in out.findings} == {"rewritten"}
            # 两条题目都被真的改写过
            await db.refresh(session)
            texts = [n["main_question"] for n in (session.chain_json or {})["nodes"]]
            assert "出差" in texts[0] and "倒班" in texts[1]

    _run(_inner())


def test_apply_rewrite_reopens_finding_when_still_hit(monkeypatch):
    """改写后仍踩同一条红线 → 这一条回到待处置，绝不能因为「点过采纳」就放行。"""
    sid = _seed_chain("你打算什么时候要孩子？")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1",
                    category="marriage",
                    snippet="你打算什么时候要孩子",
                    reason="直接询问生育计划",
                    suggestion="该岗位每月有约 4 次跨城出差，你能否接受",
                )
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            first = await fs.scan(db, user, session)
            assert first.result == "block"
            # 改写的问法里仍然带婚育 → 复检必然再次命中
            out = await fs.apply_rewrite(
                db,
                user,
                session,
                first.findings[0].id,
                fs.FairnessRewriteIn(suggestion="打算什么时候结婚？该岗位每月有约 4 次出差"),
            )
            assert out.result == "block"
            assert out.findings[0].disposition == "pending"
            assert out.notice == fs.NOTICE_STILL_HIT

    _run(_inner())


def test_accept_warn_requires_reason(monkeypatch):
    """警告级可以保留，但必须写明原因 —— 留痕是这个动作的唯一意义。"""
    sid = _seed_chain("讲一次你推动多方达成一致的经历")

    async def _fake_llm(prompt: str) -> fs._ScanLLM:
        return fs._ScanLLM(
            findings=[
                fs._FindingLLM(
                    node_id="n1", category="leading", level="warn",
                    snippet="讲一次你推动多方达成一致的经历",
                    reason="预设了候选人一定推动过多方协作",
                    suggestion="讲一次你参与的跨部门协作，你具体做了什么",
                )
            ]
        )

    monkeypatch.setattr(fs, "_call_llm", _fake_llm)
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            out = await fs.scan(db, user, session)
            assert out.result == "warn"
            finding_id = out.findings[0].id

            try:
                await fs.accept_finding(db, user, session, finding_id, fs.FairnessAcceptIn(reason=""))
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "detail", {}).get("code") == "workbench.fairness_reason_required"
            else:
                raise AssertionError("没写原因不该放行")

            out = await fs.accept_finding(
                db, user, session, finding_id, fs.FairnessAcceptIn(reason="该岗位必须做过跨部门协调")
            )
            assert out.result == "pass"
            assert out.findings[0].disposition == "accepted"
            assert out.findings[0].accepted_reason

    _run(_inner())


def test_hr_cannot_scan(monkeypatch):
    """只有被指派的面试官能扫（HR 一律只读，与 P09 同一口径）。"""
    sid = _seed_chain("讲一次你推动多方达成一致的经历")
    monkeypatch.setattr(fs, "retrieve", _fake_hits)

    async def _inner() -> None:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            hr = await db.get(User, HR)
            try:
                await fs.scan(db, hr, session)
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "detail", {}).get("code") == "workbench.read_only"
            else:
                raise AssertionError("HR 不该能触发公平性扫描")

    _run(_inner())


def test_scan_requires_chain(monkeypatch):
    """没有问题链就没有检查对象 —— 不该给出一份「通过」的假结论。"""

    async def _inner() -> None:
        from test_question_chain import _new_position, _session_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            hr = await _login(client, "hr@prepilot.dev")
            pid = await _new_position(client, hr)
            sid = await _session_id(client, hr, pid)

        async with SessionLocal() as db:
            session = await db.get(InterviewSession, sid)
            user = await db.get(User, IV_R1)
            try:
                await fs.scan(db, user, session)
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "detail", {}).get("code") == "workbench.fairness_no_chain"
            else:
                raise AssertionError("没有问题链不该能扫描")

    _run(_inner())
