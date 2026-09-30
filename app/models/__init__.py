"""ORM 模型（SQLAlchemy 2.0 声明式）。"""
from app.models.match_score import CURRENT as SCORE_CURRENT  # noqa: F401  状态常量
from app.models.match_score import STALE as SCORE_STALE  # noqa: F401
from app.models.match_score import MatchScore
from app.models.notification import Notification, NotificationSubscription
from app.models.position import JdVersion, Position, PositionRound
from app.models.rbac import Permission, Role, RoleDataScope, role_permissions
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "User",
    "Role",
    "Permission",
    "RoleDataScope",
    "role_permissions",
    "RefreshToken",
    "Notification",
    "NotificationSubscription",
    "Position",
    "PositionRound",
    "JdVersion",
    "MatchScore",
]
