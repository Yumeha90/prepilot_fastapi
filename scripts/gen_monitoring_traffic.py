#!/usr/bin/env python3
"""给监控系统制造真实流量（打云端公网入口）。

目的：让 Langfuse / Grafana / Loki 里都能看到**真实链路**的数据，
而不是手工塞几条假数据。每个动作都走公网 → nginx → api，
因此 HTTP 指标、访问日志、AI trace 会同时产生，可交叉验证。

**为什么分两个账号**：工作台（矩阵 / 问题链 / 公平性 / 润色 / 提交扫描）的写权限
只给被指派的面试官（BR-17），HR 一律只读。用 HR 去点这些接口只会拿 400，
所以读接口与看板处置用 HR，工作台用面试官。

用法：
    ./prepilot_env/bin/python scripts/gen_monitoring_traffic.py
可选环境变量：
    BASE=http://182.254.244.139   （默认云端公网）
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://182.254.244.139")
API = f"{BASE}/api"
AI_TIMEOUT = 180.0

HR = ("hr@prepilot.dev", "Prepilot@123")
INTERVIEWER = ("interviewer@prepilot.dev", "Prepilot@123")

# 一份够长的 JD（AI 拆解需要 ≥20 字，且要能拆出 3 项以上核心能力）
JD_TEXT = """高级后端工程师（Go / 分布式存储）

岗位职责：
1. 负责分布式对象存储网关的设计与开发，支撑日均百亿级请求；
2. 主导存储引擎的读写链路性能优化，P99 延迟控制在 50ms 以内；
3. 设计并落地多副本一致性方案，保障数据可靠性 99.999999%；
4. 参与容量规划与成本治理，推动存储成本逐年下降；
5. 编写核心模块的设计文档与故障复盘，参与 Code Review。

任职要求：
1. 本科及以上学历，计算机相关专业，5 年以上后端开发经验；
2. 精通 Go 语言，熟悉 channel、调度器与内存模型，能独立完成性能剖析；
3. 深入理解分布式系统：一致性协议、分区容错、故障恢复；
4. 熟悉至少一种分布式存储系统（TiKV / Etcd / Ceph / MinIO）；
5. 熟悉 Kubernetes 与容器化部署，有生产环境排障经验。

加分项：
- 有开源分布式存储项目贡献经历；
- 熟悉 RDMA 或 SPDK 等高性能存储技术；
- 有大规模数据迁移经验。
"""

RESUME_TEXT = """张三
邮箱：zhangsan@example.com  手机：13800000001

求职意向：高级后端工程师

教育经历
2014-2018  某大学  计算机科学与技术  本科

工作经历
2018.07-2021.03  某科技有限公司  后端工程师
- 负责订单系统重构，峰值 QPS 从 3000 提升到 12000；
- 使用 Go 重写对账服务，日处理订单 500 万条，耗时从 4 小时降到 25 分钟；
- 参与微服务治理，落地链路追踪与熔断降级。

2021.04-至今  某云服务公司  高级后端工程师
- 主导分布式对象存储网关开发，支撑日均 80 亿次请求，P99 延迟 42ms；
- 设计多副本一致性方案，通过 Raft 协议实现故障自动切换，数据可靠性达到 99.999999%；
- 优化读写链路，引入零拷贝与批量合并，磁盘 IOPS 降低 35%；
- 推动容量治理，冷热分层使存储成本下降 28%；
- 编写 12 篇故障复盘文档，主导团队 Code Review 规范。

专业技能
- 语言：Go（精通）、Python、C++
- 存储：TiKV、Etcd、Ceph、RocksDB
- 中间件：Kafka、Redis、Nginx
- 云原生：Kubernetes、Docker、Prometheus、Grafana
- 其他：熟悉 pprof 性能剖析、熟悉 RDMA 基本原理

