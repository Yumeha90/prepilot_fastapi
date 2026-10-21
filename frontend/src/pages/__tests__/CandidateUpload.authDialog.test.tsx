/**
 * 需求②：授权弹框面向的是操作页面的 HR，不是候选人 —— 此时授权应该已经事先取得。
 * 这里直接锁住文案方向：出现 HR 视角的表述，且不再出现「你的权利 / 联系 HR」这类候选人视角的话。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

vi.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: { hasPerm: (c: string) => boolean }) => unknown) =>
    sel({ hasPerm: () => true }),
}))

vi.mock('@/api/candidates', () => ({ createCandidate: vi.fn() }))
vi.mock('@/api/resume', () => ({ extractResume: vi.fn() }))
vi.mock('@/api/positions', () => ({ fetchPositions: vi.fn(async () => ({ items: [] })) }))

import CandidateUpload from '@/pages/CandidateUpload'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <CandidateUpload />
    </QueryClientProvider>,
  )
}

describe('上传页授权弹框', () => {
  beforeEach(() => {
    navigate.mockReset()
  })

  it('弹框正文以 HR 为对象，不再出现候选人视角的表述', async () => {
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '查看授权全文' }))

    // 面向 HR 的表述
    expect(await screen.findByText(/你的确认义务/)).toBeTruthy()
    expect(await screen.findByText(/本页由 HR 操作/)).toBeTruthy()
    // 候选人视角的旧表述必须消失
    expect(screen.queryByText(/你的权利/)).toBeNull()
    expect(screen.queryByText(/你可随时联系 HR/)).toBeNull()
  })

  it('勾选框与按钮也改成“确认已取得授权”的口径', async () => {
    renderPage()

    expect(await screen.findByText(/并确认已取得候选人的《数据处理授权》/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '查看授权全文' }))
    expect(await screen.findByRole('button', { name: '我已阅读并确认已取得授权' })).toBeTruthy()
  })
})
