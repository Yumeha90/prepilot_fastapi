"""RAG 检索封装 —— 两个向量库（PRD §6.4）。

| 库              | 集合名            | 服务对象                       | 语料来源              |
|-----------------|-------------------|--------------------------------|-----------------------|
| 组织技术资产库  | `org_assets`      | C4 问题链生成（已实现，S7）    | `ai.corpus`           |
| 合规案例库      | `compliance_cases`| C5 公平性检查（S8）            | `ai.corpus_compliance`|

**为什么分库而不是一个大库 + 类型过滤**：两条链要的东西完全不同。
问题链检索到一条「不得询问婚育情况」毫无用处，公平性检查检索到一条「缓存三原则」
同理 —— 混库只会稀释向量、抬高噪声，还让「检索到几条」这个可观测指标失去意义。
`doc_type` 保留下来，是为了**库内**再做一次粗筛（例如合规库里只想看法律法规）。

**失败策略（PRD §11.4）**：Milvus 不可用**直接失败，不降级、不做空上下文兜底**。
宁可让面试官看到「向量库不可用」，也不能给一份没检索过内部资料、看起来却一模一样的
结果 —— 那会让 RAG 变成摆设，出了问题还查不出来。

**连接为什么做成进程内单例**：MilvusClient 每次 new 都会重新建 gRPC 连接，
一次问题链生成要检索 3~6 个能力项，逐个建连既慢又容易把服务端连接数打满。

**灌入为什么用 checksum**：语料写在代码里，改一行就要让向量库跟着变。
以 `doc_id` 判重只能发现「新增」，发现不了「改了内容」；存一份文本摘要，
就能把「新增 / 改了 / 删了」三类差异全部对齐，且不用每次全量重灌烧 embedding。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass

from pymilvus import DataType, MilvusClient

from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request
from app.observability import metrics, tracing

logger = logging.getLogger(__name__)
settings = get_settings()

# 两个库（PRD §6.4：RAG 只剩这两条链）
ORG_ASSETS = "org_assets"  # 组织技术资产库：内部技术文档 / 架构规范 / 故障复盘 / 面评范例
COMPLIANCE = "compliance_cases"  # 合规案例库：公司制度 / 法律法规 / 公序良俗 / 案例
LIBRARIES: dict[str, str] = {
    ORG_ASSETS: "组织技术资产库（C4 问题链）",
    COMPLIANCE: "合规案例库（C5 公平性）",
}

INDEX_METRIC = "COSINE"
MAX_TEXT_CHARS = 2_000
DEFAULT_TOP_K = 3
# 百炼 embedding 端点单批上限 25 条（实测 36 条直接 400），灌语料时必须切批
EMBED_BATCH = 25
# 语料是内置常量，规模可控；一次全量取出做差异比对，避免逐条 query
MAX_CORPUS_ROWS = 5_000

_lock = threading.Lock()
_client: MilvusClient | None = None


@dataclass
class AssetHit:
    """一条检索结果。score 越大越相关（COSINE）。"""

    title: str
    text: str
    score: float
    doc_type: str = ""


def _uri() -> str:
    return f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"


def _connect_kwargs() -> dict:
    kwargs: dict = {"uri": _uri()}
    if settings.MILVUS_USER:
        kwargs["user"] = settings.MILVUS_USER
        kwargs["password"] = settings.MILVUS_PASSWORD
    if settings.MILVUS_DB:
        kwargs["db_name"] = settings.MILVUS_DB
    return kwargs


def client() -> MilvusClient:
    """进程内单例。连接失败统一转成带 code 的错误，不往外抛 pymilvus 原生异常。"""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                try:
                    _client = MilvusClient(**_connect_kwargs())
                except Exception as exc:  # noqa: BLE001
                    raise _unavailable(exc) from exc
    return _client


def _unavailable(exc: Exception) -> Exception:
    logger.warning("Milvus 不可用：%s: %s", type(exc).__name__, exc)
    return bad_request(
        ErrorCode.WORKBENCH_RAG_UNAVAILABLE,
        f"向量库不可用，无法检索内部资料（{type(exc).__name__}）",
    )


def _embeddings():
    """向量化模型。

    未单独配置 EMBEDDING_API_KEY / BASE_URL 时**回退到 LLM 的配置** —— 实测
    同一个 MaaS 端点同时提供 chat 与 embedding，让运维少配两个变量。
    """
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        api_key=settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY or "sk-not-configured",
        base_url=settings.EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL or None,
        # 长文本交给上层截断，不让 SDK 自己按 token 切（切完行数对不上）
        check_embedding_ctx_length=False,
    )


def embed(texts: list[str]) -> list[list[float]]:
    """向量化。**必须分批**：百炼 embedding 端点实测单批上限 25 条，
    超了直接 400（`batch size is invalid, it should not be larger than 25`）。
    语料一多就会撞上（一次灌 36 条），所以这里按 EMBED_BATCH 切批后拼接。
    """
    if not texts:
        return []
    try:
        model = _embeddings()
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(model.embed_documents([t[:MAX_TEXT_CHARS] for t in texts[i : i + EMBED_BATCH]]))
        return out
    except Exception as exc:  # noqa: BLE001
        raise bad_request(
            ErrorCode.WORKBENCH_RAG_UNAVAILABLE,
            f"向量化失败，无法检索内部资料（{type(exc).__name__}）",
        ) from exc


def _checksum(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 —— 只做变更检测


def ensure_collection(name: str) -> None:
    """建集合与索引（幂等）。

    **schema 变了会重建集合**：集合建的时候是 `enable_dynamic_field=False`，
    加字段只能删了重建。语料全部由代码定义、部署时重新灌入，重建没有数据损失 ——
    但如果是外部灌进来的语料，这里必须先想清楚再动。
    """
    if name not in LIBRARIES:
        raise bad_request(ErrorCode.WORKBENCH_RAG_UNAVAILABLE, f"未知向量库：{name}")
    c = client()
    try:
        if c.has_collection(name):
            if _schema_ok(c, name):
                return
            logger.warning("集合 %s 的 schema 与代码不一致，重建（语料会随 seed 重新灌入）", name)
            c.drop_collection(name)
        schema = c.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("doc_type", DataType.VARCHAR, max_length=32)
        schema.add_field("checksum", DataType.VARCHAR, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=200)
        schema.add_field("text", DataType.VARCHAR, max_length=MAX_TEXT_CHARS)
        index = c.prepare_index_params()
        index.add_index("vector", metric_type=INDEX_METRIC)
        c.create_collection(name, schema=schema, index_params=index)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


_REQUIRED_FIELDS = {"vector", "doc_id", "doc_type", "checksum", "title", "text"}


def _schema_ok(c: MilvusClient, name: str) -> bool:
    try:
        desc = c.describe_collection(name)
    except Exception:  # noqa: BLE001
        return False
    fields = {f.get("name") for f in (desc.get("fields") or [])}
    return _REQUIRED_FIELDS <= fields


def _existing_rows(name: str) -> list[dict]:
    """取出库里现有的 (id, doc_id, checksum)。"""
    c = client()
    try:
        return c.query(
            name,
            filter="",
            output_fields=["id", "doc_id", "checksum"],
            limit=MAX_CORPUS_ROWS,
        )
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


def ingest(name: str, docs: list[dict]) -> tuple[int, int, int]:
    """灌入语料（幂等：新增的才灌，改了的先删再灌，删了的清掉）。

    `docs` 形如 `[{"doc_id": "...", "doc_type": "...", "title": "...", "text": "..."}]`。
    返回 `(新增, 更新, 删除)` 三元组 —— 部署日志要能一眼看出语料有没有真的生效。
    """
    if not docs:
        return 0, 0, 0
    ensure_collection(name)

    wanted = {str(d["doc_id"])[:64]: d for d in docs}
    rows = _existing_rows(name)
    by_id = {str(r.get("doc_id")): r for r in rows}

    removed = [r for r in rows if str(r.get("doc_id")) not in wanted]
    changed = [
        r
        for r in rows
        if str(r.get("doc_id")) in wanted
        and str(r.get("checksum")) != _checksum(str(wanted[str(r["doc_id"])]["text"])[:MAX_TEXT_CHARS])
    ]
    stale_ids = [r["id"] for r in removed + changed]
    changed_ids = {r["id"] for r in changed}
    to_insert = [d for doc_id, d in wanted.items() if doc_id not in by_id or by_id[doc_id]["id"] in changed_ids]

    c = client()
    try:
        if stale_ids:
            c.delete(name, ids=stale_ids)
            c.flush(name)
        if not to_insert:
            return 0, 0, len(removed)
        vectors = embed([str(d["text"])[:MAX_TEXT_CHARS] for d in to_insert])
        payload = [
            {
                "vector": vec,
                "doc_id": str(d["doc_id"])[:64],
                "doc_type": str(d.get("doc_type") or "")[:32],
                "checksum": _checksum(str(d["text"])[:MAX_TEXT_CHARS]),
                "title": str(d["title"])[:200],
                "text": str(d["text"])[:MAX_TEXT_CHARS],
            }
            for d, vec in zip(to_insert, vectors)
        ]
        c.insert(name, payload)
        # Milvus standalone 默认查询是 eventual 一致性，flush 一下立刻可读
        c.flush(name)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc

    added = sum(1 for d in to_insert if str(d["doc_id"]) not in by_id)
    return added, len(changed), len(removed)


def count(name: str) -> int:
    """库内实际条数。

    **不能用 `get_collection_stats` 的 row_count**：它统计的是「已 flush 的实体数」，
    含已被逻辑删除、尚未 compaction 的行 —— 实测改 6 条语料后它报 45 而实际只有 39 条，
    看上去像灌重复了。这里改用 query 数行，语料规模可控（MAX_CORPUS_ROWS）。
    """
    c = client()
    try:
        if not c.has_collection(name):
            return 0
        return len(c.query(name, filter="", output_fields=["id"], limit=MAX_CORPUS_ROWS))
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


# 检索为什么单独埋一个 retriever span：RAG 出问题时最常见的两个原因是
# 「检索到了但不相关」和「根本没检索到」。只有把查询、命中条数、命中标题
# 单独记下来，才能区分这两种失败 —— 否则只能看到最终答案不对，查不出为什么。
@tracing.ai_step("rag.search", as_type="retriever")
def search(
    name: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    doc_types: list[str] | None = None,
) -> list[AssetHit]:
    """按语义检索指定库。向量库不可用会直接抛错（不降级）。"""
    if not query.strip():
        return []
    try:
        return _search_inner(name, query, top_k, doc_types)
    except Exception as exc:  # noqa: BLE001 —— 检索失败要能被数出来
        metrics.record_rag(name, "error")
        metrics.record_degraded(tracing.FEATURE.get(), "rag_unavailable")
        tracing.add_metadata(library=name, error=type(exc).__name__)
        raise


def _search_inner(
    name: str,
    query: str,
    top_k: int,
    doc_types: list[str] | None,
) -> list[AssetHit]:
    vectors = embed([query])
    c = client()
    try:
        if not c.has_collection(name):
            raise _unavailable(RuntimeError(f"集合 {name} 不存在，请先灌入语料"))
        res = c.search(
            name,
            data=vectors,
            limit=max(1, top_k),
            output_fields=["title", "text", "doc_type"],
            **( {"filter": _type_filter(doc_types)} if doc_types else {}),
        )
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    hits: list[AssetHit] = []
    for group in res:
        for item in group:
            entity = item.get("entity") or {}
            hits.append(
                AssetHit(
                    title=str(entity.get("title") or ""),
                    text=str(entity.get("text") or ""),
                    score=float(item.get("distance") or 0.0),
                    doc_type=str(entity.get("doc_type") or ""),
                )
            )
    metrics.record_rag(name, "ok", len(hits))
    tracing.add_metadata(
        library=name,
        top_k=top_k,
        hits=len(hits),
        doc_types=doc_types or [],
        # 只放标题不正文：命中内容是内部资料，够定位问题就行，别在 Langfuse 里多存一份
        hit_titles=[h.title for h in hits][:10],
        hit_scores=[round(h.score, 4) for h in hits][:10],
    )
    return hits


def _type_filter(doc_types: list[str]) -> str:
    quoted = ", ".join(f'"{t}"' for t in doc_types)
    return f"doc_type in [{quoted}]"


def search_assets(query: str, top_k: int = DEFAULT_TOP_K) -> list[AssetHit]:
    """C4 问题链：检索组织技术资产库。"""
    return search(ORG_ASSETS, query, top_k=top_k)


def search_compliance(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    doc_types: list[str] | None = None,
) -> list[AssetHit]:
    """C5 公平性：检索合规案例库。`doc_types` 可限定 policy/law/norm/case。"""
    return search(COMPLIANCE, query, top_k=top_k, doc_types=doc_types)
