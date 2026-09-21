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
        <Link className="btn" to={`/resume?job=${job.id}`}>
          去简历工坊准备材料 →
        </Link>
      </div>

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
