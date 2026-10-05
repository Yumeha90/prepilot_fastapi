"""Prometheus 指标定义。

**为什么自己定义而不全用 Instrumentator**：
`prometheus-fastapi-instrumentator` 只能给 HTTP 层指标，业务语义（哪类 AI 能力被调用、
烧了多少 token、触发了几次降级）它不知道。这里把两类都放进来，用同一个 registry，
Grafana 侧一个 `/metrics` 全拿到。

**为什么用单独的 registry**：prometheus_client 的默认 REGISTRY 会带上一堆 Python GC /
platform 指标，且多进程（gunicorn / celery prefork）下容易打架。自建 registry 干净可控。

标签基数控制（重要）：
- `route` 用**路由模板**（`/api/sessions/{sid}`）不是真实路径，避免每个 id 一条时间序列
  把时序库打爆；
- `feature` 是有限枚举（业务能力名），不塞用户输入的自由文本。
"""
from __future__ import annotations

import glob
import logging
import os
import tempfile

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# ---------- 多进程指标目录 ----------
# 设了它，api / celery worker 三个进程就能把指标汇总到一处（/api/metrics 读全部）。
# 不设就是普通单进程模式（本地开发默认）。
#
# **为什么按角色分子目录**（2026-10-05 修）：
# 三个容器共享同一个卷，文件名是 `<类型>_<pid>.db`。旧容器被 recreate 后，它留下的
# 文件没人删，`MultiProcessCollector` 照样读出来 —— 于是 `prepilot_app_info` 会同时
# 出现 build-66/67/68 三条，计数也被死进程抬高。
# 明明可以按 pid 存活去清，但**容器各有独立 pid 命名空间**：在 api 里看 celery 的 pid
# 不存在，一清就把兄弟容器的实时指标删了。所以改成「每个角色只写自己的子目录」，
# 启动时只清自己的目录，既不会误删别人，也不会留僵尸。
MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "")
if MULTIPROC_DIR:
    os.makedirs(MULTIPROC_DIR, exist_ok=True)
    for stale in glob.glob(os.path.join(MULTIPROC_DIR, "*.db")):
        try:
            os.remove(stale)
        except OSError:  # 并发启动时可能已被别的进程删掉
            pass

REGISTRY = CollectorRegistry(auto_describe=True)

# ---------- HTTP 层（由中间件采集）----------
HTTP_REQUESTS = Counter(
    "prepilot_http_requests_total",
    "HTTP 请求总数",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "prepilot_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "route"],
    # 云端一次 AI 调用实测 17~23s，桶必须覆盖到 120s，否则 p95 全是 +Inf
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 180),
    registry=REGISTRY,
)
HTTP_IN_FLIGHT = Gauge(
    "prepilot_http_requests_in_flight",
    "正在处理的请求数",
    registry=REGISTRY,
)

# ---------- LLM ----------
LLM_CALLS = Counter(
    "prepilot_llm_calls_total",
    "大模型调用次数",
    ["feature", "model", "status"],  # status: ok / error
    registry=REGISTRY,
)
LLM_LATENCY = Histogram(
    "prepilot_llm_duration_seconds",
    "单次大模型调用耗时（秒，含 tenacity 重试）",
    ["feature", "model"],
    buckets=(1, 3, 5, 10, 15, 20, 30, 60, 120, 180),
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "prepilot_llm_tokens_total",
    "大模型 token 消耗",
    ["feature", "model", "direction"],  # direction: input / output / total
    registry=REGISTRY,
)

# ---------- RAG ----------
RAG_SEARCHES = Counter(
    "prepilot_rag_searches_total",
    "向量库检索次数",
    ["library", "status"],  # status: ok / error
    registry=REGISTRY,
)
RAG_HITS = Histogram(
    "prepilot_rag_hits_returned",
    "单次检索返回的条数",
    ["library"],
    buckets=(0, 1, 2, 3, 5, 8, 12),
    registry=REGISTRY,
)

# ---------- AI 降级 / 合规拦截（业务可观测性的重点）----------
AI_DEGRADED = Counter(
    "prepilot_ai_degraded_total",
    "AI 能力降级 / 兜底次数（向量库不可用、模型失败、合规阻断等）",
    ["feature", "reason"],
    registry=REGISTRY,
)

# ---------- 业务动作 ----------
BUSINESS_OPS = Counter(
    "prepilot_business_ops_total",
    "关键业务动作次数",
    ["op", "status"],  # op: advance / reject / submit / purge / confirm_parse ...
    registry=REGISTRY,
)

