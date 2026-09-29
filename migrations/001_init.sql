-- 001_init: 初始化用户表（Phase 1 打通鉴权链路）
-- 幂等，可重复执行。由 migrate.py 统一执行。
--
-- 主键约定（2026-09-29 定）：全库主键统一为 int 自增（PG SERIAL），不使用 UUID。
-- 注意：本文件只建 users 的最小形态，002_auth_rbac.sql 会重建 users 为最终形态
-- （补 role_id / avatar_url / locale / last_login_at，并去掉旧的 role 字符串列）。

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL       PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,
    full_name     VARCHAR(100) NOT NULL DEFAULT '',
    password_hash VARCHAR(255) NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
