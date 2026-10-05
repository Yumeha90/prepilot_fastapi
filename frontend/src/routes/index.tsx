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
import PositionForm from '@/pages/PositionForm'
import Positions from '@/pages/Positions'
import Roles from '@/pages/Roles'
import Board from '@/pages/Board'
import CandidateUpload from '@/pages/CandidateUpload'
import CandidateParse from '@/pages/CandidateParse'
import EvaluationDetail from '@/pages/EvaluationDetail'
import EvaluationList from '@/pages/EvaluationList'
import MatchDetail from '@/pages/MatchDetail'
import Lifecycle from '@/pages/Lifecycle'
import WorkbenchList from '@/pages/WorkbenchList'
// 3.4 工作台：P09~P13 五个步骤拆成五个页面，共用 WorkbenchLayout 的进度条与会话数据
import WorkbenchLayout from '@/pages/workbench/WorkbenchLayout'
import StepMatrix from '@/pages/workbench/StepMatrix'
import StepChain from '@/pages/workbench/StepChain'
import StepFairness from '@/pages/workbench/StepFairness'
import StepEvaluation from '@/pages/workbench/StepEvaluation'
import StepSubmit from '@/pages/workbench/StepSubmit'

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
  // 3.4 AI 备面工作台：会话列表 + 五个步骤页（P09 / P10 / P11 / P12，P13 待实现）
  { path: 'workbench', element: <WorkbenchList /> },
  {
    path: 'workbench/:sessionId',
    element: <WorkbenchLayout />,
    children: [
      // 老链接 /workbench/{id} 直接落到 Step1，不用改列表页与看板的跳转
      { index: true, element: <Navigate to="matrix" replace /> },
      { path: 'matrix', element: <StepMatrix /> },
      { path: 'chain', element: <StepChain /> },
      { path: 'fairness', element: <StepFairness /> },
      { path: 'evaluation', element: <StepEvaluation /> },
      { path: 'submit', element: <StepSubmit /> },
    ],
  },
  // P17 面评：列表 + 详情（只读；面试官只看本人面评，BR-17）
  { path: 'evaluations', element: <EvaluationList /> },
  { path: 'evaluations/:sessionId', element: <EvaluationDetail /> },
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
