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
import Roles from '@/pages/Roles'

const protectedRoutes: RouteObject[] = [
  { index: true, element: <Home /> },
  { path: 'positions', element: <Placeholder /> },
  { path: 'candidates', element: <Placeholder /> },
  { path: 'workbench', element: <Placeholder /> },
  { path: 'evaluations', element: <Placeholder /> },
  { path: 'matches', element: <Placeholder /> },
  { path: 'system/roles', element: <Roles /> },
  { path: 'system/lifecycle', element: <Placeholder /> },
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
