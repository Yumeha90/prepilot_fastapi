import apiClient from './client'
import type { Competency } from './positions'

export interface JdExtractResult {
  filename: string
  text: string
}

export interface JdParseResult {
  hard_gates: string[]
  competencies: Competency[]
  bonuses: string[]
  /** 需要 HR 留意的提示（例如核心能力不足 3 项），为空表示无需提示 */
  notice: string
}

const BASE = '/api/jd'

/**
 * 上传 JD 文件并抽取纯文本（同步）。
 * 只支持文本型 PDF / DOCX / TXT / MD（≤5MB），不做 OCR；文件不落库。
 */
export async function extractJdText(file: File): Promise<JdExtractResult> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await apiClient.post<JdExtractResult>(`${BASE}/extract`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30_000,
  })
  return data
}

/**
 * AI 拆解 JD 原文（同步，约 3 秒）。
 * 结果不落库：填充表单后由 HR 确认保存才生效。
 */
export async function parseJd(rawText: string): Promise<JdParseResult> {
  const { data } = await apiClient.post<JdParseResult>(
    `${BASE}/parse`,
    { raw_text: rawText },
    { timeout: 60_000 },
  )
  return data
}
