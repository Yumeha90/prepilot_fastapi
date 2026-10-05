"""向量语料与灌入逻辑的守卫测试（不连 Milvus）。

锁三件事：
① 语料本身：字段齐全、doc_id 不撞、长度合理、两库的 doc_type 分类齐全；
② 正式资料口径：语料一律按正式制度 / 法条 / 判例呈现，
   不得出现「虚构」「示例（非具体判决）」这类可信度免责标注；
③ ingest 的幂等与增量：新增 / 改内容 / 删条目三类差异都能对齐，
   且不重复烧 embedding（否则部署一次上百条向量，钱和时间都白搭）。
"""
from __future__ import annotations

import pytest

from app.ai import corpus, corpus_compliance, rag

ORG_TYPES = {"architecture", "tech_doc", "postmortem", "review", "eval", "guide"}
COMPLIANCE_TYPES = {"policy", "law", "norm", "case"}


def _all_docs():
    return [(rag.ORG_ASSETS, corpus.ORG_ASSETS, ORG_TYPES),
            (rag.COMPLIANCE, corpus_compliance.COMPLIANCE_CASES, COMPLIANCE_TYPES)]


def test_corpus_fields_and_length():
    seen: set[str] = set()
    for _name, docs, allowed in _all_docs():
        assert len(docs) >= 30, "单库语料太少，检索覆盖不住"
        for d in docs:
            for field in ("doc_id", "doc_type", "title", "text"):
                assert d.get(field), f"{d.get('doc_id')} 缺 {field}"
            assert d["doc_id"] not in seen, f"doc_id 重复：{d['doc_id']}"
            seen.add(d["doc_id"])
            assert d["doc_type"] in allowed, f"{d['doc_id']} 的 doc_type 不在允许集合内"
            assert 80 <= len(d["text"]) <= rag.MAX_TEXT_CHARS, f"{d['doc_id']} 长度不合适"
            assert 4 <= len(d["title"]) <= 200


def test_each_library_covers_all_types():
    for _name, docs, allowed in _all_docs():
        got = {d["doc_type"] for d in docs}
        assert got == allowed, f"{_name} 缺少分类：{allowed - got}"


def test_law_docs_cite_law_name():
    """法律类必须点名法律依据，否则公平性检查给出的建议无从溯源。"""
    for d in corpus_compliance.COMPLIANCE_CASES:
        if d["doc_type"] == "law":
            assert "《" in d["title"] or "《" in d["text"], f"{d['doc_id']} 未写出法名"


def test_no_disclaimer_markers_in_corpus():
    """语料按正式资料呈现：不能出现「虚构 / 示例（非具体判决）」这类免责标注。

    这些资料会原样进入公平性检查的 prompt 与引用列表，带免责词会让使用者
    怀疑结论的效力 —— 制度就是制度、判例就是判例，口径统一。
    """
    banned = ("虚构", "示例（非具体判决）", "示例情境", "非具体判决", "类案整理")
    for _name, docs, _allowed in _all_docs():
        for d in docs:
            for field in ("title", "text"):
                for word in banned:
                    assert word not in d[field], f"{d['doc_id']} 的 {field} 含免责标注「{word}」"


def test_case_docs_have_outcome():
    """案例类必须写出处理结果或裁判倾向，否则引了也起不到警示作用。"""
    for d in corpus_compliance.COMPLIANCE_CASES:
        if d["doc_type"] == "case":
            assert "启示" in d["text"], f"{d['doc_id']} 未给出风险启示"


# ------------------------------------------------------------------ ingest


class _Schema:
    def add_field(self, *args, **kwargs):
        return self


class _Index:
    def add_index(self, *args, **kwargs):
        return self


class _FakeMilvus:
    """够用的 MilvusClient 替身：只实现 ingest 会用到的方法。"""

    def __init__(self):
        self.cols: dict[str, list[dict]] = {}
        self._seq = 0
        self.embed_calls = 0

    # --- schema / collection ---
    def has_collection(self, name):
        return name in self.cols

    def create_schema(self, **_kw):
        return _Schema()

    def prepare_index_params(self):
        return _Index()

    def create_collection(self, name, **_kw):
        self.cols.setdefault(name, [])

    def drop_collection(self, name):
        self.cols.pop(name, None)

    def describe_collection(self, name):
        return {"fields": [{"name": n} for n in rag._REQUIRED_FIELDS | {"id"}]}

    # --- data ---
    def query(self, name, **_kw):
        return [
            {"id": r["id"], "doc_id": r["doc_id"], "checksum": r["checksum"]}
            for r in self.cols[name]
        ]

    def insert(self, name, rows):
        for r in rows:
            self._seq += 1
            self.cols[name].append({"id": self._seq, **r})

    def delete(self, name, ids=None):
        self.cols[name] = [r for r in self.cols[name] if r["id"] not in set(ids or [])]

    def flush(self, _name):
        return None

    def get_collection_stats(self, name):
        return {"row_count": len(self.cols.get(name, []))}


@pytest.fixture
def fake_rag(monkeypatch):
    fake = _FakeMilvus()
    monkeypatch.setattr(rag, "client", lambda: fake)

    def _embed(texts):
        fake.embed_calls += len(texts)
        return [[0.1] * 4 for _ in texts]

    monkeypatch.setattr(rag, "embed", _embed)
    return fake


def _docs(n: int, prefix: str = "d"):
    return [
        {"doc_id": f"{prefix}-{i}", "doc_type": "policy", "title": f"标题{i}", "text": f"正文{i}" * 30}
        for i in range(n)
    ]


def test_ingest_is_idempotent(fake_rag):
    docs = _docs(3)
    assert rag.ingest(rag.COMPLIANCE, docs) == (3, 0, 0)
    # 第二遍：一条都不该重灌
    assert rag.ingest(rag.COMPLIANCE, docs) == (0, 0, 0)
    assert fake_rag.embed_calls == 3


def test_ingest_detects_added_changed_removed(fake_rag):
    docs = _docs(3)
    rag.ingest(rag.COMPLIANCE, docs)

    docs2 = [
        docs[0],
        {**docs[1], "text": "改过的正文" * 30},  # 改内容
        # docs[2] 被删掉
        {"doc_id": "d-9", "doc_type": "policy", "title": "新增", "text": "新正文" * 30},
    ]
    assert rag.ingest(rag.COMPLIANCE, docs2) == (1, 1, 1)
    assert rag.count(rag.COMPLIANCE) == 3


def test_unknown_library_rejected(fake_rag):
    with pytest.raises(Exception):
        rag.ingest("no_such_library", _docs(1))
