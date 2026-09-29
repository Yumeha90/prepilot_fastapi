"""路由层（按域拆分，main.py 统一挂到 /api 前缀下）。"""
from app.routers import admin, auth, health, me

__all__ = ["auth", "health", "me", "admin"]
