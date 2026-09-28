/**
 * 路由配置（react-router v6）。
 * 约定：本目录只放「路由」代码；页面组件一律放 src/pages/。
 */
import { createBrowserRouter, Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'

import RootLayout from '@/layouts/RootLayout'
import Home from '@/pages/Home'
import NotFound from '@/pages/NotFound'

const routes: RouteObject[] = [
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: <Home /> },
      // 后续按 PRD P01–P18 逐个追加，例如：
      // { path: 'positions', element: <PositionList /> },   // P02 岗位列表
      // { path: 'candidates', element: <CandidateList /> }, // P06 候选人看板
      { path: '404', element: <NotFound /> },
      { path: '*', element: <Navigate to="/404" replace /> },
    ],
  },
]

export const router = createBrowserRouter(routes)

export default router
