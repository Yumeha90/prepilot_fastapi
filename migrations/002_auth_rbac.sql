-- 002_auth_rbac: 用户与权限管理（PRD 3.1）建表
--   1) roles / permissions / role_permissions   —— RBAC 三件套
--   2) role_data_scopes                         —— 数据可见范围（all / assigned / own）
--   3) users                                    —— 重建为最终形态（int 自增主键 + role_id）
--   4) refresh_tokens / notifications / notification_subscriptions
--
-- 主键约定：全部 SERIAL（int 自增）。
-- ⚠️ 开发期结构对齐：users 由 Phase 1 的「varchar(uuid) 主键 + role 字符串列」改为
--    「int 自增主键 + role_id 外键」，uuid 无法无损转 int，因此这里 DROP 后重建。
--    执行前请确认 users 无真实数据（本地与云端当前均为空表）。
--    该 DROP 语句会在后续版本移除，勿照抄到有真实用户的环境。

-- ---------- 1) 角色与权限（先于 users 建，供 role_id 外键引用） ----------
CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL       PRIMARY KEY,
    code        VARCHAR(32)  NOT NULL UNIQUE,
    name_key    VARCHAR(64)  NOT NULL DEFAULT '',
    description VARCHAR(255) NOT NULL DEFAULT '',
    is_system   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permissions (
    id          SERIAL       PRIMARY KEY,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    module      VARCHAR(32)  NOT NULL DEFAULT '',
    description VARCHAR(255) NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions (id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS role_data_scopes (
    id        SERIAL      PRIMARY KEY,
    role_id   INTEGER     NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    resource  VARCHAR(32) NOT NULL,
    scope     VARCHAR(16) NOT NULL,
    CONSTRAINT uq_role_resource UNIQUE (role_id, resource)
);

-- ---------- 2) users 重建为最终形态 ----------
-- 先清掉依赖 users 的表：应用启动时 SQLAlchemy create_all 可能已经按模型建过这些表，
-- 若只 DROP users CASCADE，会连外键约束一起删掉而表还在，导致最终结构不一致。
-- 这四张表在本阶段均为空表（云端尚未有真实用户数据）。
DROP TABLE IF EXISTS notification_subscriptions CASCADE;
DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS refresh_tokens CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id            SERIAL       PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,
    full_name     VARCHAR(100) NOT NULL DEFAULT '',
    password_hash VARCHAR(255) NOT NULL,
    role_id       INTEGER      REFERENCES roles (id),
    avatar_url    VARCHAR(500) NOT NULL DEFAULT '',
    locale        VARCHAR(16)  NOT NULL DEFAULT 'zh-CN',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_role_id ON users (role_id);

-- ---------- 3) 登录态 ----------
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    user_agent  VARCHAR(255) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id);

-- ---------- 4) 通知与订阅 ----------
CREATE TABLE IF NOT EXISTS notifications (
    id           SERIAL       PRIMARY KEY,
    user_id      INTEGER      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    type         VARCHAR(32)  NOT NULL DEFAULT 'system',
    title        VARCHAR(200) NOT NULL DEFAULT '',
    body         VARCHAR(1000) NOT NULL DEFAULT '',
    payload_json TEXT         NOT NULL DEFAULT '{}',
    is_read      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id);

CREATE TABLE IF NOT EXISTS notification_subscriptions (
    id         SERIAL      PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    enabled    BOOLEAN     NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_user_event_type UNIQUE (user_id, event_type)
);
