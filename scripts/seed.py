#!/usr/bin/env python3
"""种子数据：角色 / 权限 / 数据范围 / 测试用户 / 示例通知。

设计要点：
- **幂等**：按业务唯一键（code / email）先查后插，重复执行不会重复数据；
- **本地与云端跑同一份**：云端由 deploy-prepilot.yml 在部署后执行，
  保证云端也有可登录的四角色账号（受 SEED_DEMO_DATA 开关控制）；
- 密码哈希运行期用 bcrypt 生成（不写死哈希串）。

用法：
    python scripts/seed.py
    SEED_DEMO_DATA=false python scripts/seed.py     # 只同步角色与权限，不写演示用户
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 让 `python scripts/seed.py` 也能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Notification, NotificationSubscription, Role, RoleDataScope, User
from app.models.position import JdVersion, Position, PositionRound
from app.models.rbac import Permission, role_permissions
from app.services.position import ROUND_DEFAULT_NAME
from app.services.rbac import (
    PERMISSION_CATALOG,
    ROLE_DATA_SCOPES,
    ROLE_PERMISSIONS,
)

settings = get_settings()

# 演示账号统一密码
DEFAULT_PASSWORD = "Prepilot@123"

ROLE_SEEDS = [
    ("admin", "role.admin", "超级管理员：角色权限、数据生命周期、数据粉碎"),
    ("hr_lead", "role.hr_lead", "HR 主管：招聘全流程查看与复盘"),
    ("hr", "role.hr", "HR：职位与候选人管理、流程处置"),
    ("interviewer", "role.interviewer", "面试官：被指派的面试与面评提交"),
]

USER_SEEDS = [
    # (email, full_name, role_code)
    ("admin@prepilot.dev", "张超管", "admin"),
    ("admin2@prepilot.dev", "孙系统", "admin"),
    ("hrlead@prepilot.dev", "李主管", "hr_lead"),
    ("hrlead2@prepilot.dev", "周招聘", "hr_lead"),
    ("hr@prepilot.dev", "王招聘", "hr"),
    ("hr2@prepilot.dev", "赵人力", "hr"),
    ("interviewer@prepilot.dev", "陈技术", "interviewer"),
    ("interviewer2@prepilot.dev", "刘架构", "interviewer"),
]

NOTIFICATION_SEEDS = [
    # (email, type, title, body)
    (
        "interviewer@prepilot.dev",
        "assignment",
        "你被指派面试：高级后端工程师 · 王小明（一面）",
        "面试时间：2026-10-08 14:00，请提前进入 AI 备面工作台准备。",
    ),
    (
        "interviewer@prepilot.dev",
        "evaluation_submitted",
        "面评已提交：王小明 · 一面",
        "你的面评已交由 HR 处置，可在「我的面评」中查看。",
    ),
    (
        "interviewer2@prepilot.dev",
        "assignment",
        "你被指派面试：数据平台工程师 · 李雷（二面）",
        "一面已验证项已同步至工作台，请重点核实待确认能力项。",
    ),
    (
        "hr@prepilot.dev",
        "stage_changed",
        "候选人阶段变更：王小明 已由一面推进至二面",
        "可在候选人看板查看完整流转记录。",
    ),
    (
        "hr@prepilot.dev",
        "evaluation_submitted",
        "新面评待处置：李雷 · 二面",
        "面试官刘架构已提交面评，请登录看板处置。",
    ),
    (
        "hrlead@prepilot.dev",
        "stage_changed",
        "本周待复核：3 位候选人进入 Offer 阶段",
        "可在候选人看板查看各阶段分布。",
    ),
]


async def seed_roles(db) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for code, name_key, desc in ROLE_SEEDS:
        role = await db.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name_key=name_key, description=desc, is_system=True)
            db.add(role)
            await db.flush()
        else:
            role.name_key = name_key
            role.description = desc
        roles[code] = role
    await db.commit()
    return roles


async def seed_permissions(db) -> dict[str, Permission]:
    permissions: dict[str, Permission] = {}
    for code, module, desc in PERMISSION_CATALOG:
        perm = await db.scalar(select(Permission).where(Permission.code == code))
        if perm is None:
            perm = Permission(code=code, module=module, description=desc)
            db.add(perm)
            await db.flush()
        else:
            perm.module = module
            perm.description = desc
        permissions[code] = perm
    await db.commit()
    return permissions


async def seed_role_permissions(
    db, roles: dict[str, Role], permissions: dict[str, Permission]
) -> None:
    for role_code, codes in ROLE_PERMISSIONS.items():
        role = roles[role_code]
        existing = set(
            await db.scalars(
                select(role_permissions.c.permission_id).where(
                    role_permissions.c.role_id == role.id
                )
            )
        )
        for code in codes:
            perm = permissions[code]
            if perm.id not in existing:
                await db.execute(
                    role_permissions.insert().values(
                        role_id=role.id, permission_id=perm.id
                    )
                )
    await db.commit()


async def seed_data_scopes(db, roles: dict[str, Role]) -> None:
    for role_code, scopes in ROLE_DATA_SCOPES.items():
        role = roles[role_code]
        for resource, scope in scopes.items():
            row = await db.scalar(
                select(RoleDataScope).where(
                    RoleDataScope.role_id == role.id,
                    RoleDataScope.resource == resource,
                )
            )
            if row is None:
                db.add(RoleDataScope(role_id=role.id, resource=resource, scope=scope))
            else:
                row.scope = scope
    await db.commit()


async def seed_users(db, roles: dict[str, Role]) -> dict[str, User]:
    users: dict[str, User] = {}
    for email, full_name, role_code in USER_SEEDS:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(DEFAULT_PASSWORD),
                role_id=roles[role_code].id,
                is_active=True,
            )
            db.add(user)
            await db.flush()
        else:
            # 已存在时只校正角色，不覆盖密码（避免重置人工改过的密码）
            user.role_id = roles[role_code].id
        users[email] = user
    await db.commit()
    return users


async def seed_subscriptions(db, users: dict[str, User]) -> None:
    from app.services.notification import EVENT_TYPES

    for user in users.values():
        existing = set(
            await db.scalars(
                select(NotificationSubscription.event_type).where(
                    NotificationSubscription.user_id == user.id
                )
            )
        )
        for event in EVENT_TYPES:
            if event not in existing:
                db.add(
                    NotificationSubscription(
                        user_id=user.id, event_type=event, enabled=True
                    )
                )
    await db.commit()


# 演示职位（PRD 3.2）。字段顺序与 Position 模型一致：
#   name, owner_email, status, jd(raw_text/hard_gates/competencies/bonuses), rounds[(type, interviewer_email)]
POSITION_SEEDS = [
    (
        "中级后端工程师（Go/Python）",
        "hr@prepilot.dev",
        "open",
        {
            "raw_text": (
                "岗位：中级后端工程师（Go/Python）\n"
                "要求：本科及以上学历，计算机相关专业，3 年以上后端开发经验；"
                "熟悉 Go 或 Python，熟悉至少一种主流 Web 框架；"
                "熟悉 MySQL/PostgreSQL，具备 SQL 优化能力；"
                "有微服务、消息队列（Kafka/RabbitMQ）实战经验。\n"
                "加分项：Kubernetes / 云原生经验、高并发系统调优经验、开源社区贡献者。"
            ),
            "hard_gates": ["本科及以上学历，计算机相关专业", "3 年以上后端开发经验"],
            "competencies": [
                ("Go/Python 与主流 Web 框架", 40),
                ("MySQL/PostgreSQL 与 SQL 优化", 35),
                ("微服务与消息队列实战", 25),
            ],
            "bonuses": ["Kubernetes / 云原生经验", "高并发系统调优经验"],
        },
        [("r1", "interviewer@prepilot.dev"), ("r2", "interviewer2@prepilot.dev"),
         ("hr", "hrlead@prepilot.dev"), ("offer", "hrlead@prepilot.dev")],
    ),
    (
        "前端工程师（React）",
        "hr2@prepilot.dev",
        "closed",
        {
            "raw_text": (
                "岗位：前端工程师（React）\n"
                "要求：本科及以上，2 年以上前端经验；精通 React 与 TypeScript；"
                "熟悉前端工程化（Vite/Webpack）与性能优化。\n"
                "加分项：有可视化 / 编辑器类项目经验。"
            ),
            "hard_gates": ["本科及以上学历", "2 年以上前端开发经验"],
            "competencies": [
                ("React 与 TypeScript", 45),
                ("前端工程化与构建", 30),
                ("性能优化与排障", 25),
            ],
            "bonuses": ["可视化 / 编辑器项目经验"],
        },
        [("r1", "interviewer@prepilot.dev"), ("hr", "hrlead@prepilot.dev")],
    ),
    (
        "数据平台工程师",
        "hr@prepilot.dev",
        "draft",
        None,  # 草稿：JD 与流程都还没配，用于演示「草稿可删除」
        [],
    ),
]


async def seed_positions(db, users: dict[str, User]) -> int:
    """幂等：按职位名定位，已存在则跳过（不覆盖人工编辑过的内容）。"""
    from datetime import datetime, timezone

    from app.services.position import jd_hash_of
    from app.schemas.position import CompetencyIn

    created = 0
    for name, owner_email, status, jd, rounds in POSITION_SEEDS:
        exists = await db.scalar(select(Position).where(Position.name == name))
        if exists is not None:
            continue

        owner = users.get(owner_email)
        position = Position(
            name=name,
            status=status,
            owner_id=owner.id if owner else None,
            closed_at=datetime.now(timezone.utc) if status == "closed" else None,
        )
        if jd is not None:
            comps = [
                {"id": f"c{i + 1}", "text": text, "weight": weight}
                for i, (text, weight) in enumerate(jd["competencies"])
            ]
            position.jd_raw_text = jd["raw_text"]
            position.jd_hard_gates = list(jd["hard_gates"])
            position.jd_competencies = comps
            position.jd_bonuses = list(jd["bonuses"])
            position.jd_status = "confirmed"
            position.jd_hash = jd_hash_of(
                jd["hard_gates"],
                [CompetencyIn(**c) for c in comps],
                jd["bonuses"],
            )
        db.add(position)
        await db.flush()

        if jd is not None:
            db.add(
                JdVersion(
                    position_id=position.id,
                    version=1,
                    snapshot_json={
                        "hard_gates": position.jd_hard_gates,
                        "competencies": position.jd_competencies,
                        "bonuses": position.jd_bonuses,
                    },
                    change_summary="演示数据初始化",
                    changed_by=owner.id if owner else None,
                )
            )

        for seq, (rtype, email) in enumerate(rounds, start=1):
            user = users.get(email)
            db.add(
                PositionRound(
                    position_id=position.id,
                    seq=seq,
                    name=ROUND_DEFAULT_NAME.get(rtype, rtype),
                    type=rtype,
                    interviewer_id=user.id if user else None,
                )
            )
        created += 1
    await db.commit()
    return created


async def seed_retention_policy(db, users: dict[str, User]) -> None:
    """默认保留策略（BR-10：90 天）。

    幂等：已有生效策略就跳过 —— 人工改过的天数不能被 seed 覆盖回去。
    """
    from app.models.lifecycle import CURRENT, RESOURCE_RESUME, RetentionPolicy
    from app.services.lifecycle import DEFAULT_DAYS, DEFAULT_POLICY_NAME

    exists = await db.scalar(
        select(RetentionPolicy).where(
            RetentionPolicy.resource == RESOURCE_RESUME,
            RetentionPolicy.status == CURRENT,
        )
    )
    if exists is not None:
        return
    admin = users.get("admin@prepilot.dev")
    db.add(
        RetentionPolicy(
            name=DEFAULT_POLICY_NAME,
            resource=RESOURCE_RESUME,
            days=DEFAULT_DAYS,
            enabled=True,
            status=CURRENT,
            created_by=admin.id if admin else None,
        )
    )
    await db.commit()


async def seed_notifications(db, users: dict[str, User]) -> None:
    for email, ntype, title, body in NOTIFICATION_SEEDS:
        user = users.get(email)
        if user is None:
            continue
        exists = await db.scalar(
            select(Notification).where(
                Notification.user_id == user.id, Notification.title == title
            )
        )
        if exists is not None:
            continue
        db.add(
            Notification(
                user_id=user.id,
                type=ntype,
                title=title,
                body=body,
                payload_json=json.dumps({}, ensure_ascii=False),
            )
        )
    await db.commit()


async def run() -> int:
    if not settings.SEED_DEMO_DATA:
        print("SEED_DEMO_DATA=false，跳过用户与通知，仅同步角色与权限")

    async with SessionLocal() as db:
        roles = await seed_roles(db)
        permissions = await seed_permissions(db)
        await seed_role_permissions(db, roles, permissions)
        await seed_data_scopes(db, roles)
        print(f"[ok] 角色 {len(roles)} 个 / 权限 {len(permissions)} 条 / 数据范围已同步")

        if settings.SEED_DEMO_DATA:
            users = await seed_users(db, roles)
            await seed_subscriptions(db, users)
            await seed_notifications(db, users)
            await seed_retention_policy(db, users)
            positions = await seed_positions(db, users)
            print(f"[ok] 测试用户 {len(users)} 个（统一密码：{DEFAULT_PASSWORD}）")
            print(f"[ok] 演示职位 {positions} 个")
            for email, _, role_code in USER_SEEDS:
                print(f"       {role_code:<12} {email}")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
