#!/usr/bin/env python3
"""极简迁移执行器 —— 整个「迁移框架」就这一个文件，不引入 Alembic。

用法：
    python migrate.py              应用所有待执行的迁移
    python migrate.py status       查看已执行 / 待执行
    python migrate.py --dry-run    只打印将要执行的迁移，不落库

约定：
- SQL 文件放在 migrations/ 下，按文件名字典序执行（推荐 001_xxx.sql）
- 以「文件」为最小单位，执行记录写入 _schema_migrations 表
- 不提供自动回滚：需要回滚请新增一个反向 SQL 文件
- 开发期也可让应用启动时的 create_all 自动建表，本脚本用于需要精确控制的场景
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = BASE_DIR / "migrations"

load_dotenv(BASE_DIR / ".env")

DEFAULT_DSN = "postgresql+asyncpg://prepilot:prepilot@localhost:55432/prepilot"


def get_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", DEFAULT_DSN)
    # asyncpg 不识别 SQLAlchemy 风格的 +asyncpg 前缀
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def discover_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def ensure_migration_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


async def get_applied(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("SELECT name FROM _schema_migrations")
    return {row["name"] for row in rows}


async def apply_one(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    async with conn.transaction():
        await conn.execute(sql)
        await conn.execute(
            "INSERT INTO _schema_migrations (name) VALUES ($1) "
            "ON CONFLICT (name) DO NOTHING",
            path.name,
        )


async def run(dry_run: bool, status_only: bool) -> int:
    migrations = discover_migrations()
    if not migrations:
        print(f"未找到迁移文件（{MIGRATIONS_DIR}）")
        return 0

    try:
        conn = await asyncpg.connect(get_dsn())
    except Exception as exc:  # noqa: BLE001
        print(f"连接数据库失败：{exc}")
        print("提示：本地联调请先 `docker compose up -d` 启动 postgres，再执行本脚本。")
        return 1

    try:
        await ensure_migration_table(conn)
        applied = await get_applied(conn)
        pending = [m for m in migrations if m.name not in applied]

        if status_only:
            print("已执行：")
            for name in sorted(applied) or ["（无）"]:
                print(f"  [x] {name}")
            print("待执行：")
            for m in pending or []:
                print(f"  [ ] {m.name}")
            if not pending:
                print("  （无）")
            return 0

        if not pending:
            print("没有待执行的迁移。")
            return 0

        for m in pending:
            if dry_run:
                print(f"[dry-run] 将执行 {m.name}")
                continue
            await apply_one(conn, m)
            print(f"[ok] 已执行 {m.name}")
        return 0
    finally:
        await conn.close()


def main() -> int:
    args = set(sys.argv[1:])
    status_only = "status" in args
    dry_run = "--dry-run" in args

    unknown = args - {"status", "--dry-run"}
    if unknown:
        print(f"未知参数：{', '.join(sorted(unknown))}")
        print(__doc__)
        return 2
    if dry_run and status_only:
        print("--dry-run 与 status 不可同时使用")
        return 2

    return asyncio.run(run(dry_run=dry_run, status_only=status_only))


if __name__ == "__main__":
    raise SystemExit(main())
