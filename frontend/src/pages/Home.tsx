import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Application, Job, JobSourceInfo, MatchOutcome } from '../types'
import { useProfiles } from '../App'

/** 总览：今天该做什么（基于真实数据，不造 KPI） */
export default function Home() {
  const { profiles, activeId } = useProfiles()
  const [jobs, setJobs] = useState<Job[]>([])
  const [apps, setApps] = useState<Application[]>([])
  const [sources, setSources] = useState<JobSourceInfo[]>([])
  const [topMatches, setTopMatches] = useState<MatchOutcome[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!activeId) return
    setErr('')
    Promise.all([
      api.get<{ items: Job[] }>(`/jobs${qs({ limit: 5, status: 'active' })}`),
      api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`),
      api.get<JobSourceInfo[]>('/sources'),
    ])
      .then(([j, a, s]) => {
        setJobs(j.items)
        setApps(a)
        setSources(s)
      })
      .catch((e) => setErr(String(e.message || e)))
  }, [activeId])

  const runMatch = () => {
    if (!activeId) return
    api
      .post<{ results: MatchOutcome[] }>('/match/run', { profile_id: activeId, limit: 100 })
      .then((r) => setTopMatches(r.results.filter((x) => x.eligible).slice(0, 5)))
      .catch((e) => setErr(e.message))
  }

  if (!profiles.length) {
    return (
      <div>
        <h1>欢迎使用 Job Hater</h1>
        <p className="page-sub">本地求职全流程管理：发现岗位 → 判断匹配 → 准备材料 → 跟踪投递 → 面试 → Offer。</p>
        <div className="empty">
          <p>还没有你的画像。先去「我的画像」建立档案（或导入简历事实），再设置求职方向。</p>
          <Link className="btn primary" to="/profile">
            开始建档
          </Link>
        </div>
      </div>
    )
  }

  const inProgress = apps.filter((a) =>
    ['preparing', 'materials_ready', 'ready_to_apply', 'applied_confirmed', 'assessment', 'interviewing'].includes(a.status),
  )

  return (
    <div>
      <h1>总览</h1>
      <p className="page-sub">岗位库 {jobs.length >= 5 ? '5+' : jobs.length} 条最近入库 · 投递进行中 {inProgress.length} 个 · 信源 {sources.length} 个</p>
      {err && <div className="error-box">{err}</div>}
      <div className="stat-row">
        <div className="stat">
          <b>{jobs.length >= 5 ? '5+' : jobs.length}</b>
          <span>最近入库岗位</span>
        </div>
        <div className="stat">
          <b>{inProgress.length}</b>
          <span>进行中的投递</span>
        </div>
        <div className="stat">
          <b>{sources.filter((s) => s.health_status === 'ok').length}/{sources.length || 0}</b>
          <span>健康信源</span>
        </div>
      </div>
      <div className="row">
        <button className="btn primary" onClick={runMatch}>
          为当前画像重新匹配排序
        </button>
        <Link className="btn" to="/import">
          导入 / 粘贴岗位
        </Link>
      </div>
      {topMatches.length > 0 && (
        <>
          <h2>匹配前列（可解释结果）</h2>
          {topMatches.map((m) => {
            const job = jobs.find((j) => j.id === m.job_id)
            return (
              <div className="card" key={m.job_id}>
                <div className="between">
                  <div>
                    <b>{job?.title ?? m.job_id}</b>
                    <div className="meta" style={{ color: 'var(--muted)', fontSize: 13 }}>
                      {job?.employer_name} · {job?.city ?? '城市未知'} · {job?.salary_text ?? '薪资未标注'}
                    </div>
                  </div>
                  <div className="score" style={{ fontSize: 22 }}>
                    {m.rank_score ?? '—'}
                    <span style={{ fontSize: 12, marginLeft: 6 }}>{m.verdict}</span>
                  </div>
                </div>
              </div>
            )
          })}
          <p>
            <Link to="/jobs">查看全部岗位与匹配解释 →</Link>
          </p>
        </>
      )}
      <h2>最近入库</h2>
      {jobs.length === 0 ? (
        <div className="empty">
          岗位库还是空的。去「导入岗位」粘贴一条 JD 试试，或用 wenke 等适配器批量拉取。
        </div>
      ) : (
        jobs.map((j) => (
          <Link className="job-item card" key={j.id} to={`/jobs/${j.id}`} style={{ display: 'block' }}>
            <div className="title">{j.title}</div>
            <div className="meta">
              {j.employer_name} · {j.city ?? '城市未知'} · {j.salary_text ?? '薪资未标注'}
            </div>
          </Link>
        ))
      )}
    </div>
  )
}