# ---------- 异步任务 ----------
CELERY_TASKS = Counter(
    "prepilot_celery_tasks_total",
    "Celery 任务执行次数",
    ["task", "status"],  # status: ok / error
    registry=REGISTRY,
)
CELERY_LATENCY = Histogram(
    "prepilot_celery_task_duration_seconds",
    "Celery 任务耗时（秒）",
    ["task"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

# ---------- 元信息（Grafana 面板上做「当前跑的是哪个版本」用）----------
APP_INFO = Gauge(
    "prepilot_app_info",
    "应用元信息（值恒为 1，看标签）",
    ["env", "version"],
    registry=REGISTRY,
)

_APP_INFO_SET = False


def set_app_info(env: str, version: str) -> None:
    global _APP_INFO_SET
    if not _APP_INFO_SET:
        APP_INFO.labels(env=env, version=version).set(1)
        _APP_INFO_SET = True


def render_metrics() -> tuple[bytes, str]:
    """给 /metrics 用：`(body, content_type)`。

    多进程模式下**必须另起一个 registry** 交给 MultiProcessCollector：
    指标对象本身在多进程模式下把值写进 mmap 文件，它自己的 collect() 也会读同一份文件；
    如果和 MultiProcessCollector 挂同一个 registry，每个指标会出现两次（数值翻倍）。
    """
    if not MULTIPROC_DIR:
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
    try:
        from prometheus_client.multiprocess import MultiProcessCollector

        exposed = CollectorRegistry()
        # ⚠️ 只能用**一个** collector 读全部文件：
        # 多个 collector 挂同一 registry 时，各自产出独立的 Metric 对象，
        # generate_latest 原样拼接 —— 同一条序列会出现两次且值不同，
        # VictoriaMetrics 直接报 "different value but same timestamp" 并丢弃样本。
        # MultiProcessCollector 只吃单个目录，所以先把各角色的文件
        # 软链进一个临时目录，再交给它一次性读。
        with tempfile.TemporaryDirectory() as staging:
            for index, directory in enumerate(_metric_dirs()):
                for path in glob.glob(os.path.join(directory, "*.db")):
                    _link_into(staging, path, index)
            MultiProcessCollector(exposed, path=staging)
            return generate_latest(exposed), CONTENT_TYPE_LATEST
    except Exception:  # noqa: BLE001 —— 采集失败不能让 /metrics 500
        logger.warning("多进程指标汇总失败，退回单进程读数", exc_info=True)
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def _metric_dirs() -> list[str]:
    """自己的目录 + 兄弟角色（worker / beat）的子目录。

    取父目录下所有子目录：新增角色不用改代码，部署时也不会漏。
    """
    own = os.path.abspath(MULTIPROC_DIR)
    parent = os.path.dirname(own)
    dirs = [own]
    for candidate in sorted(glob.glob(os.path.join(parent, "*"))):
        if os.path.isdir(candidate) and os.path.abspath(candidate) != own:
            dirs.append(candidate)
    return dirs


def _link_into(staging: str, path: str, index: int) -> None:
    """把一个 mmap 文件软链进暂存目录，文件名里的 pid 换成 角色序号+原pid。

    **为什么要换**：两个容器的 pid 命名空间互相独立，pid 撞号完全可能；
    collector 用文件名里的 pid 给 gauge 打标签，撞号会让两条序列互相覆盖。
    """
    base = os.path.basename(path)
    parts = base.split("_")
    if len(parts) < 2:
        return
    pid = parts[-1][:-3] if parts[-1].endswith(".db") else parts[-1]
    synthetic = f"{index}{pid}"
    # gauge_all_1.db（gauge 多一段 mode）/ counter_1.db / histogram_1.db
    new_name = (
        f"{parts[0]}_{parts[1]}_{synthetic}.db" if len(parts) >= 3 else f"{parts[0]}_{synthetic}.db"
    )
    try:
        os.symlink(path, os.path.join(staging, new_name))
    except FileExistsError:  # 同名文件已链过，跳过即可
        pass


def record_op(op: str, status: str = "ok") -> None:
    """记一次关键业务动作（推进 / 淘汰 / 录用 / 提交 / 粉碎 / 确认解析 …）。"""
    BUSINESS_OPS.labels(op=op, status=status).inc()


def record_rag(library: str, status: str, hits: int | None = None) -> None:
    """记一次向量检索。"""
    RAG_SEARCHES.labels(library=library, status=status).inc()
    if status == "ok" and hits is not None:
        RAG_HITS.labels(library=library).observe(hits)


def record_degraded(feature: str, reason: str) -> None:
    """记一次 AI 降级 / 兜底 / 拦截。这类事件必须能被数出来。"""
    AI_DEGRADED.labels(feature=feature, reason=reason).inc()


def _route_of(scope: dict) -> str:
    """取路由模板（`/api/sessions/{sid}`），拿不到就退化成归一化路径。

    **必须用模板**：真实路径里带着 session_id / candidate_id，一个候选人一条时间序列，
    几百个候选人就能把时序库的 series 数打爆（VictoriaMetrics 会直接拒绝写入）。
    """
    route = scope.get("route")
    path = getattr(route, "path", None) if route is not None else None
    if path:
        return str(path)
    raw = str(scope.get("path") or "")
    return _normalize_path(raw)


def _normalize_path(path: str) -> str:
    """把纯数字的 path 段替换成 `{id}`，兜住没有 route 对象的情况。"""
    parts = []
    for seg in path.split("/"):
        if seg.isdigit():
            parts.append("{id}")
        elif len(seg) >= 32 and all(c in "0123456789abcdef-" for c in seg.lower()):
            parts.append("{uuid}")
        else:
            parts.append(seg)
    return "/".join(parts)
