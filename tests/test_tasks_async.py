"""Celery 异步任务的事件循环/连接池守卫（2026-10-03 排障，别回退）。

背景：Celery prefork worker 复用进程，`app.core.database.engine` 是模块级单例，
连接会绑定到创建它的 loop；`asyncio.run` 每次新建 loop，于是**第二个及以后的任务**
拿到旧 loop 的连接 → `RuntimeError: ... got Future attached to a different loop`。
实测定时粉碎每小时都先失败一次、60s 重试才成功。

修法：`app.tasks.tasks._run_async` 在同一个 loop 里 `engine.dispose()`，
让下次任务在新 loop 里重新建连接。这里用不连库的假协程锁住这个行为。
"""
from __future__ import annotations

import asyncio

from app.tasks import tasks as tasks_mod


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


def test_run_async_disposes_engine(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr("app.core.database.engine", fake)

    async def _coro():
        return "done"

    assert tasks_mod._run_async(lambda: _coro()) == "done"
    assert fake.disposed == 1


def test_run_async_reusable_across_loops(monkeypatch):
    """连续多次调用（每次都是新 loop）都要成功 —— 正是修之前会炸的场景。"""
    monkeypatch.setattr("app.core.database.engine", _FakeEngine())

    async def _coro():
        return asyncio.get_running_loop().id if hasattr(asyncio.get_running_loop(), "id") else 1

    for _ in range(3):
        tasks_mod._run_async(lambda: _coro())  # 不抛异常即通过


def test_run_async_disposes_even_on_error(monkeypatch):
    """协程抛异常也要释放，否则下次任务照样踩旧 loop 的连接。"""
    fake = _FakeEngine()
    monkeypatch.setattr("app.core.database.engine", fake)

    async def _boom():
        raise RuntimeError("boom")

    try:
        tasks_mod._run_async(_boom)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("异常应向外抛出")

    assert fake.disposed == 1
