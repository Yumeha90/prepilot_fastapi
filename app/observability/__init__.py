"""可观测性（埋点）统一入口。

分三条腿，各管一件事，互不替代：

| 腿        | 载体               | 回答的问题                                   |
|-----------|--------------------|----------------------------------------------|
| tracing   | Langfuse           | 一次 AI 调用到底发生了什么（提示词 / 输出 / token / 耗时 / 嵌套步骤） |
| metrics   | Prometheus         | 系统整体健康度（QPS / 延迟 / 错误率 / 资源）  |
| logs      | Loki（JSON 行）    | 某一刻具体发生了什么（可回溯到 trace_id）    |

设计原则（2026-10-05）：
1. **埋点失败绝不影响业务**。Langfuse / VictoriaMetrics 全挂了，应用必须照常工作；
   所有调用点都包了 try/except，且未配置密钥时装饰器退化为透明空操作。
2. **不重复造轮子**。HTTP 层指标由中间件统一采集，业务代码只标「业务语义」。
3. **trace_id 是三者的连接线**：日志里带 trace_id，Grafana 里能一键跳到 Langfuse。
"""
from __future__ import annotations
