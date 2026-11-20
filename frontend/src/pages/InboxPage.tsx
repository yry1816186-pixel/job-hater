import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

/** 文件收件箱：拖文件进 inbox/ → 解析（表格/文档/截图）→ 勾选入库。 */

interface InboxStatus {
  phase: string
  running: boolean
  error: string
  files_seen: number
}

interface InboxItem {
  title: string
  company?: string
  city?: string
  salary?: string
  url?: string
  deadline?: string
  confidence?: number
  source_file?: string
  description?: string
}

interface InboxResults {
  files: { file: string; kind: string; items: number; error?: string }[]
  items: InboxItem[]
  generated_at: number
}

export default function InboxPage() {
  const [status, setStatus] = useState<InboxStatus | null>(null)
  const [results, setResults] = useState<InboxResults | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [msg, setMsg] = useState('')
  const [q, setQ] = useState('')

  const load = async () => {
    setStatus(await api.get<InboxStatus>('/inbox/status'))
    setResults(await api.get<InboxResults>('/inbox/results'))
  }
  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!status?.running) return
    const t = setInterval(async () => {
      const st = await api.get<InboxStatus>('/inbox/status')
      setStatus(st)
      if (!st.running) await load()
    }, 1500)
    return () => clearInterval(t)
  }, [status?.running])

  const items = useMemo(() => {
    const list = results?.items ?? []
    if (!q.trim()) return list
    const k = q.trim().toLowerCase()
    return list.filter((i) => `${i.title}${i.company ?? ''}`.toLowerCase().includes(k))
  }, [results, q])

  const scan = async () => {
    setMsg('')
    await api.post('/inbox/scan', {})
    const st = await api.get<InboxStatus>('/inbox/status')
    setStatus(st)
  }

  const doImport = async () => {
    const idx = [...checked]
    if (!idx.length) { setMsg('先勾选要入库的岗位'); return }
    const out = await api.post<{ imported: number; deduped: number; rejected: number }>(
      '/inbox/import', { indices: idx })
    setMsg(`入库 ${out.imported} 条（去重 ${out.deduped}，拒收 ${out.rejected}）`)
    setChecked(new Set())
  }

  const toggle = (i: number) => {
    const nx = new Set(checked)
    if (nx.has(i)) nx.delete(i); else nx.add(i)
    setChecked(nx)
  }

  const importable = (i: InboxItem) => Boolean(i.company)

  return (
    <div className="page">
      <h2>文件收件箱</h2>
      <p className="muted">
        把岗位物料拖进 <code>~/.jobhater/inbox/</code>：在线表格导出（xlsx/csv）、JD 文档
        （pdf/docx/txt/md）、招聘海报截图（png/jpg）。解析后逐条勾选才入库，原件移入
        processed/ 留证。
      </p>

      <div className="card" style={{ marginBottom: 12 }}>
        <b>状态：</b>{status ? (status.running ? '解析中…' : status.phase) : '…'}
        {status?.error && <span className="muted">（{status.error}）</span>}
        <button className="btn" onClick={scan} disabled={Boolean(status?.running)}>
          扫描 inbox/
        </button>
        <button className="btn" onClick={doImport} disabled={!checked.size}>
          导入选中 {checked.size} 条
        </button>
        {msg && <span className="ok">{msg}</span>}
      </div>

      {results?.files?.length ? (
        <div className="card" style={{ marginBottom: 12 }}>
          <b>最近一批文件：</b>
          {results.files.map((f) => (
            <div key={f.file} className="muted">
              {f.file} → {f.kind}（{f.items} 条）{f.error ? ` ⚠️ ${f.error}` : ''}
            </div>
          ))}
        </div>
      ) : null}

      <input className="input" placeholder="搜索标题/公司…" value={q}
             onChange={(e) => setQ(e.target.value)} />
      <div style={{ marginTop: 8 }}>
        {items.map((it, i) => (
          <label key={i} className="card" style={{ display: 'block', marginBottom: 6 }}>
            <div>
              <input type="checkbox" checked={checked.has(i)} disabled={!importable(it)}
                     onChange={() => toggle(i)} />
              {' '}<b>{it.title || '（未识别标题）'}</b>
              {it.company ? ` @ ${it.company}` : <span className="muted"> @（缺公司，不可入库）</span>}
              {it.city ? ` · ${it.city}` : ''}{it.salary ? ` · ${it.salary}` : ''}
              {it.confidence ? <span className="muted"> · 置信 {it.confidence}</span> : ''}
            </div>
            {it.source_file && <div className="muted">来自：{it.source_file}</div>}
            {it.description && (
              <details><summary className="muted">正文</summary>
                <pre style={{ whiteSpace: 'pre-wrap' }}>
                  {it.description.slice(0, 600)}
                </pre>
              </details>
            )}
          </label>
        ))}
        {!items.length && <p className="muted">暂无解析结果——拖文件进 inbox/ 后点「扫描」。</p>}
      </div>
    </div>
  )
}
