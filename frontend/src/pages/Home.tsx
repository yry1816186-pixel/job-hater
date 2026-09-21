import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, qs } from '../api'
import type {
  Application, Job, JobSourceInfo, MatchOutcome, Preset, ProfileView,
  Reminder, ReminderSuggestion,
} from '../types'
import { useProfiles } from '../App'
import { useToast } from '../components/ui'

function dueLabel(due: string): { text: string; overdue: boolean; today: boolean } {
  const d = due.slice(0, 10)
  const today = new Date().toISOString().slice(0, 10)
  return { text: d === today ? '今天' : d, overdue: d < today, today: d === today }
}

/** 今日驾驶舱：提醒 + 确定性跟进建议（建议不落库，采纳后才成为提醒）。 */
function ReminderPanel({ profileId }: { profileId: string | null }) {
  const { toast } = useToast()
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [suggestions, setSuggestions] = useState<ReminderSuggestion[]>([])
  const [busy, setBusy] = useState(false)

  const load = () => {
    const suffix = profileId ? qs({ profile_id: profileId }) : ''
    Promise.all([
      api.get<Reminder[]>(`/reminders${suffix ? `?${suffix}` : ''}`),
      api.get<ReminderSuggestion[]>(`/reminders/suggestions${suffix ? `?${suffix}` : ''}`),
    ])
      .then(([rs, sgs]) => {
        setReminders(rs)
        setSuggestions(sgs)
      })
      .catch(() => {
        /* 面板失败不砸整页 */
      })
  }
  useEffect(load, [profileId])

  const toggle = async (r: Reminder) => {
    try {
      await api.patch<Reminder>(`/reminders/${r.id}`, { done: !r.done })
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    }
  }
  const adopt = async (s: ReminderSuggestion) => {
    setBusy(true)
    try {
      await api.post('/reminders', {
        owner_kind: s.owner_kind, owner_id: s.owner_id,
        due_at: s.due_at, title: s.title, kind: s.kind,
      })
      toast('success', '已加入提醒')
      load()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const open = reminders.filter((r) => !r.done)
  if (!open.length && !suggestions.length) return null
  return (
    <div className="card" style={{ padding: 18, marginBottom: 18 }}>
      <h2 style={{ marginTop: 0, fontSize: 17 }}>今日待办（{open.length} 条提醒 · {suggestions.length} 条建议）</h2>
      {open.map((r) => {
        const d = dueLabel(r.due_at)
        return (
          <div key={r.id} className={`reminder-item${d.overdue ? ' overdue' : ''}`}>
            <span className="due">{d.overdue ? `⚠ ${d.text}` : d.text}</span>
            <span className="title" style={{ flex: 1 }}>
              {r.title}
              {r.owner_title && (
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>（{r.owner_employer} · {r.owner_title}）</span>
              )}
            </span>
            <button className="btn" onClick={() => toggle(r)}>完成</button>
          </div>
        )
      })}
      {suggestions.map((s) => (
        <div key={`${s.owner_kind}:${s.owner_id}:${s.kind}`} className="suggestion-item">
          <div>{s.title}</div>
          <div className="why">{s.reason} · 系统建议，不自动创建</div>
          <button className="btn" disabled={busy} onClick={() => adopt(s)}>采纳为提醒</button>
        </div>
      ))}
      {reminders.some((r) => r.done) && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: 'pointer', fontSize: 13, color: 'var(--muted)' }}>
            已完成（{reminders.filter((r) => r.done).length}）
          </summary>
          {reminders.filter((r) => r.done).map((r) => (
            <div key={r.id} className="reminder-item done">
              <span className="due">{r.due_at.slice(0, 10)}</span>
              <span className="title" style={{ flex: 1 }}>{r.title}</span>
              <button className="btn" onClick={() => toggle(r)}>撤销</button>
            </div>
          ))}
        </details>
      )}
    </div>
  )
}

/** 总览 = 新手引导清单 + 日常驾驶舱。全部基于真实数据，不造 KPI。 */
export default function Home() {
  const { profiles, activeId } = useProfiles()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobsTotal, setJobsTotal] = useState(0)
  const [jobLookup, setJobLookup] = useState<Record<string, Job>>({})
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
  // 完成口径与下方清单一致：五项全绿才撤下引导（技能/经历是匹配质量的根基，不算可选项）
  const setupDone = Object.values(setup).every(Boolean)
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
      api.get<{ total: number; items: Job[] }>(`/jobs${qs({ limit: 200, status: 'active' })}`),
      api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`),
      api.get<JobSourceInfo[]>('/sources'),
      api.get<ProfileView>(`/profiles/${activeId}`),
      api.get<Preset[]>(`/profiles/${activeId}/presets`),
    ])
      .then(([j, all, a, s, p, ps]) => {
        setJobs(j.items)
        setJobsTotal(j.total)
        setJobLookup(Object.fromEntries(all.items.map((x) => [x.id, x])))
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
    const seedDemo = async () => {
      try {
        const r = await api.post<{ added: number; deduped: number }>('/demo/seed')
        toast('success', `已导入 ${r.added} 条示例岗位——先随便看看，建档后会有匹配分`)
        navigate('/jobs')
      } catch (e) {
        toast('error', (e as Error).message)
      }
    }
    return (
      <div>
        <h1>欢迎使用 Job Hater</h1>
        <p className="page-sub">
          本地求职全流程管理：发现岗位 → 判断匹配 → 准备材料 → 跟踪投递 → 面试 → Offer。
          全部数据只保存在你这台电脑上。
        </p>
        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ marginTop: 0 }}>从一份现成的简历开始（3 分钟）</h2>
          <p style={{ margin: '4px 0 14px', fontSize: 14.5 }}>
            上传你的简历文件（PDF / DOCX / TXT / MD / JSON Resume），系统自动解析出
            学历、经历、技能——你核对一遍就完成建档；没有文件也可以粘贴文本或手动填写。
          </p>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <Link className="btn primary" to="/welcome">上传简历，开始建档 →</Link>
            <Link className="btn" to="/profile">手动建档</Link>
            <button className="btn" onClick={seedDemo}>先看看示例（导入 8 条演示岗位）</button>
          </div>
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

      <ReminderPanel profileId={activeId} />

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
        <Link className="btn" to="/analytics">求职分析（漏斗/渠道/薪资）→</Link>
      </div>

      {topMatches.length > 0 && (
        <>
          <h2>匹配前列（点开看依据）</h2>
          {topMatches.map((m) => {
            const job = jobLookup[m.job_id]
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
