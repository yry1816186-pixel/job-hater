import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

/** 微信本地数据源：一键「检测 → 扫描 → 识别 → 导入」。全程本地，不上传任何数据。 */

interface WxEnv {
  platform_ok: boolean
  installed: boolean
  version?: string | null
  data_root?: string | null
  data_root_source?: string | null
  accounts: { wxid: string; message_dbs: number; total_db_mb: number }[]
  weixin_running: boolean
  blockers: string[]
}

interface WxHit {
  company?: string | null
  title?: string | null
  cities: string[]
  salary?: string | null
  education?: string | null
  cohort?: number | null
  kind: string
  deadline?: string | null
  apply_method?: string | null
  confidence: number
  evidence: string[]
  is_recruiter_side: boolean
  talker_name: string
  sender_name: string
  time_str: string
  source_text: string
  merged: boolean
}

interface WxStatus {
  phase: string
  message: string
  error?: string | null
  running: boolean
  progress?: Record<string, unknown>
  stats?: Record<string, number>
}

interface WxResults {
  hits: WxHit[]
  stats?: Record<string, number> | null
  generated_at?: number | null
  total: number
}

const PHASE_LABEL: Record<string, string> = {
  idle: '待命',
  detecting: '检测环境…',
  extracting_key: '提取密钥（扫描微信进程内存）…',
  decrypting: '解密数据库…',
  parsing: '解析联系人与消息…',
  analyzing: '识别招聘信息…',
  done: '完成',
  failed: '失败',
}

const KIND_LABEL: Record<string, string> = {
  campus: '校招', intern: '实习', social: '社招', unknown: '未分类',
}

function confidenceColor(c: number): string {
  if (c >= 0.7) return 'var(--ok, #2e7d32)'
  if (c >= 0.5) return 'var(--warn, #b26a00)'
  return 'var(--muted)'
}

