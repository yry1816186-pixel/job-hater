import { createContext, useContext, useEffect, useState } from 'react'
import { Link, NavLink, HashRouter as Router, Routes, Route, useNavigate } from 'react-router-dom'
import { api } from './api'
import type { Profile } from './types'
import './styles.css'
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
                <Route path="/resume" element={<ResumePage />} />
                <Route path="/offers" element={<OffersPage />} />
                <Route path="/profile" element={<ProfilePage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </main>
          </div>
        </Router>
      </ToastProvider>
    </Ctx.Provider>
  )
}
