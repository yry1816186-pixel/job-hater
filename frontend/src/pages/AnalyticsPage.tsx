// 求职分析：漏斗 / 周活动 / 来源 / 公司 / 薪资。
// 诚实语义：全部是本地库的直接计数与分位数，不是预测；样本不足就明说。
import { useEffect, useState } from 'react'
import { api, qs } from '../api'
import type { SalaryInsights, StatsOverview } from '../types'
import { useProfiles } from '../App'
import { useToast } from '../components/ui'

/** 样本 < 5 不构成"行情"，只配 grey + 徽章 */
const MIN_MEANINGFUL_SAMPLE = 5

/** 后端分位数带 1 位小数，展示统一取整 */
function fmtK(v: number): string {
  return String(Math.round(v))
}

/** "2026-W38" → "W38"（图表/悬浮提示用短标签） */
function shortWeek(week: string): string {
  return week.replace(/^\d{4}-/, '')
}

const HEALTH_CARDS = [
  { key: 'stale_applications', label: '停滞投递（>7天无动静）' },
  { key: 'upcoming_interviews_7d', label: '7天内面试' },
  { key: 'pending_offers', label: '待决策 Offer' },
  { key: 'deadlines_7d', label: '7天内截止岗位' },
] as const

function funnelStages(f: StatsOverview['funnel']): Array<{ label: string; count: number; rate: number | null }> {
  return [
    { label: '发现', count: f.discovered, rate: null },
    { label: '已投', count: f.applied, rate: f.applied_rate ?? null },
    { label: '面试', count: f.interviewed, rate: f.interview_rate ?? null },
    { label: 'Offer', count: f.offered, rate: f.offer_rate ?? null },
  ]
}

