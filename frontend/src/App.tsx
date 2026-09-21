import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, HashRouter as Router, Routes, Route, useNavigate } from 'react-router-dom'
import { api, qs } from './api'
import type { Job, Profile } from './types'
import './styles.css'
import AnalyticsPage from './pages/AnalyticsPage'
import ApplicationsPage from './pages/ApplicationsPage'
import Home from './pages/Home'
import ImportPage from './pages/ImportPage'
import JobDetailPage from './pages/JobDetailPage'
import JobsPage from './pages/JobsPage'
import OffersPage from './pages/OffersPage'
import ProfilePage from './pages/ProfilePage'
import ResumePage from './pages/ResumePage'
import SettingsPage from './pages/SettingsPage'
import { ToastProvider } from './components/ui'

interface ProfileCtx {
  profiles: Profile[]
  activeId: string | null
  setActiveId: (id: string) => void
  refresh: () => Promise<void>
}

const Ctx = createContext<ProfileCtx>({
  profiles: [],
  activeId: null,
  setActiveId: () => {},
  refresh: async () => {},
})
export const useProfiles = () => useContext(Ctx)

const THEME_KEY = 'jobhater-theme'

const navCls = ({ isActive }: { isActive: boolean }) => `nav-link${isActive ? ' active' : ''}`

function NotFound() {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden>🧭</div>
      <h3>页面不存在</h3>
      <p>地址可能输错了。回到总览继续。</p>
      <div className="empty-action"><Link className="btn primary" to="/">回总览</Link></div>
    </div>
  )
}

const COMMAND_DESTS = [
  { to: '/', label: '总览 · 今日提醒与开始清单', hint: '页面' },
  { to: '/jobs', label: '岗位收件箱', hint: '页面' },
  { to: '/applications', label: '投递看板', hint: '页面' },
  { to: '/analytics', label: '求职分析 · 漏斗与洞察', hint: '页面' },
  { to: '/resume', label: '简历工坊', hint: '页面' },
  { to: '/offers', label: 'Offer 比较', hint: '页面' },
  { to: '/profile', label: '我的画像', hint: '页面' },
  { to: '/import', label: '导入岗位（粘贴 JD）', hint: '页面' },
  { to: '/settings', label: '设置与隐私 · 备份恢复', hint: '页面' },
]

/** 全局命令面板（Ctrl/⌘+K）：页面直达 + 岗位搜索跳转。 */
function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [jobs, setJobs] = useState<Job[]>([])
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 30)
    else setQ('')
  }, [open])

  useEffect(() => {
    if (!open || !q.trim()) {
      setJobs([])
      return
    }
    const t = setTimeout(() => {
      api
        .get<{ items: Job[] }>(`/jobs${qs({ q: q.trim(), limit: 6 })}`)
        .then((r) => setJobs(r.items))
        .catch(() => setJobs([]))
    }, 200) // 防抖：本地 API 也别每键一查
    return () => clearTimeout(t)
  }, [q, open])

  const dests = useMemo(
    () => COMMAND_DESTS.filter((d) => !q.trim() || d.label.toLowerCase().includes(q.trim().toLowerCase())),
    [q],
  )
  if (!open) return null
  return (
    <div className="cmdk-overlay" onClick={() => setOpen(false)}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="命令面板">
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder="搜索页面或岗位…（Esc 关闭）"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="cmdk-list">
          {dests.map((d) => (
            <button key={d.to} className="cmdk-item" onClick={() => { setOpen(false); navigate(d.to) }}>
              <span>{d.label}</span><small>{d.hint}</small>
            </button>
          ))}
          {jobs.length > 0 && <div className="cmdk-section">岗位</div>}
          {jobs.map((j) => (
            <button
              key={j.id}
              className="cmdk-item"
              onClick={() => { setOpen(false); navigate(`/jobs/${j.id}`) }}
            >
              <span>{j.title} · {j.employer_name}</span>
              <small>{j.city || ''}</small>
            </button>
          ))}
          {!dests.length && !jobs.length && (
            <div className="cmdk-empty">没有匹配的页面或岗位</div>
          )}
        </div>
      </div>
    </div>
  )
}

