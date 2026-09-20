import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, qs } from '../api'
import type { Application, FeedbackKind, Job, MatchOutcome } from '../types'
import { useProfiles } from '../App'

const DIM_LABELS: Record<string, string> = {
  skill_match: '技能匹配',
  experience_relevance: '经历相关',
  location_fit: '地点契合',
  salary_fit: '薪资契合',
  recency: '信息时效',
  feedback: '历史反馈',
}

/** 岗位详情：结论 + 依据 + 不确定性，透明到每一维 */
export default function JobDetailPage() {
  const { jobId = '' } = useParams()
  const { activeId } = useProfiles()
  const nav = useNavigate()
  const [job, setJob] = useState<(Job & { match?: MatchOutcome | null }) | null>(null)
  const [app, setApp] = useState<Application | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = () => {
    api
      .get<Job & { match?: MatchOutcome | null }>(`/jobs/${jobId}${qs({ profile_id: activeId ?? '' })}`)
      .then((j) => setJob(j))
      .catch((e) => setErr(e.message))
    if (activeId) {
      api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`).then((apps) => {
        setApp(apps.find((a) => a.job_id === jobId) ?? null)
      })
    }
  }
  useEffect(load, [jobId, activeId])

  const startApplication = async () => {
    if (!activeId || !job) return
    try {
      const r = await api.post<{ id: string }>('/applications', {
        job_id: job.id,
        profile_id: activeId,
        status: 'saved',
      })
      setMsg(`已建立投递跟踪（${r.id.slice(0, 10)}…），去投递看板推进`)
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const feedback = async (kind: FeedbackKind) => {
    if (!activeId) return
    await api.post('/feedback', { profile_id: activeId, kind, job_id: jobId })
    setMsg('反馈已记录（会影响后续排序，可在画像页查看与重置）')
    load()
  }

  if (err && !job) return <div className="error-box">{err}</div>
  if (!job) return <div className="loading">加载中…</div>

  const m = job.match
  const ev = (m?.evidence ?? {}) as {
    matched_skills?: string[]
    unmatched_core_skills?: string[]
  }

  return (
    <div>
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
      {msg && <div className="card" style={{ borderColor: 'var(--accent)' }}>{msg}</div>}
      {err && <div className="error-box">{err}</div>}

      {m && (
        <div className="card">
          <div className="between">
            <h2 style={{ margin: 0 }}>匹配结论</h2>
            <div className="score" style={{ fontSize: 26 }}>
              {m.rank_score ?? '—'} <span style={{ fontSize: 13 }}>{m.verdict}</span>
            </div>
          </div>
          {!m.eligible && (
            <div className="error-box">
              未通过硬性条件（来自你的偏好设置，可调整）：
              <ul style={{ margin: '6px 0 0' }}>
                {m.gate_reasons.filter((g) => !g.passed).map((g) => (
                  <li key={g.code}>
                    {g.code}：{g.detail}
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
      )}

      <div className="row">
        {app ? (
          <span className="tag green">已在投递跟踪中</span>
        ) : (
          <button className="btn primary" onClick={startApplication} disabled={!activeId}>
            收藏并开始跟踪
          </button>
        )}
        <button className="btn" onClick={() => feedback('interested')}>
          👍 感兴趣
        </button>
        <button className="btn" onClick={() => feedback('not_interested')}>
          👎 不感兴趣
        </button>
        <button className="btn" onClick={() => nav('/resume')}>
          准备岗位版简历 →
        </button>
      </div>

      <h2>岗位描述（原文）</h2>
      <div className="card" style={{ whiteSpace: 'pre-wrap' }}>
        {job.description || '（无描述——导入时未提供。可在原始页面查看后补充。）'}
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>
        首次发现 {job.published_at ?? '未知'} · 来源 {job.source_id}
        {job.deadline ? ` · 截止 ${job.deadline}` : ''}
        {m?.needs_review ? ' · ⚠ 数据缺失较多，建议人工复核' : ''}
      </p>
    </div>
  )
}