export default function AnalyticsPage() {
  const { activeId } = useProfiles()
  const { toast } = useToast()
  const [overview, setOverview] = useState<StatsOverview | null>(null)
  const [salary, setSalary] = useState<SalaryInsights | null>(null)
  const [cityInput, setCityInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [salaryErr, setSalaryErr] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr('')
    api
      .get<StatsOverview>(`/stats/overview${qs({ profile_id: activeId ?? undefined })}`)
      .then((o) => {
        if (cancelled) return
        setOverview(o)
        setLoading(false)
      })
      .catch((e) => {
        if (cancelled) return
        const msg = (e as Error).message
        setErr(msg)
        setLoading(false)
        toast('error', `统计加载失败：${msg}`)
      })
    return () => {
      cancelled = true
    }
  }, [activeId, toast])

  useEffect(() => {
    let cancelled = false
    setSalaryErr('')
    api
      .get<SalaryInsights>(`/stats/salary${qs({ city: cityInput })}`)
      .then((s) => {
        if (cancelled) return
        setSalary(s)
      })
      .catch((e) => {
        if (cancelled) return
        const msg = (e as Error).message
        setSalaryErr(msg)
        toast('error', `薪资数据加载失败：${msg}`)
      })
    return () => {
      cancelled = true
    }
  }, [cityInput, toast])

  const health = overview?.health
  const funnel = overview?.funnel
  const weeks = overview ? overview.weekly.slice(-12) : []
  const weeklyMax = Math.max(0, ...weeks.map((w) => Math.max(w.applications, w.interviews)))
  const sources = overview?.sources ?? []
  const topEmployers = overview
    ? [...overview.top_employers].sort((a, b) => b.n - a.n).slice(0, 5)
    : []

  return (
    <div>
      <h1>求职分析</h1>
      <p className="page-sub">全部数字来自本地库的直接计数，不是预测。</p>
      {err && <div className="error-box">加载失败：{err}（服务器在本地 8787 端口，确认已启动）</div>}

      {loading && !overview && <div className="empty">正在统计…</div>}

      {overview && health && funnel && (
        <>
          {/* 健康度：需要你注意的四件事，0 就安静地显示 0 */}
          <div className="analytics-grid">
            {HEALTH_CARDS.map((c) => {
              const v = health[c.key]
              return (
                <div className="stat-card" key={c.key}>
                  <div className="stat-num" style={v === 0 ? { color: 'var(--muted)' } : undefined}>
                    {v}
                  </div>
                  <div className="stat-label">{c.label}</div>
                </div>
              )
            })}
          </div>

          {/* 漏斗：宽度以「发现」为 100% 基准 */}
          <h2>漏斗：发现 → 已投 → 面试 → Offer</h2>
          <div className="funnel">
            {funnelStages(funnel).map((st, i) => (
              <div className="funnel-stage" key={st.label}>
                <div>
                  {st.label}
                  {i > 0 && (
                    <div style={{ color: 'var(--muted)', fontSize: 11 }}>
                      转化 {st.rate === null ? '—' : `${st.rate}%`}
                    </div>
                  )}
                </div>
                <div>
                  <div
                    className="funnel-bar"
                    style={{
                      width: funnel.discovered > 0 ? `${(st.count / funnel.discovered) * 100}%` : '0%',
                    }}
                  />
                </div>
                <div>{st.count}</div>
              </div>
            ))}
          </div>
          <p className="hint" style={{ marginTop: 6 }}>
            「已投」按你亲自确认的投递事实（applied_at）统计。
          </p>

          {/* 近 12 周：每周两根柱，投递 / 面试 */}
          <h2>近 12 周活动</h2>
          {weeks.length === 0 || weeklyMax === 0 ? (
            <div className="empty">最近 12 周没有投递或面试记录。</div>
          ) : (
            <>
              <div className="bar-chart">
                {weeks.map((w) => {
                  const tip = `${shortWeek(w.week)}: 投${w.applications} 面${w.interviews}`
                  return (
                    <div
                      key={w.week}
                      style={{ flex: 1, display: 'flex', alignItems: 'flex-end', gap: 2, height: '100%' }}
                    >
                      <div
                        className="bar"
                        data-tip={tip}
                        style={{
                          height: `${(w.applications / weeklyMax) * 100}%`,
                          background: 'var(--accent)',
                        }}
                      />
                      <div
                        className="bar"
                        data-tip={tip}
                        style={{
                          height: `${(w.interviews / weeklyMax) * 100}%`,
                          background: 'var(--warn)',
                        }}
                      />
                    </div>
                  )
                })}
              </div>
              <div className="chart-legend">
                <span>
                  <span
                    style={{
                      display: 'inline-block',
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      background: 'var(--accent)',
                      marginRight: 5,
                    }}
                  />
                  投递
                </span>
                <span>
                  <span
                    style={{
                      display: 'inline-block',
                      width: 10,
                      height: 10,
                      borderRadius: 2,
                      background: 'var(--warn)',
                      marginRight: 5,
                    }}
                  />
                  面试
                </span>
              </div>
            </>
          )}

          {/* 来源效果：全部来源都没有岗位时整节隐藏 */}
          {sources.some((s) => s.jobs > 0) && (
            <>
              <h2>来源效果</h2>
              <table className="data">
                <thead>
                  <tr>
                    <th>来源</th>
                    <th>岗位数</th>
                    <th>投递</th>
                    <th>面试</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((s) => (
                    <tr key={s.source}>
                      <td>{s.name || s.source}</td>
                      <td>{s.jobs}</td>
                      <td>{s.applications}</td>
                      <td>{s.interviews}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {/* 投递最多的公司（按投递数取前 5） */}
          {topEmployers.length > 0 && (
            <>
              <h2>投递最多的公司（前 5）</h2>
              {topEmployers.map((e) => (
                <div className="list-row between" key={e.employer}>
                  <b>{e.employer}</b>
                  <span style={{ color: 'var(--muted)', fontSize: 13 }}>
                    {e.n} 投递 · {e.progressed} 进入流程
                  </span>
                </div>
              ))}
            </>
          )}

          {/* 薪资参考：样本不足就 grey + 徽章，不冒充行情 */}
          <h2>薪资参考</h2>
          {salary && (
            <p className="hint" style={{ marginTop: 0 }}>
              {salary.note}
            </p>
          )}
          <div className="row" style={{ margin: '10px 0' }}>
            <input
              type="text"
              value={cityInput}
              onChange={(e) => setCityInput(e.target.value)}
              placeholder="按城市过滤，如：上海（留空看全部）"
              style={{ maxWidth: 280 }}
            />
          </div>
          {salaryErr && <div className="error-box">薪资数据加载失败：{salaryErr}</div>}
          {!salary && !salaryErr && <div className="empty">薪资数据加载中…</div>}
          {salary &&
            (salary.overall.count === 0 ? (
              <div className="empty">本地库还没有 min/max 双全的薪资样本，给不出分位数。</div>
            ) : (
              <>
                <div
                  className="stat-card"
                  style={salary.overall.count < MIN_MEANINGFUL_SAMPLE ? { opacity: 0.55 } : undefined}
                >
                  <div className="stat-num">
                    {fmtK(salary.overall.p25)} – {fmtK(salary.overall.p75)} K
                  </div>
                  <div className="stat-label">
                    全部样本月薪区间 P25–P75 · 中位 {fmtK(salary.overall.p50)} K · {salary.overall.count} 条样本
                  </div>
                  {salary.overall.count < MIN_MEANINGFUL_SAMPLE && (
                    <div style={{ marginTop: 6 }}>
                      <span className="tag amber">样本不足</span>
                    </div>
                  )}
                </div>

                {salary.by_city.length > 0 ? (
                  <table className="data" style={{ marginTop: 12 }}>
                    <thead>
                      <tr>
                        <th>城市</th>
                        <th>样本</th>
                        <th>P25 (K)</th>
                        <th>P50 (K)</th>
                        <th>P75 (K)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {salary.by_city.map((c) => (
                        <tr
                          key={c.city}
                          style={c.count < MIN_MEANINGFUL_SAMPLE ? { opacity: 0.55 } : undefined}
                        >
                          <td>
                            {c.city}{' '}
                            {c.count < MIN_MEANINGFUL_SAMPLE && <span className="tag amber">样本不足</span>}
                          </td>
                          <td>{c.count}</td>
                          <td>{fmtK(c.p25)}</td>
                          <td>{fmtK(c.p50)}</td>
                          <td>{fmtK(c.p75)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="empty" style={{ marginTop: 10 }}>
                    该筛选下没有分城市样本。
                  </div>
                )}
              </>
            ))}

          {overview && (
            <p className="hint" style={{ marginTop: 24 }}>
              数据生成于 {new Date(overview.generated_at).toLocaleString()}
            </p>
          )}
        </>
      )}
    </div>
  )
}
