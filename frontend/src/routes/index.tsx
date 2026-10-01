/**
 * 路由配置（react-router v6）。
 * 约定：本目录只放「路由」代码；页面组件一律放 src/pages/。
 *
 * - /login /register /forgot-password 公开，走 AuthLayout
 * - 其余页面需登录（RequireAuth）；路由级权限由 RootLayout 内的 RoutePermGuard 统一拦截
 */
import { createBrowserRouter, Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'

import AuthLayout from '@/layouts/AuthLayout'
import RootLayout from '@/layouts/RootLayout'
import RequireAuth from '@/components/RequireAuth'
import Home from '@/pages/Home'
import Login from '@/pages/Login'
import Register from '@/pages/Register'
import ForgotPassword from '@/pages/ForgotPassword'
import Forbidden from '@/pages/Forbidden'
import NotFound from '@/pages/NotFound'
import Placeholder from '@/pages/Placeholder'
import PositionForm from '@/pages/PositionForm'
import Positions from '@/pages/Positions'
import Roles from '@/pages/Roles'
import Board from '@/pages/Board'
import CandidateUpload from '@/pages/CandidateUpload'
import CandidateParse from '@/pages/CandidateParse'
import MatchDetail from '@/pages/MatchDetail'
import Lifecycle from '@/pages/Lifecycle'
import WorkbenchList from '@/pages/WorkbenchList'
import Workbench from '@/pages/Workbench'

const protectedRoutes: RouteObject[] = [
  { index: true, element: <Home /> },
  { path: 'positions', element: <Positions /> },
  { path: 'positions/new', element: <PositionForm /> },
  { path: 'positions/:id/edit', element: <PositionForm /> },
  // 候选人：看板（P08）/ 上传（P04）/ 解析确认（P05）
  { path: 'candidates', element: <Board /> },
  { path: 'candidates/new', element: <CandidateUpload /> },
  { path: 'candidates/:id/parse', element: <CandidateParse /> },
  // P18 人岗匹配：按 application 定位（一人一职位下与候选人一一对应）
  { path: 'candidates/:aid/match', element: <MatchDetail /> },
  // 3.4 AI 备面工作台：会话列表 + Step1 策略与矩阵（P09）
  { path: 'workbench', element: <WorkbenchList /> },
  { path: 'workbench/:sessionId', element: <Workbench /> },
  { path: 'evaluations', element: <Placeholder /> },
  { path: 'matches', element: <Placeholder /> },
  { path: 'system/roles', element: <Roles /> },
  // P15 数据生命周期（BR-10 90 天粉碎 + 超管手动粉碎）
  { path: 'system/lifecycle', element: <Lifecycle /> },
  { path: '403', element: <Forbidden /> },
  { path: '404', element: <NotFound /> },
  { path: '*', element: <Navigate to="/404" replace /> },
]

const routes: RouteObject[] = [
  {
    path: '/login',
    element: <AuthLayout />,
    children: [{ index: true, element: <Login /> }],
  },
  {
    path: '/register',
    element: <AuthLayout />,
    children: [{ index: true, element: <Register /> }],
  },
  {
    path: '/forgot-password',
    element: <AuthLayout />,
    children: [{ index: true, element: <ForgotPassword /> }],
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <RootLayout />
      </RequireAuth>
    ),
    children: protectedRoutes,
  },
]

export const router = createBrowserRouter(routes)

export default router