/** 画像切换器：多画像（如同时准备「技术岗」和「产品岗」）一目了然 */
function ProfileSwitcher() {
  const { profiles, activeId, setActiveId } = useProfiles()
  const navigate = useNavigate()
  if (!profiles.length) return null
  return (
    <select
      className="profile-switcher"
      value={activeId ?? ''}
      onChange={(e) => {
        if (e.target.value === '__new__') {
          navigate('/profile?new=1')
          return
        }
        setActiveId(e.target.value)
      }}
      aria-label="切换当前画像"
    >
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          画像：{p.display_name}
        </option>
      ))}
      <option value="__new__">＋ 新建画像…</option>
    </select>
  )
}

export default function App() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [theme, setTheme] = useState<'light' | 'dark'>(
    () => (localStorage.getItem(THEME_KEY) as 'light' | 'dark') || 'light',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  useEffect(() => {
    api.get<Profile[]>('/profiles')
      .then((ps) => {
        setProfiles(ps)
        const saved = localStorage.getItem('jobhater-active-profile')
        setActiveId(ps.some((p) => p.id === saved) ? saved : (ps[0]?.id ?? null))
      })
      .catch(() => setProfiles([]))
  }, [])

  const refresh = async () => {
    try {
      const ps = await api.get<Profile[]>('/profiles')
      setProfiles(ps)
      setActiveId((cur) => (ps.some((p) => p.id === cur) ? cur : (ps[0]?.id ?? null)))
    } catch {
      /* 网络错误保持现状；页面级请求会给出可见错误 */
    }
  }

  const choose = (id: string) => {
    setActiveId(id)
    localStorage.setItem('jobhater-active-profile', id)
  }

  return (
    <Ctx.Provider value={{ profiles, activeId, setActiveId: choose, refresh }}>
      <ToastProvider>
        <Router>
          <div className="app">
            <nav className="sidebar">
              <Link to="/" className="brand">
                Job Hater
                <small>本地求职管理 · 数据在你机器上</small>
              </Link>
              <ProfileSwitcher />
              <NavLink to="/" end className={navCls}>总览</NavLink>
              <div className="nav-group">准备 <small>（首次使用先做这两步）</small></div>
              <NavLink to="/profile" className={navCls}>我的画像</NavLink>
              <NavLink to="/import" className={navCls}>导入岗位</NavLink>
              <div className="nav-group">求职推进</div>
              <NavLink to="/jobs" className={navCls}>岗位收件箱</NavLink>
              <NavLink to="/applications" className={navCls}>投递看板</NavLink>
              <NavLink to="/analytics" className={navCls}>求职分析</NavLink>
              <NavLink to="/resume" className={navCls}>简历</NavLink>
              <NavLink to="/offers" className={navCls}>Offer 比较</NavLink>
              <div className="nav-group">系统</div>
              <NavLink to="/settings" className={navCls}>设置与隐私</NavLink>
              <div className="spacer" />
              <button
                className="theme-toggle"
                onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
                aria-label="切换深浅主题"
              >
                {theme === 'light' ? '◐ 深色' : '◑ 浅色'}
              </button>
            </nav>
            <main className="main">
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/jobs" element={<JobsPage />} />
                <Route path="/jobs/:jobId" element={<JobDetailPage />} />
                <Route path="/import" element={<ImportPage />} />
                <Route path="/applications" element={<ApplicationsPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/resume" element={<ResumePage />} />
                <Route path="/offers" element={<OffersPage />} />
                <Route path="/profile" element={<ProfilePage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </main>
          </div>
          <CommandPalette />
        </Router>
      </ToastProvider>
    </Ctx.Provider>
  )
}
