import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Job, MatchOutcome } from '../types'
import { useProfiles } from '../App'

const RECRUIT_LABELS: Record<string, string> = {
  campus: '校招', social: '社招', internship: '实习', unknown: '批次未知',
}

/** 岗位收件箱：检索 + 筛选 + 匹配分列 */
export default function JobsPage() {
  const { activeId } = useProfiles()
  const [q, setQ] = useState('')
  const [city, setCity] = useState('')
  const [recruit, setRecruit] = useState('')
  const [items, setItems] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [matches, setMatches] = useState<Record<string, MatchOutcome>>({})
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    api
      .get<{ total: number; items: Job[] }>(`/jobs${qs({ q, city, recruitment_type: recruit, limit: 50 })}`)
      .then(async (r) => {
        setItems(r.items)
        setTotal(r.total)
        setMatches({})
        if (activeId && r.items.length) {
          const run = await api.post<{ results: MatchOutcome[] }>('/match/run', {
            profile_id: activeId,
            limit: 50,
            statuses: ['active'],
          })
          const map: Record<string, MatchOutcome> = {}
          for (const m of run.results) map[m.job_id] = m
          setMatches(map)
        }
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false))
  }, [q, city, recruit, activeId])

  useEffect(() => {
    const t = setTimeout(load, 250) // 搜索防抖
    return () => clearTimeout(t)
  }, [load])

  return (
    <div>
      <h1>岗位收件箱</h1>
      <p className="page-sub">
        共 {total} 条（active/expired）· 中文全文检索 · 匹配分与依据来自你的画像和偏好
      </p>
      <div className="row" style={{ marginBottom: 14 }}>
        <input
          type="text"
          placeholder="搜索岗位：关键词、公司、技能…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: 1, minWidth: 220 }}
          aria-label="搜索岗位"
        />
        <input
          type="text"
          placeholder="城市"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          style={{ width: 110 }}
          aria-label="城市筛选"
        />
        <select value={recruit} onChange={(e) => setRecruit(e.target.value)} style={{ width: 120 }} aria-label="批次筛选">
          <option value="">全部批次</option>
          <option value="campus">校招</option>
          <option value="social">社招</option>
          <option value="internship">实习</option>
        </select>
      </div>
      {err && <div className="error-box">{err}</div>}
      {loading ? (
        <div className="loading">加载中…</div>
      ) : items.length === 0 ? (
        <div className="empty">
          没有匹配条件的岗位。可以放宽筛选，或去「导入岗位」添加新岗位。
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          {items.map((j) => {
            const m = matches[j.id]
            return (
              <Link className="job-item" key={j.id} to={`/jobs/${j.id}`}>
                <div className="between">
                  <div>
                    <span className="title">{j.title}</span>{' '}
                    <span className="tag">{RECRUIT_LABELS[j.recruitment_type] ?? j.recruitment_type}</span>{' '}
                    {j.status === 'expired' && <span className="tag amber">已过期</span>}
                    {(j.extras as { near_dup_of?: string }).near_dup_of && (
                      <span className="tag amber">疑似重复</span>
                    )}
                    <div className="meta">
                      {j.employer_name} · {j.city ?? '城市未知'} ·{' '}
                      {j.salary_text ?? '薪资未标注'}
                      {j.experience_required_text ? ` · ${j.experience_required_text}` : ''}
                    </div>
                  </div>
                  {m && (
                    <div style={{ textAlign: 'right' }}>
                      <div className="score">{m.rank_score ?? '—'}</div>
                      <div style={{ fontSize: 12, color: m.eligible ? 'var(--accent)' : 'var(--danger)' }}>
                        {m.eligible ? m.verdict : '不符合硬性条件'}
                      </div>
                    </div>
                  )}
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
