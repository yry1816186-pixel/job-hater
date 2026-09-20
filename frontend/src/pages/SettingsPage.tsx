import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AIProviderInfo, JobSourceInfo } from '../types'

/** 设置与隐私：AI Provider（opt-in）、信源健康、数据出境披露 */
export default function SettingsPage() {
  const [providers, setProviders] = useState<AIProviderInfo[]>([])
  const [sources, setSources] = useState<JobSourceInfo[]>([])
  const [egress, setEgress] = useState<Record<string, string>>({})
  const [form, setForm] = useState({
    kind: 'openai_compatible',
    name: '',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
  })
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = () => {
    api.get<AIProviderInfo[]>('/ai/providers').then(setProviders).catch(() => {})
    api.get<JobSourceInfo[]>('/sources').then(setSources).catch(() => {})
  }
  useEffect(load, [])

  useEffect(() => {
    for (const task of ['job_deep_review', 'resume_rewrite', 'cover_letter']) {
      api
        .get<{ disclosure: string }>(`/ai/egress${new URLSearchParams({ task }).toString()}`)
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
        {Object.entries(egress).map(([task, d]) => (
          <p key={task} style={{ margin: '6px 0', fontSize: 14 }}>
            <b>{task}</b>：{d}
          </p>
        ))}
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
        <p style={{ margin: 0 }}>
          全部业务数据存储于本机数据目录（SQLite 数据库 + 导出文件）。没有遥测、没有统计上报、
          没有云端账号。备份 = 复制数据目录；卸载 = 删除数据目录。
        </p>
      </div>
    </div>
  )
}
