import { createContext, useContext, useEffect, useState } from 'react'
import { Link, NavLink, Route, HashRouter as Router, Routes } from 'react-router-dom'
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
    api.get<Profile[]>('/profiles').then((ps) => {
      setProfiles(ps)
      const saved = localStorage.getItem('jobhater-active-profile')
      setActiveId(ps.some((p) => p.id === saved) ? saved : (ps[0]?.id ?? null))
    }).catch(() => setProfiles([]))
  }, [])

  const refresh = async () => {
    const ps = await api.get<Profile[]>('/profiles')
    setProfiles(ps)
    setActiveId((cur) => (ps.some((p) => p.id === cur) ? cur : (ps[0]?.id ?? null)))
  }

  const choose = (id: string) => {
    setActiveId(id)
    localStorage.setItem('jobhater-active-profile', id)
  }

  return (
    <Ctx.Provider value={{ profiles, activeId, setActiveId: choose, refresh }}>
      <Router>
        <div className="app">
          <nav className="sidebar">
            <Link to="/" className="brand">
              Job Hater
              <small>本地求职管理 · 数据在你机器上</small>
            </Link>
            <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              总览
            </NavLink>
            <NavLink to="/jobs" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              岗位收件箱
            </NavLink>
            <NavLink to="/import" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              导入岗位
            </NavLink>
            <NavLink to="/applications" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              投递看板
            </NavLink>
            <NavLink to="/resume" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              简历
            </NavLink>
            <NavLink to="/offers" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              Offer 比较
            </NavLink>
            <NavLink to="/profile" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              我的画像
            </NavLink>
            <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              设置与隐私
            </NavLink>
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
            </Routes>
          </main>
        </div>
      </Router>
    </Ctx.Provider>
  )
}
