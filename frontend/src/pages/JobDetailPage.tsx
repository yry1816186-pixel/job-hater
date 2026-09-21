import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, qs } from '../api'
import type {
  Application,
  ATSScanReport,
  FeedbackKind,
  InterviewReviewInfo,
  InterviewSessionInfo,
  InterviewSessionStats,
  Job,
  MatchOutcome,
} from '../types'
import { useProfiles } from '../App'
import { GATE_LABELS, useToast } from '../components/ui'

const DIM_LABELS: Record<string, string> = {
  skill_match: '技能匹配',
  experience_relevance: '经历相关',
  location_fit: '地点契合',
  salary_fit: '薪资契合',
  recency: '信息时效',
  feedback: '历史反馈',
}

const INTERVIEW_KIND_LABELS: Record<string, string> = {
  behavioral: '行为面',
  technical: '技术面',
  case: '案例面',
  hr: 'HR 面',
  group: '群面',
  final: '终面',
}

const SELF_SCORE_LABELS: Record<string, string> = {
  structure: '回答结构',
  clarity: '表达清晰',
  technical: '技术深度',
  evidence_consistency: '证据一致',
}

const SELF_SCORE_KEYS = ['structure', 'clarity', 'technical', 'evidence_consistency'] as const

/** 覆盖分类 → 展示名与满分（与后端 ats_scan 的权重一致：45/10/15，共 70 分） */
const COVERAGE_CATEGORIES: Array<{ key: string; label: string; max: number }> = [
  { key: 'hard', label: '硬技能', max: 45 },
  { key: 'soft', label: '软技能', max: 10 },
  { key: 'other', label: '其他重要词', max: 15 },
]

const ATS_VERSION_KEY = 'jobhater-ats-version'

interface InterviewQuestions {
  sections: {
    project_deep_dive: { question: string; basis?: string; note?: string }[]
    technical_basics: { question: string; basis?: string; note?: string }[]
    behavioral: { question: string; basis?: string; note?: string }[]
    reverse_questions: { question: string; basis?: string; note?: string }[]
  }
  notes?: string[]
}

interface UpskillPlan {
  integrity_note: string
  gaps: { name: string; steps: string[]; milestone: string }[]
  phases: Record<string, string[]>
  notes?: string[]
}

interface InterviewInfo {
  id: string
  round: number
  kind?: string | null
  scheduled_at?: string | null
  status: string
}

/** 把题库压平成一列问题（练习时「插入面试题」轮换用） */
function flattenQuestions(q: InterviewQuestions | null): string[] {
  if (!q) return []
  return [
    ...q.sections.project_deep_dive,
    ...q.sections.technical_basics,
    ...q.sections.behavioral,
    ...q.sections.reverse_questions,
  ].map((x) => x.question)
}

/** 428 响应体 detail = {disclosure, task}；api 客户端会把对象 detail 序列化进 message */
function egressDisclosureOf(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 428) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(e.message)
  } catch {
    return null // detail 不是 JSON（异常情形）→ 交给调用方的通用错误提示
  }
  if (typeof parsed === 'object' && parsed !== null && 'disclosure' in parsed) {
    const d = parsed.disclosure // `in` 收窄后为 unknown
    if (typeof d === 'string' && d !== '') return d
  }
  return null
}

