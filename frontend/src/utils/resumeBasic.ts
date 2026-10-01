/**
 * 从简历原文里猜「姓名 / 邮箱 / 手机号」，用于上传页自动回填。
 *
 * 这里刻意**不调大模型**：上传页只做抽取，等进了解析页才走 AI。
 * 邮箱与手机号有强特征，正则足够准；姓名靠启发式，猜错也不影响数据
 * ——用户可以直接改，改过的值不会被后续回填覆盖。
 */

export interface ResumeBasicGuess {
  name: string
  email: string
  phone: string
}

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+/

// 允许分隔符：13800138000 / 138 0013 8000 / 138-0013-8000 / +86 138 0013 8000
const PHONE_RE = /(?:\+?86[-\s]?)?(1[3-9]\d)([-\s]?\d{4})([-\s]?\d{4})(?!\d)/

// 明显不是人名的行首内容
// 明显不是人名的行首内容
const NAME_BLOCKLIST = [
  '个人简历',
  '简历',
  '求职简历',
  '个人简介',
  '求职意向',
  '基本信息',
  '个人信息',
  '工作经历',
  '工作经验',
  '项目经验',
  '教育背景',
  '专业技能',
  '技能清单',
  '自我评价',
  '期望职位',
  '期望薪资',
  'resume',
  'curriculum vitae',
]

// 行首是这些词时不是人名：英文简历常以职位头衔开头
const EN_ROLE_WORDS = [
  'senior',
  'junior',
  'engineer',
  'developer',
  'manager',
  'architect',
  'lead',
  'staff',
  'principal',
  'backend',
  'frontend',
  'front-end',
  'back-end',
  'full',
  'stack',
  'software',
  'resume',
  'curriculum',
  'vitae',
  'profile',
  'summary',
  'experience',
  'education',
  'skills',
  'contact',
  'objective',
]

// 中文姓名里不会出现这些词，出现说明这行不是姓名
const CN_ROLE_WORDS = ['工程师', '经理', '总监', '主管', '开发', '简历', '求职', '应聘', '岗位', '意向']

const EN_WORD = /^[A-Z][A-Za-z.'-]{1,20}$/

function pickName(text: string): string {
  // 1) 显式标签优先：姓名：张三
  const labelled = text.match(/(?:姓\s*名|名\s*字)\s*[:：]\s*([^\s,，、|｜;；\n]{1,20})/)
  if (labelled) return labelled[1].trim()

  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .slice(0, 8)

  for (const line of lines) {
    const lower = line.toLowerCase()
    if (NAME_BLOCKLIST.some((w) => lower.includes(w))) continue
    if (line.includes('@') || /https?:\/\//i.test(line)) continue

    // 取首个由空白 / 常见分隔符切出的片段
    const tokens = line.split(/[\s|｜,，、;；:：]+/).filter(Boolean)
    const first = tokens[0] ?? ''
    // 中文名 2–8 字（含少数民族名的间隔号），且不含职位词
    if (/^[一-龥·]{2,8}$/.test(first) && !CN_ROLE_WORDS.some((w) => first.includes(w))) {
      return first
    }
    // 英文名：John Smith / J. Smith / John A. Smith，头衔词不算
    if (EN_WORD.test(first) && !EN_ROLE_WORDS.includes(first.toLowerCase())) {
      const rest = tokens
        .slice(1, 3)
        .filter((tk) => EN_WORD.test(tk) && !EN_ROLE_WORDS.includes(tk.toLowerCase()))
      return [first, ...rest].join(' ')
    }
  }
  return ''
}

export function guessResumeBasic(text: string): ResumeBasicGuess {
  const src = text || ''
  const email = src.match(EMAIL_RE)?.[0] ?? ''

  const phoneHit = src.match(PHONE_RE)
  const phone = phoneHit
    ? [phoneHit[1], phoneHit[2], phoneHit[3]].join('').replace(/[-\s]/g, '')
    : ''

  return { name: pickName(src), email, phone }
}
