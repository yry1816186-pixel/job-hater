import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, qs } from '../api'
import type { Application, FeedbackKind, Job, MatchOutcome } from '../types'
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

/** 材料工坊：确定性生成（本地零AI依赖）——定制简历/求职信/打招呼/题库/提升计划 */
function MaterialsWorkshop({ jobId }: { jobId: string }) {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [busy, setBusy] = useState('')
  const [greeting, setGreeting] = useState<{ greeting: string; length: number } | null>(null)
  const [coverLetter, setCoverLetter] = useState<{ content_md: string } | null>(null)
  const [questions, setQuestions] = useState<InterviewQuestions | null>(null)
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
      setQuestions(r)
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

/** 岗位详情：结论 + 依据 + 不确定性，透明到每一维；无匹配时解释为什么 */
export default function JobDetailPage() {
  const { jobId = '' } = useParams()
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [job, setJob] = useState<(Job & { match?: MatchOutcome | null }) | null>(null)
  const [app, setApp] = useState<Application | null>(null)
  const [myFeedback, setMyFeedback] = useState<FeedbackKind[]>([])
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

      <MaterialsWorkshop jobId={job.id} />

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