/** 复盘卡片（自评与 AI 复盘共用一个展示形态） */
function ReviewList({ reviews }: { reviews: InterviewReviewInfo[] }) {
  if (reviews.length === 0) return null
  return (
    <div>
      {reviews.map((r) => (
        <div key={r.id} className="subcard">
          <b>{r.ai_generated ? 'AI 复盘' : '我的自评'}</b>
          {r.overall != null && <span className="tag"> 总评 {r.overall}/10</span>}
          {Object.keys(r.scores).length > 0 && (
            <p style={{ fontSize: 13, margin: '4px 0' }}>
              {Object.entries(r.scores)
                .map(([k, v]) => `${SELF_SCORE_LABELS[k] ?? k} ${v}`)
                .join(' · ')}
            </p>
          )}
          {r.strengths.length > 0 && (
            <p style={{ fontSize: 13, margin: '2px 0' }}>亮点：{r.strengths.join('；')}</p>
          )}
          {r.gaps.length > 0 && (
            <p style={{ fontSize: 13, margin: '2px 0' }}>短板：{r.gaps.join('；')}</p>
          )}
          {r.practice_items.length > 0 && (
            <>
              <b style={{ fontSize: 12.5 }}>练习建议</b>
              <ul style={{ fontSize: 13, margin: '2px 0', paddingLeft: 18 }}>
                {r.practice_items.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      ))}
    </div>
  )
}

/** 材料工坊：确定性生成（本地零AI依赖）——定制简历/求职信/打招呼/题库/提升计划 */
function MaterialsWorkshop({
  jobId,
  questions,
  onQuestions,
}: {
  jobId: string
  questions: InterviewQuestions | null
  onQuestions: (q: InterviewQuestions) => void
}) {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [busy, setBusy] = useState('')
  const [greeting, setGreeting] = useState<{ greeting: string; length: number } | null>(null)
  const [coverLetter, setCoverLetter] = useState<{ content_md: string } | null>(null)
  const [plan, setPlan] = useState<UpskillPlan | null>(null)
  const [resumeInfo, setResumeInfo] = useState<{ version_id: string } | null>(null)

  const run = async (kind: string, fn: () => Promise<void>) => {
    if (!activeId) {
      toast('info', '先在「我的画像」建档，材料才能基于你的真实经历生成')
      return
    }
    setBusy(kind)
    try {
      await fn()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const genResume = () =>
    run('resume', async () => {
      const r = await api.post<{ version_id: string; relevance: Record<string, unknown> }>(
        `/jobs/${jobId}/materials/resume`, { profile_id: activeId },
      )
      setResumeInfo(r)
      toast('success', '岗位定制简历已生成（按 JD 相关度重排，只重排不编造）——去「简历」页查看')
    })

  const genCover = () =>
    run('cover', async () => {
      const r = await api.post<{ content_md: string }>(
        `/jobs/${jobId}/materials/cover-letter`, { profile_id: activeId },
      )
      setCoverLetter(r)
    })

  const genGreeting = () =>
    run('greet', async () => {
      const r = await api.post<{ greeting: string; length: number }>(
        `/jobs/${jobId}/materials/greeting`, { profile_id: activeId },
      )
      setGreeting(r)
    })

  const genQuestions = () =>
    run('q', async () => {
      const r = await api.get<InterviewQuestions>(
        `/jobs/${jobId}/materials/interview-questions?profile_id=${activeId}`,
      )
      onQuestions(r)
    })

  const genPlan = () =>
    run('plan', async () => {
      const r = await api.get<UpskillPlan>(
        `/jobs/${jobId}/materials/upskill-plan?profile_id=${activeId}`,
      )
      setPlan(r)
    })

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(
      () => toast('success', '已复制到剪贴板'),
      () => toast('error', '复制失败，请手动选择文本'),
    )
  }

  return (
    <div className="card" style={{ marginTop: 18 }}>
      <h2 style={{ marginTop: 0 }}>材料工坊 <span className="tag">本地生成 · 不用 AI 也能用</span></h2>
      <p className="hint" style={{ marginTop: 0 }}>
        全部基于你画像里的真实条目生成：定制简历只重排不编造；求职信/话术只引用你真实做过的事。
      </p>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={genResume} disabled={!!busy}>
          {busy === 'resume' ? '生成中…' : '📄 定制简历（按 JD 重排）'}
        </button>
        <button className="btn" onClick={genCover} disabled={!!busy}>
          {busy === 'cover' ? '生成中…' : '✉️ 求职信'}
        </button>
        <button className="btn" onClick={genGreeting} disabled={!!busy}>
          {busy === 'greet' ? '生成中…' : '💬 打招呼话术'}
        </button>
        <button className="btn" onClick={genQuestions} disabled={!!busy}>
          {busy === 'q' ? '生成中…' : '🎤 面试题库'}
        </button>
        <button className="btn" onClick={genPlan} disabled={!!busy}>
          {busy === 'plan' ? '生成中…' : '📈 技能提升计划'}
        </button>
      </div>

      {resumeInfo && (
        <div className="subcard">
          <b>✓ 定制简历已生成</b>{' '}
          <Link className="btn small" to="/resume">去简历页查看/导出 →</Link>
        </div>
      )}
      {greeting && (
        <div className="subcard">
          <b>打招呼话术（{greeting.length} 字）</b>
          <p style={{ whiteSpace: 'pre-wrap', margin: '6px 0' }}>{greeting.greeting}</p>
          <button className="btn small" onClick={() => copy(greeting.greeting)}>复制</button>
        </div>
      )}
      {coverLetter && (
        <div className="subcard">
          <b>求职信</b>
          <div style={{ whiteSpace: 'pre-wrap', maxHeight: 260, overflowY: 'auto', fontSize: 13.5 }}>
            {coverLetter.content_md}
          </div>
          <button className="btn small" style={{ marginTop: 6 }} onClick={() => copy(coverLetter.content_md)}>
            复制全文
          </button>
        </div>
      )}
      {questions && (
        <div className="subcard">
          <b>面试题库</b>
          {questions.notes?.map((n, i) => <p key={i} className="hint">⚠ {n}</p>)}
          {(
            [
              ['项目深挖', questions.sections.project_deep_dive],
              ['技术基础', questions.sections.technical_basics],
              ['行为面', questions.sections.behavioral],
              ['建议反问', questions.sections.reverse_questions],
            ] as const
          ).map(([label, qs]) =>
            qs?.length ? (
              <div key={label}>
                <b style={{ fontSize: 13 }}>{label}</b>
                <ol style={{ margin: '4px 0 10px', paddingLeft: 20, fontSize: 13.5 }}>
                  {qs.map((q, i) => (
                    <li key={i}>{q.question}{q.note ? <span className="hint">（{q.note}）</span> : null}</li>
                  ))}
                </ol>
              </div>
            ) : null,
          )}
        </div>
      )}
      {plan && (
        <div className="subcard">
          <b>技能提升计划</b>
          <p className="hint">{plan.integrity_note}</p>
          {plan.gaps.length === 0 ? (
            <p>没有发现技能缺口——按 JD 关键词与你的技能对照。</p>
          ) : (
            plan.gaps.map((g) => (
              <div key={g.name}>
                <b>{g.name}</b>
                <ol style={{ margin: '2px 0 8px', paddingLeft: 20, fontSize: 13 }}>
                  {g.steps.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
                <p className="hint">里程碑：{g.milestone}</p>
              </div>
            ))
          )}
          {(Object.entries(plan.phases) as [string, string[]][]).map(([phase, items]) => (
            <div key={phase}>
              <b style={{ fontSize: 13 }}>{phase} 冲刺</b>
              <ul style={{ margin: '2px 0 8px', paddingLeft: 20, fontSize: 13 }}>
                {items.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 简历版本选项：每份简历取最新一版 */
interface ResumeVersionOption {
  id: string
  label: string
}

/**
 * ATS 匹配报告：简历版本 × JD 的确定性打分。
 * 词汇覆盖 70 分（硬技能45/软技能10/其他重要词15）+ 可解析性 30 分，无 AI 参与。
 */
function AtsReportPanel({ jobId }: { jobId: string }) {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [options, setOptions] = useState<ResumeVersionOption[]>([])
  const [optionsLoaded, setOptionsLoaded] = useState(false)
  const [versionId, setVersionId] = useState('')
  const [report, setReport] = useState<ATSScanReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadErr, setLoadErr] = useState('')
  const [expandedTerm, setExpandedTerm] = useState('')

  useEffect(() => {
    if (!activeId) return
    let alive = true
    const loadOptions = async () => {
      try {
        const resumes = await api.get<Array<{ id: string; name: string }>>(
          `/resumes${qs({ profile_id: activeId })}`,
        )
        const pairs = await Promise.all(
          resumes.map(async (r) => {
            const versions = await api.get<
              Array<{ id: string; version: number; note?: string | null }>
            >(`/resumes/${r.id}/versions`)
            const latest = versions[0] // 后端按 version 倒序，第一个即最新
            if (!latest) return null
            return {
              id: latest.id,
              label: `${r.name} · v${latest.version}${latest.note ? `（${latest.note}）` : ''}`,
            }
          }),
        )
        if (!alive) return
        const opts = pairs.filter((p): p is ResumeVersionOption => p !== null)
        setOptions(opts)
        setOptionsLoaded(true)
        const saved = window.localStorage.getItem(ATS_VERSION_KEY)
        const remembered = opts.find((o) => o.id === saved)
        setVersionId(remembered ? remembered.id : (opts[0]?.id ?? ''))
      } catch (e) {
        if (alive) setLoadErr((e as Error).message)
      }
    }
    void loadOptions()
    return () => {
      alive = false
    }
  }, [activeId])

  const pickVersion = (id: string) => {
    setVersionId(id)
    window.localStorage.setItem(ATS_VERSION_KEY, id)
  }

  const generate = () => {
    if (!versionId) {
      toast('info', '还没有可选的简历版本——先去「简历」页创建，或用上方「定制简历」生成')
      return
    }
    setBusy(true)
    setLoadErr('')
    api
      .get<ATSScanReport>(`/jobs/${jobId}/ats-scan${qs({ resume_version_id: versionId })}`)
      .then((r) => {
        setReport(r)
        setExpandedTerm('')
      })
      .catch((e) => setLoadErr((e as Error).message))
      .finally(() => setBusy(false))
  }

  const copyMissing = () => {
    if (!report || report.keywords.missing.length === 0) return
    const text = report.keywords.missing.map((k) => k.term).join('、')
    navigator.clipboard?.writeText(text).then(
      () => toast('success', `已复制 ${report.keywords.missing.length} 个缺失词`),
      () => toast('error', '复制失败，请手动选择文本'),
    )
  }

  const matched = report?.keywords.matched ?? []
  const expandedEntry = matched.find((k) => k.term === expandedTerm)

  return (
    <div className="card" style={{ marginTop: 18 }}>
      <h2 style={{ marginTop: 0 }}>ATS 匹配报告 <span className="tag">确定性打分 · 无 AI</span></h2>
      <p className="hint" style={{ marginTop: 0 }}>
        按招聘系统筛选简历的机械规则打分：关键词覆盖 70 分 + 可解析性 30 分。
        分数低不代表简历差，只说明词汇和格式没对上这份 JD。
      </p>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {options.length > 0 ? (
          <select value={versionId} onChange={(e) => pickVersion(e.target.value)}>
            {options.map((o) => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
        ) : (
          <span className="hint" style={{ margin: 0 }}>
            {!activeId
              ? '先在「我的画像」建档，才能选择简历版本'
              : optionsLoaded
                ? '还没有简历版本——先去「简历」页创建，或用上方「定制简历」生成'
                : '简历版本加载中…'}
          </span>
        )}
        <button className="btn primary" onClick={generate} disabled={busy || !versionId}>
          {busy ? '生成中…' : '生成报告'}
        </button>
      </div>
      {loadErr && <div className="error-box" style={{ marginTop: 8 }}>{loadErr}</div>}

      {report && (
        <>
          <div className="score-ring" style={{ marginTop: 14 }}>
            <div>
              <div className="big">
                {report.score}
                <span style={{ fontSize: 15, color: 'var(--muted)', fontWeight: 400 }}> /100</span>
              </div>
              <span className="tag">{report.band}</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>
              {report.score >= report.target
                ? `已达目标线 ${report.target} 分`
                : `距目标线 ${report.target} 分还差 ${report.target - report.score} 分`}
              <div>覆盖 {report.coverage.score}/70 · 解析 {report.parseability.score}/30</div>
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <b style={{ fontSize: 13 }}>关键词覆盖</b>
            {COVERAGE_CATEGORIES.map(({ key, label, max }) => {
              const c = report.coverage.by_category[key]
              if (!c) return null
              const pct = c.total > 0 ? Math.round((c.covered / c.total) * 100) : 0
              return (
                <div key={key} style={{ marginBottom: 6, fontSize: 13 }}>
                  <div className="between">
                    <span>
                      {label}（满分 {max}）：<b>{c.covered}/{c.total}</b> 词命中 · 得 {c.score} 分
                    </span>
                  </div>
                  <div style={{ height: 6, background: 'var(--line, #e3dfd7)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${pct}%`, background: 'var(--accent, #0f6b52)' }} />
                  </div>
                  {c.note && <div className="hint">{c.note}</div>}
                </div>
              )
            })}
          </div>

          <div style={{ marginTop: 12 }}>
            <b style={{ fontSize: 13 }}>可解析性（解析分 {report.parseability.score}/30）</b>
            <div style={{ marginTop: 4 }}>
              {report.parseability.checks.map((chk, i) => (
                <div key={i} style={{ fontSize: 13, marginBottom: 4 }}>
                  <span style={{ color: chk.ok ? 'var(--accent, #0f6b52)' : 'var(--danger, #b3362b)' }}>
                    {chk.ok ? '✓' : '✗'}
                  </span>{' '}
                  {chk.item} · {chk.score} 分
                  {!chk.ok && <div className="hint">修法：{chk.fix}</div>}
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <div className="between">
              <b style={{ fontSize: 13 }}>✅ 已命中（{report.keywords.matched.length}）</b>
            </div>
            {report.keywords.matched.length === 0 ? (
              <p className="hint">一个关键词都没命中——简历和这份 JD 的用词可能差得很远。</p>
            ) : (
              <>
                <div className="kw-list" style={{ marginTop: 6 }}>
                  {report.keywords.matched.map((k) => (
                    <button
                      key={k.term}
                      className="kw-chip hit"
                      style={{
                        background: 'transparent',
                        cursor: 'pointer',
                        font: 'inherit',
                        fontWeight: expandedTerm === k.term ? 700 : 400,
                      }}
                      onClick={() => setExpandedTerm((prev) => (prev === k.term ? '' : k.term))}
                      title="点击查看命中证据"
                    >
                      {k.term} ×{k.jd_count}
                    </button>
                  ))}
                </div>
                {expandedEntry && (
                  <div className="kw-evidence">
                    「{expandedEntry.term}」JD 出现 {expandedEntry.jd_count} 次，
                    简历命中 {expandedEntry.resume_count ?? 0} 处
                    {expandedEntry.hits && expandedEntry.hits.length > 0
                      ? '：' + expandedEntry.hits
                          .map((h) => `${h.section}×${h.count}「${h.snippet}」`)
                          .join('；')
                      : ''}
                  </div>
                )}
              </>
            )}
          </div>

          <div style={{ marginTop: 12 }}>
            <div className="between">
              <b style={{ fontSize: 13 }}>❌ 缺失（{report.keywords.missing.length}）</b>
              {report.keywords.missing.length > 0 && (
                <button className="btn small" onClick={copyMissing}>复制缺失词</button>
              )}
            </div>
            {report.keywords.missing.length > 0 && (
              <div className="kw-list" style={{ marginTop: 6 }}>
                {report.keywords.missing.map((k) => (
                  <span
                    key={k.term}
                    className={`kw-chip${k.category === 'hard' ? ' hard miss' : ''}`}
                    title={`JD 出现 ${k.jd_count} 次`}
                  >
                    {k.term}
                  </span>
                ))}
              </div>
            )}
            {report.keywords.hard_missing.length > 0 && (
              <p style={{ color: 'var(--danger, #b3362b)', fontSize: 13, margin: '6px 0 0' }}>
                硬技能缺口：{report.keywords.hard_missing.join('、')}
              </p>
            )}
          </div>

          {report.advisory.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <b style={{ fontSize: 13 }}>改进建议（仅提示，不计分）</b>
              {report.advisory.map((a, i) => (
                <div key={i} className="subcard" style={{ borderLeft: '3px solid var(--warn, #8a5a2b)' }}>
                  <b style={{ fontSize: 13 }}>{a.title}</b>
                  <div style={{ fontSize: 13 }}>{a.detail}</div>
                </div>
              ))}
            </div>
          )}

          <details style={{ marginTop: 10, fontSize: 12.5 }}>
            <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>报告怎么算的</summary>
            <p className="hint" style={{ whiteSpace: 'pre-wrap' }}>{report.methodology.note}</p>
            <b>计入总分：</b>
            <ul style={{ margin: '2px 0 6px', paddingLeft: 18 }}>
              {report.methodology.counted.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
            <b>仅提示不计分：</b>
            <ul style={{ margin: '2px 0', paddingLeft: 18 }}>
              {report.methodology.advisory_only.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </details>
        </>
      )}
    </div>
  )
}

/**
 * 面试练习：本地逐字稿记录（零 AI 依赖）+ 确定性统计。
 * AI 复盘是可选项，远程调用前先完整披露、确认后才发数据。
 */
function InterviewPracticePanel({
  app,
  questions,
}: {
  app: Application | null
  questions: InterviewQuestions | null
}) {
  const { toast } = useToast()
  const [interviews, setInterviews] = useState<InterviewInfo[]>([])
  const [interviewId, setInterviewId] = useState('')
  const [sessions, setSessions] = useState<InterviewSessionInfo[]>([])
  const [session, setSession] = useState<InterviewSessionInfo | null>(null)
  const [viewing, setViewing] = useState<InterviewSessionInfo | null>(null)
  const [reviews, setReviews] = useState<InterviewReviewInfo[]>([])
  const [stats, setStats] = useState<InterviewSessionStats | null>(null)
  const [persona, setPersona] = useState('')
  const [difficulty, setDifficulty] = useState(3)
  const [role, setRole] = useState<'candidate' | 'interviewer'>('candidate')
  const [draft, setDraft] = useState('')
  const [qCursor, setQCursor] = useState(0)
  const [selfScores, setSelfScores] = useState<Record<string, number>>({
    structure: 5,
    clarity: 5,
    technical: 5,
    evidence_consistency: 5,
  })
  const [strengthsText, setStrengthsText] = useState('')
  const [gapsText, setGapsText] = useState('')
  const [aiDisclosure, setAiDisclosure] = useState('')
  const [busy, setBusy] = useState('')
  const [newInterviewOpen, setNewInterviewOpen] = useState(false)
  const [newDate, setNewDate] = useState('')
  const [newKind, setNewKind] = useState('behavioral')

  useEffect(() => {
    if (!app) return
    let alive = true
    const loadAll = async () => {
      try {
        const [ivs, ss] = await Promise.all([
          api.get<InterviewInfo[]>(`/applications/${app.id}/interviews`),
          api.get<InterviewSessionInfo[]>(`/applications/${app.id}/interview-sessions`),
        ])
        if (!alive) return
        setInterviews(ivs)
        setSessions(ss)
        setInterviewId((prev) => (ivs.some((i) => i.id === prev) ? prev : (ivs[0]?.id ?? '')))
      } catch (e) {
        if (alive) toast('error', (e as Error).message)
      }
    }
    void loadAll()
    return () => {
      alive = false
    }
  }, [app, toast])

  const createInterview = () => {
    if (!app) return
    setBusy('newInterview')
    api
      .post<InterviewInfo>(`/applications/${app.id}/interviews`, {
        scheduled_at: newDate || undefined,
        kind: newKind,
      })
      .then((iv) => {
        setInterviews((prev) => [...prev, iv])
        setInterviewId(iv.id)
        setNewInterviewOpen(false)
        setNewDate('')
        toast('success', '已创建练习面试')
      })
      .catch((e) => toast('error', (e as Error).message))
      .finally(() => setBusy(''))
  }

  const startPractice = () => {
    if (!interviewId) {
      toast('info', '先创建一个练习面试，再开始练习')
      return
    }
    setBusy('start')
    api
      .post<InterviewSessionInfo>('/interview-sessions', {
        interview_id: interviewId,
        mode: 'mock',
        persona: persona || undefined,
        difficulty,
      })
      .then((s) => {
        setSession(s)
        setViewing(null)
        setStats(null)
        setReviews([])
        setAiDisclosure('')
        setDraft('')
        setRole('candidate')
      })
      .catch((e) => toast('error', (e as Error).message))
      .finally(() => setBusy(''))
  }

  const sendTurn = () => {
    const cur = session
    if (!cur || cur.ended_at) return
    const content = draft.trim()
    if (!content) return
    setBusy('turn')
    api
      .post<InterviewSessionInfo>(`/interview-sessions/${cur.id}/turns`, { role, content })
      .then((s) => {
        setSession(s)
        setDraft('')
      })
      .catch((e) => toast('error', (e as Error).message))
      .finally(() => setBusy(''))
  }

  const insertQuestion = () => {
    const pool = flattenQuestions(questions)
    if (pool.length === 0) return
    const q = pool[qCursor % pool.length]
    setQCursor((c) => c + 1)
    setRole('interviewer')
    setDraft(q)
  }

  const endPractice = () => {
    const sid = session?.id
    const appId = app?.id
    if (!sid || !appId) return
    setBusy('end')
    const finish = async () => {
      const ended = await api.post<InterviewSessionInfo>(`/interview-sessions/${sid}/end`)
      setSession(ended)
      const [st, rv] = await Promise.all([
        api.get<InterviewSessionStats>(`/interview-sessions/${sid}/stats`),
        api.get<InterviewReviewInfo[]>(`/interview-sessions/${sid}/reviews`),
      ])
      setStats(st)
      setReviews(rv)
      const ss = await api.get<InterviewSessionInfo[]>(`/applications/${appId}/interview-sessions`)
      setSessions(ss)
    }
    finish()
      .catch((e) => toast('error', (e as Error).message))
      .finally(() => setBusy(''))
  }

  const submitSelfReview = () => {
    const sid = session?.id
    if (!sid) return
    const splitLines = (t: string) =>
      t.split('\n').map((l) => l.trim()).filter((l) => l !== '')
    setBusy('self')
    api
      .post<InterviewReviewInfo>(`/interview-sessions/${sid}/reviews/self`, {
        scores: selfScores,
        strengths: splitLines(strengthsText),
        gaps: splitLines(gapsText),
        practice_items: [],
      })
      .then((rv) => {
        setReviews((prev) => [...prev, rv])
        setStrengthsText('')
        setGapsText('')
        toast('success', '自评已保存——多练几场，对比分数变化')
      })
      .catch((e) => toast('error', (e as Error).message))
      .finally(() => setBusy(''))
  }

  const openSession = (s: InterviewSessionInfo) => {
    setSession(null)
    setStats(null)
    setAiDisclosure('')
    setViewing(s)
    api
      .get<InterviewReviewInfo[]>(`/interview-sessions/${s.id}/reviews`)
      .then(setReviews)
      .catch((e) => {
        setReviews([])
        toast('error', (e as Error).message)
      })
  }

  /** ack=false 先探一次：本地模式会得到诚实降级说明，远程模式会得到 428 披露文本 */
  const requestAiReview = (ack: boolean) => {
    const sid = viewing?.id ?? session?.id
    if (!sid) return
    setBusy('ai')
    api
      .post<{ executed: boolean; review?: InterviewReviewInfo; message?: string }>(
        `/interview-sessions/${sid}/reviews/ai`,
        { ack_egress: ack },
      )
      .then((r) => {
        const review = r.review
        if (r.executed && review) {
          setReviews((prev) => [...prev, review])
          setAiDisclosure('')
          toast('success', 'AI 复盘已生成——AI 可能出错，结论请自行核对')
        } else {
          setAiDisclosure('')
          toast('info', r.message ?? '远程 AI 未执行')
        }
      })
      .catch((e) => {
        const disclosure = egressDisclosureOf(e)
        if (disclosure !== null) {
          setAiDisclosure(disclosure)
        } else {
          toast('error', (e as Error).message)
        }
      })
      .finally(() => setBusy(''))
  }

  if (!app) {
    return (
      <div className="card" style={{ marginTop: 18 }}>
        <h2 style={{ marginTop: 0 }}>面试练习</h2>
        <p className="hint" style={{ marginTop: 0 }}>
          练习挂在投递记录下面——先在投递看板为这个岗位建立跟踪，才能排练习面试、留逐字稿和复盘。
        </p>
        <Link className="btn" to="/applications">去投递看板 →</Link>
      </div>
    )
  }

  const ended = session != null && session.ended_at != null
  const shownSession = session ?? viewing
  const questionCount = flattenQuestions(questions).length

  return (
    <div className="card" style={{ marginTop: 18 }}>
      <h2 style={{ marginTop: 0 }}>面试练习 <span className="tag">本地逐字稿 · 不用 AI 也能练</span></h2>
      <p className="hint" style={{ marginTop: 0 }}>
        自己给自己出题、打字作答，全部记录留在本地；结束后看统计和复盘，找出下一场要改的点。
      </p>

      <div className="row" style={{ flexWrap: 'wrap' }}>
        {interviews.length > 0 ? (
          <select value={interviewId} onChange={(e) => setInterviewId(e.target.value)}>
            {interviews.map((iv) => (
              <option key={iv.id} value={iv.id}>
                第 {iv.round} 轮 · {INTERVIEW_KIND_LABELS[iv.kind ?? ''] ?? iv.kind ?? '未分类'}
                {iv.scheduled_at ? ` · ${iv.scheduled_at.slice(0, 10)}` : ''}
              </option>
            ))}
          </select>
        ) : (
          <span className="hint" style={{ margin: 0 }}>该岗位还没有面试记录</span>
        )}
        <button className="btn small" onClick={() => setNewInterviewOpen((v) => !v)}>
          {newInterviewOpen ? '收起' : '＋ 新建练习面试'}
        </button>
      </div>
      {newInterviewOpen && (
        <div className="row" style={{ marginTop: 6 }}>
          <input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} />
          <select value={newKind} onChange={(e) => setNewKind(e.target.value)}>
            {Object.entries(INTERVIEW_KIND_LABELS).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <button className="btn small" onClick={createInterview} disabled={busy === 'newInterview'}>
            {busy === 'newInterview' ? '创建中…' : '创建'}
          </button>
        </div>
      )}

      {sessions.length > 0 && (
        <div className="subcard">
          <b style={{ fontSize: 13 }}>历史练习（{sessions.length}）</b>
          <div className="row" style={{ marginTop: 4 }}>
            {sessions.map((s) => (
              <button
                key={s.id}
                className="btn small"
                style={viewing?.id === s.id ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
                onClick={() => openSession(s)}
              >
                {s.started_at ? s.started_at.slice(0, 16).replace('T', ' ') : '时间未知'} ·{' '}
                {s.transcript.length} 轮 · {s.ended_at ? '已结束' : '进行中'}
                {s.persona ? ` · ${s.persona}` : ''}
              </button>
            ))}
          </div>
        </div>
      )}

      {!session && (
        <div className="row" style={{ marginTop: 10 }}>
          <input
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
            placeholder="面试官人设（可选，如：严肃的技术面试官）"
            style={{ flex: 1, minWidth: 200 }}
          />
          <select value={difficulty} onChange={(e) => setDifficulty(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>难度 {d}</option>
            ))}
          </select>
          <button className="btn primary" onClick={startPractice} disabled={busy === 'start'}>
            {busy === 'start' ? '创建中…' : '开始练习'}
          </button>
        </div>
      )}

      {shownSession && (
        <div style={{ marginTop: 12 }}>
          <div className="between">
            <b style={{ fontSize: 13 }}>
              {session ? '练习进行中' : '历史回看（只读）'}
              {shownSession.persona ? ` · ${shownSession.persona}` : ''}
              {shownSession.difficulty != null ? ` · 难度 ${shownSession.difficulty}` : ''}
            </b>
            {viewing && (
              <button className="btn small" onClick={() => { setViewing(null); setReviews([]) }}>
                关闭回看
              </button>
            )}
          </div>
          <div className="transcript" style={{ marginTop: 6 }}>
            {shownSession.transcript.length === 0 && (
              <p className="hint" style={{ margin: 0 }}>
                还没有内容——面试官先提问，或直接输入你的回答。
              </p>
            )}
            {shownSession.transcript.map((t, i) => (
              <div key={i} className={`turn ${t.role}`}>
                <span className="who">{t.role === 'interviewer' ? '面试官' : '我'}</span>
                {t.content}
              </div>
            ))}
          </div>

          {session && !ended && (
            <>
              <div className="turn-input">
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value === 'interviewer' ? 'interviewer' : 'candidate')}
                >
                  <option value="candidate">我是候选人（回答）</option>
                  <option value="interviewer">我是面试官（提问）</option>
                </select>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      sendTurn()
                    }
                  }}
                  placeholder="Enter 发送，Shift+Enter 换行"
                  rows={2}
                  style={{ flex: 1, resize: 'vertical' }}
                />
                <button className="btn primary" onClick={sendTurn} disabled={busy === 'turn' || !draft.trim()}>
                  {busy === 'turn' ? '发送中…' : '发送'}
                </button>
              </div>
              <div className="row" style={{ marginTop: 6 }}>
                {questionCount > 0 && (
                  <button className="btn small" onClick={insertQuestion}>
                    插入面试题（题库 {questionCount} 题）
                  </button>
                )}
                <button className="btn" onClick={endPractice} disabled={busy === 'end'}>
                  {busy === 'end' ? '结束中…' : '结束练习'}
                </button>
              </div>
            </>
          )}

          {ended && stats && (
            <div className="subcard">
              <b style={{ fontSize: 13 }}>练习统计</b>
              <p style={{ fontSize: 13, margin: '4px 0' }}>
                问题 {stats.questions} · 回答 {stats.answers} · 未接问题 {stats.unanswered_trailing} ·
                平均回答 {stats.answer_chars.avg} 字 · 时长 {stats.duration_min ?? '—'} 分钟
              </p>
            </div>
          )}

          {ended && (
            <div className="subcard">
              <b style={{ fontSize: 13 }}>自评（打分 0-10，亮点/短板一行一条）</b>
              <div style={{ display: 'grid', gap: 4, marginTop: 4 }}>
                {SELF_SCORE_KEYS.map((k) => (
                  <label key={k} style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ minWidth: 64 }}>{SELF_SCORE_LABELS[k]}</span>
                    <input
                      type="range"
                      min={0}
                      max={10}
                      value={selfScores[k]}
                      onChange={(e) =>
                        setSelfScores((prev) => ({ ...prev, [k]: Number(e.target.value) }))
                      }
                    />
                    <b>{selfScores[k]}</b>
                  </label>
                ))}
              </div>
              <textarea
                value={strengthsText}
                onChange={(e) => setStrengthsText(e.target.value)}
                placeholder="亮点（一行一条）"
                rows={2}
                style={{ width: '100%', marginTop: 6, resize: 'vertical' }}
              />
              <textarea
                value={gapsText}
                onChange={(e) => setGapsText(e.target.value)}
                placeholder="短板（一行一条）"
                rows={2}
                style={{ width: '100%', marginTop: 4, resize: 'vertical' }}
              />
              <button
                className="btn primary small"
                style={{ marginTop: 6 }}
                onClick={submitSelfReview}
                disabled={busy === 'self'}
              >
                {busy === 'self' ? '提交中…' : '提交自评'}
              </button>
            </div>
          )}

          {(ended || viewing) && (
            <div style={{ marginTop: 8 }}>
              <ReviewList reviews={reviews} />
              {aiDisclosure ? (
                <div className="subcard" style={{ borderLeft: '3px solid var(--warn, #8a5a2b)' }}>
                  <b style={{ fontSize: 13 }}>远程 AI 复盘——先看清楚再决定</b>
                  <p style={{ fontSize: 13, whiteSpace: 'pre-wrap', margin: '4px 0' }}>{aiDisclosure}</p>
                  <div className="row">
                    <button className="btn primary small" onClick={() => requestAiReview(true)} disabled={busy === 'ai'}>
                      {busy === 'ai' ? '执行中…' : '确认并执行（逐字稿会发送到远程 AI）'}
                    </button>
                    <button className="btn small" onClick={() => setAiDisclosure('')}>取消</button>
                  </div>
                </div>
              ) : (
                <button className="btn small" onClick={() => requestAiReview(false)} disabled={busy === 'ai'}>
                  {busy === 'ai' ? '请求中…' : 'AI 复盘（远程，需确认）'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 岗位详情：结论 + 依据 + 不确定性，透明到每一维；无匹配时解释为什么 */
export default function JobDetailPage() {
  const { jobId = '' } = useParams()
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [job, setJob] = useState<(Job & { match?: MatchOutcome | null }) | null>(null)
  const [app, setApp] = useState<Application | null>(null)
  const [myFeedback, setMyFeedback] = useState<FeedbackKind[]>([])
  const [questions, setQuestions] = useState<InterviewQuestions | null>(null)
  const [err, setErr] = useState('')

  const load = () => {
    api
      .get<Job & { match?: MatchOutcome | null }>(`/jobs/${jobId}${qs({ profile_id: activeId ?? '' })}`)
      .then((j) => setJob(j))
      .catch((e) => setErr(e.message))
    if (activeId) {
      api
        .get<{ kind: FeedbackKind; job_id?: string }[]>(`/feedback${qs({ profile_id: activeId })}`)
        .then((rows) => setMyFeedback(rows.filter((e) => e.job_id === jobId).map((e) => e.kind)))
        .catch(() => setMyFeedback([]))
      api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`).then((apps) => {
        setApp(apps.find((a) => a.job_id === jobId) ?? null)
      }).catch(() => setApp(null))
    }
  }
  useEffect(load, [jobId, activeId])

  const startApplication = async () => {
    if (!activeId || !job) return
    try {
      await api.post<{ id: string }>('/applications', {
        job_id: job.id,
        profile_id: activeId,
        status: 'saved',
      })
      toast('success', '已建立投递跟踪——去投递看板推进它')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  const feedback = async (kind: FeedbackKind) => {
    if (!activeId) return
    try {
      await api.post('/feedback', { profile_id: activeId, kind, job_id: jobId })
      toast('success', '反馈已记录（影响同雇主岗位排序，画像页可查看与重置）')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }

  if (err && !job) {
    return (
      <div>
        <div className="error-box">{err}</div>
        <Link className="btn" to="/jobs">← 回收件箱</Link>
      </div>
    )
  }
  if (!job) return <div className="loading">加载中…</div>

  const m = job.match
  const ev = (m?.evidence ?? {}) as {
    matched_skills?: string[]
    unmatched_core_skills?: string[]
    salary_inversion?: { city: string; job_max_k: number; city_reference_k: number }
    hard_domain_flag?: string[]
    lexical_semantic_conflict?: number
  }

  return (
    <div>
      <Link to="/jobs" style={{ fontSize: 13 }}>← 回收件箱</Link>
      <div className="between">
        <h1>{job.title}</h1>
        {job.canonical_url && (
          <a className="btn" href={job.canonical_url} target="_blank" rel="noreferrer">
            打开原始页面 ↗
          </a>
        )}
      </div>
      <p className="page-sub">
        {job.employer_name} · {job.city ?? '城市未知'} · {job.salary_text ?? '薪资未标注'}
        {job.education_required ? ` · ${job.education_required}` : ''}
        {job.experience_required_text ? ` · ${job.experience_required_text}` : ''}
      </p>
      {err && <div className="error-box">{err}</div>}

      {m ? (
        <div className="card">
          <div className="between">
            <h2 style={{ margin: 0 }}>匹配结论</h2>
            <div className="score" style={{ fontSize: 26 }}>
              {m.rank_score ?? '—'} <span style={{ fontSize: 13 }}>{m.verdict}</span>
            </div>
          </div>
          {!m.eligible && (
            <div className="error-box">
              <b>未通过硬性条件</b>（来自你在画像页的偏好，可回去调整）：
              <ul style={{ margin: '6px 0 0' }}>
                {m.gate_reasons.filter((g) => !g.passed).map((g) => (
                  <li key={g.code}>
                    {GATE_LABELS[g.code] ?? g.code}：{g.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {m.eligible && m.gate_reasons.length > 0 && (
            <p style={{ color: 'var(--muted)', fontSize: 13.5 }}>
              硬性条件全部通过（{m.gate_reasons.filter((g) => g.passed).length} 项检查）
            </p>
          )}
          {ev.salary_inversion && (
            <div className="warn-box">
              ⚠ 疑似薪资倒挂：{ev.salary_inversion.city} 类岗位保守参考约{' '}
              {ev.salary_inversion.city_reference_k}K/月，本岗上限 {ev.salary_inversion.job_max_k}K
              （行情因行业而异，仅供参考，不影响你的底线判断）
            </div>
          )}
          <div className="dim-grid">
            {Object.entries(m.dims).map(([k, d]) => (
              <div className="dim-card card" key={k} style={{ margin: 0 }}>
                <h3>
                  <span>{DIM_LABELS[k] ?? k}</span>
                  <span className="score">{Math.round(d.score)}</span>
                </h3>
                <ul>
                  {d.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
                {d.uncertainty && <p className="unc">⚠ {d.uncertainty}</p>}
              </div>
            ))}
          </div>
          {(ev.matched_skills?.length || ev.unmatched_core_skills?.length) ? (
            <p style={{ fontSize: 13.5, color: 'var(--muted)' }}>
              命中技能：{ev.matched_skills?.join('、') || '无'}；缺口：
              {ev.unmatched_core_skills?.join('、') || '无'}
            </p>
          ) : null}
        </div>
      ) : (
        activeId && (
          <div className="card cta-card">
            <b>还没有这个岗位的匹配结果</b>
            <p className="hint" style={{ margin: '4px 0 10px' }}>
              最常见原因：还没创建求职偏好（匹配需要它），或还没跑过匹配。去收件箱刷新一次即可。
            </p>
            <Link className="btn" to="/jobs">去收件箱跑匹配</Link>
          </div>
        )
      )}

      <div className="row">
        {app ? (
          <Link to="/applications" className="tag green" style={{ textDecoration: 'none' }}>
            ✓ 已在投递看板跟踪中 →
          </Link>
        ) : (
          <button className="btn primary" onClick={startApplication} disabled={!activeId}>
            收藏并开始跟踪
          </button>
        )}
        <button
          className="btn"
          onClick={() => feedback('interested')}
          disabled={myFeedback.includes('interested')}
        >
          {myFeedback.includes('interested') ? '✓ 已标记感兴趣' : '👍 感兴趣'}
        </button>
        <button
          className="btn"
          onClick={() => feedback('not_interested')}
          disabled={myFeedback.includes('not_interested')}
        >
          {myFeedback.includes('not_interested') ? '✓ 已标记不感兴趣' : '👎 不感兴趣'}
        </button>
      </div>

      <MaterialsWorkshop jobId={job.id} questions={questions} onQuestions={setQuestions} />

      <AtsReportPanel jobId={job.id} />

      <InterviewPracticePanel app={app} questions={questions} />

      <h2>岗位描述（原文）</h2>
      <div className="card" style={{ whiteSpace: 'pre-wrap' }}>
        {job.description || '（无描述——导入时未提供。可在原始页面查看后补充。）'}
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>
        首次发现 {job.published_at ?? '未知'}
        {job.deadline ? ` · 截止 ${job.deadline}` : ''}
        {m?.needs_review ? ' · ⚠ 数据缺失较多，建议人工复核' : ''}
      </p>
    </div>
  )
}
