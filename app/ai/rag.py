"""RAG 检索封装 —— 组织技术资产库（PRD §6.4，服务 C4 问题链生成）。

**为什么只有这一条链需要它**：C3 矩阵是「JD 能力 × 简历证据」的对齐，材料全在库里，
检索外部知识只会引入噪声；C5 公平性检索的是**合规案例库**（另一条链，S8 建）。
本文件只管「组织技术资产库」这一条。

**失败策略（PRD §11.4）**：Milvus 不可用**直接失败，不降级、不做空上下文兜底**。
宁可让面试官看到「向量库不可用」，也不能给一份没检索过内部资料、看起来却一模一样的
问题链 —— 那会让 RAG 变成摆设，出了问题还查不出来。

**连接为什么做成进程内单例**：MilvusClient 每次 new 都会重新建 gRPC 连接，
一次问题链生成要检索 3~6 个能力项，逐个建连既慢又容易把服务端连接数打满。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from pymilvus import DataType, MilvusClient

from app.core.config import get_settings
from app.core.errors import ErrorCode, bad_request

logger = logging.getLogger(__name__)
settings = get_settings()

# 组织技术资产库（PRD §6.4：内部技术文档 / 架构设计规范 / 历史故障复盘 /
# 历史优秀面评 / 代码 Review 常见问题清单）
COLLECTION = "org_assets"
INDEX_METRIC = "COSINE"
MAX_TEXT_CHARS = 2_000
DEFAULT_TOP_K = 3

_lock = threading.Lock()
_client: MilvusClient | None = None


@dataclass
class AssetHit:
    """一条检索结果。score 越大越相关（COSINE）。"""

    title: str
    text: str
    score: float


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
        f"向量库不可用，无法检索组织技术资产（{type(exc).__name__}）",
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
    if not texts:
        return []
    try:
        return _embeddings().embed_documents([t[:MAX_TEXT_CHARS] for t in texts])
    except Exception as exc:  # noqa: BLE001
        raise bad_request(
            ErrorCode.WORKBENCH_RAG_UNAVAILABLE,
            f"向量化失败，无法检索组织技术资产（{type(exc).__name__}）",
        ) from exc


def ensure_collection() -> None:
    """建集合与索引（幂等）。"""
    c = client()
    try:
        if c.has_collection(COLLECTION):
            return
        schema = c.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("title", DataType.VARCHAR, max_length=200)
        schema.add_field("text", DataType.VARCHAR, max_length=MAX_TEXT_CHARS)
        index = c.prepare_index_params()
        index.add_index("vector", metric_type=INDEX_METRIC)
        c.create_collection(COLLECTION, schema=schema, index_params=index)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


def count() -> int:
    c = client()
    try:
        if not c.has_collection(COLLECTION):
            return 0
        return int(c.get_collection_stats(COLLECTION).get("row_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


def ingest(docs: list[dict]) -> int:
    """灌入语料（幂等：已灌过就跳过，不重复烧 embedding）。

    `docs` 形如 `[{"doc_id": "...", "title": "...", "text": "..."}]`。
    """
    if not docs:
        return 0
    ensure_collection()
    if count() >= len(docs):
        return 0
    vectors = embed([d["text"] for d in docs])
    rows = [
        {
            "vector": vec,
            "doc_id": str(d["doc_id"])[:64],
            "title": str(d["title"])[:200],
            "text": str(d["text"])[:MAX_TEXT_CHARS],
        }
        for d, vec in zip(docs, vectors)
    ]
    c = client()
    try:
        c.insert(COLLECTION, rows)
        # Milvus standalone 默认查询是 eventual 一致性，flush 一下立刻可读
        c.flush(COLLECTION)
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc
    return len(rows)


def search(query: str, top_k: int = DEFAULT_TOP_K) -> list[AssetHit]:
    """按语义检索资产库。向量库不可用会直接抛错（不降级）。"""
    if not query.strip():
        return []
    vectors = embed([query])
    c = client()
    try:
        if not c.has_collection(COLLECTION):
            raise _unavailable(RuntimeError(f"集合 {COLLECTION} 不存在，请先灌入语料"))
        res = c.search(
            COLLECTION,
            data=vectors,
            limit=max(1, top_k),
            output_fields=["title", "text"],
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
                )
            )
    return hits
