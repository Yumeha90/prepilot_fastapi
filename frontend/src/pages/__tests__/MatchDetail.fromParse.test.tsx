/**
 * 需求①的另一半：人岗匹配页作为建档流程的第三步，必须在 ?from=parse 时
 * 把三步进度条与「完成，返回看板」补出来；从看板卡片进来时保持原来的纯详情页。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()
let search = 'from=parse'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => ({ aid: '321' }),
    useSearchParams: () => [new URLSearchParams(search), vi.fn()],
  }
})

vi.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: { hasPerm: (c: string) => boolean }) => unknown) =>
    sel({ hasPerm: () => true }),
}))

vi.mock('@/api/match', () => ({
  fetchMatch: vi.fn(),
  recomputeMatch: vi.fn(),
  submitMatchFeedback: vi.fn(),
}))

import MatchDetail from '@/pages/MatchDetail'
import { fetchMatch } from '@/api/match'

const matchOut = {
  application_id: 321,
  candidate_id: 7,
  candidate_name: '张三',
  position_id: 1,
  position_name: '后端工程师',
  stage: 'r1',
  jd_version: 2,
  jd_status: 'confirmed',
  profile_status: 'confirmed',
  has_score: true,
  score_id: 11,
  score: 78,
  tier: 'good',
  status: 'CURRENT',
  algorithm_version: 'v1.2',
  model_version: 'qwen',
  summary_status: 'ready',
  updated_at: '2026-10-21T00:00:00',
  breakdown: [],
  evidences: [],
  bonuses: [],
  vetoed_gates: [],
  unknown_gates: [],
  feedbacks: [],
  summary: null,
  blocked_reason: null,
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MatchDetail />
    </QueryClientProvider>,
  )
}

describe('匹配页的第三步上下文', () => {
  beforeEach(() => {
    navigate.mockReset()
    vi.mocked(fetchMatch).mockResolvedValue(matchOut as never)
  })

  it('from=parse 时展示三步进度条，且当前停在第三步“确认”', async () => {
    search = 'from=parse'
    renderPage()

    expect(await screen.findByText('确认')).toBeTruthy()
    // 进度条三步都在，且高亮的是第三步「确认」（不锁 antd 的具体 class 名，只看 active 落点）
    const items = document.querySelectorAll('.ant-steps-item')
    expect(items.length).toBe(3)
    expect(items[0].className).not.toMatch(/active/)
    expect(items[1].className).not.toMatch(/active/)
    expect(items[2].className).toMatch(/active|process/)
    expect(items[2].textContent).toContain('确认')
    expect(await screen.findByText(/候选人已确认并完成派单/)).toBeTruthy()
  })

  it('from=parse 时有“完成，返回候选人看板”，点它回看板', async () => {
    search = 'from=parse'
    renderPage()

    const btn = await screen.findByRole('button', { name: '完成，返回候选人看板' })
    btn.click()
    expect(navigate).toHaveBeenCalledWith('/candidates')
  })

  it('从看板进来（无 from 参数）不显示进度条与完成按钮', async () => {
    search = ''
    renderPage()

    expect(await screen.findByText('张三')).toBeTruthy()
    expect(document.querySelector('.ant-steps')).toBeNull()
    expect(screen.queryByRole('button', { name: '完成，返回候选人看板' })).toBeNull()
  })
})
