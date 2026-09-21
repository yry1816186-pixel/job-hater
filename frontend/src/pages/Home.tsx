import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Application, Job, JobSourceInfo, MatchOutcome, Preset, ProfileView } from '../types'
import { useProfiles } from '../App'
import { useToast } from '../components/ui'

/** 总览 = 新手引导清单 + 日常驾驶舱。全部基于真实数据，不造 KPI。 */
export default function Home() {
  const { profiles, activeId } = useProfiles()
  const { toast } = useToast()
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobsTotal, setJobsTotal] = useState(0)
  const [apps, setApps] = useState<Application[]>([])
  const [sources, setSources] = useState<JobSourceInfo[]>([])
  const [topMatches, setTopMatches] = useState<MatchOutcome[]>([])
  const [view, setView] = useState<ProfileView | null>(null)
  const [presets, setPresets] = useState<Preset[]>([])
  const [matching, setMatching] = useState(false)
  const [err, setErr] = useState('')

  const setup = {
    hasProfile: !!view?.profile,
    hasSkills: (view?.skills?.length ?? 0) > 0,
    hasEvidence: (view?.experiences?.length ?? 0) > 0 || (view?.projects?.length ?? 0) > 0,
    hasPreset: presets.length > 0,
    hasJobs: jobsTotal > 0,
  }
  const setupDone = setup.hasProfile && setup.hasPreset && setup.hasJobs
  const setupSteps = [
    { done: setup.hasProfile, label: '建立画像（姓名+一句话介绍）', to: '/profile' },
    { done: setup.hasSkills, label: '添加技能（匹配引擎识别 JD 的依据）', to: '/profile' },
    { done: setup.hasEvidence, label: '补经历/项目（简历素材从这里长出来）', to: '/profile' },
    { done: setup.hasPreset, label: '设置求职偏好（目标城市/角色/薪资底线）', to: '/profile' },
    { done: setup.hasJobs, label: '导入第一条岗位（粘贴任意 JD 原文）', to: '/import' },
  ]
  const nextStep = setupSteps.find((s) => !s.done)

  useEffect(() => {
    if (!activeId) return
    setErr('')
    Promise.all([
      api.get<{ total: number; items: Job[] }>(`/jobs${qs({ limit: 5, status: 'active' })}`),
      api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`),
      api.get<JobSourceInfo[]>('/sources'),
      api.get<ProfileView>(`/profiles/${activeId}`),
      api.get<Preset[]>(`/profiles/${activeId}/presets`),
    ])
      .then(([j, a, s, p, ps]) => {
        setJobs(j.items)
        setJobsTotal(j.total)
        setApps(a)
        setSources(s)
        setView(p)
        setPresets(ps)
      })
      .catch((e) => setErr(String(e.message || e)))
  }, [activeId])

  const runMatch = async () => {
    if (!activeId || matching) return
    if (!setup.hasPreset) {
      toast('info', '先在「我的画像」创建求职偏好，匹配才能运行')
      return
    }
    setMatching(true)
    try {
      const r = await api.post<{ results: MatchOutcome[] }>('/match/run', {
        profile_id: activeId,
        limit: 100,
      })
      const eligible = r.results.filter((x) => x.eligible)
      setTopMatches(eligible.slice(0, 5))
      toast('success', `匹配完成：${r.results.length} 条岗位中 ${eligible.length} 条符合你的硬性偏好`)
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setMatching(false)
    }
  }

  // ---------- 新用户（无画像） ----------
  if (!profiles.length) {
    return (
      <div>
        <h1>欢迎使用 Job Hater</h1>
        <p className="page-sub">
          本地求职全流程管理：发现岗位 → 判断匹配 → 准备材料 → 跟踪投递 → 面试 → Offer。
          全部数据只保存在你这台电脑上。
        </p>
        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ marginTop: 0 }}>三步开始</h2>
          <ol className="setup-list">
            <li><b>建档</b>：姓名、学历、技能——匹配和简历都从这里长出来（2 分钟）</li>
            <li><b>定方向</b>：目标城市 / 角色类型 / 薪资底线，随时可改</li>
            <li><b>导岗位</b>：在任何招聘网站看到心仪岗位，复制 JD 粘贴进来</li>
          </ol>
          <Link className="btn primary" to="/profile">开始建档 →</Link>
          <p className="hint" style={{ marginTop: 12 }}>
            本系统不会代替你投递，不会上传你的数据，AI 功能默认关闭。
          </p>
        </div>
      </div>
    )
  }

  const inProgress = apps.filter((a) =>
    ['preparing', 'materials_ready', 'ready_to_apply', 'applied_confirmed', 'assessment', 'interviewing'].includes(a.status),
  )
  const interviewing = apps.filter((a) => a.status === 'interviewing').length
  const pendingConfirm = apps.filter((a) => a.status === 'ready_to_apply').length

  return (
    <div>
      <h1>总览</h1>
      <p className="page-sub">
        {view?.profile ? `当前画像：${view.profile.display_name}` : ''}
        {view?.profile.headline ? ` · ${view.profile.headline}` : ''}
      </p>
      {err && <div className="error-box">加载失败：{err}（服务器在本地 8787 端口，确认已启动）</div>}

      {/* 引导清单：未完成时置顶 */}
      {!setupDone && (
        <div className="card" style={{ padding: 20, marginBottom: 20 }}>
          <h2 style={{ marginTop: 0, fontSize: 17 }}>开始之前（剩 {setupSteps.filter((s) => !s.done).length} 步）</h2>
          <ul className="setup-list">
            {setupSteps.map((s) => (
              <li key={s.label} className={s.done ? 'done' : ''}>
                {s.done ? '✓' : '○'} {s.label}
                {!s.done && nextStep?.label === s.label && (
                  <Link className="btn primary" to={s.to} style={{ marginLeft: 10 }}>去完成 →</Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="stat-row">
        <div className="stat">
          <b>{jobsTotal}</b>
          <span>岗位库总量</span>
        </div>
        <div className="stat">
          <b>{inProgress.length}</b>
          <span>进行中的投递</span>
        </div>
        <div className="stat">
          <b>{interviewing}</b>
          <span>面试中</span>
        </div>
        <div className="stat">
          <b>{pendingConfirm}</b>
          <span>待投递（材料就绪）</span>
        </div>
        <div className="stat">
          <b>{sources.filter((s) => s.health_status === 'ok').length}/{sources.length || 0}</b>
          <span>健康信源</span>
        </div>
      </div>

      <div className="row">
        <button className="btn primary" onClick={runMatch} disabled={matching}>
          {matching ? '正在匹配…' : '重新匹配排序'}
        </button>
        <Link className="btn" to="/import">导入 / 粘贴岗位</Link>
        <Link className="btn" to="/jobs">进收件箱看匹配 →</Link>
      </div>

      {topMatches.length > 0 && (
        <>
          <h2>匹配前列（点开看依据）</h2>
          {topMatches.map((m) => {
            const job = jobs.find((j) => j.id === m.job_id)
            return (
              <Link className="job-item card" key={m.job_id} to={`/jobs/${m.job_id}`} style={{ display: 'block' }}>
                <div className="between">
                  <div>
                    <b>{job?.title ?? '岗位已删除'}</b>
                    <div className="meta" style={{ color: 'var(--muted)', fontSize: 13 }}>
                      {job ? `${job.employer_name} · ${job.city ?? '城市未知'} · ${job.salary_text ?? '薪资未标注'}` : ''}
                    </div>
                  </div>
                  <div className="score" style={{ fontSize: 22 }}>
                    {m.rank_score ?? '—'}
                    <span style={{ fontSize: 12, marginLeft: 6 }}>{m.verdict}</span>
                  </div>
                </div>
              </Link>
            )
          })}
        </>
      )}

      <h2>最近入库</h2>
      {jobs.length === 0 ? (
        <div className="empty">
          岗位库还是空的。去「导入岗位」粘贴一条 JD 原文试试——这是永不失败的入口。
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
