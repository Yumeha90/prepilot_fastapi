import apiClient, { AI_TIMEOUT } from './client'

/** 简历结构化档案（解析结果 + 人工纠错后写入） */
export interface ResumeProfile {
  basic: {
    name: string
    email: string
    phone: string
    location: string
    years: string
  }
  work: { company: string; title: string; period: string; desc: string }[]
  projects: { name: string; role: string; desc: string }[]
  skills: string[]
  education: { school: string; major: string; degree: string; period: string }[]
  /** 各区块置信度 0–1，<0.6 前端标黄提示人工核对（§7.1） */
  confidence: Record<string, number>
}

export function emptyProfile(): ResumeProfile {
  return {
    basic: { name: '', email: '', phone: '', location: '', years: '' },
    work: [],
    projects: [],
    skills: [],
    education: [],
    confidence: {},
  }
}

export interface ParseResult {
  profile: ResumeProfile
  /** >1 表示走了 L1 分块解析 */
  chunks: number
  notice: string
}

/** 上传简历文件并同步抽取纯文本（≤5MB，不做 OCR，文件不落库） */
export async function extractResume(file: File): Promise<{ filename: string; text: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await apiClient.post<{ filename: string; text: string }>(
    '/api/resume/extract',
    form,
    // 大 PDF / DOCX 抽取可能要好几秒，别用默认超时把它掐断
    { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60_000 },
  )
  return data
}

/** AI 解析简历（C2，同步，云端实测 17~23s，L1 分块更久）。结果不落库，确认后才写入 */
export async function parseResume(rawText: string): Promise<ParseResult> {
  const { data } = await apiClient.post<ParseResult>(
    '/api/resume/parse',
    { raw_text: rawText },
    { timeout: AI_TIMEOUT },
  )
  return data
}