项目经历
分布式对象存储网关（2021-2024）
- 技术栈：Go + TiKV + Kubernetes
- 成果：支撑日均 80 亿请求，P99 42ms，成本下降 28%
"""


class Ctx:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=AI_TIMEOUT)
        self.token = ""

    def login(self, email: str, password: str) -> bool:
        r = self.call("POST", "/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            print("  登录失败：", r.text[:200])
            return False
        body = r.json()
        self.token = body.get("access_token") or body.get("accessToken") or ""
        return bool(self.token)

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def call(self, method: str, path: str, **kw) -> httpx.Response:
        started = time.perf_counter()
        resp = self.client.request(method, path, headers=self.headers(), **kw)
        cost = time.perf_counter() - started
        flag = "OK " if resp.status_code < 400 else "ERR"
        print(f"  [{flag}] {method:5s} {path:52s} {resp.status_code} ({cost:5.1f}s)")
        return resp


def step(title: str) -> None:
    print(f"\n>>> {title}")


def board_cards(ctx: Ctx) -> list[dict]:
    resp = ctx.call("GET", "/board")
    if resp.status_code >= 400:
        return []
    cards: list[dict] = []
    for col in (resp.json().get("columns") or []):
        for card in (col.get("cards") or []):
            if isinstance(card, dict):
                cards.append(card)
    return cards


def wait_chain(ctx: Ctx, sid: int, timeout: int = 180) -> str:
    """等问题链异步任务出结果（轮询工作台视图里的 chain_status）。"""
    deadline = time.time() + timeout
    status = "unknown"
    while time.time() < deadline:
        resp = ctx.client.request("GET", f"/workbench/sessions/{sid}", headers=ctx.headers())
        if resp.status_code < 400:
            chain = (resp.json().get("chain") or {})
            status = str(chain.get("status") or chain.get("chain_status") or "unknown")
            if status in {"ready", "failed", "error"}:
                print(f"  问题链状态：{status}（等待 {int(timeout - (deadline - time.time()))}s）")
                return status
        time.sleep(5)
    print(f"  问题链超时未就绪（最后状态 {status}）")
    return status


def matrix_rows(ctx: Ctx, sid: int) -> list[dict]:
    """取 Step1 矩阵的能力项（评分表按 row_id 与它对齐）。"""
    resp = ctx.client.request(
        "GET", f"/workbench/sessions/{sid}", headers=ctx.headers()
    )
    if resp.status_code >= 400:
        return []
    matrix = resp.json().get("matrix") or {}
    return [r for r in (matrix.get("rows") or []) if isinstance(r, dict) and r.get("id")]


def main() -> int:
    ctx = Ctx()

    step("1. HR 登录")
    if not ctx.login(*HR):
        return 1

    step("2. 读接口（产生 HTTP 指标与访问日志）")
    for _ in range(3):
        ctx.call("GET", "/board")
        ctx.call("GET", "/positions?page=1&page_size=20")

    step("3. AI 拆解 JD（Langfuse: jd.parse）")
    ctx.call("POST", "/jd/parse", json={"raw_text": JD_TEXT})

    step("4. AI 解析简历（Langfuse: resume.parse）")
    ctx.call("POST", "/resume/parse", json={"raw_text": RESUME_TEXT})

    step("5. 看板推进（业务动作计数）")
    cards = [c for c in board_cards(ctx)
             if c.get("application_id") and c.get("stage") == "in_r1"]
    if cards:
        app_id = cards[0]["application_id"]
        ctx.call("POST", f"/board/applications/{app_id}/transition",
                 json={"action": "rollback"})
        ctx.call("POST", f"/board/applications/{app_id}/transition",
                 json={"action": "advance"})

    step("6. 切换到面试官账号跑工作台（BR-17：写权限只给被指派的面试官）")
    if not ctx.login(*INTERVIEWER):
        return 1
    # 会话提交后就变只读（BR-17），重复跑本脚本时不能再用同一个，
    # 否则整段工作台链路全是 400 —— 挑一个还没提交的。
    mine = [
        c
        for c in board_cards(ctx)
        if c.get("session_id") and str(c.get("session_status") or "") != "submitted"
    ]
    if not mine:
        print("  面试官没有可备面的会话")
        return 0
    sid = mine[0]["session_id"]
    print(f"  选用会话 session_id={sid}（{mine[0].get('candidate_name')} / "
          f"{mine[0].get('current_round_name')}）")

    ctx.call("POST", f"/workbench/sessions/{sid}/matrix/generate")
    ctx.call("POST", f"/workbench/sessions/{sid}/chain/generate")
    # 问题链是异步任务（BR-12，一次要检索 3~6 个能力项再调模型，云端 15~30s），
    # 不等它就绪就去扫公平性，会被「问题链未就绪」挡回来。
    wait_chain(ctx, sid, timeout=180)
    ctx.call("POST", f"/workbench/sessions/{sid}/fairness/scan")

    # 6b. 填一次评分草稿（Step4）—— 润色与提交扫描都以「已经有评分内容」为前提，
    #     空手去点只会被业务校验挡回 400，Langfuse 上就少两条关键 AI 链路。
    rows = matrix_rows(ctx, sid)
    if rows:
        draft = {
            "items": [
                {
                    "row_id": r.get("id", ""),
                    "capability": r.get("capability", ""),
                    # 交替给 4 / 3 分：均分落在 3 分以上，提交时不会撞上
                    # 「均分 < 2 只能判不通过」那条拦截，方便演示完整的提交链路
                    "score": 4 if i % 2 == 0 else 3,
                    "evidences": [
                        {
                            "text": (
                                f"候选人在「{r.get('capability', '')}」上给出了可核验的具体经历，"
                                "能说清自己做了什么、怎么做的、结果如何。"
                            )
                        }
                    ],
                    "note": "回答有结构，追问到细节仍能自洽。",
                }
                for i, r in enumerate(rows)
            ],
            "summary": (
                "候选人整体表现稳健：技术基础扎实，能把项目经历讲清楚，"
                "对分布式一致性的理解不只停留在概念层面。沟通表达清楚，"
                "追问时能主动补充权衡过程。建议进入下一轮。"
            ),
        }
        ctx.call("PUT", f"/workbench/sessions/{sid}/evaluation", json=draft)
        ctx.call("POST", f"/workbench/sessions/{sid}/evaluation/polish")
        ctx.call("POST", f"/workbench/sessions/{sid}/submission/scan")
        ctx.call(
            "POST",
            f"/workbench/sessions/{sid}/submission",
            json={"conclusion": "pass", "confirm": True},
        )
    else:
        print("  会话没有矩阵能力项，跳过评分 / 润色 / 提交")

    step("7. 制造 4xx（让错误率面板有东西可看）")
    ctx.call("GET", "/positions/99999999")
    ctx.call("POST", "/board/applications/99999999/transition", json={"action": "advance"})

    print("\n完成。等 30~60 秒让 Alloy 推送、Langfuse 落库后再去看面板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
