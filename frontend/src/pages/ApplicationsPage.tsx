import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import type { Application, ApplicationEvent, Job } from '../types'
import { APPLICATION_STATUS_LABELS, BOARD_COLUMNS } from '../types'
import { useProfiles } from '../App'

const NEXT_ACTIONS: Record<string, string[]> = {
  saved: ['shortlisted', 'preparing', 'withdrawn'],
  shortlisted: ['preparing', 'withdrawn'],
  preparing: ['materials_ready', 'withdrawn'],
  materials_ready: ['ready_to_apply', 'preparing'],
  ready_to_apply: [], // applied_confirmed 走独立确认按钮（诚实语义）
  applied_confirmed: ['assessment', 'interviewing', 'rejected', 'withdrawn'],
  assessment: ['interviewing', 'rejected'],
  interviewing: ['offer', 'rejected'],
  offer: [],
  rejected: [],
  withdrawn: [],
  discovered: ['saved'],
}

/** 投递看板：状态机驱动，确认投递是独立动作 */
export default function ApplicationsPage() {
  const { activeId } = useProfiles()
  const [apps, setApps] = useState<Application[]>([])
  const [jobs, setJobs] = useState<Record<string, Job>>({})
  const [err, setErr] = useState('')
  const [detail, setDetail] = useState<{ app: Application; events: ApplicationEvent[] } | null>(null)

  const load = () => {
    if (!activeId) return
    api.get<Application[]>(`/applications${qs({ profile_id: activeId })}`).then(async (as) => {
      setApps(as)
      const need = [...new Set(as.map((a) => a.job_id))]
      const map: Record<string, Job> = {}
      await Promise.all(
        need.map((id) =>
          api.get<Job>(`/jobs/${id}`).then((j) => {
            map[id] = j
          }),
        ),
      )
      setJobs(map)
    })
  }
  useEffect(load, [activeId])

  const transition = async (id: string, status: string) => {
    setErr('')
    try {
      await api.post(`/applications/${id}/transition`, { status })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const confirmApplied = async (id: string, channel: string) => {
    setErr('')
    try {
      await api.post(`/applications/${id}/confirm-applied`, { channel: channel || null })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const openDetail = async (app: Application) => {
    const events = await api.get<ApplicationEvent[]>(`/applications/${app.id}/events`)
    setDetail({ app, events })
  }

  const addOffer = async (app: Application) => {
    const base = window.prompt('月基本薪资（K，数字）', '20')
    if (!base) return
    try {
      await api.post('/offers', {
        application_id: app.id,
        base_salary_k: Number(base),
        salary_months: Number(window.prompt('年薪月数（如 15，可留空）', '14') || 0) || null,
      })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  if (!apps.length) {
    return (
      <div>
        <h1>投递看板</h1>
        <div className="empty">
          还没有投递跟踪。在岗位详情页点「收藏并开始跟踪」，你的求职管线就在这里生长。
          <br />
          <b>投递永远由你亲手完成</b>——本系统不代替你向平台发送任何东西。
        </div>
      </div>
    )
  }

  return (
    <div>
      <h1>投递看板</h1>
      <p className="page-sub">{apps.length} 条跟踪 · 状态按真实生命周期推进，「已投递」仅在你确认后生效</p>
      {err && <div className="error-box">{err}</div>}
      <div className="board">
        {BOARD_COLUMNS.map((col) => {
          const colApps = apps.filter((a) => a.status === col)
          if (!colApps.length) return null
          return (
            <div className="board-col" key={col}>
              <h3>
                {APPLICATION_STATUS_LABELS[col]}（{colApps.length}）
              </h3>
              {colApps.map((a) => {
                const j = jobs[a.job_id]
                return (
                  <div className="board-card" key={a.id}>
                    <b>{j?.title ?? a.job_id.slice(0, 12)}</b>
                    {j?.employer_name}
                    <div className="row" style={{ marginTop: 6, gap: 4 }}>
                      {(NEXT_ACTIONS[a.status] ?? []).map((s) => (
                        <button key={s} className="btn small" onClick={() => transition(a.id, s)}>
                          → {APPLICATION_STATUS_LABELS[s]}
                        </button>
                      ))}
                      {a.status === 'ready_to_apply' && (
                        <button
                          className="btn small primary"
                          onClick={() => confirmApplied(a.id, window.prompt('投递渠道（如 官网/BOSS/内推）', '官网') ?? '')}
                        >
                          ✅ 我已投递
                        </button>
                      )}
                      {a.status === 'interviewing' && (
                        <button className="btn small primary" onClick={() => addOffer(a)}>
                          + Offer
                        </button>
                      )}
                      <button className="btn small" onClick={() => openDetail(a)}>
                        事件
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
      {detail && (
        <div className="card">
          <div className="between">
            <h2 style={{ margin: 0 }}>事件审计（不可变）</h2>
            <button className="btn small" onClick={() => setDetail(null)}>
              关闭
            </button>
          </div>
          <table className="data">
            <thead>
              <tr>
                <th>时间</th>
                <th>事件</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {detail.events.map((e) => (
                <tr key={e.id}>
                  <td>{e.occurred_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td>{e.kind}</td>
                  <td>{e.note || e.payload_json?.slice(0, 120) || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
