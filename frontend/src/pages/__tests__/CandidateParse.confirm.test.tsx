/**
 * 需求①：解析页点「通过筛选，安排面试」后应落到人岗匹配页（对上进度条第三步），
 * 而不是直接跳回候选人看板。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const navigate = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate, useParams: () => ({ id: '7' }) }
})

vi.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: { hasPerm: (c: string) => boolean }) => unknown) =>
    sel({ hasPerm: () => true }),
}))

vi.mock('@/api/candidates', () => ({
  fetchCandidate: vi.fn(),
  confirmCandidate: vi.fn(),
  reparseCandidate: vi.fn(),
}))

import CandidateParse from '@/pages/CandidateParse'
import { confirmCandidate, fetchCandidate } from '@/api/candidates'

const base = {
  id: 7,
  name: '张三',
  contact_email: 'z@example.com',
  contact_phone: '',
  source: 'paste',
  resume_raw_text: '简历原文'.repeat(20),
  resume_file_name: '',
  parsed_profile: { basic: { name: '张三', email: 'z@example.com', phone: '', years: '', location: '' } },
  profile_status: 'parsed' as const,
  auth_tick: true,
  auth_at: null,
  confirmed_at: null,
  purged_at: null,
  created_by: null,
  created_by_name: 'hr',
  created_at: '2026-10-21T00:00:00',
  updated_at: '2026-10-21T00:00:00',
  applications: [],
  sessions: [],
  dispatch_notice: '',
}

const application = {
  id: 321,
  candidate_id: 7,
  position_id: 1,
  position_name: '后端工程师',
  stage: 'r1',
  current_round_name: '一面',
  interviewer_name: '李四',
  created_at: '2026-10-21T00:00:00',
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <CandidateParse />
    </QueryClientProvider>,
  )
}

describe('解析确认后跳转', () => {
  beforeEach(() => {
    navigate.mockReset()
    vi.mocked(fetchCandidate).mockResolvedValue(base as never)
  })

  it('确认成功后落到该候选人的匹配页并带 from=parse', async () => {
    vi.mocked(confirmCandidate).mockResolvedValue({
      ...base,
      profile_status: 'confirmed',
      applications: [application],
    } as never)

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /通过筛选，安排面试/ }))

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('/candidates/321/match?from=parse'),
    )
    expect(navigate).not.toHaveBeenCalledWith('/candidates')
  })

  it('拿不到 application 时兜底回看板，避免落到无 aid 的页面', async () => {
    vi.mocked(confirmCandidate).mockResolvedValue({
      ...base,
      profile_status: 'confirmed',
      applications: [],
    } as never)

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /通过筛选，安排面试/ }))

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/candidates'))
  })
})
