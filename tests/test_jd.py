"""JD 文件上传抽取与 AI 拆解（PRD 3.2 第二段）。

设计原则：**CI 不依赖外网**。真实的千问调用只在本地用脚本手动验证，
这里一律给 `structured_call` 打桩，专门覆盖「模型输出不可信」的后处理逻辑
（脏前缀、重复项、超 5 项、权重均分）。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import engine
from app.main import app
from app.schemas.position import CompetencyIn
from app.services import jd_ai
from app.services.jd_extract import extract_text


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


def _docx_bytes(lines: list[str]) -> bytes:
    import io

    import docx

    document = docx.Document()
    for line in lines:
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _image_pdf_bytes() -> bytes:
    """图片型 PDF（无文本层）—— 走 Pillow 生成，用于验证「不做 OCR」的报错路径。"""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((20, 90), "Senior Backend Engineer", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PDF")
    return buf.getvalue()


# ---------------------------------------------------------------- 抽取（纯函数）


def test_extract_plain_text():
    assert extract_text("jd.txt", "岗位：中级后端工程师\r\n\r\n\r\n要求：3 年以上经验".encode()) == (
        "岗位：中级后端工程师\n\n要求：3 年以上经验"
    )
    assert extract_text("jd.md", "# 前端工程师\n- React".encode("utf-8")) == (
        "# 前端工程师\n- React"
    )


def test_extract_docx():
    text = extract_text("jd.docx", _docx_bytes(["中级后端工程师", "熟悉 Go 或 Python"]))
    assert "中级后端工程师" in text
    assert "熟悉 Go 或 Python" in text


def test_extract_rejects_unsupported():
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        extract_text("jd.doc", b"whatever")
    assert exc.value.detail["code"] == "position.jd_file_unsupported"

    with pytest.raises(AppError):
        extract_text("jd.png", b"\x89PNG")


def test_extract_rejects_magic_mismatch():
    """扩展名伪造：内容不是 PDF 却叫 .pdf。"""
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        extract_text("jd.pdf", b"just plain text")
    assert exc.value.detail["code"] == "position.jd_file_unsupported"


def test_extract_rejects_too_large():
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        extract_text("jd.txt", b"x" * (5 * 1024 * 1024 + 1))
    assert exc.value.detail["code"] == "position.jd_file_too_large"


def test_extract_rejects_scanned_pdf():
    """扫描件 / 图片型 PDF：不做 OCR，明确报错而不是返回空串。"""
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        extract_text("scan.pdf", _image_pdf_bytes())
    assert exc.value.detail["code"] == "position.jd_file_no_text"


# ---------------------------------------------------------------- AI 拆解（打桩）


def _stub_structure(monkeypatch, payload: dict):
    """让 structured_call 返回受控的脏数据，验证后处理是否可靠。"""

    def fake(schema, system, user, **kwargs):  # noqa: ANN001
        return schema(**payload)

    monkeypatch.setattr(jd_ai, "structured_call", fake)


def test_parse_cleans_dirty_output(monkeypatch):
    """实测过模型会吐出带 `L` 前缀的条目，且会重复、超 5 项。"""
    _stub_structure(
        monkeypatch,
        {
            "hard_gates": ["L本科及以上", "本科及以上", "3 年以上经验", ""],
            "competencies": ["LGo/Python", "SQL 优化", "微服务", "微服务", "K8s", "英语"],
            "bonuses": ["开源贡献"],
        },
    )
    out = _run(jd_ai.parse_jd("岗位：中级后端工程师，要求本科及以上、3 年以上经验……" * 3))

    assert out["hard_gates"] == ["本科及以上", "3 年以上经验"]  # 去前缀 + 去重 + 去空
    assert len(out["competencies"]) == 5  # 每类 ≤5 项
    texts = [c["text"] for c in out["competencies"]]
    assert texts[0] == "Go/Python"
    assert len(set(texts)) == len(texts)
    # 权重均分且落在 5 的倍数上（BR-20 步进 5）
    weights = [c["weight"] for c in out["competencies"]]
    assert sum(weights) == 100
    assert all(w % 5 == 0 for w in weights)
    assert all(c["id"] for c in out["competencies"])  # slug 不能为空
    assert out["bonuses"] == ["开源贡献"]


def test_parse_notice_when_few_competencies(monkeypatch):
    _stub_structure(
        monkeypatch,
        {"hard_gates": ["本科及以上"], "competencies": ["Go", "SQL"], "bonuses": []},
    )
    out = _run(jd_ai.parse_jd("岗位：后端工程师，要求本科及以上，熟悉 Go 与 SQL。" * 2))
    assert len(out["competencies"]) == 2
    assert "至少需要 3 项" in out["notice"]


def test_parse_rejects_too_short():
    from app.core.errors import AppError

    with pytest.raises(AppError) as exc:
        _run(jd_ai.parse_jd("太短"))
    assert exc.value.detail["code"] == "position.jd_text_too_short"


# ---------------------------------------------------------------- 接口链路


async def _scenario_jd_apis(monkeypatch):
    _stub_structure(
        monkeypatch,
        {
            "hard_gates": ["本科及以上", "3 年以上后端经验"],
            "competencies": ["Go/Python", "数据库与 SQL 优化", "微服务与消息队列"],
            "bonuses": ["Kubernetes"],
        },
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        login = await client.post(
            "/api/auth/login", json={"email": "hr@prepilot.dev", "password": "Prepilot@123"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # 抽取：文件不落库，只回文本
        uploaded = await client.post(
            "/api/jd/extract",
            headers=headers,
            files={"file": ("jd.txt", "岗位：中级后端工程师\n要求：3 年以上经验".encode(), "text/plain")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert "中级后端工程师" in uploaded.json()["text"]

        # 拆解：结果不落库，仅回填表单
        parsed = await client.post(
            "/api/jd/parse",
            headers=headers,
            json={"raw_text": "岗位：中级后端工程师。要求本科及以上、3 年以上后端经验……"},
        )
        assert parsed.status_code == 200, parsed.text
        body = parsed.json()
        assert body["hard_gates"] == ["本科及以上", "3 年以上后端经验"]
        assert [c["weight"] for c in body["competencies"]] == [35, 35, 30]
        assert body["notice"] == ""

        # 未登录不可调用
        anon = await client.post("/api/jd/parse", json={"raw_text": "岗位：后端工程师，要求熟悉 Go。" * 3})
        assert anon.status_code in (401, 403)


def test_jd_apis(monkeypatch):
    _run(_scenario_jd_apis(monkeypatch))
