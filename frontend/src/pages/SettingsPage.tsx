import { useEffect, useRef, useState } from 'react'
import { api, qs } from '../api'
import type { AIProviderInfo, JobSourceInfo, Profile } from '../types'
import { AI_TASK_LABELS, ConfirmDialog, useToast } from '../components/ui'

/** 设置与隐私：AI Provider（opt-in）、信源健康、数据出境披露、备份恢复与导出 */
export default function SettingsPage() {
  const { toast } = useToast()
  const [providers, setProviders] = useState<AIProviderInfo[]>([])
  const [sources, setSources] = useState<JobSourceInfo[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [egress, setEgress] = useState<Record<string, string>>({})
  const [form, setForm] = useState({
    kind: 'openai_compatible',
    name: '',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
  })
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [confirmRestore, setConfirmRestore] = useState<File | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () => {
    api.get<AIProviderInfo[]>('/ai/providers').then(setProviders).catch(() => {})
    api.get<JobSourceInfo[]>('/sources').then(setSources).catch(() => {})
    api.get<Profile[]>('/profiles').then(setProfiles).catch(() => {})
  }
  useEffect(load, [])

  useEffect(() => {
    for (const task of ['job_deep_review', 'resume_rewrite', 'cover_letter']) {
      api
        .get<{ disclosure: string }>(`/ai/egress?${new URLSearchParams({ task }).toString()}`)
        .then((r) => setEgress((prev) => ({ ...prev, [task]: r.disclosure })))
        .catch(() => {})
    }
  }, [providers.length])

  const addProvider = async () => {
    setErr('')
    try {
      const r = await api.post<{ id: string }>('/ai/providers', {
        adapter_kind: form.kind,
        display_name: form.name || form.model,
        base_url: form.baseUrl || null,
        model: form.model,
        enabled: false,
      })
      setMsg(`已添加 Provider（${r.id.slice(0, 10)}…）。填入 API Key 后再启用。Key 只存系统钥匙串，不进数据库。`)
      setForm({ ...form, name: '' })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const setKey = async (id: string) => {
    const key = window.prompt('粘贴 API Key（将存入操作系统钥匙串，数据库与日志永不保存）', '')
    if (!key) return
    try {
      await api.post(`/ai/providers/${id}/key`, { api_key: key })
      setMsg('Key 已保存到系统钥匙串。')
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const toggleEnabled = async (p: AIProviderInfo) => {
    setErr('')
    try {
      await api.post(`/ai/providers/${p.id}/enabled`, { enabled: !p.enabled })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  // ---------- 备份恢复 ----------

  const pickRestoreFile = () => fileRef.current?.click()

  const uploadRestore = async (file: File) => {
    setRestoring(true)
    setConfirmRestore(null)
    try {
      const fd = new FormData()
      fd.append('file', file, file.name)
      const resp = await fetch('/api/backup/restore', { method: 'POST', body: fd })
      if (!resp.ok) {
        let detail = `${resp.status} ${resp.statusText}`
        try {
          const body = await resp.json()
          if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
        } catch {
          /* 保持状态行 */
        }
        throw new Error(detail)
      }
      const report = (await resp.json()) as {
        ok: boolean
        schema_version: number
        counts: Record<string, number>
      }
      toast('success', `恢复完成：${Object.entries(report.counts).map(([k, v]) => `${k} ${v} 条`).join('、')}`)
      setMsg(`数据库已从备份恢复（schema v${report.schema_version}）。页面数据在下次加载时生效。`)
      load()
    } catch (e) {
      toast('error', `恢复失败：${(e as Error).message}`)
    } finally {
      setRestoring(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div>
      <h1>设置与隐私</h1>
      <p className="page-sub">业务数据全部在本机；远程 AI 仅在你显式启用后才会接收任务所需数据。</p>
      {err && <div className="error-box">{err}</div>}
      {msg && <div className="card" style={{ borderColor: 'var(--accent)' }}>{msg}</div>}

      <h2>AI Provider（默认关闭 = 本地模式）</h2>
      <div className="card">
        {providers.length === 0 && (
          <p style={{ marginTop: 0, color: 'var(--muted)' }}>
            未配置任何远程模型：当前为<b>本地模式</b>。建档、导入、匹配、投递跟踪、简历编辑导出
            全部可用；仅 AI 深度分析与 AI 改写类功能需要 Provider。
          </p>
        )}
        {providers.map((p) => (
          <div className="between" key={p.id} style={{ borderBottom: '1px solid var(--line)', padding: '8px 0' }}>
            <div>
              <b>{p.display_name}</b> <span className="tag">{p.model}</span>{' '}
              {p.enabled ? <span className="tag green">已启用</span> : <span className="tag">停用</span>}
              <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>
                {p.adapter_kind} · {p.base_url ?? '默认端点'} · Key：{p.has_api_key ? '已配置' : '未配置'}
              </div>
            </div>
            <div className="row">
              <button className="btn small" onClick={() => setKey(p.id)}>
                设置 Key
              </button>
              <button className={`btn small${p.enabled ? '' : ' primary'}`} onClick={() => toggleEnabled(p)}>
                {p.enabled ? '停用' : '启用'}
              </button>
            </div>
          </div>
        ))}
        <h3 style={{ marginBottom: 6 }}>添加 Provider</h3>
        <div className="row">
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="openai_compatible">OpenAI 兼容（GLM/DeepSeek/Qwen/Kimi/vLLM）</option>
            <option value="ollama">Ollama（本地模型，数据不出机器）</option>
            <option value="anthropic">Anthropic</option>
          </select>
          <input type="text" placeholder="显示名" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input type="text" placeholder="Base URL" value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} style={{ flex: 2 }} />
          <input type="text" placeholder="模型名" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
          <button className="btn" onClick={addProvider} disabled={!form.model.trim()}>
            添加
          </button>
        </div>
      </div>

      <h2>数据出境披露（启用远程 AI 时）</h2>
      <div className="card">
        {Object.entries(egress).length === 0 ? (
          <p className="hint">披露信息加载失败或尚未加载——刷新页面重试。</p>
        ) : (
          Object.entries(egress).map(([task, d]) => (
            <p key={task} style={{ margin: '6px 0', fontSize: 14 }}>
              <b>{AI_TASK_LABELS[task] ?? task}</b>：{d}
            </p>
          ))
        )}
        <p className="hint" style={{ marginTop: 8 }}>
          本地模式下以上全部显示「不会发送任何数据」。每次调用 AI 前界面会再次展示对应披露。
        </p>
      </div>

      <h2>信源健康</h2>
      <div className="card">
        {sources.length === 0 ? (
          <div className="empty">暂无信源记录（导入第一条岗位后会自动建立）。</div>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>信源</th>
                <th>类型</th>
                <th>健康</th>
                <th>最近成功</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td>{s.display_name}</td>
                  <td>{s.adapter_kind}</td>
                  <td>
                    <span
                      className={`tag ${s.health_status === 'ok' ? 'green' : s.health_status === 'unknown' ? '' : 'red'}`}
                    >
                      {s.health_status}
                    </span>
                    {s.health_message ? ` ${s.health_message}` : ''}
                  </td>
                  <td>{s.last_success_at?.slice(0, 19).replace('T', ' ') ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <h2>数据位置与备份</h2>
      <div className="card">
        <p style={{ marginTop: 0 }}>
          全部业务数据存储于本机数据目录（SQLite 数据库 + 导出文件）。没有遥测、没有统计上报、
          没有云端账号。
        </p>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <a className="btn primary" href="/api/backup/download" download>
            ⬇ 下载全量备份（.db）
          </a>
          <button className="btn" onClick={pickRestoreFile} disabled={restoring}>
            {restoring ? '恢复中…' : '⬆ 从备份恢复…'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".db,application/octet-stream"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) setConfirmRestore(f)
            }}
          />
        </div>
        <p className="hint" style={{ marginBottom: 0 }}>
          备份包含全部表与索引；API Key 按设计只存系统钥匙串、不在备份内。
          恢复会用备份<b>原位替换</b>当前数据库（三重校验：文件头/完整性/schema 版本一致才执行）。
        </p>
      </div>

      <ConfirmDialog
        open={!!confirmRestore}
        title="确认恢复数据库？"
        body={`将用「${confirmRestore?.name ?? ''}」原位替换当前全部数据（当前数据会被覆盖）。恢复前建议先下载一份当前备份。`}
        confirmText="确认恢复"
        danger
        onCancel={() => setConfirmRestore(null)}
        onConfirm={() => confirmRestore && uploadRestore(confirmRestore)}
      />

      <h2>数据导出（随时带走）</h2>
      <div className="card">
        <p style={{ marginTop: 0 }}>导出永远是免费的——你的数据不锁在本系统里。</p>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          {profiles.length > 0 && (
            <a
              className="btn"
              href={`/api/export/applications.csv${qs({ profile_id: profiles[0].id })}`}
              download
            >
              投递记录 CSV（{profiles[0].display_name}）
            </a>
          )}
          <a className="btn" href="/api/export/jobs.csv" download>
            岗位库 CSV
          </a>
        </div>
        <p className="hint" style={{ marginBottom: 0 }}>
          CSV 带 UTF-8 BOM，Excel 直接打开不乱码。简历的 Markdown / JSON Resume / PDF 导出在「简历」页。
        </p>
      </div>
    </div>
  )
}