export default function WeChatPage() {
  const [env, setEnv] = useState<WxEnv | null>(null)
  const [status, setStatus] = useState<WxStatus | null>(null)
  const [results, setResults] = useState<WxResults | null>(null)
  const [cohortFilter, setCohortFilter] = useState<number | ''>('')
  const [kindFilter, setKindFilter] = useState('')
  const [q, setQ] = useState('')
  const [minConf, setMinConf] = useState(0.35)
  const [importMsg, setImportMsg] = useState('')
  const [importing, setImporting] = useState(false)
  const timerRef = useRef<number | null>(null)

  const refreshEnv = () => {
    api.get<WxEnv>('/wechat/env').then(setEnv).catch(() => setEnv(null))
  }

  useEffect(() => {
    refreshEnv()
    api.get<WxStatus>('/wechat/status').then(setStatus).catch(() => {})
    api.get<WxResults>('/wechat/results').then(setResults).catch(() => {})
  }, [])

  // 扫描期间轮询状态；完成后拉结果
  useEffect(() => {
    if (!status?.running) {
      if (timerRef.current) {
        window.clearInterval(timerRef.current)
        timerRef.current = null
        api.get<WxResults>('/wechat/results').then(setResults).catch(() => {})
      }
      return
    }
    if (timerRef.current) return
    timerRef.current = window.setInterval(() => {
      api.get<WxStatus>('/wechat/status').then(setStatus).catch(() => {})
    }, 1200)
    return () => {
      if (timerRef.current) {
        window.clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [status?.running])

  const startScan = async () => {
    setImportMsg('')
    try {
      const s = await api.post<WxStatus>('/wechat/scan', { min_confidence: 0.35 })
      setStatus(s)
    } catch (e) {
      setImportMsg((e as Error).message)
    }
  }

  const doImport = async () => {
    setImporting(true)
    setImportMsg('')
    try {
      const body: Record<string, unknown> = { min_confidence: minConf }
      if (cohortFilter !== '') body.cohort = cohortFilter
      const r = await api.post<{ message: string }>('/wechat/import', body)
      setImportMsg(r.message)
    } catch (e) {
      setImportMsg((e as Error).message)
    } finally {
      setImporting(false)
    }
  }

  const purge = async () => {
    if (!window.confirm('确认删除本地解密产物与扫描结果缓存？（不影响微信本体数据）')) return
    await api.del('/wechat/data')
    setResults(null)
    setStatus(null)
    setImportMsg('已清除')
  }

  const filtered = useMemo(() => {
    if (!results?.hits) return []
    return results.hits.filter((h) => {
      if (cohortFilter !== '' && h.cohort !== cohortFilter) return false
      if (kindFilter && h.kind !== kindFilter) return false
      if (h.confidence < minConf) return false
      if (q.trim()) {
        const ql = q.trim().toLowerCase()
        const hay = `${h.title ?? ''}${h.company ?? ''}${h.talker_name}${h.source_text}`.toLowerCase()
        if (!hay.includes(ql)) return false
      }
      return true
    })
  }, [results, cohortFilter, kindFilter, minConf, q])

  const cohorts = useMemo(() => {
    const set = new Set<number>()
    results?.hits?.forEach((h) => h.cohort && set.add(h.cohort))
    return [...set].sort()
  }, [results])

  const phase = status?.phase ?? 'idle'
  const busy = !!status?.running

  return (
    <div>
      <h1>微信招聘雷达</h1>
      <p className="page-sub">
        自动检测本机微信（4.x 桌面版）聊天记录，识别其中的招聘信息——群聊转发、HR 私聊、
        公众号推文里的岗位都会被提取成结构化卡片。数据全程在本机处理，不上传。
      </p>

      {/* 环境状态卡 */}
      <div className="card" style={{ marginBottom: 16 }}>
        {env === null ? (
          <p>环境检测中…</p>
        ) : env.blockers.length > 0 ? (
          <div className="error-box">
            {env.blockers.map((b, i) => (
              <div key={i}>· {b}</div>
            ))}
            <div style={{ marginTop: 8 }}>
              <button className="btn" onClick={refreshEnv}>重新检测</button>
            </div>
          </div>
        ) : (
          <div>
            <b>✓ 环境就绪</b>
            <span style={{ color: 'var(--muted)', marginLeft: 8 }}>
              微信 {env.version ?? ''} · {env.accounts.length} 个账号 ·
              主账号 {env.accounts[0]?.wxid}（{env.accounts[0]?.total_db_mb?.toFixed(0)}MB 消息库）
            </span>
            <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn primary" onClick={startScan} disabled={busy}>
                {busy ? '扫描中…' : results?.hits?.length ? '重新扫描' : '一键扫描全部聊天记录'}
              </button>
              {busy && (
                <button className="btn" onClick={() => api.post<WxStatus>('/wechat/stop', {}).then(setStatus)}>
                  停止
                </button>
              )}
              {results?.hits?.length ? (
                <button className="btn" onClick={purge} disabled={busy}>
                  清除本地解密数据
                </button>
              ) : null}
              {env.data_root && (
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                  数据目录：{env.data_root}（只读 + 内存密钥，不改微信文件）
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 扫描进度 */}
      {busy && (
        <div className="card cta-card" style={{ marginBottom: 16 }}>
          <b>{PHASE_LABEL[phase] ?? phase}</b>
          <div style={{ marginTop: 6, fontSize: 13, color: 'var(--muted)' }}>
            {status?.message}
            {status?.progress && Object.keys(status.progress).length > 0 && (
              <span> · {JSON.stringify(status.progress)}</span>
            )}
          </div>
          <div
            aria-hidden
            style={{
              marginTop: 10, height: 6, borderRadius: 3, background: 'var(--border, #ddd)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: phase === 'done' ? '100%' : '40%',
                height: '100%',
                background: 'var(--accent, #4a7dff)',
                animation: 'wx-scan 1.2s ease-in-out infinite alternate',
              } as React.CSSProperties}
            />
          </div>
          <style>{'@keyframes wx-scan { from { transform: translateX(-60%);} to { transform: translateX(180%);} }'}</style>
        </div>
      )}

      {status?.error && (
        <div className="error-box" style={{ marginBottom: 16 }}>
          扫描失败：{status.error}
        </div>
      )}

      {/* 结果 */}
      {results && results.hits && results.hits.length > 0 && (
        <>
          <div className="card" style={{ marginBottom: 12, padding: 12 }}>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <input
                type="text"
                placeholder="搜公司/岗位/群名…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                style={{ flex: 2, minWidth: 160 }}
              />
              <select value={cohortFilter} onChange={(e) => setCohortFilter(e.target.value === '' ? '' : Number(e.target.value))} aria-label="届别筛选">
                <option value="">全部届别</option>
                {cohorts.map((c) => (
                  <option key={c} value={c}>{c} 届</option>
                ))}
              </select>
              <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} aria-label="类型筛选">
                <option value="">全部类型</option>
                <option value="campus">校招</option>
                <option value="intern">实习</option>
                <option value="social">社招</option>
              </select>
              <label style={{ fontSize: 12.5, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
                置信度 ≥ {(minConf * 100).toFixed(0)}%
                <input
                  type="range" min={0.35} max={0.95} step={0.05} value={minConf}
                  onChange={(e) => setMinConf(Number(e.target.value))}
                />
              </label>
              <b style={{ marginLeft: 'auto' }}>{filtered.length} / {results.total} 条</b>
            </div>
          </div>

          <div style={{ marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
            <button className="btn primary" onClick={doImport} disabled={importing || busy || !filtered.length}>
              {importing ? '导入中…' : `导入筛选后的 ${filtered.length} 条到岗位库`}
            </button>
            <Link className="btn" to="/jobs">去岗位收件箱 →</Link>
            {importMsg && <span style={{ color: 'var(--ok, #2e7d32)' }}>{importMsg}</span>}
          </div>

          {filtered.map((h, i) => (
            <div key={i} className="card" style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <b style={{ fontSize: 15 }}>{h.title ?? '（未识别岗位名）'}</b>
                {h.company && <span>@ {h.company}</span>}
                <span style={{ color: confidenceColor(h.confidence) }}>
                  置信 {(h.confidence * 100).toFixed(0)}%
                </span>
                {h.cohort && <span className="tag">{h.cohort} 届</span>}
                {h.kind !== 'unknown' && <span className="tag">{KIND_LABEL[h.kind] ?? h.kind}</span>}
                {h.cities.slice(0, 3).map((c) => (
                  <span key={c} className="tag">{c}</span>
                ))}
                {h.salary && <span className="tag">💰 {h.salary}</span>}
                {h.deadline && <span className="tag">⏰ 截止 {h.deadline}</span>}
                {!h.is_recruiter_side && <span className="tag" style={{ opacity: 0.6 }}>疑似求职方</span>}
              </div>
              <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--muted)' }}>
                来源：{h.talker_name} · {h.sender_name} · {h.time_str}
                {h.merged ? ' · 多条合并' : ''} · {h.apply_method ?? ''}
              </div>
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer', fontSize: 13 }}>原文与依据</summary>
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 6 }}>
                    识别依据：{h.evidence.join('；')}
                  </div>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12.5, margin: 0 }}>
                    {h.source_text.slice(0, 1200)}
                    {h.source_text.length > 1200 ? '\n…' : ''}
                  </pre>
                </div>
              </details>
            </div>
          ))}
        </>
      )}

      {!busy && results && (!results.hits || results.hits.length === 0) && (
        <div className="empty-state">
          <div className="empty-icon" aria-hidden>🔍</div>
          <h3>还没有扫描结果</h3>
          <p>点击上方按钮开始扫描。首次扫描大约需要 1-3 分钟（取决于消息量）。</p>
        </div>
      )}
    </div>
  )
}
