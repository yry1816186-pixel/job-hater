import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, qs } from '../api'
import type { Job, MatchOutcome, Preset } from '../types'
import { useProfiles } from '../App'
import { EmptyState, RECRUIT_LABELS } from '../components/ui'

const PAGE = 30

/** 岗位收件箱：检索 + 筛选 + 匹配分列（无偏好时给引导而非报错） */
export default function JobsPage() {
  const { activeId } = useProfiles()
  const [q, setQ] = useState('')
  const [city, setCity] = useState('')
  const [recruit, setRecruit] = useState('')
  const [sort, setSort] = useState<'recent' | 'match'>('recent')
  const [items, setItems] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [shown, setShown] = useState(PAGE)
  const [matches, setMatches] = useState<Record<string, MatchOutcome>>({})
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [hasPreset, setHasPreset] = useState(true)
  const [staleHint, setStaleHint] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    setStaleHint('')
    // 匹配优先：直接用已存的最近一次匹配结果排序（翻页不重复计算）；
    // 最近入库：老路径——/match/run 现算当前页的分数
    const useStored = sort === 'match' && !!activeId
    api
      .get<{
        total: number
        items: Job[]
        match_by_id?: Record<string, MatchOutcome>
        match_stale_hint?: string
      }>(
        `/jobs${qs({
          q,
          city,
          recruitment_type: recruit,
          limit: shown,
          sort: useStored ? 'match' : undefined,
          profile_id: useStored ? activeId : undefined,
        })}`,
      )
      .then(async (r) => {
        setItems(r.items)
        setTotal(r.total)
        if (useStored) {
          setMatches(r.match_by_id ?? {})
          setStaleHint(r.match_stale_hint ?? '')
          return
        }
        setMatches({})
        if (!activeId) return
        // 先看有没有偏好：无偏好是引导态而非错误态
        const presets = await api
          .get<Preset[]>(`/profiles/${activeId}/presets`)
          .catch(() => [] as Preset[])
        const active = presets.find((p) => p.is_active) ?? presets[0]
        setHasPreset(!!active)
        if (!active || !r.items.length) return
        const run = await api.post<{ results: MatchOutcome[] }>('/match/run', {
          profile_id: activeId,
          preset_id: active.id,
          limit: Math.min(shown, 500),
          statuses: [],
        })
        const map: Record<string, MatchOutcome> = {}
        for (const m of run.results) map[m.job_id] = m
        setMatches(map)
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false))
  }, [q, city, recruit, shown, activeId, sort])

  useEffect(() => {
    const t = setTimeout(load, 300) // 搜索防抖
    return () => clearTimeout(t)
  }, [load])

  return (
    <div>
      <h1>岗位收件箱</h1>
      <p className="page-sub">
        共 {total} 条 · 中文全文检索 · 匹配分与依据来自你的画像和偏好
      </p>
      <div className="row" style={{ marginBottom: 14 }}>
        <input
          type="text"
          placeholder="搜索岗位：关键词、公司、技能…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setShown(PAGE) }}
          style={{ flex: 1, minWidth: 220 }}
          aria-label="搜索岗位"
        />
        <input
          type="text"
          placeholder="城市"
          value={city}
          onChange={(e) => { setCity(e.target.value); setShown(PAGE) }}
          style={{ width: 110 }}
          aria-label="城市筛选"
        />
        <select value={recruit} onChange={(e) => { setRecruit(e.target.value); setShown(PAGE) }} style={{ width: 120 }} aria-label="批次筛选">
          <option value="">全部批次</option>
          <option value="campus">校招</option>
          <option value="social">社招</option>
          <option value="internship">实习</option>
        </select>
        <select
          value={sort}
          onChange={(e) => { setSort(e.target.value as 'recent' | 'match'); setShown(PAGE) }}
          style={{ width: 150 }}
          aria-label="排序方式"
          title={sort === 'match' ? '按最近一次匹配结果排序：合格优先 → 匹配分 → 相关性' : '按入库时间倒序'}
        >
          <option value="recent">最近入库</option>
          <option value="match">匹配优先</option>
        </select>
      </div>
      {err && <div className="error-box">{err}</div>}
      {staleHint && <div className="card hint" style={{ marginBottom: 12, padding: '8px 12px' }}>{staleHint}</div>}

      {loading ? (
        <div className="loading">加载中…</div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="📭"
          title={total === 0 ? '岗位库还是空的' : '没有符合筛选条件的岗位'}
          hint={total === 0 ? '去「导入岗位」粘贴一条 JD 原文——这是永不失败的入口。' : '试试放宽搜索词或清空城市/批次筛选。'}
          action={<Link className="btn primary" to="/import">去导入岗位</Link>}
        />
      ) : (
        <>
          {!hasPreset && activeId && (
            <div className="card cta-card" style={{ marginBottom: 14 }}>
              <b>还没有求职偏好</b>
              <p className="hint" style={{ margin: '4px 0 10px' }}>
                匹配分需要偏好（目标城市/角色/薪资底线）才能计算——1 分钟就能建好。
              </p>
              <Link className="btn primary" to="/profile">去创建求职偏好 →</Link>
            </div>
          )}
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
                      <div style={{ textAlign: 'right' }} title="点击岗位看完整匹配依据">
                        <div className="score">{m.rank_score ?? '—'}</div>
                        <div
                          className="verdict-chip"
                          data-kind={
                            m.eligible
                              ? m.verdict === '强烈推荐' || m.verdict === '推荐'
                                ? 'good'
                                : m.verdict === '可考虑'
                                  ? 'mid'
                                  : 'low'
                              : 'bad'
                          }
                        >
                          {m.eligible ? m.verdict : '不符合硬性偏好'}
                        </div>
                      </div>
                    )}
                  </div>
                </Link>
              )
            })}
          </div>
          {items.length < total && (
            <div style={{ marginTop: 12, textAlign: 'center' }}>
              <button className="btn" onClick={() => setShown((n) => n + PAGE)}>
                加载更多（已显示 {items.length}/{total}）
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
